-- ---------------------------------------------------------------------------
-- 04_query.sql
--
-- The allocation query. This is the question the whole model exists to answer:
-- for the eligible pool, how many candidates sit in each occupation and
-- education segment, and which segments are too thin to fund?
--
-- Eligible pool, from the Lab 1 problem statement: adults aged 25 to 54 in the
-- low income bracket.
-- ---------------------------------------------------------------------------

SELECT
    o.occupation,
    e.education_group,
    count(*)                                                    AS candidates,
    round(avg(f.hours_per_week), 1)                             AS mean_hours,
    count(*) FILTER (WHERE f.capital_gain_is_topcoded)          AS censored_rows,
    round(avg(f.capital_gain), 2)                               AS mean_capital_gain,
    CASE WHEN count(*) < 30 THEN 'too thin to fund' END         AS note
FROM fact_person f
JOIN dim_occupation o USING (occupation_sk)
JOIN dim_education  e USING (education_sk)
WHERE f.income_gt_50k = FALSE
  AND f.age BETWEEN 25 AND 54
  AND o.is_unknown = FALSE
  AND o.is_not_applicable = FALSE
GROUP BY o.occupation, e.education_group
ORDER BY candidates DESC;


-- ---------------------------------------------------------------------------
-- Supporting counts, each one a Lab 1 finding the schema now answers directly.
-- ---------------------------------------------------------------------------

-- How much of the eligible pool cannot be assigned to a segment at all?
SELECT
    'eligible pool'                                                     AS measure,
    count(*)                                                            AS records
FROM fact_person f
WHERE f.income_gt_50k = FALSE AND f.age BETWEEN 25 AND 54
UNION ALL
SELECT
    'of those, occupation not answered',
    count(*)
FROM fact_person f
JOIN dim_occupation o USING (occupation_sk)
WHERE f.income_gt_50k = FALSE AND f.age BETWEEN 25 AND 54
  AND o.is_unknown
UNION ALL
SELECT
    'of those, no occupation to record',
    count(*)
FROM fact_person f
JOIN dim_occupation o USING (occupation_sk)
WHERE f.income_gt_50k = FALSE AND f.age BETWEEN 25 AND 54
  AND o.is_not_applicable;

-- Lab 1 reported 520 unassignable candidates, because the source string '?'
-- gave it no way to separate the two states. The split above is 519 who did
-- not answer and 1 who has never worked. Only the first group is a data
-- collection problem.


-- What the sentinel does to a ranking, which is the reason capital_gain is
-- stored NULL with a flag instead of as 99999.
SELECT
    o.occupation,
    round(avg(f.capital_gain), 0)                                       AS mean_excluding_sentinel,
    round(avg(coalesce(f.capital_gain,
              CASE WHEN f.capital_gain_is_topcoded THEN 99999 END)), 0) AS mean_including_sentinel,
    count(*) FILTER (WHERE f.capital_gain_is_topcoded)                  AS censored_rows
FROM fact_person f
JOIN dim_occupation o USING (occupation_sk)
WHERE o.is_unknown = FALSE AND o.is_not_applicable = FALSE
GROUP BY o.occupation
ORDER BY mean_including_sentinel DESC;


-- The duplicate decision, visible rather than silently applied.
SELECT
    count(*)                                            AS rows_loaded,
    count(*) FILTER (WHERE duplicate_group_id IS NOT NULL) AS rows_in_a_duplicate_group,
    count(DISTINCT duplicate_group_id)                  AS duplicate_groups,
    count(*) FILTER (WHERE duplicate_seq > 1)           AS rows_pandas_would_call_duplicated
FROM fact_person;
