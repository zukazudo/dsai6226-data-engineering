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
An average then excludes the censored records automatically instead of being poisoned by them.
The same applies to `age` at 90 and `hours.per.week` at 99. `capital.loss` has no sentinel and
loads unaltered.

**The duplicates are retained, not resolved.** Lab 1 concluded that deduplication is not a
safe automatic operation here, because a data entry duplicate and two genuinely similar people
are indistinguishable without a key. So all 32,561 rows load. The 47 rows that belong to a
duplicate group carry a `duplicate_group_id`, and `duplicate_seq` numbers them in source
order. A deduplicated view is `WHERE duplicate_seq IS NULL OR duplicate_seq = 1`. The decision
is recorded in the data instead of being applied silently at load, and it stays reversible.

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
survive: three identical records become `#1`, `#2` and `#3` instead of collapsing into one.

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

[`presentations/Team_E_Labs_1_to_4.pptx`](presentations/Team_E_Labs_1_to_4.pptx)

Twenty-one slides covering all four labs as one argument: three defects found, the two
decisions that close them, and the engine the result points at. Section dividers mark the lab
boundaries. Speaker notes are on every slide.

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
scripts/      the ingester
sql/          schema and queries
tests/        fixtures that exercise the reject path
warehouse/    generated DuckDB file, not committed
```


