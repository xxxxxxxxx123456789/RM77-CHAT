#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
██████╗ ███╗   ███╗ ██████╗  ██████╗      RM77 CHAT v4.0
██╔══██╗████╗ ████║██╔════╝ ╚════██╗     Internet group chat
██████╔╝██╔████╔██║██║  ███╗ █████╔╝     No server. No IP. No port forward.
██╔══██╗██║╚██╔╝██║██║   ██║██╔═══╝     Just share a room code.
██║  ██║██║ ╚═╝ ██║╚██████╔╝███████╗
╚═╝  ╚═╝╚═╝     ╚═╝ ╚═════╝ ╚══════╝
"""

import argparse
import json
import os
import random
import sys
import threading
import time
from datetime import datetime

# Required: pip install paho-mqtt
import paho.mqtt.client as mqtt

try:
    import colorama
    colorama.init()
except ImportError:
    pass

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
CLR_LN = "\033[2K\r"

COLORS = {
    "red": "\033[91m", "green": "\033[92m", "yellow": "\033[93m",
    "blue": "\033[94m", "magenta": "\033[95m", "cyan": "\033[96m",
    "purple": "\033[35m", "white": "\033[97m",
}

# Free public relays — no auth, anyone can connect from anywhere.
# If one is down, the tool automatically falls through to the next.
BROKERS = [
    ("broker.emqx.io", 1883),
    ("broker.hivemq.com", 1883),
    ("test.mosquitto.org", 1883),
]

LOG_FILE = "rm77_history.log"

BANNER = r"""
 ██████╗ ███╗   ███╗ ██████╗  ██████╗
 ██╔══██╗████╗ ████║██╔════╝ ╚════██╗
 ██████╔╝██╔████╔██║██║  ███╗ █████╔╝
 ██╔══██╗██║╚██╔╝██║██║   ██║██╔═══╝
 ██║  ██║██║ ╚═╝ ██║╚██████╔╝███████╗
 ╚═╝  ╚═╝╚═╝     ╚═╝ ╚═════╝ ╚══════╝
""".strip("\n")

def show_banner():
    pal = [COLORS["magenta"], COLORS["cyan"]]
    for i, line in enumerate(BANNER.splitlines()):
        print(pal[i % 2] + line + RESET)
    print(DIM + "  ===== RM77 CHAT v4 - INTERNET GROUP CHAT =====\n" + RESET)

def now_ts():
    return datetime.now().strftime("%H:%M:%S")

def clean_name(name, limit=16):
    name = "".join(c for c in (name or "").strip() if c.isprintable())
    return (name.replace(" ", "_") or "Anonymous")[:limit]

def make_code(n=6):
    return "".join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
                   for _ in range(n))

def gen_cid():
    return "rm77-" + str(random.randint(10**14, 10**15 - 1))

def notify(title, message, bells=1, ghost=False):
    if ghost:
        return
    try:
        sys.stdout.write("\a" * bells); sys.stdout.flush()
    except Exception:
        pass
    try:
        from plyer import notification
        notification.notify(title=title, message=message, timeout=3)
    except Exception:
        pass

# ------------------------------------------------------------------
# Internet chat client (everyone connects OUT to a public broker)
# ------------------------------------------------------------------
HELP_TEXT = """
  /help              show this help
  /users             who is online right now
  /nick <name>       change your name
  /color <color>     change your color
  /w <name> <msg>    private whisper
  /me <action>       do an action
  /ping              test connection latency
  /time              toggle timestamps
  /clear             clear the screen
  /wipe              delete the local log file NOW
  /panic             emergency: disconnect + delete all local traces
  /quit              leave the room
"""

class ChatClient:
    def __init__(self, code, name, color, ghost=False):
        self.code = code.upper()
        self.name, self.color = clean_name(name), color
        self.ghost = ghost
        self.cid = gen_cid()
        self.running = True
        self.connected = False
        self.show_time = True
        self.pub_lock = threading.Lock()
        self.print_lock = threading.Lock()
        self.users = {}                 # name -> {"color", "last"}
        self.prompt = f"{BOLD}{COLORS[color]}{self.name}{RESET} > "

        self.mqtt = mqtt.Client(client_id=self.cid, protocol=mqtt.MQTTv311)
        self.mqtt.on_connect = self._on_connect
        self.mqtt.on_message = self._on_message
        self.mqtt.on_disconnect = self._on_disconnect

        # topics
        self.t_chat = f"rm77/{self.code}/chat"
        self.t_sys  = f"rm77/{self.code}/sys"
        self.t_pre  = f"rm77/{self.code}/presence"
        self.t_wh   = f"rm77/{self.code}/whisper"

    # ----- helpers -----
    def _pub(self, topic, payload):
        with self.pub_lock:
            self.mqtt.publish(topic, json.dumps(payload, ensure_ascii=False))

    def _print(self, text):
        with self.print_lock:
            sys.stdout.write(CLR_LN + text + "\n" + self.prompt)
            sys.stdout.flush()

    def _log(self, plain):
        if self.ghost:
            return
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(plain + "\n")
        except Exception:
            pass

    def ts(self):
        return f"{DIM}[{now_ts()}]{RESET}" if self.show_time else ""

    # ----- mqtt callbacks -----
    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            self.mqtt.subscribe([(self.t_chat, 0), (self.t_sys, 0),
                                 (self.t_pre, 0), (self.t_wh, 0)])
            self._announce_join()
            self._print(BOLD + COLORS["green"] +
                        f"[!] Connected to internet room {self.code} — sharing link ready." + RESET)
            self._log(f"[{now_ts()}] == connected to room {self.code} as {self.name} ==")
        else:
            self._print(BOLD + COLORS["red"] + f"[!] Broker refused connection (rc={rc})." + RESET)

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        if self.running:
            self._print(DIM + "[!] Disconnected from broker. Reconnecting..." + RESET)
            threading.Timer(3.0, self._try_reconnect).start()

    def _try_reconnect(self):
        if not self.running:
            return
        try:
            self.mqtt.reconnect()
        except Exception:
            threading.Timer(3.0, self._try_reconnect).start()

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8", "replace"))
        except (ValueError, json.JSONDecodeError):
            return
        t = msg.topic
        if t == self.t_chat:
            self._show_chat(data)
        elif t == self.t_sys:
            self._show_system(data)
        elif t == self.t_pre:
            self._show_presence(data)
        elif t == self.t_wh:
            self._show_whisper(data)

    # ----- rendering -----
    def _show_chat(self, d):
        n, col, txt = d.get("name", "?"), d.get("color", "white"), d.get("text", "")
        self._touch(n, col)
        if n == self.name:
            return
        self._print(f"{self.ts()} {BOLD}{COLORS.get(col,COLORS['white'])}{n}{RESET}"
                    f"{DIM}:{RESET} {COLORS.get(col,COLORS['white'])}{txt}{RESET}")
        self._log(f"[{now_ts()}] {n}: {txt}")
        self._mention_check(n, txt)
        notify("RM77", f"{n}: {txt[:80]}", ghost=self.ghost)

    def _show_system(self, d):
        self._print(DIM + f"{self.ts()} {d.get('text','')}" + RESET)
        self._log(f"[{now_ts()}] {d.get('text','')}")

    def _show_presence(self, d):
        n, col = d.get("name"), d.get("color", "cyan")
        if not n:
            return
        kind = d.get("kind", "here")
        if kind == "join":
            self._touch(n, col)
            if n != self.name:
                self._print(f"{self.ts()} {DIM}-> {BOLD}{COLORS.get(col,COLORS['cyan'])}{n}"
                            f"{RESET}{DIM} joined the room ({self._online_count()} online){RESET}")
                self._log(f"[{now_ts()}] {n} joined ({self._online_count()} online)")
                notify("RM77", f"{n} joined the room", ghost=self.ghost)
        elif kind == "leave":
            if n != self.name:
                self._print(f"{self.ts()} {DIM}<- {BOLD}{COLORS.get(col,COLORS['cyan'])}{n}"
                            f"{RESET}{DIM} left the room ({self._online_count()} online){RESET}")
                self._log(f"[{now_ts()}] {n} left ({self._online_count()} online)")

    def _show_whisper(self, d):
        if d.get("to", "").lower() != self.name.lower():
            return
        sender, col, txt = d.get("from", "?"), d.get("color", "white"), d.get("text", "")
        self._print(f"{self.ts()} {BOLD}{COLORS.get(col,COLORS['white'])}{sender}{RESET}"
                    f"{COLORS['magenta']}->{RESET}{BOLD}{COLORS['magenta']}you{RESET}"
                    f"{DIM} (whisper){RESET} {txt}")
        self._log(f"[{now_ts()}] (whisper) {sender} -> you: {txt}")
        notify("RM77", f"Whisper from {sender}: {txt[:60]}", ghost=self.ghost)

    def _mention_check(self, sender, text):
        words = {w.strip(".,!?@:;") for w in text.lower().split()}
        if sender != self.name and (self.name.lower() in words or
                                    f"@{self.name.lower()}" in text.lower()):
            self._print(f"{BOLD}{COLORS['yellow']}>> {sender} mentioned you <<{RESET}")
            notify("RM77", f"{sender} mentioned you!", bells=3, ghost=self.ghost)

    # ----- presence management -----
    def _touch(self, name, color):
        self.users[name.lower()] = {"name": name, "color": color, "last": time.time()}

    def _online_count(self):
        self._purge()
        return len(self.users)

    def _purge(self):
        now = time.time()
        drop = [k for k, v in self.users.items() if now - v["last"] > 20]
        for k in drop:
            self.users.pop(k, None)

    def _announce_join(self):
        # persistent "here" beacon + tell others we arrived
        self.mqtt.publish(self.t_pre, json.dumps(
            {"kind": "join", "name": self.name, "color": self.color}, ensure_ascii=False), qos=0)
        # LWT: if we crash, others learn we left
        self.mqtt.will_set(self.t_pre, json.dumps(
            {"kind": "leave", "name": self.name, "color": self.color}, ensure_ascii=False), qos=0)

    def _heartbeat(self):
        while self.running and self.connected:
            self._pub(self.t_pre, {"kind": "here", "name": self.name,
                                   "color": self.color, "t": time.time()})
            time.sleep(5)

    # ----- commands -----
    def handle_command(self, raw):
        parts = raw.split()
        cmd = parts[0].lower()
        if cmd == "/help":
            self._print(DIM + HELP_TEXT + RESET)
        elif cmd == "/users":
            self._purge()
            if not self.users:
                self._print(DIM + "  Nobody else is online yet. Share code " +
                            BOLD + COLORS["cyan"] + self.code + RESET)
            else:
                names = [f"{BOLD}{COLORS.get(u['color'],COLORS['white'])}{u['name']}{RESET}"
                         for u in self.users.values()]
                self._print(DIM + f"Online ({len(self.users)}): " + RESET + "  ".join(names))
        elif cmd == "/clear":
            os.system("cls" if os.name == "nt" else "clear")
            show_banner()
        elif cmd == "/nick" and len(parts) >= 2:
            self.name = clean_name(parts[1])
            self.prompt = f"{BOLD}{COLORS[self.color]}{self.name}{RESET} > "
            self._pub(self.t_pre, {"kind": "here", "name": self.name, "color": self.color})
            self._print(DIM + f"[!] You are now {self.name}." + RESET)
        elif cmd == "/color" and len(parts) >= 2:
            if parts[1].lower() in COLORS:
                self.color = parts[1].lower()
                self.prompt = f"{BOLD}{COLORS[self.color]}{self.name}{RESET} > "
                self._pub(self.t_pre, {"kind": "here", "name": self.name, "color": self.color})
                self._print(DIM + f"[!] Color changed to {self.color}." + RESET)
            else:
                self._print(DIM + "[!] Colors: " + ", ".join(COLORS) + RESET)
        elif cmd in ("/w", "/whisper") and len(parts) >= 3:
            self._pub(self.t_wh, {"from": self.name, "color": self.color,
                                  "to": parts[1], "text": " ".join(parts[2:])})
        elif cmd == "/me" and len(parts) >= 2:
            self._pub(self.t_chat, {"type": "me", "name": self.name,
                                    "color": self.color, "text": " ".join(parts[1:])})
        elif cmd == "/ping":
            t0 = time.time()
            self._pub(self.t_sys, {"type": "ping"})
            threading.Timer(1.5, lambda: self._print(
                DIM + f"[!] RTT ~{(time.time()-t0)*1000:.0f} ms (via internet relay)" + RESET)).start()
        elif cmd == "/time":
            self.show_time = not self.show_time
            self._print(DIM + f"[!] Timestamps {'on' if self.show_time else 'off'}." + RESET)
        elif cmd == "/wipe":
            self._wipe()
        elif cmd == "/panic":
            self._panic()
        elif cmd in ("/quit", "/exit", "/leave"):
            self.leave()
        else:
            self._print(DIM + "[!] Unknown command. Type /help." + RESET)

    def _wipe(self):
        try:
            if os.path.exists(LOG_FILE):
                os.remove(LOG_FILE)
                self._print(BOLD + COLORS["green"] + "[!] Local log deleted." + RESET)
            else:
                self._print(DIM + "[!] No log file found." + RESET)
        except Exception as e:
            self._print(DIM + f"[!] Could not delete log: {e}" + RESET)

    def _panic(self):
        self._wipe()
        self.leave()
        print(DIM + "\n[!] Panic exit. All local traces cleared." + RESET)
        sys.exit(0)

    def leave(self):
        if self.connected:
            self.mqtt.publish(self.t_pre, json.dumps(
                {"kind": "leave", "name": self.name, "color": self.color},
                ensure_ascii=False), qos=0)
            self.mqtt.disconnect()
        self.running = False
        print(DIM + "\n[!] Disconnected." + RESET)

    # ----- run -----
    def run(self):
        # try each public broker until one connects
        for host, port in BROKERS:
            if not self.running:
                return
            print(DIM + f"  Dialing internet relay {host}:{port} ..." + RESET)
            try:
                self.mqtt.connect(host, port, keepalive=30)
                break
            except Exception as e:
                print(DIM + f"  - {host} unavailable ({e})" + RESET)
                continue
        else:
            print(BOLD + COLORS["red"] + "\n[!] No public relay reachable. Check your internet." + RESET)
            return

        self.mqtt.loop_start()
        threading.Thread(target=self._heartbeat, daemon=True).start()

        try:
            while self.running:
                try:
                    raw = input(self.prompt)
                except (EOFError, KeyboardInterrupt):
                    print(); self.leave(); break
                text = raw.strip()
                if not text:
                    continue
                if text.startswith("/"):
                    self.handle_command(text)
                else:
                    self._pub(self.t_chat, {"type": "chat", "name": self.name,
                                            "color": self.color, "text": text})
        finally:
            self.running = False
            try:
                self.mqtt.loop_stop()
                self.mqtt.disconnect()
            except Exception:
                pass

# ------------------------------------------------------------------
def get_identity(args):
    name = clean_name(args.name) if args.name else clean_name(input("  Enter your name: "))
    color = args.color if args.color in COLORS else None
    if not color:
        print("\n  Choose your chat color:")
        for i, cname in enumerate(COLORS):
            print(f"    [{i+1}] {COLORS[cname]}{cname:<9}{RESET}")
        while True:
            c = input("\n  Color name or number: ").strip().lower()
            if c.isdigit() and 1 <= int(c) <= len(COLORS):
                color = list(COLORS)[int(c)-1]; break
            if c in COLORS:
                color = c; break
            print("  Invalid.")
    print()
    return name, color

def main():
    ap = argparse.ArgumentParser(description="RM77 CHAT v4 - internet group chat")
    ap.add_argument("--name", help="your display name")
    ap.add_argument("--color", help="your color")
    ap.add_argument("--code", help="room code (6 letters/numbers)")
    ap.add_argument("-g", "--ghost", action="store_true",
                    help="GHOST MODE: no logs, no sounds, no traces")
    args = ap.parse_args()

    show_banner()
    if args.ghost:
        print(BOLD + COLORS["red"] + "  >> GHOST MODE: logging + notifications disabled <<\n" + RESET)

    name, color = get_identity(args)

    print("  How do you want to chat from anywhere?")
    print("    [1] CREATE a room   (I share a code with friends)")
    print("    [2] JOIN a room     (I enter a code a friend gave me)")
    while True:
        c = input("  Choice [1/2]: ").strip()
        if c in ("1", "2"):
            break
        print("  Invalid.")

    code = args.code
    if not code:
        code = make_code() if c == "1" else input("  Room code from your friend: ").strip().upper()

    print()
    if c == "1":
        print(BOLD + COLORS["green"] + "  >>> ROOM CREATED <<<" + RESET)
        print(f"  Share this code with friends ANYWHERE in the world:\n")
        print(f"      {BOLD}{COLORS['cyan']}{code}{RESET}\n")
        print(DIM + "  They run this tool, choose JOIN, and type the code." + RESET)
        print(DIM + "  Works on wifi, hotspot, or mobile data — no server, no IP, no port forward.\n")

    client = ChatClient(code, name, color, args.ghost)
    client.run()

if __name__ == "__main__":
    main()
