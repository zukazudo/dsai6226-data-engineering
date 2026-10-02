# DSAI 6226 Data Engineering, Team E

Coursework repository for the Adult census income dataset.

**Team E**

| Member | GitHub |
|---|---|
| Mhina Lukurunge | [@zukazudo](https://github.com/zukazudo) |
| Samwel Mmari | [@SuperMan-Tz](https://github.com/SuperMan-Tz) |
| Asajile Mwakyalabwe | [@mwakyalabwea-code](https://github.com/mwakyalabwea-code) |
| Essa Mohamedali | [@EssaMohamedali](https://github.com/EssaMohamedali) |
| Paschal Bizulu | [@pascode47](https://github.com/pascode47) |

## The dataset

`data/adult.csv` is the Kaggle mirror of the UCI Adult dataset, an extract of the March 1994
US Current Population Survey. 32,561 records, 15 columns, 4 MB.

The file is committed here so that every result in this repository can be reproduced from a
clone without a Kaggle account. It is the unmodified original, and no cleaning has been
applied to it.

## Setting up

```bash
pip install -r requirements.txt
```

Three packages: DuckDB, pandas and psycopg. The last is used only by the Lab 4 benchmark, so
the pipeline itself runs without it. Parquet needs no separate library, because DuckDB reads
and writes it directly.

## Lab 1: data problem statement

Deliverable: [`docs/Team_E_Lab1_Data_Problem_Statement.docx`](docs/Team_E_Lab1_Data_Problem_Statement.docx)

The statement names a consumer, a decision, and the three defects that stop the data from
supporting it. Assessed consumer is the US Department of Labor Employment and Training
Administration, allocating a training budget across occupation and education segments in the
1995 funding cycle.

The three defects:

| Category | Defect | Scale |
|---|---|---|
| Gaps | Missing values encoded as the string `?`, so `isna()` reports zero nulls | 4,262 cells across 2,399 records |
| Strange values | `capital.gain` uses 99999 as a top-code sentinel | 159 records, next real value 41,310 |
| Duplicates | Exact duplicate records with no primary key to adjudicate them | 24 rows, no identifier column |

Appendices A to D of the document carry the evidence, the secondary findings, a note on why
`fnlwgt` should be excluded from any feature set, and the code that produces every figure.

## Lab 2: modelling the dataset

Deliverable: the SQL schema in [`sql/`](sql/).

```
sql/01_staging.sql    landing zone and audit tables, all CREATE IF NOT EXISTS
sql/02_schema.sql     the star: one fact table, eight dimensions, seeded members
sql/03_load.sql       validate, reject, upsert dimensions, insert facts, all idempotent
sql/04_query.sql      the allocation query, plus three supporting counts
```

The schema is built and populated by the Lab 3 ingester. Query it with:

```bash
duckdb warehouse/adult.duckdb < sql/04_query.sql
```

Engine is DuckDB. It is one of the two the brief allows, it needs no server, and the whole
warehouse is a single file that any teammate can rebuild from a clone in about two seconds.

### Why this shape

A star schema with two deliberate departures from textbook form.

**The fact table exists to give every record a key.** Lab 1 found that this dataset has no
identifier of any kind, which is why it cannot be refreshed incrementally, joined to anything,
or audited. `fact_person.person_sk` is that missing key, and `source_row` traces any modelled
row back to the exact line of the CSV it came from. This is the single largest thing the model
adds, and no amount of cleaning the flat file would have produced it.

**Dimensions exist to hold states the source could not express.** The source writes `?` for
two different things. A person who declined to answer and a person who has never worked both
appear identically. `dim_occupation` separates them into `Unknown` and `Not applicable`,
which are surrogate keys -1 and -2. Running the allocation query now shows 519 candidates who
did not answer and 1 who has no occupation to record. Lab 1 could only report 520 unassignable
and had no way to split them.

**First departure: `education` and `education.num` are one dimension, not two.** They are a
strict one to one mapping across all 16 levels, so they are two attributes of a single
dimension member. `dim_education` also carries `education_group`, which rolls the 16 levels
into 6. Lab 1 found 93 of 201 occupation and education segments held fewer than 30 records,
which is too thin to allocate a budget against. After the rollup it is 18 of 80.

**Second departure: top-coded measures are stored NULL with a companion flag.** Where
`capital.gain` was 99999, the fact table holds NULL and `capital_gain_is_topcoded` is true.
An average then excludes the censored records automatically, so a placeholder never enters a mean.
The same applies to `age` at 90 and `hours.per.week` at 99. `capital.loss` has no sentinel and
loads unaltered.

**The duplicates are retained, not resolved.** Lab 1 concluded that deduplication is not a
safe automatic operation here, because a data entry duplicate and two genuinely similar people
are indistinguishable without a key. So all 32,561 rows load. The 47 rows that belong to a
duplicate group carry a `duplicate_group_id`, and `duplicate_seq` numbers them in source
order. A deduplicated view is `WHERE duplicate_seq IS NULL OR duplicate_seq = 1`. The decision
is recorded in the data, not applied silently at load, and it stays reversible.

### Why not one big table

For 32,561 rows, one big table would be faster and simpler, and that is the honest starting
position. DuckDB would scan a flat table of this size without noticing. The reason to model it
anyway has nothing to do with performance:

- **A flat table has nowhere to put the distinction between missing and not applicable.** The
  only options are to keep the magic string `?`, which is the defect Lab 1 documented, or to
  write NULL and destroy a real value for the seven `Never-worked` records. A dimension has a
  row for each state.
- **A flat table cannot hold the duplicate decision.** Retaining rows while marking them
  requires columns that describe the record, not the person. Those belong beside the
  surrogate key, which a flat file does not have.
- **`education` and `education.num` are redundant in every one of the 32,561 rows**, and
  nothing stops them drifting apart. In `dim_education` the pairing is asserted once and
  enforced by a unique constraint.
- **Rollups belong in one place.** `education_group` and the eventual `native.country`
  grouping are decisions about how to aggregate. Defined in a dimension they apply everywhere.
  Defined in a flat table they get retyped into every query, slightly differently each time.

The counter-argument that does hold: the staging table `stg_adult` *is* one big table, and it
is kept deliberately. It is the raw record, and the star is derived from it.

### What is cleaned, and where

Nothing is cleaned before load. `data/adult.csv` is never modified, and `stg_adult` holds
every column as text exactly as it arrives. All cleaning is in `03_load.sql`, so the raw value
behind any modelled row stays queryable.

| Source condition | What the model does |
|---|---|
| `?` in `workclass` or `native.country` | Dimension member `Unknown`, surrogate key -1 |
| `?` in `occupation`, `workclass` present | Dimension member `Not applicable`, key -2, all 7 are `Never-worked` |
| `?` in `occupation`, `workclass` also `?` | Dimension member `Unknown`, key -1 |
| `capital.gain` = 99999 | Value NULL, `capital_gain_is_topcoded` true, 159 rows |
| `age` = 90 | Value NULL, `age_is_topcoded` true, 43 rows |
| `hours.per.week` = 99 | Value NULL, `hours_is_topcoded` true, 85 rows |
| Exact duplicate rows | All retained, tagged with `duplicate_group_id` and `duplicate_seq` |
| Everything else | Cast to a real type and loaded unchanged |

The load is checked against the Lab 1 figures on every build: row counts, the `?` split, each
sentinel count, the duplicate groups, and referential integrity on all eight dimensions.

## Lab 3: the re-runnable ingester

Deliverable: [`scripts/ingest.py`](scripts/ingest.py)

Run it in exactly one command:

```bash
python scripts/ingest.py
```

That reads `data/adult.csv`, lands it in staging, validates it, and loads what passes into the
star schema, creating the schema first if it does not exist. Pass a different path to ingest
another file, or a directory to ingest every `.csv` in it.

### Proof of idempotency

Running it a second time changes nothing. The row count is unchanged and no work is repeated:

```
run 1: adult.csv (4,104,734 bytes, sha256 250e154ed757)
  rows read 32561
  staged    32561  (0 already present from an earlier run)
  rejected      0
  loaded    32561
  fact_person now holds 32,561 rows

run 2: adult.csv (4,104,734 bytes, sha256 250e154ed757)
  rows read 32561
  staged        0  (32561 already present from an earlier run)
  rejected      0
  loaded        0
  fact_person now holds 32,561 rows
```

Every run is also recorded in the `load_run` table, so the proof survives the terminal
scrollback. `python scripts/ingest.py --summary` prints it:

| run | source | read | staged | rejected | loaded | status |
|---|---|---|---|---|---|---|
| 1 | adult.csv | 32561 | 32561 | 0 | 32561 | ok |
| 2 | adult.csv | 32561 | 0 | 0 | 0 | ok |

### How the idempotency actually works

The source has no identifier of any kind, which was the third defect in the Lab 1 statement,
so there is nothing to match an incoming row against. The ingester manufactures identity from
content. `record_key` is an md5 of all 15 source fields, suffixed with the occurrence number
of that exact content within the file:

```
0f1eb4dcb00ec0cf9f76e1b0e60cbb95#1
```

Re-reading the same file reproduces exactly the same keys, so the insert finds them already
present and does nothing. The occurrence suffix is what lets the 24 known duplicate rows
survive: three identical records become `#1`, `#2` and `#3`, where otherwise they would collapse into one.

The limitation is worth stating plainly. Two genuinely different people who match on all 15
attributes are indistinguishable to this scheme, exactly as they were indistinguishable to
Lab 1. The ingester inherits that ambiguity from the source and does not pretend to resolve it.

### What it logs

Rows read, rows staged, rows rejected with the reason for each, and rows loaded. Rejects go to
the `load_reject` table as well as the console, so a rejected row can be investigated after the
run, and not only read in a log.

Eight validation rules are enforced. They reject rows that are **malformed**, not rows that are
merely **odd**: the 2,399 records carrying `?` and the three records contradicting themselves
on sex and relationship are real observations and load normally.

`tests/fixtures/adult_bad_rows.csv` is a nine-row file that triggers every rule once, so the
reject path is demonstrated rather than assumed:

```bash
python scripts/ingest.py tests/fixtures/adult_bad_rows.csv
```

```
rows read     9
staged        9
rejected      8, by reason:
    age_not_an_integer               1   e.g. age = abc
    age_out_of_range                 1   e.g. age = 201
    fnlwgt_not_a_positive_integer    1   e.g. fnlwgt = -5
    hours_per_week_out_of_range      1   e.g. hours.per.week = 0
    capital_value_invalid            1   e.g. gain = -100, loss = 0
    income_not_recognised            1   e.g. income = 50K+
    sex_not_recognised               1   e.g. sex = Unknown
    education_mapping_mismatch       1   e.g. education = HS-grad, education.num = 12
loaded        1
```

One row is valid and loads, eight are rejected. The `education_mapping_mismatch` rule is worth
noting: `dim_education` is seeded with the canonical 16-level ladder and not inferred from
the data, so it can serve as the reference an incoming pair is checked against. A file claiming
`HS-grad` at level 12 is rejected instead of quietly widening the dimension.

### Lineage

`fact_person` carries `load_run_id`, `source_file` and `source_row`, and `load_run` records the
sha256 and byte count of every file ingested. Any row in the warehouse can be traced back to
the exact line of the exact file that produced it, and forward to the run that admitted it.
Lab 1 concluded that a decision spending public money had no auditable lineage. This is the
part that fixes it.

To start over from an empty database:

```bash
python scripts/ingest.py --reset
```

## Presentation

[`presentations/Team_E_Labs_1_to_5.pptx`](presentations/Team_E_Labs_1_to_5.pptx)

Twenty-five slides covering all five labs as one argument: three defects found, the two
decisions that close them, the engine the result points at, and what the same question costs
in a cloud warehouse. Section dividers mark the lab boundaries. Speaker notes are on every
slide.

## Lab 4: benchmark, do not believe

Deliverable: the table and verdict below. Reproduce with
[`scripts/benchmark.py`](scripts/benchmark.py).

```bash
python scripts/benchmark.py
```

PostgreSQL runs in a throwaway container, described in `docker-compose.yml`. Nothing else in
this repository depends on it, so it only needs to be running while benchmarking:

```bash
docker compose up -d
```

It listens on 55432 to stay clear of any PostgreSQL already installed on the machine.
`docker compose down -v` removes it again.

### The comparison

One aggregate query, the allocation question from Lab 1, run against identical data in three
places. 32,561 rows is the real dataset. The 3.3 million row copy is included because at the
native size the numbers say more about process startup than about the engines.

**Native, 32,561 rows**

| Engine | Stored size | One-off load | Query | Query including read |
|---|---:|---:|---:|---:|
| CSV + pandas | 5.3 MB | n/a | 44.8 ms | 357 ms |
| PostgreSQL 16 | 6.5 MB | 1,165 ms | 35.8 ms | n/a |
| DuckDB + Parquet | 0.3 MB | n/a | **17.2 ms** | n/a |

**Scaled, 3,256,100 rows**

| Engine | Stored size | One-off load | Query | Query including read |
|---|---:|---:|---:|---:|
| CSV + pandas | 545.5 MB | n/a | 1,450.6 ms | 30,373 ms |
| PostgreSQL 16 | 602.7 MB | 85,017 ms | 1,263.0 ms | n/a |
| DuckDB + Parquet | 14.6 MB | n/a | **125.1 ms** | n/a |

Median of 7 runs after a warm-up, on an AMD Ryzen 5 PRO 5650U, 12 threads, 15.3 GB RAM,
Windows 11, Python 3.14.5, DuckDB 1.5.5, pandas 3.0.3, PostgreSQL 16.14 in Docker.

### How the numbers were kept honest

**All three engines see identical data.** Benchmarking the raw `adult.csv` against the cleaned
warehouse would compare different numbers. The denormalised `analytic_person` table is written
once to CSV, Parquet and PostgreSQL, so every engine answers the same question.

**The script asserts the three answers match before reporting any timing.** All three return
the same 87 groups with the same counts and means. A timing from an engine that computed a
different answer would be worthless, and this is the check that catches it.

**Load cost is separated from query cost.** pandas pays the read on every single run, which is
the 357 ms and 30 second columns. PostgreSQL and DuckDB pay it once. Reporting only per-query
time flatters pandas; reporting only total time flatters the databases. Both are shown.

**The scaled storage figures overstate the Parquet advantage and should not be quoted.** The
larger table is the real one replicated 100 times, with only `person_sk` and `fnlwgt` varied,
so its columns are far more repetitive than real data and compress better than real data would.
The scaled rows are included for query time. For storage, the honest number is the native one:
0.3 MB against 5.3 MB, roughly seventeen times smaller.

### Does it reproduce?

The benchmark was run a second time on different hardware, inside Colab, with PostgreSQL 14
instead of 16. The ranking of the three engines held. The ordering of two of them did not.

| Run | Rows | CSV + pandas | PostgreSQL | DuckDB + Parquet |
|---|---:|---:|---:|---:|
| Local, PostgreSQL 16.14 | 3,256,100 | 1,450.6 ms | 1,263.0 ms | 125.1 ms |
| Colab, PostgreSQL 14.24 | 1,628,050 | 551.9 ms | 760.2 ms | 107.5 ms |

PostgreSQL beat pandas on the local run and lost to it on the Colab run. Three things differ
between those rows at once, being the row count, the PostgreSQL version and the machine, so
the flip cannot be pinned on any one of them without further work. That is the point worth
taking from it: the gap between pandas and PostgreSQL on this query is small enough to invert
when the environment changes, so neither result should be quoted as a fact about the engines.

DuckDB with Parquet was between four and ten times faster than the next engine in both
environments, and that is the finding the verdict rests on.

### Verdict

At 32,561 rows every engine answers in under 50 milliseconds, so for this dataset as it stands
today speed is not what decides the question. We would still choose DuckDB with Parquet,
because it is the fastest of the three at both sizes, the file is seventeen times smaller than
the CSV, and it needs no server running before anyone can ask a question. PostgreSQL earns its
1.2 second load and its extra disk only when several people write at once and the data has to
survive a crash, and neither is true of a coursework warehouse that one analyst rebuilds from
a clone in seconds.

## Lab 5: touch a real cloud warehouse

Deliverable: [`docs/Team_E_Lab5_Cloud_Warehouse.docx`](docs/Team_E_Lab5_Cloud_Warehouse.docx)

The denormalised table loaded into the BigQuery sandbox, one aggregate run against it, and the
full cloud pipeline designed with a cost note on every box.

```
sql/06_bigquery.sql       the three sandbox queries, with the measured figures
scripts/bq_bytes.py       predicts a BigQuery scan from the data, before running it
```

Run on 13 September 2026 in project `dsai6226-team-e`, dataset `adult`, region
`africa-south1`. All 32,561 records uploaded.

### The figure the lab is about

| Query | Predicted scan | Measured | Billed |
|---|---:|---:|---:|
| The aggregate, six columns referenced | 1,898,872 bytes (1.81 MB) | 1.81 MB | 10 MB |
| `SELECT *` over all twenty-four | 5,604,157 bytes (5.34 MB) | 5.34 MB | 10 MB |

Both predictions were exact. They were computed locally before the sandbox was opened, by
`scripts/bq_bytes.py`: column widths in BigQuery are fixed by type, a NULL costs nothing, and
a query reads only the columns it names, so the scan is arithmetic rather than a mystery.

Billing rounds both to 10 MB, the per-table minimum, and the sandbox includes the first
terabyte scanned each month. The saving between them is real in bytes and zero in money at
this size.

The upload was checked against Lab 1 in one query: 32,561 records, an eligible pool of 16,005,
1,836 occupations unknown, 7 not applicable, 159 top-coded capital gains. Every figure survives
the chain from source CSV through the warehouse and the export into the cloud, and the
aggregate returned the same 87 segments.

### What we got wrong first, and what it taught us

Unit 5 calls `SELECT *` the most expensive words in the cloud. The query written to demonstrate
that did not demonstrate it. Wrapping the table in a subquery selecting everything, then
aggregating six columns, still scanned 1.81 MB, because BigQuery prunes columns a query never
references. Only a bare `SELECT *` with no aggregation, which leaves nothing to prune, reached
5.34 MB.

The rule that survives testing is narrower than the slogan: you pay for the columns your query
**references**, so the saving comes from needing fewer columns, not from how the `FROM` clause
is written. The failed query is kept as a comment in `sql/06_bigquery.sql`, because it is more
instructive than the one that worked.

### One line of the lecture is now out of date

Unit 5 names AWS Cape Town and Azure South Africa as the nearest major regions and says much
GCP analytics still runs from Europe. BigQuery now offers `africa-south1` in Johannesburg, and
it accepted the dataset without complaint, so that is the region this project uses.

Proximity is not compliance. Johannesburg is still outside Tanzania, so moving personal data
there would need safeguards under the PDPA regardless. The point is that the region was chosen
and can be defended, and that no default was accepted.

## Lab 6: guard the pipeline

Deliverable: the checks in [`sql/03_load.sql`](sql/03_load.sql) and
[`scripts/quality_checks.py`](scripts/quality_checks.py), the `quarantine` table, and the
lineage and PDPA sections below.

```bash
python scripts/ingest.py          # ten rules run on every load
python scripts/quality_checks.py  # thirty-two assertions on the result
```

Unit 6 names five dimensions of quality: complete, valid, fresh, unique and consistent. The
checks are split across two places, because two different kinds of failure need two different
answers.

| | Row level, in `sql/03_load.sql` | Table level, in `scripts/quality_checks.py` |
|---|---|---|
| Judges | one record at a time | the warehouse as a whole |
| On failure | the row is quarantined, the load continues | the build is wrong, exit code 1 |
| Count | 10 rules | 32 assertions |
| Covers | validity, completeness, uniqueness | all five dimensions |

A row-level rule can set one record aside and let the other 32,560 through. A table-level
property, such as no surrogate key being duplicated, cannot be blamed on any single row and
cannot be fixed by removing one, so there is nothing to quarantine and the only honest
response is to fail the build.

### The ten rules that decide whether a row may enter

| Rule | Dimension | Rejects |
|---|---|---|
| `age_not_an_integer` | valid | an age that will not cast |
| `age_out_of_range` | valid | an age outside 1 to 120 |
| `fnlwgt_not_a_positive_integer` | valid | a survey weight that is zero, negative or unparseable |
| `hours_per_week_out_of_range` | valid | hours outside 1 to 99 |
| `capital_value_invalid` | valid | a negative or unparseable capital gain or loss |
| `income_not_recognised` | valid | an income band outside the two the source defines |
| `sex_not_recognised` | valid | a value outside the two the source defines |
| `education_mapping_mismatch` | valid | a label and level pair the seeded ladder does not contain |
| `required_field_empty` | complete | a field that is empty or whitespace |
| `duplicate_record_key` | unique | the same record_key staged twice |

Eight of these predate Lab 6. The last two are new, and each needed a decision about what the
dimension actually means for this dataset.

**Completeness is not the same as rejecting the question marks.** 2,399 records carry `?`,
which is the source saying *asked, not answered*. Lab 2 models that as a real dimension
member, split into `Unknown` and `Not applicable`, because the seven `Never-worked` records
genuinely have no occupation rather than a missing one. A completeness rule that rejected `?`
would delete 1,836 records and undo two labs of reasoning. `required_field_empty` therefore
fires on a field that is empty or whitespace, which is the source saying nothing at all, and
that is a delivery fault. The distinction between *nothing was recorded* and *the answer was
unknown* is the entire content of the rule.

**Uniqueness is mostly not a row-level property here.** The source has no business key, and
Lab 2 decided its 47 duplicate rows are kept and marked rather than dropped, because nothing
proves they are errors. `record_key` is a content hash plus the occurrence number of that
content, so two identical rows become `#1` and `#2` and both load correctly. The only
row-level uniqueness statement that is true of this dataset is that a key must not be staged
twice, and by construction it cannot be. Real uniqueness lives one level up, in the eleven
table-level assertions, where it is both checkable and has been seen to fail.

### Quarantine

`load_reject` already recorded *that* a row was refused and why. It did not hold the row, so
answering what was actually in it meant joining back to staging and hoping staging was still
there. Unit 6 asks for rows that are visible and fixable, and a reason code on its own is
neither.

`quarantine` holds the refused record whole: every source field as text, the reason code, the
detail, the run, and the moment it was set aside. It is filled from `load_reject` rather than
by repeating the rule predicates, so the two tables cannot disagree about what was refused.

Proving it, with the fixture in
[`tests/fixtures/adult_quarantine_demo.csv`](tests/fixtures/adult_quarantine_demo.csv):

```bash
python scripts/ingest.py tests/fixtures/adult_quarantine_demo.csv --db demo.duckdb
```

```
rows read     2
staged        2
rejected      1, by reason:
    required_field_empty   1   e.g. empty: occupation
loaded        1
```

Two rows, and the pair is the point. One has an empty `occupation` and is quarantined with its
values intact. The other has `occupation` set to `?` and loads, mapped to the `Unknown`
member. The second row is the control: if it is ever refused, the completeness rule has
silently become a Lab 2 regression that deletes 1,836 records.

```
quarantine
  record_key   cdb4b4a7b45d9c2ae7a4f27ad553fbb5#1
  reason       required_field_empty
  detail       empty: occupation
  source_row   1   occupation NULL   age 44   workclass Private

fact_person
  source_row   2   occupation 'Unknown'   is_unknown true
```

Nine of the ten rules are demonstrated against
[`tests/fixtures/adult_bad_rows.csv`](tests/fixtures/adult_bad_rows.csv), which quarantines
nine rows and loads one. `duplicate_record_key` is the tenth and cannot be demonstrated from a
file, for the reason given above.

### The checks have been seen to fail

A suite that has only ever been green is a wish. Faults were injected into a copy of the
warehouse to confirm the suite notices:

| Injected | Caught by |
|---|---|
| `capital_gain` written back as the raw 99999 sentinel | `capital_gain_topcode_is_null`, `capital_gain_sentinel_absent` |
| `quarantine` emptied, so refused rows vanish from the record | `staged_rows_all_accounted` |

Three further attempts were refused by the schema before the checks ever saw them. A duplicate
`person_sk` hit the primary key, a nulled `source_file` hit a NOT NULL constraint, and
deleting a `dim_race` member hit a foreign key. That is the more comfortable finding of the
two. Constraints make a class of corruption impossible, and checks catch what constraints
cannot express. Both layers are doing work.

### Lineage: every table, every hop

```
data/adult.csv                             32,561 rows, sha256 recorded per run
  |
  |  scripts/ingest.py, stage()
  |  record_key = md5(all 15 source fields) + '#' + occurrence number
  |  append-only, anti-joined on record_key, so a re-read stages nothing
  v
stg_adult                                  every field as text, nothing cleaned
  |
  |  sql/03_load.sql, step 1: the ten rules
  |-------------------------------> load_reject    one row per (record_key, reason)
  |                                      |
  |                                      v
  |                                 quarantine     the refused record, whole
  |
  |  sql/03_load.sql, step 2: dimension upserts
  |-------------------------------> dim_workclass, dim_education, dim_marital_status,
  |                                 dim_occupation, dim_relationship, dim_race,
  |                                 dim_sex, dim_native_country
  |                                 new members only, surrogate keys never renumbered
  |
  |  sql/03_load.sql, step 3: fact load
  |  '?' resolves to the reserved member, never NULL
  |  top-coded measures load as NULL beside a boolean flag
  v
fact_person                                32,561 rows, 8 NOT NULL foreign keys
  |                                        carries load_run_id, source_file, source_row
  |  sql/05_analytic.sql, applied by the ingester
  v
analytic_person                            the one published table, 24 columns
  |
  |--> scripts/benchmark.py       CSV, Parquet and PostgreSQL copies for Lab 4
  |--> cloud/adult_analytic.csv   the BigQuery upload for Lab 5

load_run     one row per execution: file, sha256, byte count, rows read, staged,
             rejected, loaded, start, finish, status
```

Every arrow is written down, which is what Unit 6 asks for. Two properties follow from it.

**Any published number traces back to a line of a file.** `fact_person` carries
`load_run_id`, `source_file` and `source_row`, and `load_run` carries the sha256 and byte
count of the file that run read. A figure in `analytic_person` resolves to a fact row, to a
staged record, to a numbered line of a file whose hash is on record. Lab 1 concluded that a
decision spending public money had no auditable lineage, and this is the part that fixes it.

**Nothing leaves the pipeline unaccounted for.** Every staged row is either in `fact_person`
or in `quarantine`, which the `staged_rows_all_accounted` assertion checks on every run. The
failure mode it exists to prevent is silent deletion, where rows disappear and the only
evidence is a row count nobody compared.

Lab 6 also closed a gap in this map. `analytic_person` was previously created on demand by
`scripts/benchmark.py` rather than by the pipeline, so a fresh clone that ran the ingester had
a star and no serving view, and the hop above would have described something the pipeline did
not do. `05_analytic.sql` is now applied by the ingester.

### PDPA: does this dataset contain personal data?

Unit 6 gives a four-step walkthrough. Running it honestly on this project gives two different
answers, and the difference between them is the point.

**Step 1, is there personal data?** Not in this file. `adult.csv` is an extract of the 1994
United States Census Bureau Current Population Survey, published by the UCI repository as
public-use microdata. It carries no name, address, identifier or contact detail, and no data
subject in it is Tanzanian.

That is not quite the end of the question, because the file is person-level and its columns
are quasi-identifiers: age, sex, race, education, occupation, marital status, native country,
hours and capital gains, in combination, could in principle single someone out. The
reassuring part is that the publisher already applied disclosure control, and Lab 1 found the
evidence without knowing that was what it was looking at. The three top-coded measures, `age`
capped at 90 with zero records at 89, `capital.gain` at 99999 and `hours.per.week` at 99, are
exactly that: the tails compressed into one bucket so the unusual respondent cannot be picked
out. What Lab 1 recorded as a data quality defect is anonymisation doing its job. Both
readings are true at once, which is why those measures are kept, flagged and excluded from
averages rather than deleted.

**Steps 2 to 4 do not bite on this file**, because step 1 answered no. Stating a purpose,
identifying a lawful basis, minimising the fields and documenting retention are obligations
that attach to personal data, and there is none here. Claiming to comply would misrepresent
what the law requires.

**They would bite on the pipeline Lab 1 described.** The consumer in the Lab 1 problem
statement receives outcome records back from delivery providers, and those are about
identifiable living people in Tanzania. For that system the Personal Data Protection Act, No.
11 of 2022 applies in full, and the obligations are concrete rather than decorative:

- **Register with the Personal Data Protection Commission before processing begins.** Unit 6
  is blunt that being small is not an exemption. This is a prerequisite, not a closing task.
- **State the purpose in writing and identify a lawful basis**, then keep only the fields that
  purpose needs. Minimisation is a constraint on the schema, not a cleanup job afterwards.
- **Keep it inside compliant borders.** Lab 5 chose `africa-south1` deliberately rather than
  accepting a default, and recorded why. Under the PDPA a transfer outside Tanzania needs
  adequate protection and a prior transfer permit from the Commission, so the region is a
  legal decision before it is a latency one.
- **Separate identity from analysis.** The 87-row segment summary this project produces
  contains no personal data at all. A design that keeps identifiable records in one place and
  publishes only aggregates reduces both the legal surface and the cost.

The honest summary is that this repository holds no personal data and owes the PDPA nothing,
while the system it rehearses would owe it a great deal. Saying the first without the second
would misread the law. Saying the second without the first would be compliance theatre over a
public teaching file.

## Reproducing the Lab 1 figures

```bash
python -c "import pandas as pd; df=pd.read_csv('data/adult.csv'); print((df=='?').sum()); print(df.duplicated().sum())"
```

Requires pandas. Developed against Python 3.14 and pandas 3.0.3.

## Repository layout

```
data/         source data, unmodified
docs/         lab deliverables
notebooks/    exploratory analysis
presentations/ slide decks
scripts/      the ingester and the quality checks
sql/          schema, quality rules and queries
tests/        fixtures that exercise the reject and quarantine paths
cloud/        generated CSV export for the sandbox, not committed
warehouse/    generated DuckDB file, not committed
```


