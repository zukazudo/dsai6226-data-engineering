"""
Lab 9: profile every step of the pipeline, then prove one fix.

Unit 9's iron rule is measure first, because intuition about where time goes
is usually wrong and a 2x speed-up on a step taking 1 per cent of the time
improves the whole by 1 per cent. So this script times every stage separately,
reports rows in and rows out for each, and names the step that dominates.

It builds into a throwaway database, so profiling never disturbs the warehouse
anyone is using.

    python scripts/profile_pipeline.py
    python scripts/profile_pipeline.py --repeats 3
    python scripts/profile_pipeline.py --explain     # also print the query plan

Rows in and rows out are printed beside each timing because, as Unit 9 puts it,
a step that reads forty million rows to output two hundred is announcing its
own inefficiency.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"
SOURCE = ROOT / "data" / "adult.csv"

# Split 03_load.sql into statements without reordering them.
SPLIT_RE = r'\n(?=INSERT INTO|UPDATE |DELETE FROM)'
TARGET_RE = r'INSERT INTO\s+(\w+)|DELETE FROM\s+(\w+)'


class Step:
    """One timed stage, with the row counts that explain its cost."""

    def __init__(self, name, seconds, rows_in, rows_out, note=""):
        self.name, self.seconds = name, seconds
        self.rows_in, self.rows_out, self.note = rows_in, rows_out, note


def count(con, table) -> int:
    try:
        return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    except Exception:
        return 0


def profile_once(db_path: Path, explain: bool) -> list[Step]:
    """Run the whole pipeline against an empty database, timing each stage."""
    steps: list[Step] = []
    con = duckdb.connect(str(db_path))
    try:
        # ---------------------------------------------------------- schema
        t0 = time.perf_counter()
        for name in ["01_staging.sql", "02_schema.sql", "05_analytic.sql"]:
            con.execute((SQL_DIR / name).read_text(encoding="utf-8"))
        steps.append(Step("schema (DDL)", time.perf_counter() - t0, 0, 0,
                          "tables, seeds and the analytic view"))

        con.execute("INSERT INTO load_run (load_run_id, started_at, source_file, "
                    "source_sha256, source_bytes, status) "
                    "VALUES (1, now(), 'adult.csv', 'profile', 0, 'running')")

        # ---------------------------------------------- read and stage
        t0 = time.perf_counter()
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE incoming AS
            SELECT md5(concat_ws('|', "age","workclass","fnlwgt","education",
                       "education.num","marital.status","occupation","relationship",
                       "race","sex","capital.gain","capital.loss","hours.per.week",
                       "native.country","income")) AS content_hash,
                   CAST(row_number() OVER () AS INTEGER) AS source_row, *
            FROM read_csv('{SOURCE.as_posix()}', all_varchar = true, header = true)
        """)
        rows_read = count(con, "incoming")
        steps.append(Step("read CSV and hash", time.perf_counter() - t0,
                          rows_read, rows_read,
                          "parse 15 text columns, md5 per row"))

        t0 = time.perf_counter()
        con.execute("""
            INSERT INTO stg_adult
            WITH keyed AS (
                SELECT i.content_hash || '#' || CAST(row_number() OVER (
                           PARTITION BY i.content_hash ORDER BY i.source_row) AS VARCHAR) AS record_key,
                       i.source_row, i."age", i."workclass", i."fnlwgt", i."education",
                       i."education.num", i."marital.status", i."occupation",
                       i."relationship", i."race", i."sex", i."capital.gain",
                       i."capital.loss", i."hours.per.week", i."native.country", i."income"
                FROM incoming i)
            SELECT k.record_key, 1, 'adult.csv', k.source_row, k.* EXCLUDE (record_key, source_row)
            FROM keyed k
            WHERE NOT EXISTS (SELECT 1 FROM stg_adult s WHERE s.record_key = k.record_key)
        """)
        staged = count(con, "stg_adult")
        steps.append(Step("stage (anti-join)", time.perf_counter() - t0, rows_read, staged,
                          "idempotency: NOT EXISTS on record_key"))

        # ------------------------------------------------- transform, in parts
        load_sql = (SQL_DIR / "03_load.sql").read_text(encoding="utf-8").replace("$run_id", "1")

        # Statements are timed IN FILE ORDER and never regrouped. The first cut
        # of this script collected them into a dict keyed by a label, which
        # silently reordered them: the fact insert mentions load_reject in its
        # CTE, so it was grouped with the validation statement and therefore ran
        # before the dimension upserts it depends on. It inserted nothing, the
        # mart and feature steps then measured empty tables, and the profile
        # still looked plausible. A profiler that reorders the work is not
        # profiling the work.
        stmts = [s for s in re.split(SPLIT_RE, load_sql)
                 if s.strip() and not s.lstrip().startswith("--")]

        def target_of(stmt: str) -> str:
            m = re.search(TARGET_RE, stmt)
            return (m.group(1) or m.group(2)) if m else "unknown"

        # Consecutive statements writing to a dimension are one logical step.
        pending: list[str] = []
        for i, stmt in enumerate(stmts):
            tgt = target_of(stmt)
            nxt_is_dim = (i + 1 < len(stmts)) and target_of(stmts[i + 1]).startswith("dim_")
            if tgt.startswith("dim_"):
                pending.append(stmt)
                if nxt_is_dim:
                    continue
                t0 = time.perf_counter()
                for s in pending:
                    con.execute(s)
                steps.append(Step("dimension upserts", time.perf_counter() - t0,
                                  staged, 0, f"{len(pending)} tables, new members only"))
                pending = []
                continue

            t0 = time.perf_counter()
            con.execute(stmt)
            dt = time.perf_counter() - t0
            if tgt == "load_reject":
                steps.append(Step("validate (10 rules)", dt, staged, count(con, "load_reject"),
                                  "ten UNION ALL predicates over staging"))
            elif tgt == "quarantine":
                steps.append(Step("quarantine rows", dt, count(con, "load_reject"),
                                  count(con, "quarantine"), "join rejects back to staging"))
            elif tgt == "fact_person":
                steps.append(Step("fact load", dt, staged, count(con, "fact_person"),
                                  "eight dimension joins per row"))
            else:
                steps.append(Step(f"write {tgt}", dt, staged, count(con, tgt), ""))

        # ------------------------------------------------------- serving layers
        t0 = time.perf_counter()
        con.execute((SQL_DIR / "07_mart.sql").read_text(encoding="utf-8"))
        steps.append(Step("refresh mart", time.perf_counter() - t0,
                          count(con, "analytic_person"), count(con, "mart_segment_allocation"),
                          "aggregate the eligible pool to 87 segments"))

        t0 = time.perf_counter()
        con.execute((SQL_DIR / "08_features.sql").read_text(encoding="utf-8"))
        steps.append(Step("build features", time.perf_counter() - t0,
                          count(con, "analytic_person"), count(con, "feature_person"),
                          "per-occupation medians, then one row per person"))

        if explain:
            print("\n  EXPLAIN, the fact load's plan\n")
            plan = con.execute("EXPLAIN SELECT count(*) FROM analytic_person").fetchall()
            for row in plan:
                print("   ", str(row[-1])[:160])
    finally:
        con.close()
    return steps


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--explain", action="store_true")
    args = ap.parse_args(argv)

    if not SOURCE.exists():
        sys.exit(f"no source file at {SOURCE}")

    runs: list[list[Step]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(args.repeats):
            db = Path(tmp) / f"profile{i}.duckdb"
            runs.append(profile_once(db, args.explain and i == 0))

    names = [s.name for s in runs[0]]
    medians = {n: statistics.median(r[i].seconds for r in runs) for i, n in enumerate(names)}
    total = sum(medians.values())

    width = max(len(n) for n in names)
    print()
    print("  " + "=" * (width + 58))
    print(f"  {'step':<{width}} {'seconds':>9} {'share':>7} {'rows in':>10} {'rows out':>10}")
    print("  " + "=" * (width + 58))
    for i, n in enumerate(names):
        s = runs[0][i]
        share = 100 * medians[n] / total
        bar = "#" * max(1, round(share / 3)) if share >= 1 else ""
        print(f"  {n:<{width}} {medians[n]:9.3f} {share:6.1f}% "
              f"{s.rows_in:>10,} {s.rows_out:>10,}  {bar}")
    print("  " + "=" * (width + 58))
    print(f"  {'total':<{width}} {total:9.3f}  median of {args.repeats} full rebuilds")

    slowest = max(medians, key=medians.get)
    print()
    print(f"  BOTTLENECK: {slowest}, {medians[slowest]:.3f} s, "
          f"{100 * medians[slowest] / total:.0f}% of the pipeline.")
    runner_up = sorted(medians.items(), key=lambda kv: -kv[1])[1]
    print(f"  Next slowest: {runner_up[0]}, {runner_up[1]:.3f} s. "
          f"Fixing anything below the top step cannot win more than "
          f"{100 - 100 * medians[slowest] / total:.0f}% of the total.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
