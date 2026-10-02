-- ---------------------------------------------------------------------------
-- 07_mart.sql
--
-- Lab 7. The one curated output table this pipeline publishes.
--
-- analytic_person is row level: it exists so a flat file can answer the same
-- question the star answers, and it is an input to benchmarks and exports. It
-- is not a serving table. Unit 7 is explicit that a mart is small, documented
-- and computed at a stated grain, so that every consumer drinks the same
-- number instead of each chart recomputing its own.
--
-- mart_segment_allocation is that table. Grain: one row per occupation and
-- education group. It answers the allocation question Lab 1 was written
-- around, and every metric in it is defined once, in metrics.md.
--
-- A TABLE rather than a VIEW, deliberately. A view is recomputed on read and
-- therefore cannot ever be stale, which would make the freshness label the lab
-- asks for meaningless and the "break the refresh" demonstration impossible.
-- Materialising it means the table carries the moment it was built, and a
-- failed refresh is visible rather than invisible.
--
-- Applied by scripts/ingest.py on every run, so the pipeline refreshes it.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mart_segment_allocation (
    occupation              VARCHAR   NOT NULL,
    education_group         VARCHAR   NOT NULL,
    candidates              INTEGER   NOT NULL,
    pct_of_eligible_pool    DOUBLE    NOT NULL,
    mean_hours              DOUBLE,
    mean_capital_gain       DOUBLE,
    hours_measured_on       INTEGER   NOT NULL,
    capital_gain_measured_on INTEGER  NOT NULL,
    segment_is_sparse       BOOLEAN   NOT NULL,
    occupation_is_assignable BOOLEAN  NOT NULL,
    refreshed_at            TIMESTAMP NOT NULL,
    source_load_run_id      INTEGER   NOT NULL,
    PRIMARY KEY (occupation, education_group)
);

-- Rebuilt whole on every run. The table is 87 rows, so there is nothing to be
-- gained from an incremental merge and a great deal to be lost: a stale row
-- surviving a rebuild is exactly the failure a serving table must not have.
DELETE FROM mart_segment_allocation;

INSERT INTO mart_segment_allocation
WITH eligible AS (
    -- The eligible pool, defined once here and nowhere else. Working age and
    -- not already above the income threshold, which is the population the
    -- Lab 1 consumer actually allocates against.
    SELECT *
    FROM analytic_person
    WHERE income_gt_50k = FALSE
      AND age BETWEEN 25 AND 54
),
pool AS (SELECT count(*) AS n FROM eligible)
SELECT
    e.occupation,
    e.education_group,
    CAST(count(*) AS INTEGER)                              AS candidates,
    round(100.0 * count(*) / (SELECT n FROM pool), 4)       AS pct_of_eligible_pool,

    -- Top-coded measures are NULL in the star, so avg() already excludes them.
    -- The count of rows each mean was actually computed on is published beside
    -- it, because a mean over 12 records and a mean over 1,200 are different
    -- claims and a consumer cannot tell them apart from the number alone.
    avg(e.hours_per_week)                                   AS mean_hours,
    avg(e.capital_gain)                                     AS mean_capital_gain,
    CAST(count(e.hours_per_week) AS INTEGER)                AS hours_measured_on,
    CAST(count(e.capital_gain) AS INTEGER)                  AS capital_gain_measured_on,

    -- Lab 1 found that most segments are too small to carry a decision. The
    -- flag travels with the row so a consumer cannot plot a 7 record segment
    -- beside a 700 record one without being told.
    count(*) < 30                                           AS segment_is_sparse,

    -- '?' resolves to Unknown or Not applicable, which are real members but
    -- are not occupations anyone can allocate against. Published rather than
    -- filtered out, so the total still reconciles to the pool.
    NOT (max(e.occupation_unknown) OR max(e.occupation_not_applicable))
                                                            AS occupation_is_assignable,
    now()                                                   AS refreshed_at,
    (SELECT max(load_run_id) FROM fact_person)              AS source_load_run_id
FROM eligible e
GROUP BY e.occupation, e.education_group;
