#!/usr/bin/env python3
"""Track 4 simulator: replay a real CAISO day over a fleet of compute sites and score a policy.

The policy is a compiled LOGOS program run as a long-lived process. For every site, every 5-minute interval,
the simulator writes one line of integers to the policy's stdin and reads one line back:

  in : minute,site,price,carbon,event,target_w,urgent_w,batch_w,backlog_w,left,other_headroom_w,other_price,other_in_event,own_room_w
  out: pause_w,move_w,boost_w

  price, other_price   cents per MWh at this site's hub, and at the receiving site's hub
  carbon               grams CO2 per kWh, grid average
  event                1 while this site is inside an active demand response event, else 0
  target_w             watts this site is asked to shed below its baseline (0 outside an event)
  urgent_w             inference load that must be served here this interval (never paused, never moved)
  batch_w              batch load that would run here this interval if nothing changes
  backlog_w            batch work left at this site, in watt-intervals (deadline: end of day)
  left                 intervals left in the day
  other_headroom_w     spare watts at the best receiving site (elastic bursting target)
  other_in_event       1 if that receiving site is inside the event zone
  own_room_w           spare watts at this site right now (its limit minus idle, urgent, batch and arriving work)

  pause_w   batch watts to pause here this interval (the work waits)
  move_w    batch watts to move to the receiving site this interval (elastic bursting, 5 percent overhead)
  boost_w   extra batch watts to run here this interval, on top of the usual rate (time-shifting work into clean or
            cheap hours). Boosting past the work that is left does nothing and draws no extra power.

Safety rules, enforced here at run time as a backstop to the largo prove theorems. Any breach
disqualifies the policy (score 0):
  1. pause_w >= 0, move_w >= 0 and boost_w >= 0
  2. pause_w + move_w <= batch_w        (only batch work is ever touched, so urgent work is never paused)
  3. move_w <= other_headroom_w          (a receiving site never goes past its power limit)
  4. boost_w <= own_room_w               (this site never goes past its own power limit)

Usage:
  python3 -I simulate.py --policy path/to/binary [--json out.json] [--timeseries out.csv]
  python3 -I simulate.py --policy none          (the do-nothing baseline)
"""
import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
INTERVAL_MIN = 5
INTERVALS = 24 * 60 // INTERVAL_MIN
MOVE_EFFICIENCY_PCT = 95      # moved work loses 5 percent to transfer and warm-up
BACKLOG_PCT_OF_DAY = 93       # each site's batch work fills 93 percent of the day, so slack is about 100 minutes

# Score weights. Fixed, documented, and never visible to the agent.
W_DELIVERY = 10.0      # per percentage point of delivery accuracy during the event
W_RESPONSE = 5.0       # per minute to reach 95 percent of the committed shed
W_MISSED = 20.0        # per kWh of batch work not done by its deadline
W_COST = 1.0           # per dollar of energy
W_CARBON = 0.5         # per kg CO2


def load_trace(day: str) -> list[dict]:
    with open(DATA / f"grid-trace-{day}.csv", newline="") as f:
        return [{k: int(v) if k != "time_pt" else v for k, v in r.items()} for r in csv.DictReader(f)]


def load_urgent_shape(trace: list[dict]) -> list[list[int]]:
    """Per-site urgent load factor (percent) per interval.

    Burstiness comes from the real Azure LLM code-assistant trace (one hour, 2023-11-16): total tokens per
    5-minute bucket, relative to the mean. The daily shape is CAISO demand relative to its mean. Tiling one
    hour across a day, and offsetting it per site, is SYNTHETIC and labeled as such.
    """
    buckets: dict[int, int] = {}
    t0 = None
    with open(DATA / "raw" / "azure-llm-inference-code-2023.csv", newline="") as f:
        for r in csv.DictReader(f):
            hh, mm, ss = r["TIMESTAMP"].split(" ")[1].split(":")
            sec = int(hh) * 3600 + int(mm) * 60 + float(ss)
            t0 = sec if t0 is None else t0
            b = int((sec - t0) // 300)
            buckets[b] = buckets.get(b, 0) + int(r["ContextTokens"]) + int(r["GeneratedTokens"])
    full = [buckets[b] for b in sorted(buckets) if b < 11]  # drop the partial last bucket
    mean_b = sum(full) / len(full)
    mean_d = sum(r["demand_mw"] for r in trace) / len(trace)
    shapes = []
    for site in range(6):
        row = []
        for i, r in enumerate(trace):
            burst = full[(i + site * 2) % len(full)] / mean_b
            daily = r["demand_mw"] / mean_d
            row.append(round(100 * burst * daily))
        shapes.append(row)
    return shapes


class Policy:
    def __init__(self, path: str):
        self.p = subprocess.Popen([path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, bufsize=1)

    def decide(self, fields: list[int]) -> tuple[int, int]:
        self.p.stdin.write(",".join(str(x) for x in fields) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline().strip()
        if not line:
            err = self.p.stderr.read() if self.p.poll() is not None else ""
            raise RuntimeError(f"policy gave no answer (exit {self.p.poll()}) {err.strip()[:300]}")
        a, b, c = line.split(",")
        return int(a), int(b), int(c)

    def close(self):
        try:
            self.p.stdin.write("end\n")
            self.p.stdin.flush()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def run(policy_path: str | None, day: str, sites: list[dict], event: dict, trace, shapes, baseline=None):
    n = len(sites)
    zone = event["zone"]
    ev_start = event["intervalPeriod"]["start_minute"]
    ev_end = ev_start + event["intervalPeriod"]["duration_minutes"]
    backlog = [s["batch_w"] * INTERVALS * BACKLOG_PCT_OF_DAY // 100 for s in sites]
    policy = Policy(policy_path) if policy_path else None
    power = [[0] * INTERVALS for _ in range(n)]
    moved_total_wi = 0
    paused_total_wi = 0
    boosted_total_wi = 0
    cost = 0.0
    carbon = 0.0
    violation = None
    committed_w = None
    if baseline is not None:
        zone_base = sum(baseline["power"][s["id"]][ev_start // INTERVAL_MIN] for s in sites if s["hub"] == zone)
        committed_w = zone_base * event["shed_pct"] // 100

    try:
        for i, r in enumerate(trace):
            minute = r["minute"]
            in_event = ev_start <= minute < ev_end
            urgent = [s["urgent_w"] * shapes[s["id"]][i] // 100 for s in sites]
            batch = [min(s["batch_w"], backlog[s["id"]]) for s in sites]
            incoming = [0] * n
            running = list(batch)
            # Event sites decide first, so receiving sites see the work that arrives.
            order = sorted(range(n), key=lambda k: 0 if sites[k]["hub"] == zone else 1)
            for k in order:
                s = sites[k]
                ev = 1 if (in_event and s["hub"] == zone) else 0
                target = 0
                if ev and committed_w is not None:
                    base_k = baseline["power"][k][i]
                    zone_base_i = sum(baseline["power"][j][i] for j in range(n) if sites[j]["hub"] == zone)
                    target = committed_w * base_k // max(zone_base_i, 1)
                # Best receiver: most spare watts among the other sites, outside the event zone if possible.
                def headroom(j):
                    return sites[j]["max_w"] - (sites[j]["idle_w"] + urgent[j] + running[j] + incoming[j])
                others = [j for j in range(n) if j != k]
                others.sort(key=lambda j: (1 if (in_event and sites[j]["hub"] == zone) else 0, -headroom(j)))
                o = others[0]
                o_head = max(0, headroom(o))
                o_ev = 1 if (in_event and sites[o]["hub"] == zone) else 0
                own_room = max(0, headroom(k))
                if policy is None:
                    pause, move, boost = 0, 0, 0
                else:
                    fields = [minute, k, r[f"price_{s['hub']}"], r["carbon_g_kwh"], ev, target, urgent[k],
                              batch[k], backlog[k], INTERVALS - i, o_head, r[f"price_{sites[o]['hub']}"], o_ev, own_room]
                    pause, move, boost = policy.decide(fields)
                    if pause < 0 or move < 0 or boost < 0:
                        violation = f"rule 1 at {r['time_pt']} site {s['name']}: negative pause {pause}, move {move} or boost {boost}"
                    elif pause + move > batch[k]:
                        violation = f"rule 2 at {r['time_pt']} site {s['name']}: pause {pause} + move {move} > batch {batch[k]}"
                    elif move > o_head:
                        violation = f"rule 3 at {r['time_pt']} site {s['name']}: move {move} > receiver headroom {o_head}"
                    elif boost > own_room:
                        violation = f"rule 4 at {r['time_pt']} site {s['name']}: boost {boost} > own room {own_room}"
                    if violation:
                        raise StopIteration
                running[k] -= pause + move
                incoming[o] += move
                backlog[k] -= move * MOVE_EFFICIENCY_PCT // 100
                # Boosted work only runs while there is work left to do.
                boost = min(boost, max(0, backlog[k] - running[k]))
                running[k] += boost
                moved_total_wi += move
                paused_total_wi += pause
                boosted_total_wi += boost
            for k, s in enumerate(sites):
                backlog[k] -= running[k]
                p = s["idle_w"] + urgent[k] + running[k] + incoming[k]
                power[k][i] = p
                kwh = p * INTERVAL_MIN / 60 / 1000
                cost += kwh * r[f"price_{s['hub']}"] / 100 / 1000
                carbon += kwh * r["carbon_g_kwh"] / 1000
    except StopIteration:
        pass
    except Exception as e:
        violation = f"policy crashed or misbehaved: {e}"
    finally:
        if policy:
            policy.close()

    return {"power": power, "backlog_end_wi": backlog, "moved_wi": moved_total_wi, "paused_wi": paused_total_wi,
            "boosted_wi": boosted_total_wi,
            "cost": cost, "carbon": carbon, "violation": violation, "committed_w": committed_w}


def score(result, baseline, sites, event, trace):
    zone = event["zone"]
    ev_start = event["intervalPeriod"]["start_minute"]
    ev_end = ev_start + event["intervalPeriod"]["duration_minutes"]
    ramp = event["ramp_minutes"]
    committed = result["committed_w"]
    wi_to_kwh = INTERVAL_MIN / 60 / 1000
    out = {"committed_kw": round(committed / 1000, 1)}
    if result["violation"]:
        out.update({"score": 0.0, "disqualified": result["violation"]})
        return out
    delivered_series = []
    response_min = None
    for i, r in enumerate(trace):
        m = r["minute"]
        if not (ev_start <= m < ev_end):
            continue
        d = sum(baseline["power"][k][i] - result["power"][k][i] for k, s in enumerate(sites) if s["hub"] == zone)
        delivered_series.append((m, d))
        if response_min is None and d >= committed * 95 // 100:
            response_min = m - ev_start
    counted = [(m, d) for m, d in delivered_series if m >= ev_start + ramp]
    accuracy = 100 * sum(min(d / committed, 1.0) for _, d in counted) / len(counted)
    if response_min is None:
        response_min = event["intervalPeriod"]["duration_minutes"]
    missed_kwh = sum(max(0, b) for b in result["backlog_end_wi"]) * wi_to_kwh
    fleet_peak_kw = max(sum(result["power"][k][i] for k in range(len(sites))) for i in range(len(trace))) / 1000
    total = (W_DELIVERY * accuracy - W_RESPONSE * response_min - W_MISSED * missed_kwh
             - W_COST * result["cost"] - W_CARBON * result["carbon"])
    out.update({
        "score": round(total, 1),
        "delivery_accuracy_pct": round(accuracy, 1),
        "mean_delivered_kw": round(sum(d for _, d in counted) / len(counted) / 1000, 1),
        "response_minutes": response_min,
        "missed_batch_kwh": round(missed_kwh, 1),
        "moved_kwh": round(result["moved_wi"] * wi_to_kwh, 1),
        "paused_kwh": round(result["paused_wi"] * wi_to_kwh, 1),
        "boosted_kwh": round(result["boosted_wi"] * wi_to_kwh, 1),
        "energy_cost_usd": round(result["cost"], 2),
        "carbon_kg": round(result["carbon"], 1),
        "fleet_peak_kw": round(fleet_peak_kw, 1),
        "min_size_100kw_met": fleet_peak_kw >= 100,
    })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", required=True, help="path to a compiled LOGOS policy binary, or 'none'")
    ap.add_argument("--day", default="2026-11-07")
    ap.add_argument("--json", help="write the score breakdown here")
    ap.add_argument("--timeseries", help="write per-interval fleet power (baseline and policy) here")
    a = ap.parse_args()

    sites = json.load(open(HERE / "sites.json"))["sites"]
    event = json.load(open(HERE / "event.json"))
    trace = load_trace(a.day)
    shapes = load_urgent_shape(trace)
    baseline = run(None, a.day, sites, event, trace, shapes)
    path = None if a.policy == "none" else a.policy
    result = run(path, a.day, sites, event, trace, shapes, baseline=baseline)
    if result["committed_w"] is None:
        zone_base = sum(baseline["power"][s["id"]][event["intervalPeriod"]["start_minute"] // INTERVAL_MIN]
                        for s in sites if s["hub"] == event["zone"])
        result["committed_w"] = zone_base * event["shed_pct"] // 100
    breakdown = score(result, baseline, sites, event, trace)
    print(json.dumps(breakdown, indent=2))
    if a.json:
        Path(a.json).write_text(json.dumps(breakdown, indent=2))
    if a.timeseries:
        with open(a.timeseries, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["minute", "time_pt"] + [f"{s['name']}_w" for s in sites] + ["baseline_zone_w", "policy_zone_w"])
            for i, r in enumerate(trace):
                zb = sum(baseline["power"][k][i] for k, s in enumerate(sites) if s["hub"] == event["zone"])
                zp = sum(result["power"][k][i] for k, s in enumerate(sites) if s["hub"] == event["zone"])
                w.writerow([r["minute"], r["time_pt"]] + [result["power"][k][i] for k in range(len(sites))] + [zb, zp])
    return 0 if not breakdown.get("disqualified") else 2


if __name__ == "__main__":
    sys.exit(main())
