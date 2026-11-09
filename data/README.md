# Grid replay data

One real California grid day, Saturday 2026-11-07 (Pacific Standard Time), at 5-minute resolution. Every value
in `grid-trace-2026-11-07.csv` comes from CAISO public data with no key, downloaded 2026-11-08. Rebuild with:

```bash
python3 -I data/build_trace.py 2026-11-07
```

## Columns

| Column | Unit | Source |
| --- | --- | --- |
| `minute` | minutes since local midnight | derived |
| `time_pt` | HH:MM Pacific | derived |
| `price_np15`, `price_zp26`, `price_sp15` | cents per MWh (integer, can be negative) | real-time 5-minute LMP at the three CAISO trading hubs: NP15 (Northern California, including San Francisco), ZP26 (Central), SP15 (Southern) |
| `carbon_g_kwh` | grams CO2 per kWh | total CO2 from the Today's Outlook emissions file divided by total supply from the fuel mix file |
| `demand_mw` | MW | CAISO current demand |

Integers only, because LOGOS has no documented float type.

## Sources

| Raw file | URL |
| --- | --- |
| `raw/caiso-rtm-lmp-np15-2026-11-07.csv` | `https://oasis.caiso.com/oasisapi/SingleZip?queryname=PRC_INTVL_LMP&startdatetime=20261107T08:00-0000&enddatetime=20261108T08:00-0000&version=3&market_run_id=RTM&node=TH_NP15_GEN-APND&resultformat=6` |
| `raw/caiso-rtm-lmp-zp26-2026-11-07.csv` | the same query with `node=TH_ZP26_GEN-APND` |
| `raw/caiso-rtm-lmp-sp15-2026-11-07.csv` | the same query with `node=TH_SP15_GEN-APND` |
| `raw/caiso-outlook-co2-2026-11-07.csv` | `https://www.caiso.com/outlook/history/20261107/co2.csv` |
| `raw/caiso-outlook-fuelsource-2026-11-07.csv` | `https://www.caiso.com/outlook/history/20261107/fuelsource.csv` |
| `raw/caiso-outlook-demand-2026-11-07.csv` | `https://www.caiso.com/outlook/history/20261107/demand.csv` |

## What the day looks like

| | Min | Max | Mean |
| --- | --- | --- | --- |
| Price, $/MWh | 21.12 | 62.83 (15:50) | 38.94 |
| Carbon, g CO2/kWh | 35 (midday solar) | 333 (night) | 196 |

## Caveats to say out loud

- **No price spike on this day.** The demand response event in the demo is scripted and must be
  labeled synthetic, sitting on top of real prices and carbon.
- **Carbon is an average, not a marginal, estimate.** It is the grid mix as a whole, not the plant that would
  turn on or off because of our load. Imports are counted with CAISO's own emissions figure for them.
- Batteries count as supply only while discharging.
- The Outlook files end with the next midnight, also labeled 00:00. The build keeps the first row for each time.

## Urgent load shape

`raw/azure-llm-inference-code-2023.csv` is the one-hour Azure LLM inference trace for a code assistant (2023-11-16), from the Azure Public Dataset (https://github.com/Azure/AzurePublicDataset, `AzureLLMInferenceTrace_code`), published under CC BY 4.0. The simulator uses its tokens per minute only for burstiness, tiled over the day and scaled by CAISO demand.
