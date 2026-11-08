# SiteSignal Safety Pipeline

Turns messy maintenance exports into a **Safety Index** per machine and per site, then has an LLM
propose what to do, with the rules enforced in code. Built for small and medium buildings that have
no building sensors: it uses only data they already have (work orders, asset list, weather).

```
messy CSV/XLSX/text ──Nemotron──> clean JSON ──> Safety Index ──> Postgres ──Nemotron──> decisions
     extract.py                  bundle.json    safety_index.py    load_pg.py      decide.py
                                       ▲
                    NWS forecast ──────┘ weather_nws.py
```

This folder is separate from `sitesignal-backend/`. The two define tables with the same names
(`sites`, `assets`) but different columns, so use a separate database for each.

## Run it locally (Postgres on your machine)

```bash
cd safety-pipeline
./setup_local.sh
```

The script starts Postgres 16 in Docker if you have Docker, or else uses your local Postgres
(`brew install postgresql@16 && brew services start postgresql@16` on a Mac). It then restores the
demo data from `out/sitesignal_dump.sql` and installs the Python dependencies. When it finishes, it
prints the `DATABASE_URL` to export.

Useful queries:

```sql
SELECT * FROM asset_worklist;                         -- ranked machines, worst first
SELECT * FROM latest_decisions;                       -- proposed actions, pending human review
SELECT context FROM llm_site_context WHERE site_id = 'D101';   -- what the decision LLM reads
```

## Re-run the pipeline on new data

Needs `NVIDIA_API_KEY` (from build.nvidia.com) for the two Nemotron steps.

```bash
export NVIDIA_API_KEY=...
python -m sitesignal.extract all samples/sites.csv samples/assets.csv samples/work_orders.csv -o out/bundle.json
python -m sitesignal.weather_nws out/bundle.json -o out/weather.json
python -m sitesignal.safety_index out/bundle.json --weather out/weather.json -o out/safety.json
python -m sitesignal.load_pg --bundle out/bundle.json --safety out/safety.json --weather out/weather.json
python -m sitesignal.decide            # add --dry-run to print without saving
```

## What each piece does

| File | Role |
|---|---|
| `sitesignal/extract.py` | Nemotron (`nemotron-3-ultra-550b-a55b`, falls back to `nemotron-3.5-lightning-30b-a3b`) reads messy input. Code validates every row: enums, dates, IDs, foreign keys, dedupe. Bad rows go to `rejected` with a reason. Every change made while cleaning is kept in the row's `flags`, with the original value. Negative costs are kept, since they may be credits, and flagged. |
| `sitesignal/weather_nws.py` | 7-day forecast highs per site from the US National Weather Service (free, no key). |
| `sitesignal/safety_index.py` | Score from 0 to 100, where 100 is safest. Every point lost is tied to a named driver. `confidence` is separate from the score. Missing install date gives `unknown`, never "safe". No work-order history caps the score at 85. Constraints such as `DO_NOT_DEFER_WITHOUT_HUMAN_REVIEW` are computed in code. |
| `sitesignal/load_pg.py` + `db/safety_schema.sql` | All-or-nothing, idempotent load. Keeps score history per run. Views for the LLM and the worklist. |
| `sitesignal/decide.py` | Nemotron chooses the action, urgency, tasks and citations for each asset. It runs twice independently: if the runs disagree, the more protective action wins and a human must review. Code checks the allowed actions, the citations and that no numbers are invented, and falls back to a rule-based default. Code writes every sentence a person reads, from the data. Nothing is executed: everything is `pending_review`. |
| `sitesignal/sensitivity.py` | How much the weights move the rankings, on a synthetic portfolio. |

## Honest limits

- **Weights are expert judgement, not calibrated.** No public failure dataset pairs failures with
  equipment age and repair history. Sensitivity test: perturbing all weights by up to ±30% gives an
  average rank correlation of 0.93 (worst 0.70), and about 12% of assets change band. Calibrating
  needs your own work-order history with failures marked.
- **Service life:** rooftop units are 15 years (ASHRAE, via secondary sources). Walk-in coolers
  (15 years) come from trade sources only. Water heaters (10 years) are unverified. Weaker sources
  lower `confidence`.
- **Not a probability.** The output carries `calibrated: false` and tells the LLM not to read the
  index as a percentage.
- **Small test data.** Tested on 3 sites and 5 assets of sample data.
