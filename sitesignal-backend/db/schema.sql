-- PenguinRE schema. Works on plain Postgres 14+; on Tiger Data (TimescaleDB) the
-- time-series tables become hypertables automatically (see the DO block at the end).
-- Run:  psql "$DATABASE_URL" -f db/schema.sql

BEGIN;

-- ---------------------------------------------------------------- reference data
CREATE TABLE IF NOT EXISTS regions (
    region_id   text PRIMARY KEY,                 -- 'dallas'
    name        text NOT NULL
);

CREATE TABLE IF NOT EXISTS sites (
    site_id              text PRIMARY KEY,        -- 'S001'
    name                 text NOT NULL,
    type                 text NOT NULL,           -- retail, grocery, bank branch, clinic
    city                 text,
    region_id            text NOT NULL REFERENCES regions,
    has_refrigeration    boolean NOT NULL DEFAULT false,
    revenue_per_day_usd  numeric(12,2),
    open_hours           text,
    lat                  double precision,
    lon                  double precision
);

CREATE TABLE IF NOT EXISTS assets (
    asset_id                text PRIMARY KEY,     -- 'S001-RTU1'
    site_id                 text NOT NULL REFERENCES sites ON DELETE CASCADE,
    asset_type              text NOT NULL CHECK (asset_type IN ('rtu','walk_in_cooler','water_heater')),
    install_date            date,                 -- NULL = unknown -> "insufficient data"
    install_date_estimated  boolean NOT NULL DEFAULT false,  -- e.g. decoded from serial number
    make                    text,
    model                   text,
    serial_number           text,
    refrigerant             text,                 -- 'R-410A', 'R-22'
    pm_interval_days        integer NOT NULL DEFAULT 90
);
CREATE INDEX IF NOT EXISTS assets_site_idx ON assets (site_id);

-- ---------------------------------------------------------------- the 3 model inputs
-- 1. Work-order history (raw; features are derived in asset_features())
CREATE TABLE IF NOT EXISTS work_order_history (
    id                 bigserial PRIMARY KEY,
    asset_id           text NOT NULL REFERENCES assets ON DELETE CASCADE,
    opened_at          date NOT NULL,
    kind               text NOT NULL CHECK (kind IN ('repair','emergency','pm','inspection')),
    issue_category     text,                     -- 'not_cooling', 'short_cycling', 'leak', ...
    refrigerant_added  boolean NOT NULL DEFAULT false,
    cost_usd           numeric(10,2),
    note               text,                     -- technician free text (data, never instructions)
    source             text                      -- which system/file it came from
);
CREATE INDEX IF NOT EXISTS woh_asset_date_idx ON work_order_history (asset_id, opened_at DESC);

-- 2. Asset list = the assets table above (age from install_date)

-- 3. Weather forecast (latest forecast per site and day)
CREATE TABLE IF NOT EXISTS weather_forecasts (
    site_id        text NOT NULL REFERENCES sites ON DELETE CASCADE,
    forecast_date  date NOT NULL,
    max_temp_f     numeric(5,1) NOT NULL,
    fetched_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (site_id, forecast_date)
);

-- ---------------------------------------------------------------- people (via Photon)
-- No phone numbers stored: Photon conversation ids only, set when someone texts "JOIN ...".
CREATE TABLE IF NOT EXISTS contacts (
    id         serial PRIMARY KEY,
    kind       text NOT NULL CHECK (kind IN ('region','site')),
    key        text NOT NULL,                    -- region_id or site_id, lower case
    name       text NOT NULL,
    space_id   text NOT NULL,
    agent_id   text NOT NULL,
    joined_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kind, key)
);
CREATE INDEX IF NOT EXISTS contacts_space_idx ON contacts (space_id);

CREATE TABLE IF NOT EXISTS field_reports (
    id           bigserial PRIMARY KEY,
    site_id      text NOT NULL REFERENCES sites ON DELETE CASCADE,
    asset_id     text REFERENCES assets ON DELETE CASCADE,
    text         text NOT NULL,
    working      boolean,                       -- false = "not working right"
    received_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS field_reports_asset_idx ON field_reports (asset_id, received_at DESC);

-- ---------------------------------------------------------------- agent outputs
CREATE SEQUENCE IF NOT EXISTS work_order_seq;

CREATE TABLE IF NOT EXISTS work_orders (
    id            text PRIMARY KEY DEFAULT 'WO-' || lpad(nextval('work_order_seq')::text, 4, '0'),
    asset_id      text NOT NULL REFERENCES assets ON DELETE CASCADE,
    site_id       text NOT NULL REFERENCES sites ON DELETE CASCADE,
    prob          numeric(6,4) NOT NULL,
    tier          text NOT NULL CHECK (tier IN ('medium','high')),
    urgency       text NOT NULL CHECK (urgency IN ('today','48h','this_week')),
    likely_issue  text NOT NULL,
    tasks         jsonb NOT NULL DEFAULT '[]',
    do_by         date,
    reason        text NOT NULL,
    status        text NOT NULL DEFAULT 'pending_approval'
                  CHECK (status IN ('pending_approval','approved','on_hold','cancelled','done')),
    status_note   text,
    notified      boolean NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
-- The database itself guarantees one open work order per asset.
CREATE UNIQUE INDEX IF NOT EXISTS one_open_wo_per_asset
    ON work_orders (asset_id) WHERE status IN ('pending_approval','approved');

CREATE TABLE IF NOT EXISTS dashboard_entries (
    id          bigserial PRIMARY KEY,
    asset_id    text NOT NULL REFERENCES assets ON DELETE CASCADE,
    status      text NOT NULL CHECK (status IN ('watch','waiting_on_site','work_order_pending','ok','needs_review')),
    note        text,
    prob        numeric(6,4),
    tier        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS risk_scores (            -- one row per asset per sweep, for trend charts
    asset_id     text NOT NULL REFERENCES assets ON DELETE CASCADE,
    scored_at    timestamptz NOT NULL DEFAULT now(),
    prob         numeric(6,4) NOT NULL,
    base_prob    numeric(6,4) NOT NULL,
    multiplier   numeric(14,3) NOT NULL,
    tier         text NOT NULL,
    drivers      jsonb NOT NULL,
    PRIMARY KEY (asset_id, scored_at)
);

CREATE TABLE IF NOT EXISTS messages (               -- every Photon send attempt
    id               bigserial PRIMARY KEY,
    kind             text NOT NULL CHECK (kind IN ('region','site')),
    to_key           text NOT NULL,
    recipient_name   text,
    text             text NOT NULL,
    sent             boolean NOT NULL DEFAULT false,
    dry_run          boolean NOT NULL DEFAULT false,
    error            text,
    idempotency_key  text UNIQUE,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id       bigserial PRIMARY KEY,
    trigger      text NOT NULL,                     -- 'sweep' or the event text
    summary      text,
    steps        integer,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz
);

CREATE TABLE IF NOT EXISTS audit_log (              -- every tool call the agent makes
    id          bigserial PRIMARY KEY,
    run_id      bigint REFERENCES agent_runs ON DELETE SET NULL,
    tool        text NOT NULL,
    args        jsonb NOT NULL,
    result      jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
-- Append-only: no updates or deletes on the audit log.
CREATE OR REPLACE FUNCTION audit_log_no_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$;
DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log;
CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_no_change();


-- ---------------------------------------------------------------- ingestion + inbound bookkeeping
CREATE TABLE IF NOT EXISTS import_quarantine (       -- rows the importer could not trust
    id          bigserial PRIMARY KEY,
    source      text NOT NULL,                       -- file name
    kind        text NOT NULL,                       -- sites | assets | work_orders
    row_number  integer,
    raw         jsonb NOT NULL,
    reason      text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS inbound_messages (        -- Photon messages already handled (dedupe)
    message_id   text PRIMARY KEY,
    space_id     text,
    text         text,
    result       jsonb,
    received_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS job_state (               -- e.g. the poller's cursor
    name   text PRIMARY KEY,
    value  text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- model features
-- Turns raw history into the features risk_model.py expects, as of any date.
CREATE OR REPLACE FUNCTION asset_features(as_of date)
RETURNS TABLE (
    asset_id                     text,
    site_id                      text,
    asset_type                   text,
    install_date                 date,
    calls_90d                    integer,
    refrigerant_recharges_180d   integer,
    repeat_issue                 boolean,
    days_since_last_repair       integer,
    pm_overdue_months            numeric,
    forecast_max_temps_f         numeric[],
    field_report_negative        boolean
) LANGUAGE sql STABLE AS $$
    SELECT
        a.asset_id, a.site_id, a.asset_type, a.install_date,
        (SELECT count(*) FROM work_order_history h
          WHERE h.asset_id = a.asset_id AND h.kind IN ('repair','emergency')
            AND h.opened_at > as_of - 90 AND h.opened_at <= as_of)::int,
        (SELECT count(*) FROM work_order_history h
          WHERE h.asset_id = a.asset_id AND h.refrigerant_added
            AND h.opened_at > as_of - 180 AND h.opened_at <= as_of)::int,
        EXISTS (SELECT 1 FROM work_order_history h
                 WHERE h.asset_id = a.asset_id AND h.kind IN ('repair','emergency')
                   AND h.issue_category IS NOT NULL
                   AND h.opened_at > as_of - 90 AND h.opened_at <= as_of
                 GROUP BY h.issue_category HAVING count(*) >= 2),
        (SELECT as_of - max(h.opened_at) FROM work_order_history h
          WHERE h.asset_id = a.asset_id AND h.kind IN ('repair','emergency') AND h.opened_at <= as_of),
        GREATEST(0, round(((as_of - COALESCE(
            (SELECT max(h.opened_at) FROM work_order_history h
              WHERE h.asset_id = a.asset_id AND h.kind = 'pm' AND h.opened_at <= as_of),
            as_of + a.pm_interval_days)) - a.pm_interval_days) / 30.0, 1)),  -- no PM record = unknown, not overdue
        ARRAY(SELECT w.max_temp_f FROM weather_forecasts w
               WHERE w.site_id = a.site_id AND w.forecast_date >= as_of
               ORDER BY w.forecast_date LIMIT 7),
        COALESCE((SELECT NOT f.working FROM field_reports f
                   WHERE f.asset_id = a.asset_id AND f.working IS NOT NULL
                   ORDER BY f.received_at DESC LIMIT 1), false)
    FROM assets a
$$;

-- Phone calls (photon_call.py). Phone is optional: only needed for managers who get calls.
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS phone text;

CREATE TABLE IF NOT EXISTS calls (                  -- every call attempt, real or dry run
    id               bigserial PRIMARY KEY,
    asset_id         text NOT NULL,
    work_order_id    text,
    region_id        text NOT NULL,
    recipient_name   text,
    script           text NOT NULL,
    called           boolean NOT NULL DEFAULT false,
    dry_run          boolean NOT NULL DEFAULT false,
    call_sid         text,
    error            text,
    idempotency_key  text NOT NULL,                -- not unique: a dry run must not block the real call
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS calls_idem_idx ON calls (idempotency_key);

COMMIT;

-- ---------------------------------------------------------------- Tiger Data / TimescaleDB
-- Hypertables need the time column in every unique key, so only risk_scores and
-- audit-style tables qualify as-is. Skipped silently on plain Postgres.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb') THEN
        CREATE EXTENSION IF NOT EXISTS timescaledb;
        PERFORM create_hypertable('risk_scores', 'scored_at', if_not_exists => true, migrate_data => true);
    END IF;
END $$;
