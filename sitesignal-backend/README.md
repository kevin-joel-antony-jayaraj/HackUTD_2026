# PenguinRE

Prevents equipment failures at small commercial sites with no building sensors.
A risk model (Weibull age curve x work-order history x weather forecast) scores every
machine; **Gemini 3.8 Flash** acts as an agent that decides what to do; **Photon 2.0**
texts each region's manager and calls its service worker; everything lives in **Postgres**.

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
                                # (.env is loaded automatically; no export needed)

python -m sitesignal.jobs schema     # create tables (safe to rerun)
python -m sitesignal.jobs seed       # demo portfolio: 12 fictional sites, 51 machines, 4 troubled
python -m sitesignal.jobs weather    # real 7-day forecasts from Open-Meteo
uvicorn api.main:app --reload        # API + Photon webhook + scheduler, docs at :8000/docs
```

No keys yet? `BRAIN=mock` swaps Gemini for a scripted stand-in, and `PHOTON_SEND=0`
(the default) logs texts instead of sending them.

## What runs where

| Piece | File | What it does |
|---|---|---|
| Risk model | `sitesignal/risk_model.py` | Probability of failure in 30 days + the drivers |
| Cost comparison | `sitesignal/costs.py` | Preventive visit vs. emergency failure (repair + lost revenue + spoiled stock) per machine, expected savings, and the portfolio savings counter (`/savings`). Visit and repair costs are the medians of the portfolio's own invoices once 5+ exist per machine type (upload via `/import/work_orders`), otherwise cited national figures (DOE/FEMP, trade and cost guides; quotes and URLs in `costs.SOURCES`). Downtime, revenue lost and spoiled stock are stated assumptions |
| Agent | `sitesignal/agent.py`, `tools.py` | Gemini 3.8 Flash calling 8 tools in a loop, guardrails in code |
| Scheduler | `sitesignal/scheduler.py` | Inside the API: 06:00 Central -> weather refresh + agent sweep; every 30 min -> rescore all assets (no texts) |
| Data import | `sitesignal/importer.py` | Messy CSV/XLSX -> clean rows; loose column matching, fuzzy asset IDs, recharge detection from notes, duplicates skipped, bad rows quarantined |
| Weather job | `sitesignal/weather.py` | Open-Meteo 7-day max temps per site (free, no key) |
| Photon outbound | `sitesignal/photon_notify.py` | Sends texts into registered conversations, deduplicated |
| Photon calls | `sitesignal/photon_call.py` | After each sweep and after any report that changes risk, calls the region's manager about each high-risk asset (riskiest first, up to `MAX_CALLS_PER_RUN`=3, once per asset per day; Twilio dials over Photon's SIP voice number; `PHOTON_CALL=0` logs instead) |
| Photon inbound | `sitesignal/photon_inbound.py`, `inbound.py` | Webhook (signature-verified) or poller -> JOIN / APPROVE / HOLD / field reports -> agent |
| API | `api/main.py` | FastAPI endpoints below |
| Database | `db/schema.sql`, `sitesignal/store.py` | 16 tables, `asset_features()` SQL function, one-open-work-order and append-only-audit rules |

## API

Reads (open): `GET /health`, `/portfolio`, `/savings`, `/assets?tier=high`, `/assets/{id}`, `/work-orders`,
`/dashboard`, `/messages`, `/calls`, `/field-reports`, `/runs`, `/runs/{id}/audit`, `/import/quarantine`

Actions (`Authorization: Bearer $ADMIN_TOKEN`):
`POST /work-orders/{id}/approve`, `/work-orders/{id}/hold`, `/jobs/sweep`, `/jobs/weather`,
`/jobs/score`, `/jobs/call-urgent`, `/contacts/worker/{region}/phone`, `/import/{sites|assets|work_orders}` (file upload), `/admin/reset-demo`,
`/photon/simulate` (fake an incoming text for demos)

Photon: `POST /photon/webhook` (needs `PHOTON_WEBHOOK_SECRET`)

Example responses for every endpoint, as a JSON list: `frontend/src/mock/api-samples.json`.

## Demo portfolio (`seed.py`)

12 fictional sites (bank branches, clinics, groceries, retail stores) in real Dallas, Houston and
Austin neighborhoods. Square footage and weekly hours are the U.S. EIA CBECS 2018 averages for each
building type (Table B15); equipment is sized from square footage (assumption: ~1 ton of cooling per
350 sq ft, 10-ton rooftop units). Every machine has two years of maintenance history and 30 days of
back-scored risk. Four are deliberately troubled:

| Machine | Story | Risk |
|---|---|---|
| `S005-WALK_IN_COOLER1` Montrose Market | Leaking cooler: two refrigerant recharges, keeps losing temperature | High |
| `S001-RTU2` Uptown Bank Branch | 21-year-old R-22 unit, short cycling, maintenance 7 months overdue | High |
| `S006-WATER_HEATER1` Katy Family Clinic | 12-year-old heater past its expected life, repaired last month | Medium |
| `S004-RTU1` Frisco Retail Store | 15-year-old unit with a slow refrigerant leak | Medium |

## Importing real data

```bash
python -m sitesignal.jobs import sites samples/sites.csv
python -m sitesignal.jobs import assets samples/assets.csv
python -m sitesignal.jobs import work_orders samples/work_orders.csv
```
or upload through `POST /import/{kind}`. The `samples/` files are deliberately messy;
see what was rejected at `GET /import/quarantine`.

## People and Photon

**Quickest demo setup:** put two numbers in `.env` and they're used for every region, no JOIN texts:
`MANAGER_PHONE` (work-order texts, approvals, problem reports) and `SERVICE_WORKER_PHONE` (urgent
calls). The first real text opens a Photon conversation with the manager's number, which needs
`PHOTON_AGENT_USER_ID` (your agent's User id in Photon); the request shapes come from Photon's
2.0 launch blog, so verify them. If it fails, text `JOIN all` from the manager's phone to the
Photon number once. Without these variables, register people per region as below.

1. In Photon's dashboard, point a webhook at `https://<your-api>/photon/webhook` and copy its
   `whsec_...` secret into `PHOTON_WEBHOOK_SECRET`. (No public URL yet? Run
   `python -m sitesignal.jobs poll` instead; it uses Photon's list_messages.)
2. One manager per region texts the Photon number `JOIN dallas` (or houston, austin). No phone
   numbers are needed for texts, only Photon conversation ids. That manager:
   - gets a text for every work order and replies `APPROVE WO-0003` or `HOLD WO-0003`;
   - can text a problem by naming the site or area, e.g. `Katy clinic has no hot water again` or
     `Montrose cooler isn't cooling`. PenguinRE finds the machine, saves its new risk right away
     (the dashboard updates), and the agent creates or escalates a work order. A message that
     could mean two sites (just "Houston") is ignored rather than guessed.
   (`JOIN site S010` still registers a site-level contact who can report on that site only.)
3. One service worker (technician) per region texts `JOIN tech dallas`. They get a phone call for
   every high-risk machine in their region, including one a manager's report just pushed to high,
   so the person reporting a problem and the person sent to fix it are different people.
4. Set `PHOTON_SEND=1` when you want real texts.
5. Calls: after a service worker JOINs, set their number with `POST /contacts/worker/dallas/phone`
   `{"phone": "+1..."}` (use a teammate's phone for demos). Fill in the `TWILIO_*` and `PHOTON_SIP_*`
   values from Photon's Voice profile, then set `PHOTON_CALL=1`. A dry-run sweep never dials.

## Deploy (Postgres + Render)

1. Create a hosted Postgres 14+ database (e.g. Render Postgres); copy its connection string.
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
