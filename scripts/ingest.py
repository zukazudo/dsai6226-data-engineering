"""Re-runnable ingester for the Adult census dataset.

    python scripts/ingest.py

Reads a CSV drop, lands it in staging, validates it, and loads what passes into
the star schema. Running it twice loads nothing the second time.

Idempotency comes from `record_key`. The source has no identifier of any kind,
which was the third defect in the Lab 1 problem statement, so identity is
manufactured from content: an md5 of all 15 source fields, suffixed with the
occurrence number of that exact content within the file. Re-reading the same
file reproduces exactly the same keys, and the fact insert skips keys it
already holds.

That suffix is what lets the 24 known duplicate rows survive. Three identical
records become <hash>#1, <hash>#2 and <hash>#3 rather than colliding into one.

The limitation is worth stating plainly: two genuinely different people who
match on all 15 attributes are indistinguishable to this scheme, exactly as
they were indistinguishable to Lab 1. The ingester inherits that ambiguity from
the source and does not pretend to resolve it.

Usage:
    python scripts/ingest.py                      # ingest data/adult.csv
    python scripts/ingest.py path/to/file.csv     # ingest one file
    python scripts/ingest.py path/to/directory/   # ingest every .csv in it
    python scripts/ingest.py --reset              # drop everything and rebuild
    python scripts/ingest.py --summary            # print state and exit
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"
DEFAULT_DB = ROOT / "warehouse" / "adult.duckdb"
DEFAULT_SOURCE = ROOT / "data" / "adult.csv"

DDL_FILES = ["01_staging.sql", "02_schema.sql"]
LOAD_FILE = "03_load.sql"

# The 15 source columns, in file order, with the snake_case names used from
# staging onward. The source uses dots, which must be quoted in every statement
# that touches them, so they are renamed once at the boundary. Values are never
# altered here.
COLUMNS = [
    ("age", "age"),
    ("workclass", "workclass"),
    ("fnlwgt", "fnlwgt"),
    ("education", "education"),
    ("education.num", "education_num"),
    ("marital.status", "marital_status"),
    ("occupation", "occupation"),
    ("relationship", "relationship"),
    ("race", "race"),
    ("sex", "sex"),
    ("capital.gain", "capital_gain"),
    ("capital.loss", "capital_loss"),
    ("hours.per.week", "hours_per_week"),
    ("native.country", "native_country"),
    ("income", "income"),
]

log = logging.getLogger("ingest")


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def sha256_of(path: Path) -> tuple[str, int]:
    """Digest and size of a file, recorded so a run can prove what it read."""
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def apply_ddl(con: duckdb.DuckDBPyConnection) -> None:
    for name in DDL_FILES:
        con.execute((SQL_DIR / name).read_text(encoding="utf-8"))
    log.debug("schema present")


def reset(con: duckdb.DuckDBPyConnection) -> None:
    log.warning("--reset: dropping every table and sequence")
    for t in [
        "fact_person", "load_reject", "stg_adult", "load_run",
        "dim_workclass", "dim_education", "dim_marital_status", "dim_occupation",
        "dim_relationship", "dim_race", "dim_sex", "dim_native_country",
    ]:
        con.execute(f"DROP TABLE IF EXISTS {t}")
    con.execute("DROP SEQUENCE IF EXISTS seq_load_run")


def begin_run(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    digest, size = sha256_of(path)
    run_id = con.execute("SELECT nextval('seq_load_run')").fetchone()[0]
    con.execute(
        """
        INSERT INTO load_run (load_run_id, started_at, source_file,
                              source_sha256, source_bytes, status)
        VALUES (?, ?, ?, ?, ?, 'running')
        """,
        [run_id, datetime.now(timezone.utc), path.name, digest, size],
    )
    log.info("run %d: %s (%s bytes, sha256 %s)", run_id, path.name, f"{size:,}", digest[:12])
    return run_id


def stage(con: duckdb.DuckDBPyConnection, path: Path, run_id: int) -> tuple[int, int]:
    """Land the file. Returns (rows read, rows newly staged).

    Staging is append-only and keyed by record_key, so re-reading a file adds
    nothing. The raw record therefore accumulates across drops rather than
    being replaced.
    """
    select_cols = ",\n        ".join(
        f'"{src}" AS {dst}' for src, dst in COLUMNS
    )
    hash_args = ", ".join(f'"{src}"' for src, _ in COLUMNS)

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE incoming AS
        SELECT
            md5(concat_ws('|', {hash_args})) AS content_hash,
            CAST(row_number() OVER () AS INTEGER) AS source_row,
            {select_cols}
        FROM read_csv(?, all_varchar = true, header = true)
        """,
        [str(path)],
    )
    rows_read = con.execute("SELECT count(*) FROM incoming").fetchone()[0]

    # record_key = content hash plus the occurrence number of that content.
    #
    # The anti-join must alias both sides. An unqualified `record_key` inside
    # the NOT EXISTS binds to the subquery's own column, making the predicate
    # `s.record_key = s.record_key`, which is true for every existing row and
    # silently blocks all further staging.
    dst_cols = ", ".join(dst for _, dst in COLUMNS)
    con.execute(
        f"""
        INSERT INTO stg_adult
        WITH keyed AS (
            SELECT
                i.content_hash || '#' ||
                    CAST(row_number() OVER (PARTITION BY i.content_hash
                                            ORDER BY i.source_row) AS VARCHAR) AS record_key,
                i.source_row,
                {dst_cols}
            FROM incoming i
        )
        SELECT k.record_key, {run_id}, ?, k.source_row, {dst_cols}
        FROM keyed k
        WHERE NOT EXISTS (
            SELECT 1 FROM stg_adult s WHERE s.record_key = k.record_key
        )
        """,
        [path.name],
    )
    staged = con.execute(
        "SELECT count(*) FROM stg_adult WHERE load_run_id = ?", [run_id]
    ).fetchone()[0]
    con.execute("DROP TABLE IF EXISTS incoming")
    return rows_read, staged


def transform(con: duckdb.DuckDBPyConnection, run_id: int) -> None:
    sql = (SQL_DIR / LOAD_FILE).read_text(encoding="utf-8")
    con.execute(sql.replace("$run_id", str(run_id)))


def finish_run(con: duckdb.DuckDBPyConnection, run_id: int, counts: dict) -> None:
    con.execute(
        """
        UPDATE load_run SET finished_at = ?, rows_read = ?, rows_staged = ?,
               rows_rejected = ?, rows_loaded = ?, rows_already_present = ?,
               status = 'ok'
        WHERE load_run_id = ?
        """,
        [
            datetime.now(timezone.utc), counts["read"], counts["staged"],
            counts["rejected"], counts["loaded"], counts["already"], run_id,
        ],
    )


def fail_run(con: duckdb.DuckDBPyConnection, run_id: int) -> None:
    con.execute(
        "UPDATE load_run SET finished_at = ?, status = 'failed' WHERE load_run_id = ?",
        [datetime.now(timezone.utc), run_id],
    )


def report_rejects(con: duckdb.DuckDBPyConnection, run_id: int) -> None:
    rows = con.execute(
        """
        SELECT reason, count(*) AS n, min(detail) AS example
        FROM load_reject WHERE load_run_id = ?
        GROUP BY reason ORDER BY n DESC
        """,
        [run_id],
    ).fetchall()
    if not rows:
        log.info("  rejected     0")
        return
    total = sum(r[1] for r in rows)
    log.warning("  rejected %5d, by reason:", total)
    for reason, n, example in rows:
        log.warning("      %-32s %5d   e.g. %s", reason, n, example)


def ingest_file(con: duckdb.DuckDBPyConnection, path: Path) -> dict:
    started = time.perf_counter()
    run_id = begin_run(con, path)
    try:
        rows_read, staged = stage(con, path, run_id)

        before = con.execute("SELECT count(*) FROM fact_person").fetchone()[0]
        transform(con, run_id)
        after = con.execute("SELECT count(*) FROM fact_person").fetchone()[0]

        rejected = con.execute(
            "SELECT count(DISTINCT record_key) FROM load_reject WHERE load_run_id = ?",
            [run_id],
        ).fetchone()[0]

        loaded = after - before
        already = rows_read - staged

        counts = {
            "read": rows_read, "staged": staged, "rejected": rejected,
            "loaded": loaded, "already": already,
        }
        finish_run(con, run_id, counts)

        log.info("  rows read %5d", rows_read)
        log.info("  staged    %5d  (%d already present from an earlier run)", staged, already)
        report_rejects(con, run_id)
        log.info("  loaded    %5d", loaded)
        log.info("  fact_person now holds %s rows  (%.0f ms)",
                 f"{after:,}", (time.perf_counter() - started) * 1000)
        return counts
    except Exception:
        fail_run(con, run_id)
        log.exception("run %d failed", run_id)
        raise


def print_summary(con: duckdb.DuckDBPyConnection) -> None:
    print()
    print("load history")
    print("-" * 78)
    rows = con.execute(
        """
        SELECT load_run_id, source_file, rows_read, rows_staged,
               rows_rejected, rows_loaded, status
        FROM load_run ORDER BY load_run_id
        """
    ).fetchall()
    print(f"{'run':>4}  {'source':<28} {'read':>7} {'staged':>7} {'rejected':>9} {'loaded':>7}  status")
    for r in rows:
        print(f"{r[0]:>4}  {str(r[1])[:28]:<28} {r[2]:>7} {r[3]:>7} {r[4]:>9} {r[5]:>7}  {r[6]}")

    print()
    print("table sizes")
    print("-" * 78)
    for t in [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main' ORDER BY table_name"
    ).fetchall()]:
        n = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        print(f"  {t:<24} {n:>8,}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Re-runnable ingester for the Adult dataset.")
    ap.add_argument("source", nargs="?", default=str(DEFAULT_SOURCE),
                    help="CSV file or directory of CSV files (default: data/adult.csv)")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="DuckDB file to write")
    ap.add_argument("--reset", action="store_true", help="drop everything first")
    ap.add_argument("--summary", action="store_true", help="print state and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    setup_logging(args.verbose)

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))

    if args.summary:
        print_summary(con)
        con.close()
        return 0

    if args.reset:
        reset(con)
    apply_ddl(con)

    source = Path(args.source)
    if source.is_dir():
        files = sorted(source.glob("*.csv"))
        if not files:
            log.error("no .csv files in %s", source)
            return 1
    elif source.is_file():
        files = [source]
    else:
        log.error("source not found: %s", source)
        return 1

    for path in files:
        ingest_file(con, path)

    print_summary(con)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
