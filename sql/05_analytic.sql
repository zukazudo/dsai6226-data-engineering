-- ---------------------------------------------------------------------------
-- 05_analytic.sql
--
-- The denormalised table the Lab 4 benchmark runs against.
--
-- The star is the right shape for correctness and for holding the Lab 1
-- findings. It is not the right shape for a benchmark: comparing a joined
-- star in DuckDB against a flat CSV in pandas would measure the join, not the
-- storage format. So the star is flattened once, here, and the identical rows
-- are then written to CSV, to Parquet and to PostgreSQL. All three engines see
-- exactly the same data and must return exactly the same answer.
--
-- Dimension labels are used in place of surrogate keys, since a flat file has
-- no way to resolve a key.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW analytic_person AS
SELECT
    f.person_sk,
    f.age,
    f.age_is_topcoded,
    w.workclass,
    e.education,
    e.education_num,
    e.education_group,
    m.marital_status,
    o.occupation,
    o.is_unknown        AS occupation_unknown,
    o.is_not_applicable AS occupation_not_applicable,
    r.relationship,
    ra.race,
    sx.sex,
    f.capital_gain,
    f.capital_gain_is_topcoded,
    f.capital_loss,
    f.hours_per_week,
    f.hours_is_topcoded,
    nc.native_country,
    f.fnlwgt,
    f.income_gt_50k,
    f.duplicate_group_id,
    f.duplicate_seq
FROM fact_person f
JOIN dim_workclass      w  USING (workclass_sk)
JOIN dim_education      e  USING (education_sk)
JOIN dim_marital_status m  USING (marital_status_sk)
JOIN dim_occupation     o  USING (occupation_sk)
JOIN dim_relationship   r  USING (relationship_sk)
JOIN dim_race           ra USING (race_sk)
JOIN dim_sex            sx USING (sex_sk)
JOIN dim_native_country nc USING (native_country_sk);
