# Launch video: claims and sources

`docs/launch-video.mp4` runs 132 s (1920x1080, 30 fps, silent). It was made with fframes on
2026-11-08. Every number in it comes from this repository.

| On screen | Source |
| --- | --- |
| Many small compute sites acting as one resource the grid can call on | `README.md`, opening paragraph |
| Six sites, simulated, peaking above the 100 kW FERC Order 2222 minimum | `sim/sites.json`, `README.md` "What is real and what is not" |
| The carbon and NP15 price curves for 7 November 2026, cleanest 35 g per kWh at 09:05 | `data/grid-trace-2026-11-07.csv` (drawn every 15 minutes), `data/README.md` |
| Scripted event: Bay Area, cut 35 percent, 17:00 to 20:00, no real event that day | `sim/event.json`, `data/README.md` |
| The loop, and what Claude does and does not see | `README.md` "What happens" steps 2 to 5 |
| The v001 and v021 code | `policy/versions/v001.lg` lines 6 to 9, `policy/versions/v021.lg` lines 46 to 52, verbatim |
| Scores per version, 255.5 for v001, 665.6 for v002, 718.9 for v021 | `policy/leaderboard.json`, `README.md` "Results" |
| Carbon 491.1 to 402.2 kg (18 percent less), cost $89.46 to $79.92, missed work 20.5 to 0 kWh | `README.md` "Results" table, and each version's breakdown in `policy/leaderboard.json` |
| The four safety rules, and Rule 1 as written | `README.md` "What happens" step 4, `policy/rules.lg` |
| 22 of 22 Claude versions proven, v001 fails rule 1 on a negative grid target, proofs in 6 to 37 s | `policy/prove-all.log`, `policy/versions/v001.prove.txt`, `README.md` "Results" |
| Bay Area event: 15.0 W to 10.2 W, 4.8 W of 5.25 W in about 3 s, fleet 17.4 W to 19.5 W, no work lost | `README.md` "Real machines" table, chart from `live/runs/live-162414.csv` |
| The other machine goes from 2.3 W to 9.4 W | `live/runs/live-162414.csv`, median of `recv_mw` before and during the event |
| Statewide event: fleet 17.4 W to 12.6 W, work pauses and resumes after | `README.md` "Real machines" table, chart from `live/runs/live-162832.csv` |
| Two Intel N100 machines, about 15 W each, RAPL package power, scripted event | `README.md` "What happens" step 6 and "What is real and what is not" |

The video does not claim certified OpenADR, and it says the Bay Area case raises the fleet total. The
machines are named by role, not by hostname. The map in the first scene is an illustration, not to scale.
