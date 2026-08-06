#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
██████╗ ███╗   ███╗ ██████╗  ██████╗      RM77 CHAT v2.0
██╔══██╗████╗ ████║██╔════╝ ╚════██╗     Online terminal group chat
██████╔╝██╔████╔██║██║  ███╗ █████╔╝     Host a room, or join a friend's.
██╔══██╗██║╚██╔╝██║██║   ██║██╔═══╝
██║  ██║██║ ╚═╝ ██║╚██████╔╝███████╗
╚═╝  ╚═╝╚═╝     ╚═╝ ╚═════╝ ╚══════╝
"""

import argparse
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime

# ------------------------------------------------------------------
# ANSI colors / terminal helpers
# ------------------------------------------------------------------
try:                      # needed on Windows to enable ANSI colors
    import colorama
    colorama.init()
except ImportError:
    pass

RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
CLR_LN  = "\033[2K\r"     # clear current line, return to column 0

COLORS = {
    "red":     "\033[91m",
    "green":   "\033[92m",
    "yellow":  "\033[93m",
    "blue":    "\033[94m",
    "magenta": "\033[95m",
    "cyan":    "\033[96m",
    "purple":  "\033[35m",
    "white":   "\033[97m",
}

BANNER = r"""
██████╗ ███╗   ███╗███████╗███████╗
██╔══██╗████╗ ████║╚════██║╚════██║
██████╔╝██╔████╔██║    ██╔╝    ██╔╝
██╔══██╗██║╚██╔╝██║   ██╔╝    ██╔╝
██║  ██║██║ ╚═╝ ██║   ██║     ██║
╚═╝  ╚═╝╚═╝     ╚═╝   ╚═╝     ╚═╝

██████╗██╗  ██╗ █████╗ ████████╗
██╔════╝██║  ██║██╔══██╗╚══██╔══╝
██║     ███████║███████║   ██║
██║     ██╔══██║██╔══██║   ██║
╚██████╗██║  ██║██║  ██║   ██║
 ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝
""".strip("\n")

def show_banner():
    """Print the banner with a magenta/cyan color cycle."""
    palette = [COLORS["magenta"], COLORS["cyan"]]
    for i, line in enumerate(BANNER.splitlines()):
        print(palette[i % 2] + line + RESET)
    print(DIM + "  ===== RM77 CHAT v2.0 - ONLINE TERMINAL GROUP CHAT =====\n" + RESET)

def now_ts():
    return datetime.now().strftime("%H:%M:%S")

def clean_name(name, limit=16):
    """Sanitize a username: strip junk, cap length."""
    name = "".join(ch for ch in (name or "").strip() if ch.isprintable())
    name = name.replace(" ", "_")[:limit] or "Anonymous"
    return name

def get_identity(args):
    """Ask for name + color (or reuse CLI args)."""
    name = clean_name(args.name) if args.name else None
    if not name:
        name = clean_name(input("  Enter your name: "))
    color = args.color if args.color in COLORS else None
    if not color:
        print("\n  Choose your chat color:")
        items = list(COLORS.items())
        for i in range(0, len(items), 4):
            row = "   "
            for cname, code in items[i:i + 4]:
                row += f"  [{i + items.index((cname, code)) + 1}] {code}{cname:<9}{RESET}"
            print(row)
        while True:
            choice = input("\n  Color name or number: ").strip().lower()
            if choice.isdigit() and 1 <= int(choice) <= len(COLORS):
                color = list(COLORS)[int(choice) - 1]
                break
            if choice in COLORS:
                color = choice
                break
            print("  Invalid color. Try again.")
    print()
    return name, color

# ------------------------------------------------------------------
# Room codes: RM77-XXXXXX  ->  deterministic port
# ------------------------------------------------------------------
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O/1/I/L

def make_code(length=6):
    return "".join(random.choice(ALPHABET) for _ in range(length))

def code_to_port(code):
    code = code.upper().replace("RM77-", "").strip()
    n = 0
    for ch in code:
        n = n * 32 + ALPHABET.index(ch)
    return 20000 + (n % 40000)

def get_lan_ip():
    """Best-effort LAN IP (sends no real packets)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()

def fetch_public_ip():
    """Public IP lookup, non-blocking via a thread."""
    try:
        import urllib.request
        with urllib.request.urlopen("https://api.ipify.org", timeout=4) as r:
            return r.read().decode().strip()
    except Exception:
        return None

# ------------------------------------------------------------------
# Notifications (best effort)
# ------------------------------------------------------------------
def notify(title, message, bells=1):
    """Terminal bell + desktop notification when available."""
    try:
        sys.stdout.write("\a" * bells)
        sys.stdout.flush()
    except Exception:
        pass
    try:
        from plyer import notification
        notification.notify(title=title, message=message, timeout=3)
    except Exception:
        try:
            if sys.platform == "darwin":
                subprocess.Popen(
                    ["osascript", "-e",
                     'display notification "{}" with title "{}"'.format(message, title)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif sys.platform.startswith("linux"):
                subprocess.Popen(["notify-send", title, message],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

# ------------------------------------------------------------------
# SERVER  (the room hub)
# ------------------------------------------------------------------
class ChatServer:
    def __init__(self, port):
        self.port = port
        self.clients = {}       # sock -> {"name","color"}
        self.lock = threading.Lock()
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", port))
        self.sock.listen(20)
        self.sock.settimeout(1.0)

    # ---- low level helpers ----
    def send_to(self, sock, payload):
        try:
            sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            pass

    def broadcast(self, payload, exclude=None):
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        with self.lock:
            for sock in list(self.clients):
                if sock is exclude:
                    continue
                try:
                    sock.sendall(data)
                except OSError:
                    pass

    def user_list(self):
        return [{"name": v["name"], "color": v["color"]} for v in self.clients.values()]

    def push_users(self):
        self.broadcast({"type": "users", "users": self.user_list()})

    def make_unique_name(self, name):
        taken = {v["name"].lower() for v in self.clients.values()}
        if name.lower() not in taken:
            return name
        i = 2
        while f"{name}{i}".lower() in taken:
            i += 1
        return f"{name}{i}"

    # ---- per-client connection ----
    def handle_client(self, conn, addr):
        buffer = b""
        info = {"name": None, "color": "cyan"}
        try:
            # handshake: first line must be a "join" payload
            while b"\n" not in buffer:
                chunk = conn.recv(4096)
                if not chunk:
                    conn.close()
                    return
                buffer += chunk
            line, _, buffer = buffer.partition(b"\n")
            try:
                hello = json.loads(line.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                conn.close()
                return

            if hello.get("type") != "join":
                conn.close()
                return

            with self.lock:
                info["name"] = self.make_unique_name(clean_name(hello.get("name", "Anonymous")))
                info["color"] = hello.get("color", "cyan") if hello.get("color") in COLORS else "cyan"
                self.clients[conn] = info
                users = self.user_list()

            self.send_to(conn, {"type": "welcome", "name": info["name"],
                                "color": info["color"], "users": users})
            self.broadcast({"type": "system",
                            "text": f"{info['name']} joined the room."})
            self.broadcast({"type": "join", "name": info["name"], "color": info["color"],
                            "online": len(self.clients)})
            self.push_users()

            # main message loop
            while True:
                while b"\n" not in buffer:
                    chunk = conn.recv(4096)
                    if not chunk:
                        raise ConnectionError
                    buffer += chunk
                line, _, buffer = buffer.partition(b"\n")
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    continue
                self.route(msg, conn, info)

        except (ConnectionError, OSError):
            pass
        finally:
            with self.lock:
                self.clients.pop(conn, None)
            try:
                conn.close()
            except OSError:
                pass
            if info["name"]:
                self.broadcast({"type": "leave", "name": info["name"],
                                "color": info["color"], "online": len(self.clients)})
                self.broadcast({"type": "system", "text": f"{info['name']} left the room."})
                self.push_users()

    def route(self, msg, conn, info):
        mtype = msg.get("type")
        if mtype == "chat":
            text = str(msg.get("text", "")).strip()[:2000]
            if text:
                self.broadcast({"type": "chat", "name": info["name"],
                                "color": info["color"], "text": text})
        elif mtype == "me":
            text = str(msg.get("text", "")).strip()[:500]
            if text:
                self.broadcast({"type": "me", "name": info["name"],
                                "color": info["color"], "text": text})
        elif mtype == "whisper":
            target = str(msg.get("to", "")).strip().lower()
            text = str(msg.get("text", "")).strip()[:1000]
            if not text:
                return
            with self.lock:
                found = next((s for s, v in self.clients.items()
                              if v["name"].lower() == target), None)
            if found is None:
                self.send_to(conn, {"type": "system",
                                    "text": f"No user named '{target}' is online."})
            elif found is conn:
                self.send_to(conn, {"type": "system", "text": "You cannot whisper to yourself."})
            else:
                payload = {"type": "whisper", "from": info["name"],
                           "color": info["color"], "to": self.clients[found]["name"],
                           "text": text}
                self.send_to(found, payload)
                self.send_to(conn, payload)
        elif mtype == "nick":
            new_name = clean_name(str(msg.get("name", "")))
            if new_name and new_name.lower() != info["name"].lower():
                with self.lock:
                    new_name = self.make_unique_name(new_name)
                    old = info["name"]
                    info["name"] = new_name
                self.broadcast({"type": "system",
                                "text": f"{old} is now known as {new_name}."})
                self.push_users()
        elif mtype == "color":
            new_color = msg.get("color")
            if new_color in COLORS:
                old = info["color"]
                info["color"] = new_color
                self.broadcast({"type": "system",
                                "text": f"{info['name']} changed color to {new_color}."})
                self.push_users()
        elif mtype == "users":
            self.send_to(conn, {"type": "users", "users": self.user_list()})
        elif mtype == "ping":
            self.send_to(conn, {"type": "pong", "t": msg.get("t", 0)})

    # ---- accept loop ----
    def run(self):
        while self.running:
            try:
                conn, addr = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self.handle_client, args=(conn, addr),
                             daemon=True).start()
        self.stop()

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass
        with self.lock:
            for sock in list(self.clients):
                try:
                    sock.close()
                except OSError:
                    pass
            self.clients.clear()

# ------------------------------------------------------------------
# CLIENT  (chat UI — used by host and joiners alike)
# ------------------------------------------------------------------
HELP_TEXT = """
  /help              show this help
  /users             who is online right now
  /nick <name>       change your name
  /color <color>     change your color (red, green, yellow, blue,
                     magenta, cyan, purple, white)
  /w <name> <msg>    send a private whisper
  /me <action>       do an action, e.g.  /me slaps Bob with a trout
  /ping              measure your latency to the room
  /time              toggle timestamps
  /clear             clear the screen
  /quit              leave the room
"""

class ChatClient:
    def __init__(self, host, port, name, color):
        self.host, self.port = host, port
        self.name, self.color = name, color
        self.running = True
        self.show_time = True
        self.sock = None
        self.print_lock = threading.Lock()
        self.log_lock = threading.Lock()
        self.prompt = f"{BOLD}{COLORS[color]}{name}{RESET} > "

    # ---- helpers ----
    def send(self, payload):
        try:
            self.sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            pass

    def _print(self, text):
        with self.print_lock:
            sys.stdout.write(CLR_LN + text + "\n" + self.prompt)
            sys.stdout.flush()

    def _log(self, plain):
        try:
            with self.log_lock:
                with open("rm77_history.log", "a", encoding="utf-8") as f:
                    f.write(plain + "\n")
        except Exception:
            pass

    def ts(self):
        return f"{DIM}[{now_ts()}]{RESET}" if self.show_time else ""

    # ---- receive loop ----
    def receive_loop(self):
        buffer = b""
        try:
            while self.running:
                while b"\n" not in buffer:
                    chunk = self.sock.recv(4096)
                    if not chunk:
                        raise ConnectionError
                    buffer += chunk
                line, _, buffer = buffer.partition(b"\n")
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    continue
                self.dispatch(msg)
        except (ConnectionError, OSError):
            if self.running:
                self._print(DIM + "[!] Connection lost — the room closed or the host went offline." + RESET)
                self.running = False

    def dispatch(self, msg):
        t = msg.get("type")

        if t == "welcome":
            self.name = msg["name"]
            self.color = msg["color"]
            self.prompt = f"{BOLD}{COLORS[self.color]}{self.name}{RESET} > "
            online = len(msg.get("users", []))
            self._print(BOLD + COLORS["green"] +
                        f"[!] Connected! You are {self.name} — {online} online." + RESET)
            self._log(f"[{now_ts()}] == session started as {self.name} ==")
            notify("RM77 CHAT", f"Connected as {self.name}", bells=1)

        elif t == "chat":
            if msg["name"] == self.name:
                return                      # own echo already handled client-side? no -> show it
            line = f"{self.ts()} {BOLD}{COLORS.get(msg['color'], COLORS['white'])}{msg['name']}{RESET}{DIM}:{RESET} {COLORS.get(msg['color'], COLORS['white'])}{msg['text']}{RESET}"
            self._print(line)
            self._log(f"[{now_ts()}] {msg['name']}: {msg['text']}")
            self._mention_check(msg["name"], msg["text"])
            notify("RM77 CHAT", f"{msg['name']}: {msg['text'][:80]}")

        elif t == "me":
            line = f"{self.ts()} {BOLD}* {COLORS.get(msg['color'], COLORS['cyan'])}{msg['name']}{RESET} {msg['text']}"
            self._print(line)
            self._log(f"[{now_ts()}] * {msg['name']} {msg['text']}")
            self._mention_check(msg["name"], msg["text"])

        elif t == "whisper":
            if msg["from"] == self.name:
                line = (f"{self.ts()} {BOLD}{COLORS['magenta']}you{RESET} {COLORS['magenta']}->{RESET} "
                        f"{BOLD}{COLORS.get(msg['color'], COLORS['white'])}{msg['to']}{RESET}"
                        f"{DIM} (whisper){RESET} {msg['text']}")
                plain = f"[{now_ts()}] (whisper) you -> {msg['to']}: {msg['text']}"
            else:
                line = (f"{self.ts()} {BOLD}{COLORS.get(msg['color'], COLORS['white'])}{msg['from']}{RESET} "
                        f"{COLORS['magenta']}->{RESET} {BOLD}{COLORS['magenta']}you{RESET}"
                        f"{DIM} (whisper){RESET} {msg['text']}")
                plain = f"[{now_ts()}] (whisper) {msg['from']} -> you: {msg['text']}"
                notify("RM77 CHAT", f"Whisper from {msg['from']}: {msg['text'][:80]}")
            self._print(line)
            self._log(plain)

        elif t == "system":
            self._print(DIM + f"{self.ts()} {msg['text']}" + RESET)
            self._log(f"[{now_ts()}] {msg['text']}")

        elif t == "join":
            line = (f"{self.ts()} {DIM}-> {BOLD}{COLORS.get(msg['color'], COLORS['cyan'])}"
                    f"{msg['name']}{RESET}{DIM} joined the room ({msg['online']} online){RESET}")
            self._print(line)
            self._log(f"[{now_ts()}] {msg['name']} joined ({msg['online']} online)")

        elif t == "leave":
            line = (f"{self.ts()} {DIM}<- {BOLD}{COLORS.get(msg['color'], COLORS['cyan'])}"
                    f"{msg['name']}{RESET}{DIM} left the room ({msg['online']} online){RESET}")
            self._print(line)
            self._log(f"[{now_ts()}] {msg['name']} left ({msg['online']} online)")

        elif t == "users":
            names = []
            for u in msg.get("users", []):
                names.append(f"{BOLD}{COLORS.get(u['color'], COLORS['white'])}{u['name']}{RESET}")
            self._print(DIM + f"Online ({len(names)}): " + RESET + "  ".join(names))

        elif t == "pong":
            rtt = (time.time() - msg.get("t", 0)) * 1000
            self._print(f"{BOLD}{COLORS['green']}[!] Pong! {rtt:.0f} ms{RESET}")

    def _mention_check(self, sender, text):
        words = {w.strip(".,!?@:;") for w in text.lower().split()}
        if sender != self.name and (self.name.lower() in words or f"@{self.name.lower()}" in text.lower()):
            self._print(f"{BOLD}{COLORS['yellow']}>> {sender} mentioned you <<{RESET}")
            notify("RM77 CHAT", f"{sender} mentioned you!", bells=3)

    # ---- command handling ----
    def handle_command(self, raw):
        parts = raw.split()
        cmd = parts[0].lower()

        if cmd == "/help":
            self._print(DIM + HELP_TEXT + RESET)
        elif cmd == "/users":
            self.send({"type": "users"})
        elif cmd == "/clear":
            os.system("cls" if os.name == "nt" else "clear")
            show_banner()
        elif cmd == "/nick" and len(parts) >= 2:
            self.send({"type": "nick", "name": clean_name(parts[1])})
        elif cmd == "/color" and len(parts) >= 2:
            if parts[1].lower() in COLORS:
                self.send({"type": "color", "color": parts[1].lower()})
            else:
                self._print(DIM + "[!] Unknown color. Try: " + ", ".join(COLORS) + RESET)
        elif cmd in ("/w", "/whisper") and len(parts) >= 3:
            self.send({"type": "whisper", "to": parts[1], "text": " ".join(parts[2:])})
        elif cmd == "/me" and len(parts) >= 2:
            self.send({"type": "me", "text": " ".join(parts[1:])})
        elif cmd == "/ping":
            self.send({"type": "ping", "t": time.time()})
        elif cmd == "/time":
            self.show_time = not self.show_time
            self._print(DIM + f"[!] Timestamps {'on' if self.show_time else 'off'}." + RESET)
        elif cmd in ("/quit", "/exit", "/leave"):
            self.leave()
        else:
            self._print(DIM + "[!] Unknown command. Type /help for the list." + RESET)

    def leave(self):
        self.send({"type": "leave"})
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass
        print(DIM + "\n[!] Disconnected. See you next time!" + RESET)

    # ---- main loop ----
    def run(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))
        self.sock.settimeout(None)

        threading.Thread(target=self.receive_loop, daemon=True).start()
        self.send({"type": "join", "name": self.name, "color": self.color})

        try:
            while self.running:
                try:
                    raw = input(self.prompt)
                except (EOFError, KeyboardInterrupt):
                    print()
                    self.leave()
                    break
                text = raw.strip()
                if not text:
                    continue
                if text.startswith("/"):
                    self.handle_command(text)
                else:
                    self.send({"type": "chat", "text": text})
        finally:
            self.running = False
            try:
                self.sock.close()
            except OSError:
                pass

# ------------------------------------------------------------------
# HOST / JOIN entry points
# ------------------------------------------------------------------
def offer_tunnel(port):
    """If ngrok is installed, offer a public tunnel so friends can
    connect from anywhere without port forwarding."""
    ngrok = shutil.which("ngrok")
    if not ngrok:
        return
    try:
        ans = input("  [i] ngrok found. Start a public tunnel? (y/N): ").strip().lower()
        if ans == "y":
            subprocess.Popen([ngrok, "tcp", str(port)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("  [i] ngrok started. Share the 'Forwarding' address (tcp://...) with friends.\n")
    except Exception:
        pass

def run_host(name, color):
    code = make_code()
    port = code_to_port(code)
    server = ChatServer(port)
    threading.Thread(target=server.run, daemon=True).start()

    lan_ip = get_lan_ip()
    print(BOLD + COLORS["green"] + "  >>> ROOM CREATED <<<" + RESET)
    print(f"  Room code : {BOLD}{COLORS['cyan']}RM77-{code}{RESET}")
    print(f"  Port      : {port}")
    print(f"  LAN IP    : {lan_ip}")
    print(f"  You chat  : localhost")
    print(DIM + "  Friends on your network run the tool and choose JOIN,")
    print("  then enter your LAN IP + the room code." + RESET)

    def pub():
        ip = fetch_public_ip()
        if ip:
            print(f"  Public IP : {ip}")
            print(DIM + "  (for internet friends: forward TCP port "
                  f"{port} to this machine, or use ngrok/tailscale)" + RESET)
            offer_tunnel(port)
    threading.Thread(target=pub, daemon=True).start()

    print()
    client = ChatClient("127.0.0.1", port, name, color)
    try:
        client.run()
    finally:
        server.stop()

def run_join(ip, code, name, color):
    port = code_to_port(code)
    client = ChatClient(ip, port, name, color)
    try:
        client.run()
    except (ConnectionRefusedError, OSError):
        print(f"{BOLD}{COLORS['red']}[!] Could not reach {ip}:{port}.{RESET}")
        print(DIM + "    Check the IP and room code, and make sure the host's room is online." + RESET)

# ------------------------------------------------------------------
# main
# ------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="RM77 CHAT - online terminal group chat")
    parser.add_argument("mode", nargs="?", choices=["host", "join"],
                        help="host a room or join one (interactive menu if omitted)")
    parser.add_argument("--name", help="your display name")
    parser.add_argument("--color", help="your color: " + ", ".join(COLORS))
    parser.add_argument("--ip", help="host IP to join")
    parser.add_argument("--code", help="room code, e.g. RM77-ABCDEF or just ABCDEF")
    args = parser.parse_args()

    show_banner()
    name, color = get_identity(args)

    mode = args.mode
    if not mode:
        print("  What do you want to do?")
        print("    [1] HOST a room (create a group for your friends)")
        print("    [2] JOIN a friend's room")
        while True:
            choice = input("  Choice [1/2]: ").strip()
            if choice == "1":
                mode = "host"
                break
            if choice == "2":
                mode = "join"
                break
            print("  Invalid choice.")

    if mode == "host":
        run_host(name, color)
    else:
        ip = args.ip
        if not ip:
            ip = input("  Friend's IP (or 'localhost'): ").strip() or "localhost"
        code = (args.code or input("  Room code (RM77-XXXXXX): ").strip()).upper()
        code = code.replace("RM77-", "")
        run_join(ip, code, name, color)

if __name__ == "__main__":
    main()
