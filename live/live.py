#!/usr/bin/env python3
"""Live demo controller: real machines answer a demand response event.

Two machines: "shed" plays a Bay Area site inside the event zone, "recv" a receiving site outside it. Both run
drnode.py over one SSH session each. Which machines they are lives in live/machines.json, which is NOT in the
repository: copy machines.example.json and fill in your own SSH host aliases (see docs/SETUP.md). The same compiled LOGOS policy the simulator scored decides what to do,
and the same three safety rules are checked before anything is applied.

Units: the policy works in watts in the simulator. These boxes are about a thousand times smaller, so the live
run passes MILLIWATTS in the same fields. The policy's logic only compares and takes minimums, so it is
scale-free.

Measured power steps (Intel N100, RAPL package power, 2026-11-08 14:15, full-speed workers):
  0 workers about 2.6 W, 1 worker about 9.5 W, 2 workers about 14.5 to 15 W, 3 or 4 workers 15 W (power limit).
So the demo uses 0 to 2 workers per box.

Usage: python3 -I live.py --policy ../policy/bin/v002 [--baseline-seconds 20] [--event-seconds 60] [--wait-for-enter]
Writes live/runs/live-<HHMMSS>.csv for the dashboard.
"""
import argparse
import csv
import json
import os
import secrets
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCAFFOLD = HERE.parent
STEP_MW = {0: 2600, 1: 9500, 2: 14800}     # measured package power by running workers
LIMIT_MW = 15000                           # N100 package power limit seen in testing
BASE_WORKERS = {"shed": 2, "recv": 0}


def load_machines() -> dict:
    """Read the local machine config. It holds connection details, so it is gitignored and never published."""
    path = Path(os.environ.get("LIVE_MACHINES", HERE / "machines.json"))
    if not path.exists():
        sys.exit(f"No machine config at {path}. Copy live/machines.example.json to live/machines.json and fill it "
                 "in with your own SSH host aliases (see docs/SETUP.md).")
    return json.loads(path.read_text())


CFG = load_machines()
HOSTS = {r: CFG[r]["ssh"] for r in ("shed", "recv")}
NAMES = {r: CFG[r].get("name", r) for r in ("shed", "recv")}
# Optional private link: the shed machine's commands and readings cross a private network (we used our WireGuard
# mesh) from the recv machine, instead of the shed machine's own SSH session, which then only starts the agent.
LINK = CFG.get("private_link")
MESH = {"shed": {"via": "recv", "ip": LINK["shed_ip"], "port": int(LINK.get("port", 7707)),
                 "describe": LINK.get("describe_command")}} if LINK else {}
BRIDGE = ("import os,socket,sys,threading\nc=socket.create_connection((sys.argv[1],int(sys.argv[2])),10)\n"
          "def up():\n    for l in sys.stdin.buffer:\n        c.sendall(l)\n    c.shutdown(socket.SHUT_WR)\n"
          "threading.Thread(target=up,daemon=True).start()\n"
          "while True:\n    d=c.recv(4096)\n    if not d: break\n    sys.stdout.buffer.write(d); sys.stdout.flush()\nos._exit(0)\n")


class Node:
    def __init__(self, role: str):
        self.role = role
        host = HOSTS[role]
        subprocess.run(["ssh", "-o", "BatchMode=yes", host, "cat > /tmp/drnode.py"],
                       stdin=open(HERE / "drnode.py"), check=True, timeout=60)
        self.launcher = None
        self.link = "SSH session"
        mesh = MESH.get(role)
        if mesh:
            token = secrets.token_hex(16)
            self.launcher = subprocess.Popen(
                ["ssh", "-o", "BatchMode=yes", host,
                 f"DUTY_PCT=100 DRNODE_LISTEN={mesh['ip']}:{mesh['port']} python3 -I /tmp/drnode.py"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            self.launcher.stdin.write(token + "\n")
            self.launcher.stdin.flush()
            first = self.launcher.stdout.readline().strip()
            if not first.startswith("listening"):
                raise RuntimeError(f"{NAMES[role]} agent did not start: {first}")
            via = HOSTS[mesh["via"]]
            self.link = mesh_path(via, mesh)
            self.p = subprocess.Popen(["ssh", "-o", "BatchMode=yes", via,
                                       f"python3 -I -c {shlex.quote(BRIDGE)} {mesh['ip']} {mesh['port']}"],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
            self.send(token)
            threading.Thread(target=lambda: [None for _ in self.launcher.stdout], daemon=True).start()
        else:
            self.p = subprocess.Popen(["ssh", "-o", "BatchMode=yes", host, "DUTY_PCT=100 python3 -I /tmp/drnode.py"],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.mw = 0
        self.running = 0
        self.paused = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            parts = line.strip().split(",")
            if len(parts) == 4 and parts[0] == "W":
                self.mw = int(parts[1]) * 100
                self.running, self.paused = int(parts[2]), int(parts[3])

    def send(self, cmd: str):
        self.p.stdin.write(cmd + "\n")
        self.p.stdin.flush()

    def close(self):
        try:
            self.send("quit")
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()
        if self.launcher:
            try:
                self.launcher.stdin.close()     # the agent also stops when its SSH session closes
                self.launcher.wait(timeout=10)
            except Exception:
                self.launcher.kill()


def mesh_path(via: str, mesh: dict) -> str:
    """Say how the private link carries this machine's traffic, from the relaying machine's own view: the network
    device the route uses, plus one line from an optional describe_command. Addresses are never printed."""
    cmd = f"ip route get {shlex.quote(mesh['ip'])} | head -1"
    if mesh.get("describe"):
        cmd += f"; {mesh['describe']}"
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", via, cmd], capture_output=True, text=True, timeout=30).stdout
    lines = out.splitlines()
    words = lines[0].split() if lines else []
    dev = words[words.index("dev") + 1] if "dev" in words else "unknown device"
    extra = lines[1].split(":", 1)[-1].split(",")[0].strip() if len(lines) > 1 else ""
    return f"private link from {NAMES['recv']} on {dev}" + (f", {extra}" if extra else "")


def ask_policy(binary: str, fields: list[int]) -> tuple[int, int, int]:
    r = subprocess.run([binary], input=",".join(map(str, fields)) + "\nend\n", capture_output=True, text=True, timeout=10)
    a, b, c = r.stdout.strip().splitlines()[0].split(",")
    return int(a), int(b), int(c)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", required=True)
    ap.add_argument("--baseline-seconds", type=int, default=20)
    ap.add_argument("--event-seconds", type=int, default=60)
    ap.add_argument("--after-seconds", type=int, default=15)
    ap.add_argument("--wait-for-enter", action="store_true", help="fire the event when Enter is pressed")
    ap.add_argument("--scope", choices=["zone", "statewide"], default="zone",
                    help="zone: only the shed machine is inside the event, so work can move to the recv machine. "
                         "statewide: both sites are inside it, so there is nowhere to move and work must stop.")
    a = ap.parse_args()
    event = json.load(open(SCAFFOLD / "sim" / "event.json"))
    trace = list(csv.DictReader(open(SCAFFOLD / "data" / "grid-trace-2026-11-07.csv")))
    at = next(r for r in trace if int(r["minute"]) == event["intervalPeriod"]["start_minute"])

    runs = HERE / "runs"
    runs.mkdir(exist_ok=True)
    out_path = runs / f"live-{time.strftime('%H%M%S')}.csv"
    out = open(out_path, "w", newline="")
    w = csv.writer(out)
    w.writerow(["t", "phase", "shed_mw", "recv_mw", "shed_workers", "recv_workers", "baseline_mw", "target_mw", "delivered_mw",
                "fleet_mw", "scope"])
    fleet = {"baseline": [], "event": []}

    nodes = {r: Node(r) for r in HOSTS}
    for r, n in nodes.items():
        print(f"{NAMES[r]} link: {n.link}", flush=True)
    state = {"phase": "baseline", "baseline": None, "target": 0}
    t0 = time.time()

    def log_loop():
        while state["phase"] != "done":
            s, rcv = nodes["shed"], nodes["recv"]
            base = state["baseline"] or 0
            delivered = max(0, base - s.mw) if state["phase"] == "event" else 0
            w.writerow([round(time.time() - t0, 1), state["phase"], s.mw, rcv.mw, s.running, rcv.running, base,
                        state["target"], delivered, s.mw + rcv.mw, a.scope])
            if state["phase"] in fleet and (state["phase"] == "baseline" and time.time() - t0 > 5
                                            or state["phase"] == "event" and time.time() - state.get("fired", 0) > 4):
                fleet[state["phase"]].append(s.mw + rcv.mw)
            out.flush()
            print(f"{time.time()-t0:6.1f}s {state['phase']:<9} shed {s.mw/1000:5.1f} W ({s.running} workers)  "
                  f"recv {rcv.mw/1000:5.1f} W ({rcv.running} workers)  fleet {(s.mw + rcv.mw)/1000:5.1f} W"
                  + (f"  target {state['target']/1000:.1f} W  delivered {delivered/1000:.1f} W" if state["phase"] == "event" else ""),
                  flush=True)
            time.sleep(1)

    try:
        for r, n in nodes.items():
            n.send(f"run {BASE_WORKERS[r]}")
        threading.Thread(target=log_loop, daemon=True).start()
        time.sleep(a.baseline_seconds)
        samples = []
        for _ in range(5):
            samples.append(nodes["shed"].mw)
            time.sleep(1)
        state["baseline"] = sum(samples) // len(samples)
        if a.wait_for_enter:
            input("\nPress Enter to fire the grid event...\n")
        else:
            time.sleep(a.after_seconds)

        base = state["baseline"]
        target = base * event["shed_pct"] // 100
        batch = base - STEP_MW[0]
        room = max(0, LIMIT_MW - nodes["recv"].mw)
        fields = [event["intervalPeriod"]["start_minute"], 0, int(at["price_np15"]), int(at["carbon_g_kwh"]), 1,
                  target, 0, batch, batch * 100, 84, room, int(at["price_sp15"]), 1 if a.scope == "statewide" else 0,
                  max(0, LIMIT_MW - nodes["shed"].mw)]
        pause, move, boost = ask_policy(a.policy, fields)
        where = "the Bay Area only" if a.scope == "zone" else "both sites (statewide)"
        print(f"\nGRID EVENT in {where}: shed {event['shed_pct']} percent of {base/1000:.1f} W = {target/1000:.1f} W. "
              f"Policy says pause {pause/1000:.1f} W, move {move/1000:.1f} W to the {NAMES['recv']}.", flush=True)
        if pause < 0 or move < 0 or boost < 0 or pause + move > batch or move > room or boost > fields[13]:
            print("SAFETY RULE BROKEN, nothing applied.", flush=True)
            return 2
        # Map watts onto whole workers: the fewest workers removed that reach the requested shed.
        shed_req = pause + move
        keep = BASE_WORKERS["shed"]
        while keep > 0 and STEP_MW[BASE_WORKERS["shed"]] - STEP_MW[keep] < shed_req * 9 // 10:
            keep -= 1
        removed = BASE_WORKERS["shed"] - keep
        moved = min(removed, round(removed * move / shed_req)) if shed_req else 0
        paused = removed - moved
        state["phase"], state["target"], state["fired"] = "event", target, time.time()
        nodes["shed"].send(f"run {BASE_WORKERS['shed']}")
        nodes["shed"].send(f"pause {paused}") if paused else None
        nodes["shed"].send(f"run {keep + paused}")
        nodes["shed"].send(f"pause {paused}")
        nodes["recv"].send(f"run {BASE_WORKERS['recv'] + moved}")
        print(f"Applied: {moved} worker(s) moved to the {NAMES['recv']}, {paused} paused, {keep} still running on the {NAMES['shed']}.\n", flush=True)
        time.sleep(a.event_seconds)

        state["phase"] = "restored"
        if fleet["baseline"] and fleet["event"]:
            b = sum(fleet["baseline"]) / len(fleet["baseline"]) / 1000
            e = sum(fleet["event"]) / len(fleet["event"]) / 1000
            print(f"\nFLEET TOTAL: {b:.1f} W before the event, {e:.1f} W during it ({e - b:+.1f} W). "
                  + ("Work moved, so the zone got its cut and no work was lost, but the fleet as a whole did not save energy."
                     if a.scope == "zone" else "Nowhere to move, so the policy paused work and the whole fleet used less.") + "\n",
                  flush=True)
        nodes["shed"].send(f"pause 0")
        nodes["shed"].send(f"run {BASE_WORKERS['shed']}")
        nodes["recv"].send(f"run {BASE_WORKERS['recv']}")
        time.sleep(10)
    finally:
        state["phase"] = "done"
        for n in nodes.values():
            n.close()
        out.close()
        print(f"\nwrote {out_path.relative_to(SCAFFOLD)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
