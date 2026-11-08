# SiteSignal

Prevents equipment failures at small commercial sites with no building sensors.
A risk model (Weibull age curve x work-order history x weather forecast) scores every
machine; **Gemini 3.8 Flash** acts as an agent that decides what to do; **Photon 2.0**
texts service managers and site managers; everything lives in **Postgres** (Tiger Data).

```
 CSV/Excel exports --import--> Postgres <--weather job-- Open-Meteo
                                  |
             scheduler (06:00) -> agent sweep (Gemini + tools)
                                  |               |
                         dashboard API      Photon texts --> managers
                                  ^               |
                                  +-- webhook <---+  (replies: APPROVE, HOLD, "N", JOIN)
```

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env            # fill in DATABASE_URL, GEMINI_API_KEY, Photon keys, ADMIN_TOKEN
export $(grep -v '^#' .env | xargs)

python -m sitesignal.jobs schema     # create tables (safe to rerun)
python -m sitesignal.jobs seed       # synthetic demo portfolio (40 sites)
python -m sitesignal.jobs weather    # real 7-day forecasts from Open-Meteo
uvicorn api.main:app --reload        # API + Photon webhook + scheduler, docs at :8000/docs
```

No keys yet? `BRAIN=mock` swaps Gemini for a scripted stand-in, and `PHOTON_SEND=0`
(the default) logs texts instead of sending them.

To use a Nemotron-compatible endpoint instead of Gemini, set:

```bash
export BRAIN=nemotron
export NEMOTRON_API_KEY=...
export NEMOTRON_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
export NEMOTRON_BASE_URL=https://integrate.api.nvidia.com/v1
```

The agent will query Postgres through the same tool schema and convert the model response
back into the function-calling format the rest of the app expects. Nemotron reasoning is
enabled for its decisions, while the app consumes tool calls and the concise final summary
instead of exposing the model's reasoning trace. Portfolio sweeps prefetch candidate snapshots
in one batch, use parallel tool calls, and keep a smaller reasoning budget to reduce API
round trips without dropping candidate assets or truncating the source data.

## What runs where

| Piece | File | What it does |
|---|---|---|
| Risk model | `sitesignal/risk_model.py` | Probability of failure in 30 days + the drivers |
| Agent | `sitesignal/agent.py`, `tools.py` | Gemini 3.8 Flash calling 8 tools in a loop, guardrails in code |
| Scheduler | `sitesignal/scheduler.py` | Inside the API: 06:00 Central -> weather refresh + agent sweep; every 30 min -> rescore all assets (no texts) |
| Data import | `sitesignal/importer.py` | Messy CSV/XLSX -> clean rows; loose column matching, fuzzy asset IDs, recharge detection from notes, duplicates skipped, bad rows quarantined |
| Weather job | `sitesignal/weather.py` | Open-Meteo 7-day max temps per site (free, no key) |
| Photon outbound | `sitesignal/photon_notify.py` | Sends texts into registered conversations, deduplicated |
| Photon inbound | `sitesignal/photon_inbound.py`, `inbound.py` | Webhook (signature-verified) or poller -> JOIN / APPROVE / HOLD / field reports -> agent |
| API | `api/main.py` | FastAPI endpoints below |
| Database | `db/schema.sql`, `sitesignal/store.py` | 16 tables, `asset_features()` SQL function, one-open-work-order and append-only-audit rules |

## API

Reads (open): `GET /health`, `/portfolio`, `/assets?tier=high`, `/assets/{id}`, `/work-orders`,
`/dashboard`, `/messages`, `/field-reports`, `/runs`, `/runs/{id}/audit`, `/import/quarantine`

Actions (`Authorization: Bearer $ADMIN_TOKEN`):
`POST /work-orders/{id}/approve`, `/work-orders/{id}/hold`, `/jobs/sweep`, `/jobs/weather`,
`/jobs/score`, `/import/{sites|assets|work_orders}` (file upload), `/admin/reset-demo`,
`/photon/simulate` (fake an incoming text for demos)

Photon: `POST /photon/webhook` (needs `PHOTON_WEBHOOK_SECRET`)

## Importing real data

```bash
python -m sitesignal.jobs import sites samples/sites.csv
python -m sitesignal.jobs import assets samples/assets.csv
python -m sitesignal.jobs import work_orders samples/work_orders.csv
```
or upload through `POST /import/{kind}`. The `samples/` files are deliberately messy;
see what was rejected at `GET /import/quarantine`.

## People and Photon

1. In Photon's dashboard, point a webhook at `https://<your-api>/photon/webhook` and copy its
   `whsec_...` secret into `PHOTON_WEBHOOK_SECRET`. (No public URL yet? Run
   `python -m sitesignal.jobs poll` instead; it uses Photon's list_messages.)
2. Each service manager texts the Photon number `JOIN dallas` (their region); each site manager
   texts `JOIN site S010`. No phone numbers are stored, only Photon conversation ids.
3. Set `PHOTON_SEND=1` when you want real texts.

## Deploy (Tiger Data + Render)

1. Create a Tiger Data service; copy its connection string.
2. Push this repo to GitHub; in Render choose New -> Blueprint and pick the repo (`render.yaml`).
3. Fill in `DATABASE_URL`, `GEMINI_API_KEY`, `PHOTON_*`. Create the tables once from your laptop:
   `DATABASE_URL=... python -m sitesignal.jobs schema`.
4. Seed once: `curl -X POST https://<api>/admin/reset-demo -H "Authorization: Bearer $ADMIN_TOKEN"`.
5. Set the Photon webhook URL to the Render URL.

The free plan sleeps after about 15 minutes with no traffic and takes up to a minute to
wake. Before judging, open `https://<api>/health` to wake it, and trigger the sweep with
`POST /jobs/sweep` instead of waiting for 06:00.

## Tests

`python -m tests.smoke_test` (with `DATABASE_URL`, `BRAIN=mock`) checks import, weather,
scoring, scheduler, API read queries, webhook signatures, dedupe and the APPROVE flow.

## Verify before the demo

- **Gemini request fields** in `agent.py` (native generateContent with functionDeclarations,
  reply appended verbatim to keep thought signatures).
- **Photon SDK argument names** in `photon_notify._send_text` and `photon_inbound._list_received`.
- **Photon webhook event JSON**: the docs don't print it. `extract_message` reads common field
  names; log one real event and adjust if needed.
- All risk weights and thresholds are demo assumptions.
