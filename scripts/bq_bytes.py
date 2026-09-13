"""Predict what a BigQuery query will scan, before running it.

    python scripts/bq_bytes.py

BigQuery on-demand pricing bills for bytes READ, and a query reads only the
columns it names. The size of a column is fixed by its type and its contents,
so the figure can be computed from the data without going near the cloud.

Sizes, from the BigQuery data type documentation:

    INT64, FLOAT64, DATE, DATETIME, TIME, TIMESTAMP    8 bytes
    BOOL                                               1 byte
    STRING                                             2 bytes + UTF-8 length
    NULL                                               0 bytes, any type

The last line matters here: the columns carrying censored values are NULL on
the affected rows, so the Lab 2 decision to store NULL beside a flag makes the
table marginally cheaper to scan as well as more correct.

The point of this script is the comparison it prints at the end. Selecting the
six columns the allocation query needs, against selecting all twenty-four,
is the difference the Unit 5 lecture puts at up to a hundredfold.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "warehouse" / "adult.duckdb"
SQL_DIR = ROOT / "sql"

# The columns the allocation aggregate actually reads.
QUERY_COLUMNS = [
    "occupation", "education_group", "hours_per_week",
    "capital_gain", "income_gt_50k", "age",
]

# BigQuery's fixed-width types, mapped from the DuckDB types in our table.
FIXED = {
    "BIGINT": 8, "INTEGER": 8, "SMALLINT": 8, "HUGEINT": 16,
    "DOUBLE": 8, "FLOAT": 8, "DATE": 8, "TIMESTAMP": 8,
    "BOOLEAN": 1,
}
BILLING_MINIMUM = 10 * 1024 * 1024      # BigQuery bills at least 10 MB per table


def column_bytes(con, table: str, name: str, duck_type: str) -> int:
    """Bytes BigQuery would read for one column, NULLs counting as zero."""
    if duck_type in FIXED:
        width = FIXED[duck_type]
        n = con.execute(
            f'SELECT count("{name}") FROM {table}'      # count() skips NULLs
        ).fetchone()[0]
        return n * width
    if duck_type.startswith("VARCHAR"):
        # 2 bytes of length prefix plus the UTF-8 payload, per non-null value.
        # strlen is DuckDB's byte length; length would return characters, which
        # undercounts anything outside ASCII.
        return con.execute(
            f'SELECT coalesce(sum(2 + strlen("{name}")), 0) FROM {table}'
        ).fetchone()[0]
    raise ValueError(f"no BigQuery size rule for {duck_type} on column {name}")


def human(n: int) -> str:
    return f"{n / 1024 / 1024:.2f} MB" if n >= 1024 * 1024 else f"{n / 1024:.1f} KB"


def main() -> int:
    con = duckdb.connect(str(DB_PATH))
    con.execute((SQL_DIR / "05_analytic.sql").read_text(encoding="utf-8"))

    cols = con.execute("DESCRIBE analytic_person").fetchall()
    rows = con.execute("SELECT count(*) FROM analytic_person").fetchone()[0]

    sizes = {}
    for name, duck_type, *_ in cols:
        sizes[name] = column_bytes(con, "analytic_person", name, duck_type)

    total = sum(sizes.values())
    needed = sum(sizes[c] for c in QUERY_COLUMNS)

    print(f"analytic_person: {rows:,} rows, {len(cols)} columns\n")
    print(f"{'column':<28} {'bytes':>12} {'share':>8}   used by the query")
    print("-" * 74)
    for name, _t, *_ in cols:
        b = sizes[name]
        mark = "yes" if name in QUERY_COLUMNS else ""
        print(f"{name:<28} {b:>12,} {b/total*100:>7.1f}%   {mark}")
    print("-" * 74)
    print(f"{'TOTAL, all columns':<28} {total:>12,}   {human(total)}")
    print(f"{'the six the query reads':<28} {needed:>12,}   {human(needed)}")
    print()

    print("What BigQuery would charge for")
    print("-" * 74)
    for label, b in [("SELECT the six needed columns", needed),
                     ("SELECT * (all 24 columns)", total)]:
        billed = max(b, BILLING_MINIMUM)
        floored = "  (raised to the 10 MB minimum)" if billed > b else ""
        print(f"  {label:<32} scans {human(b):>9}   billed {human(billed):>9}{floored}")

    print()
    print(f"  Selecting only what is needed reads {total / needed:.1f} times less data.")
    print(f"  At the on-demand rate of $5 per TB, the whole table is ${total / 1e12 * 5:.6f}.")
    print()
    print("  Both land under the 10 MB per-table billing minimum, so on this dataset")
    print("  the saving is real in bytes and zero in money. The ratio is the lesson;")
    print("  the bill only starts responding once the table is much larger.")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
