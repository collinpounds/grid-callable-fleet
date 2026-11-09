#!/usr/bin/env python3
"""Prove the four safety rules for every version on the leaderboard that has no proof result yet.

Usage: python3 -I prove_all.py [--all]
Adds to each leaderboard entry: proved (true/false), prove_seconds, prove_failure (first failing rule, if any).
"""
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOARD = HERE / "leaderboard.json"


def prove(version: int) -> dict:
    src = HERE / "versions" / f"v{version:03d}.lg"
    t0 = time.time()
    try:
        r = subprocess.run([str(HERE / "prove.sh"), str(src)], capture_output=True, text=True, timeout=600)
        code = r.returncode
    except subprocess.TimeoutExpired:
        code = -1
    text = src.with_suffix(".prove.txt").read_text() if src.with_suffix(".prove.txt").exists() else ""
    fails = [l for l in text.splitlines() if l.startswith("✗")]
    return {"proved": code == 0, "prove_seconds": round(time.time() - t0, 1),
            "prove_failure": (fails[0][:400] if fails else ("timed out after 600 s" if code == -1 else None))}


def main():
    redo = "--all" in sys.argv
    for e in json.loads(BOARD.read_text()):
        if e.get("build_error") or ("proved" in e and not redo):
            continue
        res = prove(e["version"])
        board = json.loads(BOARD.read_text())          # re-read: the loop may have appended meanwhile
        for x in board:
            if x["version"] == e["version"]:
                x.update(res)
        BOARD.write_text(json.dumps(board, indent=2))
        print(f"v{e['version']:03d}: {'proved' if res['proved'] else 'NOT proved'} in {res['prove_seconds']}s"
              + (f"  {res['prove_failure'][:160]}" if res["prove_failure"] else ""), flush=True)


if __name__ == "__main__":
    main()
