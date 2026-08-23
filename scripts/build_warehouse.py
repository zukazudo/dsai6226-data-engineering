"""Build the DuckDB warehouse from adult.csv.

Runs the SQL files in sql/ in order and prints the row counts each step
produced. Run from the repository root:

    python scripts/build_warehouse.py

Lab 3 replaces this with a proper ingester that is idempotent, logs rejected
rows and can be re-run against a moving source. This one just gets the schema
built so it can be queried.
"""

import sys
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "warehouse" / "adult.duckdb"
SQL_DIR = ROOT / "sql"
STEPS = ["01_staging.sql", "02_schema.sql", "03_load.sql"]


def main() -> int:
    csv_path = ROOT / "data" / "adult.csv"
    if not csv_path.exists():
        print(f"error: {csv_path} not found", file=sys.stderr)
        return 1

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))

    # The SQL refers to data/adult.csv relative to the repository root.
    con.execute(f"SET file_search_path = '{ROOT.as_posix()}'")

    for name in STEPS:
        path = SQL_DIR / name
        started = time.perf_counter()
        con.execute(path.read_text(encoding="utf-8"))
        elapsed = (time.perf_counter() - started) * 1000
        print(f"  {name:<20} {elapsed:7.0f} ms")

    print()
    tables = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main' ORDER BY table_name"
    ).fetchall()]
    for t in tables:
        n = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        print(f"  {t:<22} {n:>7,} rows")

    con.close()
    print(f"\nwarehouse written to {DB_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
