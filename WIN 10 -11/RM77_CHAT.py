#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RM77 CHAT - Internet group chat (Python version, auto-installs requirements)
Works on Windows 10/11, Linux, macOS, Termux - all interoperate.
No server, no IP, no port forwarding. Everyone connects OUT to a free
public broker. Your room code = your channel. Share the code, that's it.
"""

import argparse
import importlib
import json
import os
import random
import subprocess
import sys
import threading
import time
from datetime import datetime

# ---------------- auto-install missing requirements ----------------
def _ensure(pkg, modname):
    try:
        return importlib.import_module(modname)
    except ImportError:
        print("[i] First run: installing %s ..." % pkg)
        for args in (["-m", "pip", "install", "--quiet", "--disable-pip-version-check", pkg],
                     ["-m", "pip", "install", "--quiet", "--user", pkg]):
            try:
                subprocess.check_call([sys.executable] + args)
                return importlib.import_module(modname)
            except Exception:
                continue
        print("[!] Could not auto-install %s. Run manually:" % pkg)
        print("    %s -m pip install %s" % (sys.executable, pkg))
        sys.exit(1)

mqtt = _ensure("paho-mqtt", "paho.mqtt.client")
colorama = _ensure("colorama", "colorama")
colorama.init()

try:  # utf-8 output on Windows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# compat with both paho-mqtt 1.x and 2.x
try:
    mqtt.CallbackAPIVersion
    def _mkclient(cid):
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=cid,
                           protocol=mqtt.MQTTv311)
except AttributeError:
    def _mkclient(cid):
        return mqtt.Client(client_id=cid, protocol=mqtt.MQTTv311)

# ---------------- constants ----------------
RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"; CLR = "\033[2K\r"
COLORS = {"red": "\033[91m", "green": "\033[92m", "yellow": "\033[93m",
          "blue": "\033[94m", "magenta": "\033[95m", "cyan": "\033[96m",
          "purple": "\033[35m", "white": "\033[97m"}
BROKERS = [("broker.emqx.io", 1883), ("broker.hivemq.com", 1883),
           ("test.mosquitto.org", 1883)]
LOG_FILE = "rm77_history.log"
BANNER = ("  RRRR   M   M  77777  77777\n"
          "  R   R  MM MM    7      7\n"
          "  R   R  MM MM   7      7\n"
          "  RRRR   M M M  7      7\n"
          "  R  R   M   M 7      7\n"
          "  R   R  M   M 7      7\n"
          "  R    R M   M 77777  77777")
HELP = """  /help   show help          /code   show room code
  /users  who's online       /nick <n> rename      /color <c>
  /w <n> <msg> whisper       /me <action>          /ping
  /time   /clear             /wipe delete log      /panic erase+exit
  /quit leave"""

def show_banner():
    for i, l in enumerate(BANNER.split("\n")):
        print((COLORS["magenta"] if i % 2 == 0 else COLORS["cyan"]) + l + RESET)
    print(DIM + "  ==== RM77 CHAT - INTERNET GROUP CHAT (all devices) ====\n" + RESET)

def now_ts():
    return datetime.now().strftime("%H:%M:%S")

def clean(n, l=16):
    n = "".join(c for c in str(n or "").strip() if c.isprintable())
    return (n.replace(" ", "_") or "Anonymous")[:l]

def gen_code(n=6):
    return "".join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
                   for _ in range(n))

def notify(t, m, b=1, ghost=False):
    if ghost:
        return
    try:
        sys.stdout.write("\a" * b); sys.stdout.flush()
    except Exception:
        pass
    try:
        from plyer import notification
        notification.notify(title=t, message=m, timeout=3)
    except Exception:
        pass

# ---------------- chat client ----------------
class Chat:
    def __init__(self, cd, name, color, ghost=False):
        self.code, self.name, self.color, self.ghost = cd, clean(name), color, ghost
        self.cid = "rm77-" + str(random.randint(10 ** 14, 10 ** 15 - 1))
        self.running = True
        self.connected = False
        self.show_time = True
        self.pending = None
        self.pub_lock = threading.Lock()
        self.print_lock = threading.Lock()
        self.users = {}
        self.prompt = BOLD + COLORS[color] + self.name + RESET + " > "
        self.tc = "rm77/" + cd + "/chat"
        self.tsy = "rm77/" + cd + "/sys"
        self.tp = "rm77/" + cd + "/presence"
        self.tw = "rm77/" + cd + "/whisper"
        self.m = _mkclient(self.cid)
        self.m.on_connect = self.oc
        self.m.on_message = self.om
        self.m.on_disconnect = self.od

    # --- low-level helpers ---
    def pub(self, t, p):
        with self.pub_lock:
            self.m.publish(t, json.dumps(p, ensure_ascii=False))

    def show(self, t):
        with self.print_lock:
            sys.stdout.write(CLR + t + "\n" + self.prompt)
            sys.stdout.flush()

    def log(self, p):
        if self.ghost:
            return
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(p + "\n")
        except Exception:
            pass

    def ts(self):
        return (DIM + "[" + now_ts() + "]" + RESET) if self.show_time else ""

    # --- presence ---
    def touch(self, n, c):
        self.users[n.lower()] = {"name": n, "color": c, "last": time.time()}

    def purge(self):
        t = time.time()
        for k in [k for k, v in self.users.items() if t - v["last"] > 20]:
            self.users.pop(k, None)

    # --- mqtt callbacks ---
    def oc(self, c, u, f, rc):
        if rc != 0:
            self.show(BOLD + COLORS["red"] + "[!] Relay refused (rc=%d)" % rc + RESET)
            return
        self.connected = True
        self.m.subscribe([(self.tc, 0), (self.tsy, 0), (self.tp, 0), (self.tw, 0)])
        self.touch(self.name, self.color)
        self.pub(self.tp, {"kind": "join", "name": self.name, "color": self.color})
        self.m.will_set(self.tp, json.dumps({"kind": "leave", "name": self.name,
                                             "color": self.color}))
        self.show(BOLD + COLORS["green"] + "[!] Connected to room " + self.code +
                  " - share the code!" + RESET)
        self.log("[%s] == joined %s ==" % (now_ts(), self.code))

    def od(self, c, u, rc):
        self.connected = False
        if self.running:
            self.show(DIM + "[!] Dropped. Reconnecting..." + RESET)
            threading.Timer(3, self.rec).start()

    def rec(self):
        if not self.running:
            return
        try:
            self.m.reconnect()
        except Exception:
            threading.Timer(3, self.rec).start()

    def om(self, c, u, m):
        try:
            d = json.loads(m.payload.decode("utf-8", "replace"))
        except Exception:
            return
        t = m.topic
        if t == self.tc:
            self.chat(d)
        elif t == self.tsy:
            self.sys(d)
        elif t == self.tp:
            self.pres(d)
        elif t == self.tw:
            self.whis(d)

    # --- rendering ---
    def chat(self, d):
        n, c, t = d.get("name", "?"), d.get("color", "white"), d.get("text", "")
        self.touch(n, c)
        if n == self.name:
            return
        if d.get("type") == "me":
            self.show(self.ts() + " " + BOLD + "* " + COLORS.get(c, COLORS["cyan"]) +
                      n + RESET + " " + t)
            self.log("[%s] * %s %s" % (now_ts(), n, t))
            self.mention(n, t)
            return
        self.show(self.ts() + " " + BOLD + COLORS.get(c, COLORS["white"]) + n + RESET +
                  DIM + ":" + RESET + " " + COLORS.get(c, COLORS["white"]) + t + RESET)
        self.log("[%s] %s: %s" % (now_ts(), n, t))
        self.mention(n, t)
        notify("RM77", n + ": " + t[:80], ghost=self.ghost)

    def sys(self, d):
        st = d.get("type")
        if st == "ping":
            if d.get("from") != self.name:
                self.pub(self.tsy, {"type": "pong", "id": d.get("id")})
            return
        if st == "pong":
            if self.pending and d.get("id") == self.pending[0]:
                rtt = (time.time() - self.pending[1]) * 1000
                self.pending = None
                self.show(BOLD + COLORS["green"] + "[!] Pong! ~%.0f ms" % rtt + RESET)
            return
        self.show(DIM + self.ts() + " " + d.get("text", "") + RESET)
        self.log("[%s] %s" % (now_ts(), d.get("text", "")))

    def pres(self, d):
        n, c = d.get("name"), d.get("color", "cyan")
        if not n:
            return
        k = d.get("kind", "here")
        if k == "here":
            self.touch(n, c)
        elif k == "join":
            self.touch(n, c)
            if n != self.name:
                cnt = len(self.users)
                self.show(self.ts() + " " + DIM + "-> " + BOLD +
                          COLORS.get(c, COLORS["cyan"]) + n + RESET + DIM +
                          " joined (%d online)" % cnt + RESET)
                self.log("[%s] %s joined (%d)" % (now_ts(), n, cnt))
                notify("RM77", n + " joined", ghost=self.ghost)
        elif k == "leave":
            self.users.pop(n.lower(), None)
            if n != self.name:
                cnt = len(self.users)
                self.show(self.ts() + " " + DIM + "<- " + BOLD +
                          COLORS.get(c, COLORS["cyan"]) + n + RESET + DIM +
                          " left (%d online)" % cnt + RESET)
                self.log("[%s] %s left (%d)" % (now_ts(), n, cnt))

    def whis(self, d):
        if d.get("to", "").lower() != self.name.lower():
            return
        n, c, t = d.get("from", "?"), d.get("color", "white"), d.get("text", "")
        self.show(self.ts() + " " + BOLD + COLORS.get(c, COLORS["white"]) + n + RESET +
                  COLORS["magenta"] + " -> " + RESET + BOLD + COLORS["magenta"] +
                  "you" + RESET + DIM + " (whisper)" + RESET + " " + t)
        self.log("[%s] (w) %s -> you: %s" % (now_ts(), n, t))
        notify("RM77", "Whisper from " + n, ghost=self.ghost)

    def mention(self, sender, text):
        w = {x.strip(".,!?@:;") for x in text.lower().split()}
        if sender != self.name and (self.name.lower() in w or
                                    ("@" + self.name.lower()) in text.lower()):
            self.show(BOLD + COLORS["yellow"] + ">> " + sender + " mentioned you <<" + RESET)
            notify("RM77", sender + " mentioned you!", 3, ghost=self.ghost)

    def beat(self):
        while self.running:
            if self.connected:
                self.pub(self.tp, {"kind": "here", "name": self.name, "color": self.color})
            time.sleep(5)

    # --- commands ---
    def cmd(self, raw):
        p = raw.split()
        c = p[0].lower()
        if c == "/help":
            self.show(DIM + HELP + RESET)
        elif c == "/code":
            self.show(DIM + "[!] Room code: " + BOLD + COLORS["cyan"] + self.code + RESET)
        elif c == "/users":
            self.purge()
            if not self.users:
                self.show(DIM + "  None yet. Share code " + BOLD +
                          COLORS["cyan"] + self.code + RESET)
            else:
                nm = [BOLD + COLORS.get(u["color"], COLORS["white"]) + u["name"] + RESET
                      for u in self.users.values()]
                self.show(DIM + "Online (%d): " % len(self.users) + RESET + "  ".join(nm))
        elif c == "/clear":
            os.system("cls" if os.name == "nt" else "clear")
            show_banner()
        elif c == "/nick" and len(p) >= 2:
            self.name = clean(p[1])
            self.prompt = BOLD + COLORS[self.color] + self.name + RESET + " > "
            self.touch(self.name, self.color)
            self.pub(self.tp, {"kind": "here", "name": self.name, "color": self.color})
            self.show(DIM + "[!] You are now " + self.name + RESET)
        elif c == "/color" and len(p) >= 2:
            if p[1].lower() in COLORS:
                self.color = p[1].lower()
                self.prompt = BOLD + COLORS[self.color] + self.name + RESET + " > "
                self.touch(self.name, self.color)
                self.pub(self.tp, {"kind": "here", "name": self.name, "color": self.color})
                self.show(DIM + "[!] Color changed." + RESET)
            else:
                self.show(DIM + "[!] Colors: " + ", ".join(COLORS) + RESET)
        elif c in ("/w", "/whisper") and len(p) >= 3:
            txt = " ".join(p[2:])
            self.pub(self.tw, {"from": self.name, "color": self.color,
                               "to": p[1], "text": txt})
            self.show(self.ts() + " " + BOLD + COLORS[self.color] + "you" + RESET +
                      COLORS["magenta"] + " -> " + RESET + BOLD + COLORS["magenta"] +
                      p[1] + RESET + DIM + " (whisper)" + RESET + " " + txt)
        elif c == "/me" and len(p) >= 2:
            self.pub(self.tc, {"type": "me", "name": self.name, "color": self.color,
                               "text": " ".join(p[1:])})
        elif c == "/ping":
            pid = random.randint(0, 10 ** 9)
            self.pending = (pid, time.time())
            self.pub(self.tsy, {"type": "ping", "id": pid, "from": self.name})
            threading.Timer(2.5, self.pingto, args=(pid,)).start()
        elif c == "/time":
            self.show_time = not self.show_time
            self.show(DIM + "[!] Timestamps " +
                      ("on" if self.show_time else "off") + RESET)
        elif c == "/wipe":
            self.wipe()
        elif c == "/panic":
            self.wipe()
            self.leave()
            print(DIM + "\n[!] Panic exit. All traces cleared." + RESET)
            sys.exit(0)
        elif c in ("/quit", "/exit", "/leave"):
            self.leave()
        else:
            self.show(DIM + "[!] Unknown. Type /help." + RESET)

    def wipe(self):
        try:
            if os.path.exists(LOG_FILE):
                os.remove(LOG_FILE)
                self.show(BOLD + COLORS["green"] + "[!] Log deleted." + RESET)
            else:
                self.show(DIM + "[!] No log file." + RESET)
        except Exception as e:
            self.show(DIM + "[!] %s" % e + RESET)

    def pingto(self, pid):
        if self.pending and self.pending[0] == pid:
            self.pending = None
            self.show(DIM + "[!] No peer answered." + RESET)

    def leave(self):
        if self.connected:
            self.pub(self.tp, {"kind": "leave", "name": self.name, "color": self.color})
            try:
                self.m.disconnect()
            except Exception:
                pass
        self.running = False
        print(DIM + "\n[!] Disconnected." + RESET)

    # --- main loop ---
    def run(self):
        for h, pt in BROKERS:
            if not self.running:
                return
            print(DIM + "  Dialing relay %s:%s ..." % (h, pt) + RESET)
            try:
                self.m.connect(h, pt, keepalive=60)
                break
            except Exception:
                print(DIM + "  - %s unavailable" % h + RESET)
                continue
        else:
            print(BOLD + COLORS["red"] +
                  "\n[!] No relay reachable. Check your internet." + RESET)
            return
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.kernel32.SetConsoleTitleW("RM77 CHAT - " + self.code)
            except Exception:
                pass
        self.m.loop_start()
        threading.Thread(target=self.beat, daemon=True).start()
        try:
            while self.running:
                try:
                    raw = input(self.prompt)
                except (EOFError, KeyboardInterrupt):
                    print()
                    self.leave()
                    break
                t = raw.strip()
                if not t:
                    continue
                if t.startswith("/"):
                    self.cmd(t)
                else:
                    self.pub(self.tc, {"type": "chat", "name": self.name,
                                       "color": self.color, "text": t})
        finally:
            self.running = False
            try:
                self.m.loop_stop()
                self.m.disconnect()
            except Exception:
                pass

# ---------------- entry point ----------------
def ident(args):
    name = clean(args.name) if args.name else clean(input("  Enter your name: "))
    color = args.color if args.color in COLORS else None
    if not color:
        print("\n  Color:")
        for i, c in enumerate(COLORS):
            print("    [%d] %s%-9s%s" % (i + 1, COLORS[c], c, RESET))
        while True:
            c = input("  Name or number: ").strip().lower()
            if c.isdigit() and 1 <= int(c) <= len(COLORS):
                color = list(COLORS)[int(c) - 1]
                break
            if c in COLORS:
                color = c
                break
            print("  Invalid.")
    print()
    return name, color

def main():
    a = argparse.ArgumentParser(description="RM77 CHAT")
    a.add_argument("--name")
    a.add_argument("--color")
    a.add_argument("--code")
    a.add_argument("-g", "--ghost", action="store_true")
    args = a.parse_args()
    show_banner()
    if args.ghost:
        print(BOLD + COLORS["red"] + "  >> GHOST MODE: no logs / no sounds <<\n" + RESET)
    name, color = ident(args)
    print("  [1] CREATE a room    [2] JOIN a room")
    while True:
        c = input("  Choice [1/2]: ").strip()
        if c in ("1", "2"):
            break
        print("  Invalid.")
    cd = args.code or (gen_code() if c == "1"
                       else input("  Room code from friend: ").strip().upper())
    print()
    if c == "1":
        print(BOLD + COLORS["green"] + "  >>> ROOM CREATED <<<" + RESET)
        print("  Share this code anywhere:\n      " + BOLD + COLORS["cyan"] + cd + RESET + "\n")
        print(DIM + "  Friends: JOIN + enter the code. Works on any internet, all devices." + RESET + "\n")
    try:
        Chat(cd, name, color, args.ghost).run()
    except Exception as e:
        import traceback
        print("\n[!] Error: %s" % e)
        traceback.print_exc()
        try:
            input("Press Enter to close...")
        except EOFError:
            pass
        sys.exit(1)

if __name__ == "__main__":
    main()