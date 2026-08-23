-- ---------------------------------------------------------------------------
-- 01_staging.sql
--
-- Lands adult.csv exactly as it arrives. Every column is VARCHAR, so nothing
-- is coerced, rejected or silently altered at the boundary. All cleaning
-- happens on the way out of this table, in 03_load.sql, which means the raw
-- value behind every modelled row stays queryable.
--
-- The only change made here is to column names: the source uses dots
-- (education.num, capital.gain), which have to be double-quoted in every
-- statement that touches them. Renaming to snake_case removes a whole class
-- of quoting bugs. No value is modified.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE TABLE stg_adult AS
SELECT
    CAST(row_number() OVER () AS INTEGER) AS source_row,
    age,
    workclass,
    fnlwgt,
    education,
    "education.num"   AS education_num,
    "marital.status"  AS marital_status,
    occupation,
    relationship,
    race,
    sex,
    "capital.gain"    AS capital_gain,
    "capital.loss"    AS capital_loss,
    "hours.per.week"  AS hours_per_week,
    "native.country"  AS native_country,
    income
FROM read_csv('data/adult.csv', all_varchar = true, header = true);

-- source_row is position in the file as loaded, and is the lineage handle back
-- from any modelled row. DuckDB preserves insertion order by default, so this
-- matches the line order of the CSV.
