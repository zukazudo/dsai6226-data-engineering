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
```

## Conventions

Lab deliverables are Word documents under `docs/`. This README is the only Markdown file in
the repository.

Later labs use DuckDB as the primary engine. Lab 4 additionally benchmarks PostgreSQL because
the exercise requires the comparison, and that container is not a dependency of anything else
here.
