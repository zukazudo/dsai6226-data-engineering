# Metric definitions

Every number this project publishes is defined here, once. Unit 7's rule is that a metric has
a formula, a grain, filters and an owner, and that it is computed in the pipeline rather than
inside each chart. If a consumer needs a number that is not on this page, the number does not
exist yet and the fix is to add it here and compute it in `sql/07_mart.sql`, not to work it
out in the dashboard.

| | |
|---|---|
| Serving table | `mart_segment_allocation` |
| Built by | `sql/07_mart.sql`, applied by `scripts/ingest.py` on every run |
| Grain | one row per occupation, per education group |
| Rows | 87 |
| Owner | Team E, Mhina Lukurunge · v1.0, 2 October 2026 |
| Refresh promise | rebuilt by every pipeline run; the dashboard shows the age of the build |
| Upstream | `analytic_person` ← `fact_person` ← `stg_adult` ← `data/adult.csv` |

## The population every metric is computed over

Named once here because it is the filter most likely to be applied differently by two people.

**`eligible`** = rows of `analytic_person` where

- `income_gt_50k = FALSE` — the consumer allocates support to people below the threshold, so
  those already above it are out of scope.
- `age BETWEEN 25 AND 54` — working age. The lower bound excludes people still in education,
  the upper bound excludes those approaching retirement.

That is **16,005** of 32,561 records. Every metric below has this filter applied and none
reapplies it, which is the point: the pool is defined in one place.

Note what is deliberately *not* filtered out. Records whose occupation is `Unknown` or
`Not applicable` stay in, and carry `occupation_is_assignable = false` instead. Dropping them
would make the segment counts stop summing to the pool, and a total that does not reconcile is
how two dashboards start disagreeing.

## Metrics

### `candidates`

| | |
|---|---|
| Formula | `count(*)` over `eligible`, grouped by occupation and education group |
| Grain | per occupation, per education group |
| Filters | the `eligible` population above; nothing further |
| Type | integer, never null |
| Owner | Team E |

The number of people in the pool who fall in this segment. Sums to 16,005 across all 87 rows.

### `pct_of_eligible_pool`

| | |
|---|---|
| Formula | `round(100.0 * count(*) / 16005, 4)` |
| Grain | per occupation, per education group |
| Filters | as `candidates` |
| Type | double, 0 to 100, four decimal places |
| Owner | Team E |

The segment's share of the whole pool. The denominator is the pool, never the sum of some
subset of segments, so the column sums to 100 to within rounding (99.9999 as built). If it ever
sums to something else, a segment has been dropped somewhere upstream.

### `mean_hours`

| | |
|---|---|
| Formula | `avg(hours_per_week)` over `eligible` |
| Grain | per occupation, per education group |
| Filters | the `eligible` population, **and top-coded values excluded** |
| Type | double, nullable |
| Owner | Team E |

Average usual hours worked per week. The exclusion matters and is not optional: 85 records
carry `hours.per.week = 99`, which is a censoring sentinel rather than a measurement. The star
stores those as NULL beside `hours_is_topcoded`, so `avg()` already excludes them and this
definition inherits that. Including them would pull every affected segment upward by an amount
nobody could see.

Null when every record in a segment was top-coded. Read it beside `hours_measured_on`.

### `mean_capital_gain`

| | |
|---|---|
| Formula | `avg(capital_gain)` over `eligible` |
| Grain | per occupation, per education group |
| Filters | the `eligible` population, **and top-coded values excluded** |
| Type | double, nullable |
| Owner | Team E |

Average capital gain. 159 records carry `capital.gain = 99999`, the same censoring mechanism,
and the distortion is larger here than for hours because the sentinel is enormous relative to
the real values. Lab 1 found that excluding it moves 9 of 14 occupations in the ranking. Any
consumer quoting this metric without that exclusion is quoting a different number.

Null when every record in a segment was top-coded. Read it beside
`capital_gain_measured_on`.

### `hours_measured_on`, `capital_gain_measured_on`

| | |
|---|---|
| Formula | `count(hours_per_week)` and `count(capital_gain)`, which skip nulls |
| Grain | per occupation, per education group |
| Filters | as the mean they accompany |
| Type | integer, never null |
| Owner | Team E |

How many records each mean was actually computed on. Published because a mean over 12 records
and a mean over 1,200 are different claims, and the mean alone cannot tell them apart. The
difference between this and `candidates` is the number of top-coded records in the segment.

### `segment_is_sparse`

| | |
|---|---|
| Formula | `count(*) < 30` |
| Grain | per occupation, per education group |
| Filters | as `candidates` |
| Type | boolean, never null |
| Owner | Team E |

True when the segment holds fewer than thirty people. **20 of the 87 segments** are sparse.
Thirty is a convention rather than a law, chosen because it is the threshold the project has
used since Lab 1 and because stating an arbitrary line explicitly is better than leaving every
consumer to pick their own silently. The flag travels with the row so that a chart cannot put
a seven-person segment beside a seven-hundred-person one without the viewer being told.

### `occupation_is_assignable`

| | |
|---|---|
| Formula | `NOT (occupation_unknown OR occupation_not_applicable)` |
| Grain | per occupation, per education group |
| Filters | as `candidates` |
| Type | boolean, never null |
| Owner | Team E |

False for the segments whose occupation is `Unknown` (the source recorded `?`) or
`Not applicable` (the respondent has never worked). **7 of the 87 segments** are unassignable.
These rows are published rather than filtered so the totals reconcile, but no allocation
decision should be made against them, because there is no occupation to allocate to.

## Freshness

| | |
|---|---|
| Formula | `now() - max(refreshed_at)` from `mart_segment_allocation` |
| Promise | the table is rebuilt by every pipeline run |
| Thresholds | fresh under 24 h, ageing 24 h to 7 days, **stale** beyond 7 days |
| Shown on | the dashboard banner, computed at render time |
| Owner | Team E |

`refreshed_at` is written by `sql/07_mart.sql` when it rebuilds the table, so the label is
computed from the data and never typed by hand. Unit 7 is blunt that hand-typed freshness
rots; the banner is generated from this column every time the dashboard is built.

`source_load_run_id` records which load run produced the rows, so a mart can be traced to the
run, and the run to the file and its sha256.

## Changing a metric

1. Change the definition here first, and bump the version in the header.
2. Change `sql/07_mart.sql` to match.
3. Re-run `python scripts/ingest.py`, which republishes the table.
4. Run `python scripts/validate_labs.py`, which checks the invariants this page claims:
   that `candidates` sums to the pool and `pct_of_eligible_pool` sums to 100.

Changing the chart instead of this page is the failure mode the whole file exists to prevent.
