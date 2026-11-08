-- SiteSignal data + Safety Index schema. Plain Postgres 14+. Safe to re-run.
--   psql "$DATABASE_URL" -f db/safety_schema.sql     (or let sitesignal.load_pg apply it)

BEGIN;

CREATE TABLE IF NOT EXISTS sites (
    site_id              text PRIMARY KEY,
    name                 text NOT NULL,
    type                 text NOT NULL,
    city                 text,
    region_id            text NOT NULL,
    has_refrigeration    boolean NOT NULL DEFAULT false,
    revenue_per_day_usd  numeric(12,2) CHECK (revenue_per_day_usd IS NULL OR revenue_per_day_usd >= 0),
    lat                  double precision CHECK (lat BETWEEN -90 AND 90),
    lon                  double precision CHECK (lon BETWEEN -180 AND 180),
    flags                jsonb NOT NULL DEFAULT '[]',     -- what cleaning changed, with original values
    updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS assets (
    asset_id                text PRIMARY KEY,
    site_id                 text NOT NULL REFERENCES sites ON DELETE CASCADE,
    asset_type              text NOT NULL CHECK (asset_type IN ('rtu','walk_in_cooler','water_heater')),
    install_date            date,                         -- NULL = unknown -> Safety Index band 'unknown'
    install_date_estimated  boolean NOT NULL DEFAULT false,
    make                    text,
    model                   text,
    serial_number           text,
    refrigerant             text,
    pm_interval_days        integer NOT NULL DEFAULT 90,
    flags                   jsonb NOT NULL DEFAULT '[]',
    updated_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS assets_site_idx ON assets (site_id);

-- Negative cost_usd is allowed on purpose: it can be a credit/refund (flagged in `flags`).
CREATE TABLE IF NOT EXISTS work_order_history (
    id                 bigserial PRIMARY KEY,
    asset_id           text NOT NULL REFERENCES assets ON DELETE CASCADE,
    opened_at          date NOT NULL,
    kind               text NOT NULL CHECK (kind IN ('repair','emergency','pm','inspection')),
    issue_category     text,
    refrigerant_added  boolean NOT NULL DEFAULT false,
    cost_usd           numeric(10,2),
    note               text,
    source             text,
    flags              jsonb NOT NULL DEFAULT '[]',
    loaded_at          timestamptz NOT NULL DEFAULT now()
);
-- Same work order loaded twice is a no-op.
CREATE UNIQUE INDEX IF NOT EXISTS woh_dedupe ON work_order_history (asset_id, opened_at, kind, (coalesce(note, '')));
CREATE INDEX IF NOT EXISTS woh_asset_date_idx ON work_order_history (asset_id, opened_at DESC);

CREATE TABLE IF NOT EXISTS weather_forecasts (
    site_id        text NOT NULL REFERENCES sites ON DELETE CASCADE,
    forecast_date  date NOT NULL,
    max_temp_f     numeric(5,1) NOT NULL,
    source         text NOT NULL DEFAULT 'nws',
    fetched_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (site_id, forecast_date)
);

-- Rows the extractor refused, with the reason. Nothing is silently dropped.
CREATE TABLE IF NOT EXISTS rejected_rows (
    id         bigserial PRIMARY KEY,
    source     text,
    raw        jsonb NOT NULL,
    reason     text NOT NULL,
    loaded_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS rejected_dedupe ON rejected_rows ((coalesce(source, '')), md5(raw::text), reason);

-- ---------------------------------------------------------------- Safety Index
CREATE TABLE IF NOT EXISTS safety_runs (
    run_id         bigserial PRIMARY KEY,
    as_of          date NOT NULL,
    model_version  text NOT NULL,
    calibrated     boolean NOT NULL DEFAULT false,
    meta           jsonb NOT NULL,                        -- weights, assumptions, llm_guidance
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS asset_safety_scores (
    run_id            bigint NOT NULL REFERENCES safety_runs ON DELETE CASCADE,
    asset_id          text NOT NULL REFERENCES assets ON DELETE CASCADE,
    safety_index      smallint CHECK (safety_index BETWEEN 0 AND 100),   -- NULL = could not be scored
    band              text NOT NULL CHECK (band IN ('good','fair','poor','critical','unknown')),
    confidence        numeric(3,2) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    drivers           jsonb NOT NULL DEFAULT '[]',
    data_gaps         jsonb NOT NULL DEFAULT '[]',
    constraints       jsonb NOT NULL DEFAULT '[]',
    business_exposure jsonb,
    inputs            jsonb NOT NULL DEFAULT '{}',
    repair_spend_usd  numeric(12,2),
    credits_present   boolean NOT NULL DEFAULT false,
    flags             jsonb NOT NULL DEFAULT '[]',
    PRIMARY KEY (run_id, asset_id),
    CHECK ((safety_index IS NULL) = (band = 'unknown'))   -- 'unknown' and a number never coexist
);

CREATE TABLE IF NOT EXISTS site_safety_scores (
    run_id             bigint NOT NULL REFERENCES safety_runs ON DELETE CASCADE,
    site_id            text NOT NULL REFERENCES sites ON DELETE CASCADE,
    safety_index       smallint CHECK (safety_index BETWEEN 0 AND 100),
    band               text NOT NULL CHECK (band IN ('good','fair','poor','critical','unknown')),
    confidence         numeric(3,2) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    worst_asset_id     text REFERENCES assets,
    assets_without_score jsonb NOT NULL DEFAULT '[]',
    constraints        jsonb NOT NULL DEFAULT '[]',
    business_exposure  jsonb,
    PRIMARY KEY (run_id, site_id)
);

-- ---------------------------------------------------------------- views for the decision-making LLM
CREATE OR REPLACE VIEW latest_safety_run AS
    SELECT * FROM safety_runs ORDER BY created_at DESC, run_id DESC LIMIT 1;

-- One JSON document per site: everything the LLM should read, nothing it should not (no raw notes).
CREATE OR REPLACE VIEW llm_site_context AS
SELECT s.site_id,
       jsonb_build_object(
         'guidance',   r.meta -> 'llm_guidance',
         'model',      jsonb_build_object('version', r.model_version, 'as_of', r.as_of,
                                          'calibrated', r.calibrated, 'scale', '0-100, 100 = safest'),
         'site',       jsonb_build_object('site_id', s.site_id, 'name', st.name, 'type', st.type,
                                          'safety_index', ss.safety_index, 'band', ss.band,
                                          'confidence', ss.confidence, 'worst_asset_id', ss.worst_asset_id,
                                          'constraints', ss.constraints, 'business_exposure', ss.business_exposure,
                                          'assets_without_score', ss.assets_without_score),
         'assets',     (SELECT coalesce(jsonb_agg(jsonb_build_object(
                          'asset_id', a.asset_id, 'type', a.asset_type, 'safety_index', x.safety_index,
                          'band', x.band, 'confidence', x.confidence, 'drivers', x.drivers,
                          'data_gaps', x.data_gaps, 'constraints', x.constraints,
                          'repair_spend_usd', x.repair_spend_usd, 'credits_present', x.credits_present)
                          ORDER BY x.safety_index NULLS FIRST), '[]')
                        FROM asset_safety_scores x JOIN assets a USING (asset_id)
                        WHERE x.run_id = r.run_id AND a.site_id = s.site_id)
       ) AS context
FROM latest_safety_run r
JOIN site_safety_scores ss ON ss.run_id = r.run_id
JOIN sites st ON st.site_id = ss.site_id
JOIN (SELECT site_id FROM sites) s ON s.site_id = st.site_id;

-- Ranked worklist: worst first, 'unknown' on top because it needs data, not trust.
CREATE OR REPLACE VIEW asset_worklist AS
SELECT x.asset_id, a.site_id, a.asset_type, x.safety_index, x.band, x.confidence, x.constraints,
       x.drivers -> 0 ->> 'why' AS top_driver
FROM latest_safety_run r
JOIN asset_safety_scores x ON x.run_id = r.run_id
JOIN assets a USING (asset_id)
ORDER BY x.safety_index NULLS FIRST, x.confidence;

-- ---------------------------------------------------------------- decisions (LLM proposes, code enforces)
CREATE TABLE IF NOT EXISTS decisions (
    id                    bigserial PRIMARY KEY,
    run_id                bigint NOT NULL REFERENCES safety_runs ON DELETE CASCADE,
    site_id               text NOT NULL REFERENCES sites ON DELETE CASCADE,
    asset_id              text NOT NULL REFERENCES assets ON DELETE CASCADE,
    action                text NOT NULL CHECK (action IN ('create_work_order','request_data','monitor','no_action')),
    urgency               text NOT NULL CHECK (urgency IN ('today','48h','this_week','none')),
    tasks                 jsonb NOT NULL DEFAULT '[]',
    reason                text NOT NULL,
    cites                 jsonb NOT NULL DEFAULT '[]',
    requires_human_review boolean NOT NULL,
    status                text NOT NULL DEFAULT 'pending_review'
                          CHECK (status IN ('pending_review','approved','rejected','done')),
    source                text NOT NULL CHECK (source IN ('llm','rule_default')),   -- rule_default = code overrode the LLM
    violations            jsonb NOT NULL DEFAULT '[]',                              -- why the LLM answer was not used
    model                 text,
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, asset_id)
);

-- Run-to-run consistency: did independent model runs agree, and what did the others say?
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS agreement    boolean NOT NULL DEFAULT true;
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS alternatives jsonb   NOT NULL DEFAULT '[]';

CREATE TABLE IF NOT EXISTS decision_summaries (
    run_id     bigint NOT NULL REFERENCES safety_runs ON DELETE CASCADE,
    site_id    text NOT NULL REFERENCES sites ON DELETE CASCADE,
    summary    text NOT NULL,
    questions  jsonb NOT NULL DEFAULT '[]',
    model      text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, site_id)
);

DROP VIEW IF EXISTS latest_decisions;   -- recreated so it picks up columns added above
CREATE VIEW latest_decisions AS
SELECT d.*, x.safety_index, x.band, x.confidence
FROM latest_safety_run r
JOIN decisions d ON d.run_id = r.run_id
JOIN asset_safety_scores x ON x.run_id = d.run_id AND x.asset_id = d.asset_id
ORDER BY d.requires_human_review DESC, x.safety_index NULLS FIRST;

COMMIT;
