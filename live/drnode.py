#!/usr/bin/env python3
"""Demand response node agent. Runs on a demo machine, driven over one SSH session.

stdout, once a second:  W,<package_watts_x10>,<running_workers>,<paused_workers>
stdin commands:         run <n>     keep exactly n batch workers alive (spawn or kill)
                        pause <k>   pause k of them (SIGSTOP), resume the rest
                        quit
Safety: when stdin closes (the laptop disconnected or the demo ended), every worker is killed and the machine
is back to normal. Workers also exit on their own after MAX_SECONDS, whatever happens.

Mesh mode: with DRNODE_LISTEN=<mesh ip>:<port>, and a one-time token as the first line on stdin, the commands and readings travel over
one TCP connection on a private network instead of the SSH session. The node binds only to that address, takes
exactly one connection whose first line is the token, and gives up if none arrives within LISTEN_SECONDS. The SSH
session that started it still counts: closing either one kills every worker.

Power: Intel RAPL package energy, read with `sudo -n` because the counter is root-only on these boxes.
"""
import hmac
import os
import select
import signal
import socket
import subprocess
import sys
import time

RAPL = "/sys/class/powercap/intel-rapl:0/energy_uj"
MAX_SECONDS = 3600
LISTEN_SECONDS = 30
DUTY_PCT = int(os.environ.get("DUTY_PCT", "35"))
# Each worker is busy DUTY_PCT of every 100 ms and asleep the rest. Full-speed workers would hit the N100's
# package power limit, and then pausing some workers would not lower power (the others turbo up to fill it).
BURN = ("import time\nend=time.time()+%d\nx=0\nwhile time.time()<end:\n    t=time.time()\n"
        "    while time.time()-t<%f:\n        x=(x*1103515245+12345)&0xffffffff\n    time.sleep(%f)\n"
        % (MAX_SECONDS, DUTY_PCT / 1000, (100 - DUTY_PCT) / 1000))


def energy_uj() -> int:
    return int(subprocess.run(["sudo", "-n", "cat", RAPL], capture_output=True, text=True).stdout.strip())


workers: list[subprocess.Popen] = []
paused = 0


def set_running(n: int) -> None:
    global workers
    workers = [w for w in workers if w.poll() is None]
    while len(workers) < n:
        workers.append(subprocess.Popen([sys.executable, "-I", "-c", BURN]))
    while len(workers) > n:
        w = workers.pop()
        w.kill()


def set_paused(k: int) -> None:
    global paused
    k = max(0, min(k, len(workers)))
    for i, w in enumerate(workers):
        try:
            os.kill(w.pid, signal.SIGSTOP if i < k else signal.SIGCONT)
        except ProcessLookupError:
            pass
    paused = k


def shutdown(*_):
    for w in workers:
        try:
            os.kill(w.pid, signal.SIGCONT)
            w.kill()
        except ProcessLookupError:
            pass
    sys.exit(0)


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGHUP, shutdown)

def open_link():
    """Return (input fd, write function, extra fds whose EOF also means stop)."""
    listen = os.environ.get("DRNODE_LISTEN")
    if not listen:
        return sys.stdin.fileno(), lambda text: (sys.stdout.write(text), sys.stdout.flush()), []
    host, port = listen.rsplit(":", 1)
    token = sys.stdin.readline().strip()   # never on a command line, where ps would show it
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, int(port)))
    srv.listen(1)
    srv.settimeout(LISTEN_SECONDS)
    print(f"listening on {host}:{port} over the private link", flush=True)
    try:
        conn, peer = srv.accept()
    except socket.timeout:
        print("no connection arrived over the private link", flush=True)
        sys.exit(1)
    srv.close()
    conn.settimeout(10)
    first = b""
    while not first.endswith(b"\n") and len(first) < 256:
        got = conn.recv(1)
        if not got:
            break
        first += got
    if not hmac.compare_digest(first.strip(), token.encode()):
        print(f"refused {peer[0]}: wrong token", flush=True)
        conn.close()
        sys.exit(1)
    conn.settimeout(None)
    print(f"connected from {peer[0]} over the private link", flush=True)
    return conn.fileno(), lambda text: conn.sendall(text.encode()), [sys.stdin.fileno()]


in_fd, emit, watch = open_link()
last_e, last_t = energy_uj(), time.time()
buf = ""
while True:
    r, _, _ = select.select([in_fd] + watch, [], [], 1.0)
    for fd in r:
        if fd != in_fd and not os.read(fd, 4096):
            shutdown()
    if in_fd in r:
        chunk = os.read(in_fd, 4096).decode()
        if not chunk:
            shutdown()
        buf += chunk
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "run":
                set_running(int(parts[1]))
                set_paused(paused)
            elif parts[0] == "pause":
                set_paused(int(parts[1]))
            elif parts[0] == "quit":
                shutdown()
    now = time.time()
    if now - last_t >= 1.0:
        e = energy_uj()
        de = e - last_e if e >= last_e else e + (1 << 32) - last_e
        watts_x10 = round(de / (now - last_t) / 100000)
        last_e, last_t = e, now
        alive = len([w for w in workers if w.poll() is None])
        try:
            emit(f"W,{watts_x10},{alive - paused},{paused}\n")
        except OSError:
            shutdown()
