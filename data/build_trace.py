#!/usr/bin/env python3
"""Build the grid replay trace from raw CAISO files for one Pacific day.

Inputs (data/raw/, downloaded 2026-11-08, see data/README.md for exact URLs):
  caiso-rtm-lmp-<hub>-<day>.csv         OASIS PRC_INTVL_LMP, real-time 5-minute LMP at TH_<HUB>_GEN-APND
  caiso-outlook-co2-<day>.csv           Today's Outlook history: CO2 by source, metric tons per hour
  caiso-outlook-fuelsource-<day>.csv    Today's Outlook history: generation by fuel, MW
  caiso-outlook-demand-<day>.csv        Today's Outlook history: demand, MW

Output: data/grid-trace-<day>.csv, one row per 5-minute interval, integers only (LOGOS has no float type):
  minute            minutes since local midnight, Pacific time (0, 5, ... 1435)
  time_pt           HH:MM Pacific
  price_<hub>       real-time LMP in cents per MWh (can be negative) at NP15, ZP26 and SP15
  carbon_g_kwh      grid-average carbon intensity, grams CO2 per kWh
  demand_mw         CAISO current demand, MW

Carbon intensity = total CO2 (t/h) / total supply (MW) * 1000. Supply counts every fuel row including imports
and batteries only when discharging (positive). This is an average-emissions estimate, not a marginal one.

Usage: python3 -I build_trace.py 2026-11-07
"""
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
PACIFIC_STANDARD = timezone(timedelta(hours=-8))  # 2026-11-07 is after the 2026-11-01 DST change


def hhmm_to_minute(t: str) -> int:
    h, m = t.strip().split(":")
    return int(h) * 60 + int(m)


HUBS = ("np15", "zp26", "sp15")  # Northern, Central and Southern California trading hubs


def load_prices(day: str, hub: str) -> dict[int, int]:
    out = {}
    with open(RAW / f"caiso-rtm-lmp-{hub}-{day}.csv", newline="") as f:
        for row in csv.DictReader(f):
            if row["LMP_TYPE"] != "LMP":
                continue
            start = datetime.fromisoformat(row["INTERVALSTARTTIME_GMT"]).astimezone(PACIFIC_STANDARD)
            if start.strftime("%Y-%m-%d") != day:
                continue
            out[start.hour * 60 + start.minute] = round(float(row["VALUE"]) * 100)
    return out


def load_outlook(day: str, name: str) -> dict[int, dict[str, float]]:
    out = {}
    with open(RAW / f"caiso-outlook-{name}-{day}.csv", newline="") as f:
        for row in csv.DictReader(f):
            t = (row.get("Time") or "").strip()
            if not t:
                continue
            minute = hhmm_to_minute(t)
            # The files end with the next midnight, also labeled 00:00. Keep the first row per time.
            if minute >= 24 * 60 or minute in out:
                continue
            vals = {}
            for k, v in row.items():
                if k == "Time":
                    continue
                try:
                    vals[k] = float(v)
                except (TypeError, ValueError):
                    pass
            out[minute] = vals
    return out


def main() -> None:
    day = sys.argv[1] if len(sys.argv) > 1 else "2026-11-07"
    prices = {hub: load_prices(day, hub) for hub in HUBS}
    co2 = load_outlook(day, "co2")
    fuel = load_outlook(day, "fuelsource")
    demand = load_outlook(day, "demand")

    rows = []
    for minute in range(0, 24 * 60, 5):
        missing = [n for n, d in (*((f"price_{h}", prices[h]) for h in HUBS), ("co2", co2), ("fuel", fuel), ("demand", demand)) if minute not in d]
        if missing:
            sys.exit(f"interval {minute} missing from {missing}, refusing to invent data")
        tons_per_hour = sum(co2[minute].values())
        supply_mw = sum(v for v in fuel[minute].values() if v > 0)
        carbon = round(tons_per_hour / supply_mw * 1000)
        rows.append({
            "minute": minute,
            "time_pt": f"{minute // 60:02d}:{minute % 60:02d}",
            **{f"price_{h}": prices[h][minute] for h in HUBS},
            "carbon_g_kwh": carbon,
            "demand_mw": round(demand[minute]["Current demand"]),
        })

    out = HERE / f"grid-trace-{day}.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    c = [r["carbon_g_kwh"] for r in rows]
    print(f"wrote {out.name}: {len(rows)} intervals")
    for h in HUBS:
        p = [r[f"price_{h}"] for r in rows]
        peak = max(rows, key=lambda r: r[f"price_{h}"])
        print(f"{h} $/MWh  min {min(p)/100:.2f}  max {max(p)/100:.2f}  mean {sum(p)/len(p)/100:.2f}  peak at {peak['time_pt']}")
    print(f"carbon g/kWh  min {min(c)}  max {max(c)}  mean {sum(c)//len(c)}")


if __name__ == "__main__":
    main()
