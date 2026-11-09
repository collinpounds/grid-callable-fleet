# Launch video: claims and sources

`docs/launch-video.mp4` runs 158 s (1920x1080, 30 fps, silent). It is the second cut, made with
fframes on 2026-11-08 for Datacenter.Dev. Every number in it comes from this repository.

| On screen | Source |
| --- | --- |
| "When the grid calls, our compute fleet cuts its power by 27% in 3 seconds." | `live/runs/live-162832.csv` and `live/runs/live-163829.csv`, both statewide. The dashboard's method (`summarize` in `dashboard/index.html`) takes the mean fleet power before the event (after 5 s) and during it (from its fifth row). That gives 17.38 W to 12.65 W (−27.2%) and 17.36 W to 12.66 W (−27.1%). The cut counts as settled at the first event row that delivers 90% of the settled cut, 3 s after the event starts in both runs. |
| "Fleet total: 17.4 W to 12.6 W" and the cold-open chart | `README.md` "Real machines" table, statewide row. The chart is `live/runs/live-162832.csv`. |
| "Claude wrote the policy that decides it. The policy is in plain English that compiles. It was proven safe before it touched a machine." | The dashboard's opening line (`dashboard/index.html`), and `README.md` "What happens" |
| "Thousands of small compute sites could be one resource the grid can call on." | Track 4's framing, from the brief. It states the opportunity and is not a measured number. |
| Six simulated sites, peaking above the 100 kW FERC Order 2222 minimum | `sim/sites.json`, `README.md` "What is real and what is not" |
| "In LOGOS, a grid operator can read every rule, and it compiles to a real program." | The dashboard's first card, and `README.md` (LOGOS compiles to Rust, and `policy/build.sh` builds `policy/bin/v021`) |
| v001's code, "prover found a bug" | `policy/versions/v001.lg` lines 6 to 9, verbatim, and `policy/versions/v001.prove.txt` |
| v021's `moveFor`, "proven safe" | `policy/versions/v021.lg` lines 46 to 52, verbatim, and `policy/prove-all.log` |
| v021's `pauseFor` event branch | `policy/versions/v021.lg` lines 26 to 34, verbatim |
| "If the other site has room, move the work. Otherwise, pause it." and the line notes | The dashboard's note on the policy card, and the comments at the top of `policy/versions/v021.lg` |
| The four safety rules, and Rule 1 as written | `README.md` "What happens" step 4, `policy/rules.lg` |
| 22 of 22 Claude versions proven, v001 fails rule 1 on a negative grid target that the simulator never sends, proofs in 6 to 37 s | `policy/prove-all.log` (5.7 to 36.9 s), `policy/versions/v001.prove.txt`, `README.md` "Results" |
| Scores per version, 255.5 to 718.9 | `policy/leaderboard.json`, `README.md` "Results" |
| Carbon 402.2 kg, 18% less than v001's 491.1 kg | `README.md` "Results" table |
| Energy cost $79.92, 11% lower than v001's $89.46 | `README.md` "Results" table. 1 − 79.92 / 89.46 = 10.7%, rounded to 11% as the dashboard does. |
| Missed work down from 20.5 kWh to 0 | `README.md` "Results" table |
| "Every version replayed on the same real CAISO grid day, 7 November 2026" | `README.md` "Results", `data/README.md` |
| Bay Area call: the zone gets 4.8 W of the 5.25 W asked in about 3 s, no work lost, fleet total 17.4 W to 19.5 W | `README.md` "Real machines" table. The chart is `live/runs/live-162414.csv`. |
| Bay Area machine 15.0 W to 10.2 W | `README.md` "Real machines" table |
| Other machine 2.3 W to 9.4 W | `live/runs/live-162414.csv`, median of `recv_mw` before and during the event |
| Statewide call: nowhere to move, the policy pauses, 17.4 W to 12.6 W, −27% in 3 s, paused work resumes | `README.md` "Real machines" table and the paragraph under it, `live/runs/live-162832.csv` |
| "The same compiled v021 ran both calls. The choice comes from its own move-or-pause lines." | `README.md` ("the same v021 binary", "The policy chooses between the two from the situation it is given") |
| Two Intel N100 machines, about 15 W each, RAPL package power, scripted event on a real grid day | `README.md` "What happens" step 6 and "What is real and what is not" |

The video does not claim certified OpenADR. It says the Bay Area call raises the fleet total, and that
only the statewide call cuts total energy. The machines are named by role, not by hostname. The map in
the problem scene is an illustration, not to scale.
