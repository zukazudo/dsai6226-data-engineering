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

[`presentations/Team_E_Labs_1_to_9.pptx`](presentations/Team_E_Labs_1_to_9.pptx)

Thirty-nine slides covering all nine labs as one argument: three defects found, the decisions
that close them, the engine the evidence points at, what the same question costs in a cloud
warehouse, the checks that guard it, the one tap it serves, the feature table it feeds, and
the measurement that showed where the time actually goes. Section dividers mark the lab
boundaries. Speaker notes are on every slide.

A fuller written companion is
[`docs/Team_E_Revision_Guide_Labs_1_to_9.docx`](docs/Team_E_Revision_Guide_Labs_1_to_9.docx):
twenty-six pages, one section per lab in the same rhythm, with a command reference and every
figure worth memorising.

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

## Lab 7: open one clean tap

Deliverable: the serving table [`sql/07_mart.sql`](sql/07_mart.sql), the definitions in
[`metrics.md`](metrics.md), and the consumer view in [`app.py`](app.py).

```bash
python scripts/ingest.py      # publishes mart_segment_allocation, 87 segments
python -m streamlit run app.py   # the dashboard, on localhost:8501
python scripts/dashboard.py   # optional: a static snapshot to docs/dashboard.html
```

Run it through `python -m` rather than the bare `streamlit` command. pip installs a
`streamlit.exe` into the interpreter's Scripts directory, and on a default Windows Python that
directory is frequently not on PATH, so `streamlit run app.py` reports that it is not
recognised even though the package is installed and working. `python -m streamlit` uses the
interpreter that is already running and needs no PATH entry.

The dashboard is a Streamlit app. The HTML export is not a second dashboard: it renders the
same table for the cases the app cannot cover, such as attaching a dated snapshot to a
submission or reading the numbers without a Python environment. Both read
`mart_segment_allocation`, neither computes a metric of its own, and the export imports its
freshness thresholds from the app rather than keeping a second copy, so they cannot disagree.

### The table

`mart_segment_allocation`, one row per occupation and education group, 87 rows. It answers the
question Lab 1 was written around: where in the eligible pool are the candidates, and which of
those segments can carry a decision.

It is not `analytic_person`. That table is row level, exists so a flat file can answer the
same question the star answers, and feeds the Lab 4 benchmark and the Lab 5 export. A serving
table is a different thing: small, aggregated to a stated grain, and documented. Unit 7 calls
the alternative the anti-pattern, where every chart computes its own numbers from raw data and
ten charts produce ten slightly different answers.

| Column | What it is |
|---|---|
| `occupation`, `education_group` | the grain |
| `candidates` | people in the segment; sums to 16,005 |
| `pct_of_eligible_pool` | share of the pool; sums to 100 |
| `mean_hours`, `mean_capital_gain` | averages, top-coded values excluded |
| `hours_measured_on`, `capital_gain_measured_on` | how many records each mean used |
| `segment_is_sparse` | fewer than 30 people; 20 of 87 |
| `occupation_is_assignable` | false where the occupation is Unknown or Not applicable; 7 of 87 |
| `refreshed_at`, `source_load_run_id` | when it was built, and by which run |

Two of those columns exist because a number alone can mislead. Publishing `mean_hours` without
`hours_measured_on` hides the difference between a mean over 12 records and a mean over 1,200.
Publishing `candidates` without `segment_is_sparse` lets a chart put a seven-person segment
beside a seven-hundred-person one with no visible difference.

Nothing is filtered out of the table that belongs in the pool. The seven unassignable segments
are published and flagged rather than dropped, because dropping them would stop the segment
counts summing to 16,005, and a total that does not reconcile is how two dashboards begin to
disagree.

### It is a table, not a view, and that was the decision

A view would be recomputed on every read and could therefore never be stale. That sounds like
an advantage until the lab asks for a freshness label, at which point a view makes the label
meaningless: it would always say fresh, whatever had happened to the pipeline, and a label
that cannot go stale is worse than no label because it is a promise nothing can break.

Materialising the table means it carries `refreshed_at`, the moment it was actually built. A
pipeline that stops running leaves a table that visibly ages. `scripts/ingest.py` rebuilds it
on every run, so "refreshed by the pipeline itself" is a fact rather than an aspiration, and
`--reset` drops it so a stale serving table cannot survive a rebuild.

### metrics.md

Every published number has a formula, a grain, filters and an owner in
[`metrics.md`](metrics.md). The file also defines the one thing most likely to be applied two
different ways: the eligible pool is `income_gt_50k = FALSE AND age BETWEEN 25 AND 54`, which
is 16,005 of 32,561 records, and it is applied once in the mart and nowhere else.

Unit 7's worked example is three teams reporting 82, 76 and 88 per cent coverage in the same
meeting, each correct by its own definition. The cure is not a better chart. It is one written
definition computed once.

### The consumer view

[`app.py`](app.py), a Streamlit app over the serving table. Against the five hygiene rules
Unit 7 sets out:

| Rule | What the view does |
|---|---|
| Know the audience | the Lab 1 consumer allocating support, who needs segments and their trustworthiness, not a national trend |
| One question per view | the heading is the question: where are the eligible candidates |
| Show freshness | a computed banner, below |
| Offer a drill path | pool totals, then charts, then all 87 segments, with filters by occupation and education group and toggles for the sparse and unassignable rows |
| Cut the junk | no gauges, no pie charts; one bar chart, one scatter, one table |

The drill path is the rule this view was built to satisfy and the reason it is an app rather
than a page. Selecting Craft-repair narrows the view to its six segments and 2,453 candidates,
and the tiles show both the pool total and what is currently in scope, so narrowing the view
never hides what it is a fraction of.

The app computes nothing. Every figure on it is a column of the serving table, which is the
rule Unit 7 exists to enforce.

### The app never holds a lock on the warehouse

This mattered more here than anywhere else in the project. DuckDB allows many readers or one
writer, and a Streamlit server is a long-lived process: a connection held open by the
dashboard, even a read-only one, makes the next `python scripts/ingest.py` fail with
`Could not set lock on file`. The project's notes already record a Lab 3 notebook hitting
exactly that.

A dashboard that blocks the pipeline could not perform its own demonstration, because the
demonstration is to break the refresh, run the pipeline, and watch the banner clear. So the
app copies the warehouse to a temporary file, reads the copy, and closes it immediately. The
copy is keyed on the warehouse's modification time, so a pipeline run invalidates the cache
and the next interaction shows new data. Reading bytes takes no DuckDB lock at all.

Verified rather than assumed: with the app serving on port 8501, a full
`python scripts/ingest.py --reset`, which drops and rebuilds every table, completed normally.

### Breaking the refresh on purpose

The label is computed from `max(refreshed_at)`, never typed. Thresholds: fresh under 24 hours,
ageing up to seven days, stale beyond that.

To prove it reacts, the last successful refresh was pushed back nine days directly in the
warehouse, which is what a pipeline that silently stopped running would leave behind. The
dashboard was then rebuilt with no override of any kind:

```
UPDATE mart_segment_allocation SET refreshed_at = refreshed_at - INTERVAL 9 DAY
python scripts/dashboard.py
```

```
87 segments, freshness STALE, data 216.0 h old

STALE
Data is 9.0 days old. This view is not current and should not be used for a
decision until the pipeline has run.
```

The banner turns from green to red and the sentence changes from a reassurance to an
instruction. Running the pipeline again clears it without anyone editing the page:

```
python scripts/ingest.py     # published 87 segments to mart_segment_allocation
```

The banner returns to green on the next interaction, with no restart and nothing edited. That
recovery is the half worth noticing: the label is a function of the data, so it goes stale on
its own and clears on its own. `--stale-hours` exists on the export script for rehearsing the
other thresholds, but the run above used none of it.

### A bug worth recording

The first version of the app cached its snapshot on a parameter named `_mtime`. Streamlit
treats a leading underscore on a `cache_data` argument as do not hash this, so the one value
the cache key existed for was excluded, and the page served the first snapshot it ever read.
It looked perfectly correct until the pipeline ran and the banner refused to move. Renaming it
to `mtime` fixed it. A caching bug is invisible exactly when the data has not changed, which is
most of the time.

## Lab 8: build an honest feature table

Deliverable: the feature table [`sql/08_features.sql`](sql/08_features.sql), the split strategy
below, and [`DATASHEET.md`](DATASHEET.md).

```bash
python scripts/ingest.py   # rebuilds feature_person, 32,561 rows, 21 columns
```

Target: `income_gt_50k`. Base rate 24.1 per cent.

### Ten engineered features, each with a story

| Feature | The story |
|---|---|
| `age_band` | Earnings rise with age and then plateau. A linear term cannot express a plateau. |
| `education_num` | The ordered ladder, kept. The education label is dropped: same fact, second spelling. |
| `education_vs_occupation_median` | Being over-educated for your trade is a different situation from being well educated, and the raw level cannot say which. |
| `hours_vs_occupation_median` | Forty hours means one thing where the median is 40 and another where it is 25. |
| `is_full_time` | The part-time boundary is a real employment threshold and carries more than the exact hours either side of it. |
| `workclass_group` | Nine values collapse to four that behave differently: private, government, self-employed, other. |
| `is_partnered` | Seven marital states collapse to the one distinction that matters for household income. |
| `country_is_united_states` | 42 countries with a long tail become the distinction that carries signal, rather than forty categories to memorise. |
| `occupation_is_assignable` | The `?` handling travels into the model rather than being quietly imputed. |
| `age_was_censored`, `hours_was_censored` | Unit 8: filling a gap silently teaches the model that gaps never happen. The fact of censoring is a column. |

### The leakage audit, column by column

Unit 8's question is whether a value would have been knowable at the moment the prediction had
to be made. Four columns failed it, and one failed badly enough to be worth evidence.

**`capital.gain` and `capital.loss` are cut for target leakage.** They are not predictors of
income above 50,000 dollars; they are a component of the income that defines the answer. The
data says so plainly:

| `capital_gain` | Records | Above 50K |
|---|---:|---:|
| zero | 29,849 | 20.7% |
| 1 to 999 | 55 | 0.0% |
| 1,000 to 4,999 | 1,009 | 17.9% |
| 5,000 to 9,999 | 878 | 84.3% |
| 10,000 and over | 611 | 97.7% |
| **top-coded at 99999** | **159** | **100.0%** |

Against a base rate of 24.1 per cent. Above five thousand the column is close to deterministic,
and at the censored value it is a perfect predictor of 159 records. Unit 8's definition of
target leakage is a feature that is a disguised copy of the answer, and this is one. Keeping it
would have produced a model that scores well, learns nothing, and fails the moment it meets a
population whose income is reported differently.

**`fnlwgt` is cut because it is not a property of the person.** It is the Census Bureau's
post-stratification weight, derived from known population totals rather than observed from the
respondent. It encodes demographic information about how people like this one were sampled,
which is a route for information to enter the model that has nothing to do with the individual
whose income is being predicted. Lab 1 already established it is not an identifier either:
21,648 distinct values over 32,561 rows.

**`education` is cut as redundant**, not leaky. It is the same ladder as `education_num` in
words, and two spellings of one fact adds nothing.

**Pipeline metadata is cut**: `record_key`, `source_file`, `source_row`, `load_run_id` and
`duplicate_seq` are facts about how the row arrived, not about the person. `person_sk` and
`duplicate_group_id` stay in the table but are not features. The first exists so a prediction
can be traced back to a row, a file and a line; the second is what makes the split honest.

**One column survived the audit that deserves a note.** `relationship` overlaps `marital_status`
heavily, since `Husband` and `Wife` encode both a marital state and a sex. It is kept because
it also encodes household position, which the other two do not, but anyone reading feature
importances should know the three columns are entangled.

### The split strategy, written before any modelling

**It is not time-based, and that is forced rather than chosen.** Unit 8 is clear that
time-ordered data must be split by time. This dataset is a single 1994 snapshot with no date
column of any kind, so there is no past to train on and no future to be judged against. The
honest thing is to say so rather than to invent an ordering. This is the same absence Lab 5
ran into from the other direction: with no date column there is nothing to partition on in
BigQuery either, which is why our column-pruning saving was three times rather than the
hundred the lecture quotes. One missing column, two labs, two different consequences.

**It is group-aware, and on this dataset that is the only real leakage risk left.** 47 records
sit in 23 groups of byte-identical rows, retained deliberately since Lab 2 because nothing
proves they are errors. If a group straddles the boundary, the model sees the same person in
training and is tested on a copy of them, which is Unit 8's split leakage exactly: the model
memorises individuals instead of learning patterns. The hash is therefore taken over
`duplicate_group_id` where there is one, so every member of a group lands on the same side.

**It is stratified, and deterministic without a seed.** The assignment is
`hash(group key || target) % 100`, with 0 to 59 train, 60 to 79 validate, 80 to 99 test.
Hashing is uniform, and applying it within each class of the target preserves the base rate in
all three parts without a separate stratification step. There is no shuffle and therefore no
seed to lose: the same rows land in the same places on any machine and in any engine, which is
a stronger reproducibility guarantee than a recorded seed.

The result:

| Split | Rows | Share | Above 50K |
|---|---:|---:|---:|
| train | 19,636 | 60.3% | 23.91% |
| validate | 6,483 | 19.9% | 24.36% |
| test | 6,442 | 19.8% | 24.34% |
| **whole table** | **32,561** | 100% | **24.08%** |

**Duplicate groups straddling a split boundary: 0.** Checked by
`python scripts/validate_labs.py`, because a property that is only true until someone edits the
SQL is not a property.

The test set has not been looked at. No model has been trained, which is the cleanest way to
keep that true, and the split was written down before any of this was built, which is the order
Unit 8 asks for.

### One compromise, stated rather than buried

`education_vs_occupation_median` and `hours_vs_occupation_median` compare each person against a
median computed over the **whole** table, not over the training split. Strictly, Unit 8's rule
against preprocessing before splitting says those medians should be fitted on train only.

The reason it is acceptable here, and the reason it is written down anyway: these are
population norms of a closed 1994 census extract, not parameters learned from a target, and the
split is assigned by a hash that nothing about these values influences. The leakage that rule
guards against is test-set statistics flowing into training, and a median of hours worked per
occupation is not a test-set statistic in any meaningful sense. If this table were ever rebuilt
over a live, growing source, the medians would have to move to a train-only fit. That is
recorded here so the next person inherits the decision rather than the assumption.

## Lab 9: find it, fix it, prove it

Deliverable: the profiler [`scripts/profile_pipeline.py`](scripts/profile_pipeline.py), the
table below, and the before and after numbers for the one change that was kept.

```bash
python scripts/profile_pipeline.py --repeats 3
```

It builds into a throwaway database, so profiling never disturbs the warehouse anyone is using.

### The profile

Median of three full rebuilds, AMD Ryzen 5 PRO 5650U, 15.3 GB RAM, Windows 11, DuckDB 1.5.5.

| Step | Seconds | Share | Rows in | Rows out |
|---|---:|---:|---:|---:|
| schema (DDL) | 0.046 | 1.8% | 0 | 0 |
| read CSV and hash | 0.285 | 11.2% | 32,561 | 32,561 |
| stage (anti-join) | 0.333 | 13.1% | 32,561 | 32,561 |
| validate (10 rules) | 0.053 | 2.1% | 32,561 | 0 |
| quarantine rows | 0.005 | 0.2% | 0 | 0 |
| dimension upserts | 0.077 | 3.0% | 32,561 | 0 |
| **fact load** | **1.544** | **60.5%** | 32,561 | 32,561 |
| refresh mart | 0.048 | 1.9% | 32,561 | 87 |
| build features | 0.161 | 6.3% | 32,561 | 32,561 |
| **total** | **2.552** | | | |

**The bottleneck is the fact load, at 61 per cent.** Nothing else is close: the second slowest
step is a fifth of its size, so even eliminating every other step entirely could win less than
40 per cent of the total.

### The profiler had a bug, and the bug changed the answer

The first version grouped statements by label into a dictionary before timing them. That
silently reordered them: the fact insert mentions `load_reject` in its CTE, so it was grouped
with the validation statement and therefore ran **before** the dimension upserts it depends on.
It inserted nothing. The mart and feature steps then measured empty tables, and the profile
looked entirely plausible:

| | First, buggy profile | Corrected profile |
|---|---|---|
| Named bottleneck | stage (anti-join), 30% | fact load, 61% |
| Total | 1.140 s | 2.552 s |

The wrong answer was not obviously wrong. It identified a real step, gave it a believable
share, and would have sent the whole lab off to optimise something that was never the problem.
A profiler that reorders the work is not profiling the work, and statements are now timed in
file order and never regrouped.

### What the fact load actually spends its time on

Having found the dominant step, the next question is which part of it dominates. Measured by
rebuilding the warehouse with pieces removed:

| Variant | Fact load |
|---|---:|
| as shipped | 1.450 s |
| without the `UNIQUE` on `record_key` | 1.394 s |
| without the eight foreign keys | 0.483 s |

**Two thirds of the fact load is foreign key enforcement**, roughly 40 per cent of the whole
pipeline. Insert timings at three batch sizes confirm it is per row, about 18 microseconds
each, which for 32,561 rows across eight keys is 260,488 index probes.

**That cost is not being removed.** Lab 6 tried to corrupt this warehouse five ways and three
of the attempts were refused by these very constraints before any quality check saw them. The
foreign keys are not overhead that happens to be slow; they are the reason a class of
corruption cannot occur. Trading them for 40 per cent of a 2.5 second rebuild would be a bad
bargain, and the honest thing is to say that the dominant cost is one we have chosen to pay.

### Six rewrites that did not work

The dominant step having a cost we keep does not excuse not trying. Each of these was
implemented, measured against the version it replaced, and reverted:

| Attempted change | Result |
|---|---|
| Materialise the fact rows into a temp table, then insert plainly | 4% slower |
| Drop `UNIQUE` on `record_key`, a 34 character VARCHAR index | 0.056 s, under 4% |
| Materialise `analytic_person` instead of leaving it a view | 35% slower |
| Replace `md5()` with the 64-bit `hash()` in the record key | saves 0.017 s, 0.7% |
| Restrict the duplicate-group scan to hashes in the arriving batch | 2% slower |
| Restrict `admissible` to the current run's staged rows | 7% slower |

Two of those deserve a sentence, because the reasoning behind them was sound and the
measurement still said no.

**Materialising the view looked obviously right.** `analytic_person` is an eight-way join read
by both the mart and the feature build, so it appeared to be doing the same joins twice per
load. Materialising it cost 35 per cent more: DuckDB inlines and optimises the view into each
consumer, and paying to write 32,561 rows to disk is worse than letting the planner handle it.

**The incremental scan looked like the clearest defect of all.** Adding 200 rows to the
warehouse took 1,773 ms against 4,043 ms for the original 32,561, about seventy times the work
per row, which is exactly Unit 9's description of a step reading far more than it returns. Two
separate rewrites targeted it. Both were slower, and a diagnostic variant that removed the
scan **entirely** was also no faster, which settled it: the scan was never the cost. The
intuition was good, the hypothesis was testable, and the test said no twice.

### The change that was kept

`sql/08_features.sql` computes a per-occupation median of hours and education before building
the feature rows. It read those three columns through `analytic_person`, the eight-way join
view, which asked the engine to resolve seven dimension joins whose output is immediately
discarded, on a second pass over rows the main statement already scans. It now reads
`fact_person` with the two dimensions that actually supply those columns.

| | Feature build |
|---|---:|
| **before**, medians via the 24-column view | **184.1 ms** |
| **after**, medians via the star and two dimensions | **161.0 ms** |
| | **12.5% faster** |

Median of five full rebuilds. The fingerprint of `feature_person`, hashed over `person_sk` and
both derived columns, is identical before and after, so the table is unchanged and only the
route to it is shorter.

**Why it worked.** Columnar engines read only the columns a query references, and a join is
only free if its output is never needed. Reading through the view defeated both: the planner
had to produce the view's shape before the aggregate could discard most of it. Naming the two
tables that hold the three required columns removed six joins from a pass over 32,561 rows.
This is Unit 9's first cheap win, select only what you need, applied to a join rather than to a
column list.

**Why it is small, and why that is the honest headline.** It is 0.9 per cent of the pipeline. A
12 per cent improvement to a step worth 7 per cent cannot be more. The lab asks for one
deliberate improvement and this is the one that survived measurement, but the finding worth
carrying away is the other one: the dominant step is dominated by a correctness guarantee, six
attempts to make it cheaper all failed, and the only honest report of that is the one that
shows the failures. Optimisation without measurement is superstition with extra effort, and so
is optimisation that only publishes the attempts that worked.

## Reproducing the Lab 1 figures

```bash
python -c "import pandas as pd; df=pd.read_csv('data/adult.csv'); print((df=='?').sum()); print(df.duplicated().sum())"
```

Requires pandas. Developed against Python 3.14 and pandas 3.0.3.

## Repository layout

```
data/         source data, unmodified
docs/         lab deliverables and the generated dashboard
notebooks/    exploratory analysis
presentations/ slide decks
DATASHEET.md  what this dataset is, and what it is not
metrics.md    the definition of every published number
app.py        the dashboard, run with streamlit
scripts/      the ingester, the checks, the HTML export and the profiler
sql/          schema, quality rules, the serving table and queries
tests/        fixtures that exercise the reject and quarantine paths
cloud/        generated CSV export for the sandbox, not committed
warehouse/    generated DuckDB file, not committed
```


