#!/usr/bin/env python3
"""Agent loop: Claude writes a LOGOS policy, largo builds it, the simulator scores it, repeat.

Honesty rules:
  - The proposer runs as `claude -p` with every tool disabled, from an empty folder. It sees only this prompt:
    the input/output contract, the safety rules, the best policy so far and the score history. It never sees the
    simulator code, the score weights or the data files.
  - Every attempt is kept, including ones that fail to build or break a safety rule, with the reason.

Usage: python3 -I loop.py --iterations 10 [--model claude-opus-5-5]
Writes policy/versions/vNNN.lg, vNNN.score.json and leaderboard.json.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VERSIONS = HERE / "policy" / "versions"
BOARD = HERE / "policy" / "leaderboard.json"
CELL = Path(tempfile.gettempdir()) / "energy-hack-agent-cell"   # empty folder the proposer runs in

CONTRACT = """You are improving a scheduling policy for a fleet of six compute sites in California. The power grid
can call on the fleet during a demand response event: the grid asks the sites in one zone to cut power below
their normal baseline. Your policy decides, for one site and one 5-minute interval at a time, how much batch
work to pause, how much to move to another site (elastic bursting), and how much extra batch work to run
here right now (boosting, which shifts work into clean or cheap hours).

The policy is written in LOGOS, an English-like language that compiles to Rust. You write exactly two
functions. A harness around them (which you do not write) defines this record and two helpers:

  A Situation has these Int fields:
    minute        minutes since midnight, Pacific time (0 to 1435, steps of 5)
    site          site number 0 to 5
    price         wholesale power price at this site's grid hub, in cents per MWh
    carbon        grid carbon intensity, grams of CO2 per kWh
    event         1 while this site is inside an active demand response event, else 0
    target        watts this site is asked to shed below its baseline right now (0 outside an event)
    urgent        watts of urgent inference work here (you cannot touch it)
    batch         watts of batch work that would run here this interval if you do nothing
    backlog       batch work still to do at this site today, in watt-intervals (deadline: end of day)
    left          intervals left in the day
    room          spare watts at the best other site that could receive moved work
    otherPrice    price at that receiving site's hub, cents per MWh
    otherInEvent  1 if that receiving site is itself inside the event zone, else 0
    ownRoom       spare watts at THIS site right now, up to its power limit

  minOf(a, b) and maxOf(a, b) return the smaller and the larger of two Ints.

  You write:
    ## To pauseFor (s: Situation) -> Int:     watts of batch work to pause here this interval
    ## To moveFor (s: Situation) -> Int:      watts of batch work to move to the receiving site this interval
    ## To boostFor (s: Situation) -> Int:     extra watts of batch work to run here this interval, on top of the usual rate

What happens: paused work waits and must still be done before the end of the day, so pausing too much makes
the site miss its deadline. Moved work runs at the receiving site, costs 5 percent extra work, and draws power
there instead of here. Boosted work runs here now, so it finishes earlier and leaves slack for later (only while
work is left, boosting past that does nothing). Batch work runs at a fixed usual rate unless boosted, so without
boosting there is only about 100 minutes of slack in the day. Shedding is measured against what the site would
have drawn if the policy did nothing, so boosting at a site inside an active event undoes part of its cut.
Carbon intensity and prices change a lot through the day (solar at midday, gas at night), but the policy only
sees the current interval, with no forecast.

What is scored, roughly in order of importance: how much of the requested cut is delivered during the event,
how fast the cut is reached, batch work missed by the deadline, energy cost, and carbon.

SAFETY RULES. Breaking any of them, even once, disqualifies the policy with a score of 0:
  1. pauseFor, moveFor and boostFor never return a negative number.
  2. pauseFor(s) + moveFor(s) is never more than s's batch.
  3. moveFor(s) is never more than s's room.
  4. boostFor(s) is never more than s's ownRoom.

PROOF. After scoring, every version is also checked by a prover (largo prove) against these rules for EVERY
possible input, not just the inputs the simulation happens to send. Rule 1 has no premise. Rules 2, 3 and 4 may
assume s's batch, s's room and s's ownRoom (respectively) are at least 0. A version that cannot be proven safe is
never deployed to a real machine and can never become the best version, whatever its score. So clamp every
return value with minOf and maxOf so it is safe even for strange inputs (a negative target, for example). The
prover handles If, Otherwise, Let, Set, minOf, maxOf, + - *, and division by a constant. It cannot handle loops,
and a rule that depends on the exact value of one field multiplied by another field will fail to prove.

LOGOS syntax that is known to work (follow it exactly):
  - A function header:   ## To pauseFor (s: Situation) -> Int:
  - Field access:        s's target
  - Conditions:          If s's event equals 1:     If s's price is greater than 4000:     If a is less than b:
                         Combine with "and" / "or". Use "Otherwise:" for else. Indent bodies by 4 spaces.
  - Locals:              Let x be minOf(s's target, s's batch).
                         Let mutable y be 0.    then    Set y to y + 1.
  - Arithmetic:          + - * / on Int only. There are no decimals. Integer division rounds down.
  - Every statement ends with a period. Every function must end every path with "Return <Int>.".
  - Explanations go ONLY in lines that start with "# " (a comment). Never write a plain prose line between
    or after functions: it breaks the parser.
  - Do not define any other "## " block and do not write a Main.

Reply with the complete policy file only, between a line "BEGIN POLICY" and a line "END POLICY". Start the file
with "# " comment lines that explain in plain English what this version does and what it changed from the best
version so far. Keep the explanation short and readable by a grid operator."""


def ask_claude(prompt: str, model: str) -> str:
    CELL.mkdir(exist_ok=True)
    r = subprocess.run(["claude", "-p", "--tools", "", "--strict-mcp-config", "--no-session-persistence",
                        "--model", model], input=prompt, capture_output=True, text=True, cwd=CELL, timeout=600)
    return r.stdout


def extract_policy(reply: str) -> str | None:
    m = re.search(r"BEGIN POLICY\s*\n(.*?)\nEND POLICY", reply, re.S)
    if not m:
        return None
    body = m.group(1).strip("\n")
    body = re.sub(r"^```[a-z]*\n|\n```$", "", body)
    return body + "\n"


def build(path: Path) -> tuple[str | None, str]:
    r = subprocess.run([str(HERE / "policy" / "build.sh"), str(path)], capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        return None, (r.stderr or r.stdout)[-1500:]
    return r.stdout.strip().splitlines()[-1], ""


def simulate(binary: str, out_json: Path) -> dict:
    subprocess.run([sys.executable, "-I", str(HERE / "sim" / "simulate.py"), "--policy", binary,
                    "--json", str(out_json), "--timeseries", str(out_json.with_suffix("").with_suffix(".timeseries.csv"))],
                   capture_output=True, text=True, timeout=300)
    return json.loads(out_json.read_text())


def load_board() -> list[dict]:
    return json.loads(BOARD.read_text()) if BOARD.exists() else []


def save_board(board: list[dict]) -> None:
    BOARD.write_text(json.dumps(board, indent=2))


def history_text(board: list[dict]) -> str:
    lines = []
    for e in board:
        if e.get("build_error"):
            lines.append(f"v{e['version']:03d}: did not build: {e['build_error'][-400:]}")
        elif e["breakdown"].get("disqualified"):
            lines.append(f"v{e['version']:03d}: DISQUALIFIED: {e['breakdown']['disqualified']}")
        elif e.get("proved") is False:
            lines.append(f"v{e['version']:03d}: score {e['breakdown']['score']} but NOT PROVEN SAFE, so it cannot be "
                         f"deployed or become best: {(e.get('prove_failure') or '')[:300]}")
        else:
            b = e["breakdown"]
            lines.append(f"v{e['version']:03d}: score {b['score']} | delivered {b['delivery_accuracy_pct']}% of "
                         f"{b['committed_kw']} kW | response {b['response_minutes']} min | missed batch "
                         f"{b['missed_batch_kwh']} kWh | moved {b['moved_kwh']} kWh | paused {b['paused_kwh']} kWh | "
                         f"boosted {b.get('boosted_kwh', 0)} kWh | "
                         f"cost ${b['energy_cost_usd']} | carbon {b['carbon_kg']} kg")
    return "\n".join(lines)


def best(board: list[dict]) -> dict:
    ok = [e for e in board if not e.get("build_error") and not e["breakdown"].get("disqualified")]
    proven = [e for e in ok if e.get("proved") is True]
    return max(proven or ok, key=lambda e: e["breakdown"]["score"])


def prove(version: int) -> dict:
    src = VERSIONS / f"v{version:03d}.lg"
    t0 = time.time()
    try:
        code = subprocess.run([str(HERE / "policy" / "prove.sh"), str(src)], capture_output=True, text=True,
                              timeout=600).returncode
    except subprocess.TimeoutExpired:
        code = -1
    out = src.with_suffix(".prove.txt")
    fails = [l for l in (out.read_text() if out.exists() else "").splitlines() if l.startswith("✗")]
    return {"proved": code == 0, "prove_seconds": round(time.time() - t0, 1),
            "prove_failure": fails[0][:400] if fails else ("timed out after 600 s" if code == -1 else None)}


def ensure_v1(board: list[dict]) -> list[dict]:
    if board:
        return board
    v1 = VERSIONS / "v001.lg"
    binary, err = build(v1)
    if not binary:
        sys.exit(f"baseline v001 does not build: {err}")
    board.append({"version": 1, "author": "hand", "parent": None, "breakdown": simulate(binary, VERSIONS / "v001.score.json"),
                  "time": time.strftime("%H:%M:%S")})
    save_board(board)
    return board


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=10)
    ap.add_argument("--model", default="claude-opus-5-5")
    a = ap.parse_args()
    board = ensure_v1(load_board())
    for _ in range(a.iterations):
        top = best(board)
        version = max(e["version"] for e in board) + 1
        src = (VERSIONS / f"v{top['version']:03d}.lg").read_text()
        prompt = (CONTRACT + f"\n\nBEST POLICY SO FAR (v{top['version']:03d}):\nBEGIN POLICY\n{src}END POLICY\n\n"
                  f"EVERY ATTEMPT SO FAR:\n{history_text(board)}\n\nWrite version {version}. Aim to beat the best score.")
        t0 = time.time()
        reply = ask_claude(prompt, a.model)
        policy = extract_policy(reply)
        path = VERSIONS / f"v{version:03d}.lg"
        entry = {"version": version, "author": a.model, "parent": top["version"], "time": time.strftime("%H:%M:%S")}
        if not policy:
            entry.update({"build_error": "reply had no BEGIN POLICY / END POLICY block", "breakdown": {}})
            path.with_suffix(".reply.txt").write_text(reply)
        else:
            path.write_text(policy)
            binary, err = build(path)
            if not binary:
                entry.update({"build_error": err, "breakdown": {}})
            else:
                entry["breakdown"] = simulate(binary, VERSIONS / f"v{version:03d}.score.json")
                if not entry["breakdown"].get("disqualified"):
                    entry.update(prove(version))
        entry["seconds"] = round(time.time() - t0, 1)
        board.append(entry)
        save_board(board)
        b = entry["breakdown"]
        status = (entry.get("build_error", "")[:120] or b.get("disqualified") or
                  f"score {b.get('score')}, {'proven' if entry.get('proved') else 'NOT proven'}")
        print(f"v{version:03d} (from v{top['version']:03d}) {entry['seconds']}s: {status}", flush=True)
    print(f"best: v{best(board)['version']:03d} score {best(board)['breakdown']['score']}")


if __name__ == "__main__":
    main()
