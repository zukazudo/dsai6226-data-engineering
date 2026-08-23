-- ---------------------------------------------------------------------------
-- 02_schema.sql
--
-- Star schema: one fact table of people, eight conformed dimensions.
-- Rationale is in the README under "Why this shape".
--
-- All DDL is CREATE IF NOT EXISTS and all seeds are insert-if-absent, because
-- the ingester runs this file on every invocation.
--
-- Two conventions carry the Lab 1 findings into the model:
--
--   1. Every dimension reserves surrogate key -1 for 'Unknown'. Missing values
--      become a dimension member, never a NULL foreign key, so a group-by
--      always accounts for every record. dim_occupation additionally reserves
--      -2 for 'Not applicable', which is where the seven Never-worked records
--      go. Those two states are genuinely different and the source file
--      collapsed both into the string '?'.
--
--   2. Top-coded measures are stored NULL with a companion flag. An average
--      then excludes the censored records instead of being poisoned by them,
--      and the flag preserves the fact that censoring occurred.
-- ---------------------------------------------------------------------------

-- --------------------------------------------------------------------- dims

CREATE TABLE IF NOT EXISTS dim_workclass (
    workclass_sk    INTEGER     PRIMARY KEY,
    workclass       VARCHAR     NOT NULL UNIQUE,
    is_unknown      BOOLEAN     NOT NULL DEFAULT FALSE
);

-- education and education.num are a strict 1:1 mapping across all 16 levels,
-- so they are one dimension with two attributes and not two dimensions.
-- This dimension is seeded rather than derived, which makes it the reference
-- the ingester validates incoming education pairs against: a file claiming
-- HS-grad at level 12 is rejected instead of quietly widening the dimension.
-- education_sk is education_num, since that ordering is meaningful.
-- education_group is the coarsening Lab 1 called for: 93 of 201 raw
-- occupation-by-education segments held fewer than 30 records.
CREATE TABLE IF NOT EXISTS dim_education (
    education_sk    INTEGER     PRIMARY KEY,
    education       VARCHAR     NOT NULL UNIQUE,
    education_num   SMALLINT    NOT NULL UNIQUE,
    education_group VARCHAR     NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_marital_status (
    marital_status_sk   INTEGER     PRIMARY KEY,
    marital_status      VARCHAR     NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS dim_occupation (
    occupation_sk       INTEGER     PRIMARY KEY,
    occupation          VARCHAR     NOT NULL UNIQUE,
    is_unknown          BOOLEAN     NOT NULL DEFAULT FALSE,
    is_not_applicable   BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS dim_relationship (
    relationship_sk     INTEGER     PRIMARY KEY,
    relationship        VARCHAR     NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS dim_race (
    race_sk         INTEGER     PRIMARY KEY,
    race            VARCHAR     NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS dim_sex (
    sex_sk          INTEGER     PRIMARY KEY,
    sex             VARCHAR     NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS dim_native_country (
    native_country_sk   INTEGER     PRIMARY KEY,
    native_country      VARCHAR     NOT NULL UNIQUE,
    is_unknown          BOOLEAN     NOT NULL DEFAULT FALSE
);

-- --------------------------------------------------------------------- fact

CREATE TABLE IF NOT EXISTS fact_person (
    -- The surrogate key Lab 1 found missing. This is the only reason a row in
    -- this warehouse can be referred to, updated or joined to at all.
    person_sk                   BIGINT      PRIMARY KEY,

    -- The content-derived identity that makes the load idempotent. Re-reading
    -- the same source reproduces the same key, so the insert in 03_load.sql
    -- finds the row already present and does nothing.
    record_key                  VARCHAR     NOT NULL UNIQUE,

    -- Lineage. Which run loaded this row, from which file, and which line of
    -- it. Any modelled row can be traced back to the exact source that
    -- produced it, and forward to the load_run that admitted it.
    load_run_id                 INTEGER     NOT NULL,
    source_file                 VARCHAR     NOT NULL,
    source_row                  INTEGER     NOT NULL,

    workclass_sk                INTEGER     NOT NULL REFERENCES dim_workclass(workclass_sk),
    education_sk                INTEGER     NOT NULL REFERENCES dim_education(education_sk),
    marital_status_sk           INTEGER     NOT NULL REFERENCES dim_marital_status(marital_status_sk),
    occupation_sk               INTEGER     NOT NULL REFERENCES dim_occupation(occupation_sk),
    relationship_sk             INTEGER     NOT NULL REFERENCES dim_relationship(relationship_sk),
    race_sk                     INTEGER     NOT NULL REFERENCES dim_race(race_sk),
    sex_sk                      INTEGER     NOT NULL REFERENCES dim_sex(sex_sk),
    native_country_sk           INTEGER     NOT NULL REFERENCES dim_native_country(native_country_sk),

    -- Measures. NULL where the source value was a censoring sentinel.
    age                         SMALLINT,
    age_is_topcoded             BOOLEAN     NOT NULL,
    hours_per_week              SMALLINT,
    hours_is_topcoded           BOOLEAN     NOT NULL,
    capital_gain                INTEGER,
    capital_gain_is_topcoded    BOOLEAN     NOT NULL,
    capital_loss                INTEGER     NOT NULL,

    -- Survey weight. Kept for weighted descriptives only. Lab 1 Appendix C
    -- sets out why it must not enter a feature set.
    fnlwgt                      INTEGER     NOT NULL,

    -- Target.
    income_gt_50k               BOOLEAN     NOT NULL,

    -- Duplicate adjudication, retained rather than resolved. Rows that match
    -- another row on all 15 source columns share a duplicate_group_id and are
    -- numbered by duplicate_seq in source order. Unique rows have NULL in both.
    --   every row of a duplicate group : duplicate_group_id IS NOT NULL
    --   the rows pandas duplicated()   : duplicate_seq > 1
    --   a deduplicated view            : duplicate_seq IS NULL OR duplicate_seq = 1
    duplicate_group_id          INTEGER,
    duplicate_seq               SMALLINT
);

-- -------------------------------------------------------------------- seeds

-- The canonical education ladder. Seeded, not inferred, so it can serve as the
-- reference that incoming education pairs are validated against.
INSERT INTO dim_education (education_sk, education, education_num, education_group)
SELECT * FROM (VALUES
    ( 1, 'Preschool',     1, 'No high school diploma'),
    ( 2, '1st-4th',       2, 'No high school diploma'),
    ( 3, '5th-6th',       3, 'No high school diploma'),
    ( 4, '7th-8th',       4, 'No high school diploma'),
    ( 5, '9th',           5, 'No high school diploma'),
    ( 6, '10th',          6, 'No high school diploma'),
    ( 7, '11th',          7, 'No high school diploma'),
    ( 8, '12th',          8, 'No high school diploma'),
    ( 9, 'HS-grad',       9, 'High school graduate'),
    (10, 'Some-college', 10, 'Some college'),
    (11, 'Assoc-voc',    11, 'Associate degree'),
    (12, 'Assoc-acdm',   12, 'Associate degree'),
    (13, 'Bachelors',    13, 'Bachelors degree'),
    (14, 'Masters',      14, 'Postgraduate'),
    (15, 'Prof-school',  15, 'Postgraduate'),
    (16, 'Doctorate',    16, 'Postgraduate')
) AS v(sk, ed, num, grp)
WHERE NOT EXISTS (SELECT 1 FROM dim_education d WHERE d.education_sk = v.sk);

-- The Unknown and Not applicable members every dimension needs before any fact
-- row can point at them.
INSERT INTO dim_workclass (workclass_sk, workclass, is_unknown)
SELECT -1, 'Unknown', TRUE
WHERE NOT EXISTS (SELECT 1 FROM dim_workclass WHERE workclass_sk = -1);

INSERT INTO dim_native_country (native_country_sk, native_country, is_unknown)
SELECT -1, 'Unknown', TRUE
WHERE NOT EXISTS (SELECT 1 FROM dim_native_country WHERE native_country_sk = -1);

INSERT INTO dim_occupation (occupation_sk, occupation, is_unknown, is_not_applicable)
SELECT -1, 'Unknown', TRUE, FALSE
WHERE NOT EXISTS (SELECT 1 FROM dim_occupation WHERE occupation_sk = -1);

INSERT INTO dim_occupation (occupation_sk, occupation, is_unknown, is_not_applicable)
SELECT -2, 'Not applicable', FALSE, TRUE
WHERE NOT EXISTS (SELECT 1 FROM dim_occupation WHERE occupation_sk = -2);
