-- ---------------------------------------------------------------------------
-- 01_staging.sql
--
-- The landing zone and the audit trail. All DDL here is CREATE IF NOT EXISTS,
-- because the ingester runs this on every invocation and must not destroy
-- anything it finds.
--
-- stg_adult holds every column as VARCHAR, so nothing is coerced, rejected or
-- silently altered at the boundary. Cleaning happens on the way out, in
-- 03_load.sql, which keeps the raw value behind every modelled row queryable.
--
-- The only change made to the source is column naming: the file uses dots
-- (education.num, capital.gain), which must be double-quoted in every
-- statement that touches them. Renaming to snake_case removes a class of
-- quoting bugs. No value is modified.
-- ---------------------------------------------------------------------------

CREATE SEQUENCE IF NOT EXISTS seq_load_run START 1;

-- Every invocation of the ingester writes one row here. This is the auditable
-- lineage the Lab 1 statement said the dataset did not have.
CREATE TABLE IF NOT EXISTS load_run (
    load_run_id     INTEGER     PRIMARY KEY,
    started_at      TIMESTAMP   NOT NULL,
    finished_at     TIMESTAMP,
    source_file     VARCHAR     NOT NULL,
    source_sha256   VARCHAR     NOT NULL,
    source_bytes    BIGINT      NOT NULL,
    rows_read       INTEGER,
    rows_staged     INTEGER,
    rows_rejected   INTEGER,
    rows_loaded     INTEGER,
    rows_already_present INTEGER,
    status          VARCHAR     NOT NULL
);

-- record_key is what makes the load re-runnable. The source has no identifier,
-- so identity is manufactured from content: an md5 of all 15 source fields,
-- suffixed with the occurrence number of that exact content within the file.
-- The 24 duplicate rows therefore get distinct keys (#1, #2, #3) instead of
-- colliding, and re-reading the same file reproduces exactly the same keys.
CREATE TABLE IF NOT EXISTS stg_adult (
    record_key      VARCHAR     PRIMARY KEY,
    load_run_id     INTEGER     NOT NULL,
    source_file     VARCHAR     NOT NULL,
    source_row      INTEGER     NOT NULL,

    age             VARCHAR,
    workclass       VARCHAR,
    fnlwgt          VARCHAR,
    education       VARCHAR,
    education_num   VARCHAR,
    marital_status  VARCHAR,
    occupation      VARCHAR,
    relationship    VARCHAR,
    race            VARCHAR,
    sex             VARCHAR,
    capital_gain    VARCHAR,
    capital_loss    VARCHAR,
    hours_per_week  VARCHAR,
    native_country  VARCHAR,
    income          VARCHAR
);

-- Rows that failed validation, with the reason. Queryable rather than only
-- printed, so a rejected row can be investigated after the run.
CREATE TABLE IF NOT EXISTS load_reject (
    load_run_id     INTEGER     NOT NULL,
    record_key      VARCHAR     NOT NULL,
    source_file     VARCHAR     NOT NULL,
    source_row      INTEGER     NOT NULL,
    reason          VARCHAR     NOT NULL,
    detail          VARCHAR,
    PRIMARY KEY (record_key, reason)
);
