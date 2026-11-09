#!/usr/bin/env python3
"""Demo dashboard server. Offline: no CDN, no external calls except the live run's SSH sessions.

  GET  /             the dashboard page
  GET  /api/state    leaderboard, best and first policy source, simulated fleet day, grid trace, latest live run
  POST /api/fire?scope=zone|statewide   start a live run on the real machines with the best proven policy (one at a time)

Usage: python3 -I serve.py [--port 8765]   then open http://localhost:8765
"""
import argparse
import csv
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SC = HERE.parent
VERSIONS = SC / "policy" / "versions"
LIVE_RUNS = SC / "live" / "runs"
COORDS = {"San Francisco": (37.77, -122.42), "Oakland": (37.80, -122.27), "Sacramento": (38.58, -121.49),
          "Fresno": (36.74, -119.79), "Los Angeles": (34.05, -118.24), "San Diego": (32.72, -117.16)}
live_proc: dict = {"p": None, "log": []}
PORT = 8765


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def board() -> list[dict]:
    p = SC / "policy" / "leaderboard.json"
    return json.loads(p.read_text()) if p.exists() else []


def best_version(b: list[dict]) -> dict | None:
    ok = [e for e in b if not e.get("build_error") and e.get("breakdown") and not e["breakdown"].get("disqualified")]
    # Only a version proven safe by largo prove may run on real machines or be shown as the best.
    proven = [e for e in ok if e.get("proved") is True]
    pool = proven or ok
    return max(pool, key=lambda e: e["breakdown"]["score"]) if pool else None


def state() -> dict:
    b = board()
    top = best_version(b)
    sites = json.loads((SC / "sim" / "sites.json").read_text())["sites"]
    for s in sites:
        s["lat"], s["lon"] = COORDS.get(s["name"], (0, 0))
    event = json.loads((SC / "sim" / "event.json").read_text())
    trace = read_csv(SC / "data" / "grid-trace-2026-11-07.csv")

    def series(v):
        rows = read_csv(VERSIONS / f"v{v:03d}.timeseries.csv")
        return [{k: (int(x) if k != "time_pt" else x) for k, x in r.items()} for r in rows]

    runs = sorted(LIVE_RUNS.glob("live-*.csv"), key=lambda p: p.stat().st_mtime) if LIVE_RUNS.exists() else []
    live = read_csv(runs[-1]) if runs else []
    # The headline always shows the latest statewide run: that is the case where the fleet as a whole uses less.
    statewide = next((r for r in (read_csv(p) for p in reversed(runs)) if r and r[-1].get("scope") == "statewide"), [])
    running = live_proc["p"] is not None and live_proc["p"].poll() is None
    return {
        "leaderboard": b,
        "best": top["version"] if top else None,
        "best_source": (VERSIONS / f"v{top['version']:03d}.lg").read_text() if top else "",
        "first_source": (VERSIONS / "v001.lg").read_text() if (VERSIONS / "v001.lg").exists() else "",
        "best_series": series(top["version"]) if top else [],
        "first_series": series(1),
        "sites": sites,
        "event": event,
        "trace": trace,
        "live": live,
        "live_statewide": statewide,
        "live_file": runs[-1].name if runs else None,
        "live_running": running,
        "live_log": live_proc["log"][-12:],
    }


def fire(scope: str = "zone") -> tuple[int, dict]:
    if scope not in ("zone", "statewide"):
        return 400, {"error": "scope must be zone or statewide"}
    if live_proc["p"] is not None and live_proc["p"].poll() is None:
        return 409, {"error": "a live run is already going"}
    top = best_version(board())
    if not top:
        return 400, {"error": "no policy has scored yet"}
    binary = SC / "policy" / "bin" / f"v{top['version']:03d}"
    if not binary.exists():
        subprocess.run([str(SC / "policy" / "build.sh"), str(VERSIONS / f"v{top['version']:03d}.lg")], check=True)
    live_proc["log"] = [f"starting a {'Bay Area' if scope == 'zone' else 'statewide'} live run with policy v{top['version']:03d}"]
    p = subprocess.Popen([sys.executable, "-I", str(SC / "live" / "live.py"), "--policy", str(binary),
                          "--baseline-seconds", "15", "--after-seconds", "2", "--event-seconds", "60", "--scope", scope],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=SC / "live")
    live_proc["p"] = p

    def pump():
        for line in p.stdout:
            line = line.rstrip()
            if line and not line.lstrip()[:1].isdigit():
                live_proc["log"].append(line)
    threading.Thread(target=pump, daemon=True).start()
    return 202, {"started": True, "policy": top["version"]}


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _local(self) -> bool:
        """Refuse anything a web page elsewhere could send: a foreign Host (DNS rebinding) or a foreign Origin
        (a cross-site POST). Only this page, served from this machine, may start a live run."""
        allowed = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
        if self.headers.get("Host", "") not in allowed:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://{h}" for h in allowed}

    def do_GET(self):
        if not self._local():
            return self._send(403, {"error": "forbidden"})
        if self.path in ("/", "/index.html"):
            self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._send(200, state())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._local():
            return self._send(403, {"error": "forbidden"})
        path, _, query = self.path.partition("?")
        if path == "/api/fire":
            params = dict(kv.split("=", 1) for kv in query.split("&") if "=" in kv)
            code, body = fire(params.get("scope", "zone"))
            self._send(code, body)
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    PORT = a.port
    print(f"dashboard on http://localhost:{a.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
