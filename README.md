# DSAI 6226 Data Engineering, Team E

Coursework repository for the Adult census income dataset.

**Team E:** Mhina Lukurunge, Samwel Mmari, Asajile Mwakyalabwe, Essa Mohamedali, Paschal Bizulu

## The dataset

`data/adult.csv` is the Kaggle mirror of the UCI Adult dataset, an extract of the March 1994
US Current Population Survey. 32,561 records, 15 columns, 4 MB.

The file is committed here so that every result in this repository can be reproduced from a
clone without a Kaggle account. It is the unmodified original, and no cleaning has been
applied to it.

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
sql/01_staging.sql    land the CSV as raw text, no coercion
sql/02_schema.sql     the star: one fact table, eight dimensions
sql/03_load.sql       transform staging into the star, the only place cleaning happens
sql/04_query.sql      the allocation query, plus three supporting counts
```

Build it and query it in one command each:

```bash
python scripts/build_warehouse.py
```

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
  requires columns that describe the record rather than the person. Those belong beside the
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
scripts/      build and load tooling
sql/          schema and queries
warehouse/    generated DuckDB file, not committed
```

## Conventions

Lab deliverables are Word documents under `docs/`. This README is the only Markdown file in
the repository.

DuckDB is the engine for the project. Lab 4 additionally benchmarks PostgreSQL because the
exercise requires the comparison, and that container is not a dependency of anything else here.
