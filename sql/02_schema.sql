-- ---------------------------------------------------------------------------
-- 02_schema.sql
--
-- Star schema: one fact table of people, eight conformed dimensions.
-- Rationale is in the README under "Why this shape".
--
-- Two conventions carry the Lab 1 findings into the model:
--
--   1. Every dimension reserves surrogate key -1 for 'Unknown'. Missing values
--      become a dimension member, never a NULL foreign key, so a group-by
--      always accounts for all 32,561 records. dim_occupation additionally
--      reserves -2 for 'Not applicable', which is where the seven Never-worked
--      records go. Those two states are genuinely different and the source
--      file collapsed both into the string '?'.
--
--   2. Top-coded measures are stored NULL with a companion flag. An average
--      then excludes the censored records instead of being poisoned by them,
--      and the flag preserves the fact that censoring occurred.
-- ---------------------------------------------------------------------------

DROP TABLE IF EXISTS fact_person;
DROP TABLE IF EXISTS dim_workclass;
DROP TABLE IF EXISTS dim_education;
DROP TABLE IF EXISTS dim_marital_status;
DROP TABLE IF EXISTS dim_occupation;
DROP TABLE IF EXISTS dim_relationship;
DROP TABLE IF EXISTS dim_race;
DROP TABLE IF EXISTS dim_sex;
DROP TABLE IF EXISTS dim_native_country;

-- --------------------------------------------------------------------- dims

CREATE TABLE dim_workclass (
    workclass_sk    INTEGER     PRIMARY KEY,
    workclass       VARCHAR     NOT NULL UNIQUE,
    is_unknown      BOOLEAN     NOT NULL DEFAULT FALSE
);

-- education and education.num are a strict 1:1 mapping across all 16 levels,
-- so they are one dimension with two attributes and not two dimensions.
-- education_group is the coarsening Lab 1 called for: 97 of the raw
-- occupation-by-education segments held fewer than 30 records.
CREATE TABLE dim_education (
    education_sk    INTEGER     PRIMARY KEY,
    education       VARCHAR     NOT NULL UNIQUE,
    education_num   SMALLINT    NOT NULL UNIQUE,
    education_group VARCHAR     NOT NULL
);

CREATE TABLE dim_marital_status (
    marital_status_sk   INTEGER     PRIMARY KEY,
    marital_status      VARCHAR     NOT NULL UNIQUE
);

CREATE TABLE dim_occupation (
    occupation_sk       INTEGER     PRIMARY KEY,
    occupation          VARCHAR     NOT NULL UNIQUE,
    is_unknown          BOOLEAN     NOT NULL DEFAULT FALSE,
    is_not_applicable   BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE TABLE dim_relationship (
    relationship_sk     INTEGER     PRIMARY KEY,
    relationship        VARCHAR     NOT NULL UNIQUE
);

CREATE TABLE dim_race (
    race_sk         INTEGER     PRIMARY KEY,
    race            VARCHAR     NOT NULL UNIQUE
);

CREATE TABLE dim_sex (
    sex_sk          INTEGER     PRIMARY KEY,
    sex             VARCHAR     NOT NULL UNIQUE
);

CREATE TABLE dim_native_country (
    native_country_sk   INTEGER     PRIMARY KEY,
    native_country      VARCHAR     NOT NULL UNIQUE,
    is_unknown          BOOLEAN     NOT NULL DEFAULT FALSE
);

-- --------------------------------------------------------------------- fact

CREATE TABLE fact_person (
    -- The surrogate key Lab 1 found missing. This is the only reason a row in
    -- this warehouse can be referred to, updated or joined to at all.
    person_sk                   BIGINT      PRIMARY KEY,

    -- Lineage back to stg_adult, so any modelled row can be traced to the
    -- exact source line it came from.
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
