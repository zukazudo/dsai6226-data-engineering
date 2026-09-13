-- ---------------------------------------------------------------------------
-- 06_bigquery.sql
--
-- The Lab 5 queries, in BigQuery Standard SQL. Run on 13 September 2026
-- against dsai6226-team-e.adult.adult_analytic in region africa-south1.
--
-- Replace the table reference with your own if you rebuild the sandbox. In a
-- fresh sandbox the project id is generated for you and looks something like
-- "sunlit-flare-123456".
--
-- Read the estimate the editor prints above the Run button before running
-- anything. That figure is the cost driver.
-- ---------------------------------------------------------------------------


-- --------------------------------------------------------------- Query 1
-- The allocation question from Lab 1, naming only the six columns it needs.
--
--   predicted  1,898,872 bytes   1.81 MB
--   measured                     1.81 MB processed, 10 MB billed, 494 ms
--   returns    87 segments, matching the local warehouse exactly

SELECT
    occupation,
    education_group,
    COUNT(*)                AS candidates,
    AVG(hours_per_week)     AS mean_hours,
    AVG(capital_gain)       AS mean_capital_gain
FROM `dsai6226-team-e.adult.adult_analytic`
WHERE income_gt_50k = FALSE
  AND age BETWEEN 25 AND 54
GROUP BY occupation, education_group
ORDER BY candidates DESC;


-- --------------------------------------------------------------- Query 2
-- Reads every column, and is the only way we found to actually pay for them.
--
--   predicted  5,604,157 bytes   5.34 MB
--   measured                     5.34 MB
--
-- Three times the scan of Query 1, for a table nobody needs in full.

SELECT * FROM `dsai6226-team-e.adult.adult_analytic`;


-- --------------------------------------------------------------- Query 3
-- Confirms the upload is faithful. Every figure below matches Lab 1.
--
--   32,561 records, 16,005 eligible, 1,836 unknown occupations,
--   7 not applicable, 159 top-coded capital gains

SELECT
    COUNT(*)                                               AS total_records,
    COUNTIF(NOT income_gt_50k AND age BETWEEN 25 AND 54)   AS eligible_pool,
    COUNTIF(occupation_unknown)                            AS occ_unknown,
    COUNTIF(occupation_not_applicable)                     AS occ_not_applicable,
    COUNTIF(capital_gain IS NULL)                          AS capgain_topcoded
FROM `dsai6226-team-e.adult.adult_analytic`;


-- ---------------------------------------------------------------------------
-- The query that did NOT behave as expected
--
-- This was written to demonstrate that SELECT star costs more. It does not.
-- The editor prices it at 1.81 MB, identical to Query 1, because BigQuery
-- prunes columns the query never references, even through the subquery. Only
-- six columns are referenced, so only six are read and only six are billed.
--
--     SELECT occupation, education_group, COUNT(*) AS candidates
--     FROM (SELECT * FROM `dsai6226-team-e.adult.adult_analytic`)
--     WHERE income_gt_50k = FALSE AND age BETWEEN 25 AND 54
--     GROUP BY occupation, education_group;
--
-- Kept here because the failed demonstration is the useful one. What costs
-- money is the set of columns a query references, not the literal text of the
-- SELECT list. Rewriting SELECT star into a column list saves nothing if the
-- columns were never going to be read.
-- ---------------------------------------------------------------------------


-- ---------------------------------------------------------------------------
-- Pricing the query without running it
--
--     bq query --use_legacy_sql=false --dry_run 'SELECT ...'
--
-- A dry run is free. On the sandbox the first 1 TB scanned each month is also
-- free, and BigQuery bills a minimum of 10 MB per table per query, so both
-- queries above round to the same bill of nothing. The ratio between them is
-- the lesson; the bill only starts responding on a much larger table.
-- ---------------------------------------------------------------------------
