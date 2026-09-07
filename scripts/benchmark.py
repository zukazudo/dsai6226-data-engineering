"""Lab 4: run the same aggregate query three ways and time it honestly.

    python scripts/benchmark.py

Three engines, one query, identical data:

    CSV + pandas          read the file, group it in memory
    PostgreSQL            server in a container, table loaded once
    DuckDB + Parquet      columnar file, scanned in process

The data is the denormalised analytic_person table from the warehouse, written
out to all three. Using the raw adult.csv for the pandas leg and the cleaned
warehouse for the others would compare different numbers, so every engine gets
byte-identical content and the script asserts all three return the same answer
before it reports a single timing.

Two things this measures that a naive benchmark hides:

  1. Load cost is separated from query cost. pandas pays the read on every run.
     Postgres and DuckDB pay it once. Reporting only per-query time flatters
     pandas; reporting only total time flatters the databases.

  2. The native dataset is 32,561 rows. At that size the numbers are dominated
     by process and connection startup, not by engine performance. The script
     therefore also runs a scaled copy so the comparison says something that
     survives the dataset growing.

Options:
    --repeats N     timed runs per engine (default 7, median reported)
    --scale N       also benchmark an N times larger copy (default 100, 0 to skip)
    --pg-dsn DSN    PostgreSQL connection string
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "warehouse" / "adult.duckdb"
BENCH_DIR = ROOT / "warehouse" / "bench"
SQL_DIR = ROOT / "sql"

DEFAULT_DSN = "postgresql://lab:labpass@localhost:55432/adult"

# The allocation question from Lab 1, denormalised so a flat file can answer it.
QUERY_SQL = """
SELECT occupation,
       education_group,
       count(*)                    AS candidates,
       avg(hours_per_week)         AS mean_hours,
       avg(capital_gain)           AS mean_capital_gain
FROM {table}
WHERE income_gt_50k = {false_literal}
  AND age BETWEEN 25 AND 54
GROUP BY occupation, education_group
ORDER BY candidates DESC, occupation, education_group
"""


def q(table: str, false_literal: str = "FALSE") -> str:
    return QUERY_SQL.format(table=table, false_literal=false_literal)


def timed(fn, repeats: int) -> tuple[float, list[float]]:
    """Run fn repeats times, return (median ms, all ms). One warm-up first."""
    fn()                                   # warm caches, JIT, connection pool
    samples = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples), samples


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Put a result frame in a canonical form so engines can be compared."""
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    for c in ["mean_hours", "mean_capital_gain"]:
        df[c] = pd.to_numeric(df[c]).round(6)
    df["candidates"] = pd.to_numeric(df["candidates"]).astype("int64")
    return (df.sort_values(["candidates", "occupation", "education_group"],
                           ascending=[False, True, True])
              .reset_index(drop=True)[
                  ["occupation", "education_group", "candidates",
                   "mean_hours", "mean_capital_gain"]])


# --------------------------------------------------------------- preparation

def build_inputs(con, scale: int) -> dict:
    """Write the analytic table to CSV and Parquet at each scale."""
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    con.execute((SQL_DIR / "05_analytic.sql").read_text(encoding="utf-8"))

    out = {}
    for label, factor in [("native", 1)] + ([("scaled", scale)] if scale > 1 else []):
        if factor == 1:
            src = "analytic_person"
        else:
            # Replicate rows to grow the table. The columns the query touches
            # are left alone so the answer stays interpretable: the same 87
            # groups, with candidate counts multiplied by the factor.
            #
            # person_sk and fnlwgt are made distinct per copy on purpose. A
            # straight replication produces a table where every column is
            # perfectly repetitive, which Parquet dictionary-encodes into
            # almost nothing and makes the storage comparison meaningless.
            # Varying two columns does not make this realistic data, and the
            # scaled storage figures are still not to be trusted. They are
            # reported for query time only, and the README says so.
            con.execute(f"""
                CREATE OR REPLACE TABLE bench_scaled AS
                SELECT
                    a.* REPLACE (
                        (a.person_sk + m.r * 1000000)::BIGINT AS person_sk,
                        (a.fnlwgt + ((a.person_sk * 7919 + m.r * 104729) % 50000))::INTEGER AS fnlwgt
                    )
                FROM analytic_person a,
                     (SELECT unnest(range({factor})) AS r) m
            """)
            src = "bench_scaled"

        csv_path = BENCH_DIR / f"adult_{label}.csv"
        pq_path = BENCH_DIR / f"adult_{label}.parquet"
        con.execute(f"COPY (SELECT * FROM {src}) TO '{csv_path.as_posix()}' (FORMAT CSV, HEADER)")
        con.execute(f"COPY (SELECT * FROM {src}) TO '{pq_path.as_posix()}' (FORMAT PARQUET, COMPRESSION zstd)")
        rows = con.execute(f"SELECT count(*) FROM {src}").fetchone()[0]
        out[label] = {
            "rows": rows, "csv": csv_path, "parquet": pq_path,
            "csv_bytes": csv_path.stat().st_size,
            "parquet_bytes": pq_path.stat().st_size,
        }
        print(f"  {label:8} {rows:>10,} rows   csv {csv_path.stat().st_size/1e6:7.1f} MB"
              f"   parquet {pq_path.stat().st_size/1e6:6.1f} MB")
    return out


def load_postgres(dsn: str, csv_path: Path, table: str) -> tuple[float, int]:
    """COPY the CSV into PostgreSQL. Returns (load ms, table bytes)."""
    import psycopg

    ddl = f"""
        DROP TABLE IF EXISTS {table};
        CREATE TABLE {table} (
            person_sk BIGINT, age SMALLINT, age_is_topcoded BOOLEAN,
            workclass TEXT, education TEXT, education_num SMALLINT,
            education_group TEXT, marital_status TEXT, occupation TEXT,
            occupation_unknown BOOLEAN, occupation_not_applicable BOOLEAN,
            relationship TEXT, race TEXT, sex TEXT,
            capital_gain INTEGER, capital_gain_is_topcoded BOOLEAN,
            capital_loss INTEGER, hours_per_week SMALLINT,
            hours_is_topcoded BOOLEAN, native_country TEXT, fnlwgt INTEGER,
            income_gt_50k BOOLEAN, duplicate_group_id INTEGER,
            duplicate_seq SMALLINT
        );
    """
    t0 = time.perf_counter()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(ddl)
        with conn.cursor().copy(
            f"COPY {table} FROM STDIN WITH (FORMAT CSV, HEADER)"
        ) as cp, csv_path.open("rb") as fh:
            while chunk := fh.read(1 << 20):
                cp.write(chunk)
        conn.execute(f"ANALYZE {table}")
        load_ms = (time.perf_counter() - t0) * 1000
        size = conn.execute(
            "SELECT pg_total_relation_size(%s)", [table]
        ).fetchone()[0]
    return load_ms, size


# ------------------------------------------------------------------ benchmark

def bench_scale(label: str, info: dict, dsn: str, repeats: int) -> list[dict]:
    print(f"\n{'=' * 74}\n{label.upper()}: {info['rows']:,} rows\n{'=' * 74}")
    results = []

    # ---- 1. CSV + pandas -------------------------------------------------
    csv = info["csv"]

    def pandas_cold():
        df = pd.read_csv(csv)
        g = (df[(~df.income_gt_50k) & df.age.between(25, 54)]
             .groupby(["occupation", "education_group"], dropna=False)
             .agg(candidates=("person_sk", "size"),
                  mean_hours=("hours_per_week", "mean"),
                  mean_capital_gain=("capital_gain", "mean"))
             .reset_index())
        return g

    warm_df = pd.read_csv(csv)

    def pandas_warm():
        return (warm_df[(~warm_df.income_gt_50k) & warm_df.age.between(25, 54)]
                .groupby(["occupation", "education_group"], dropna=False)
                .agg(candidates=("person_sk", "size"),
                     mean_hours=("hours_per_week", "mean"),
                     mean_capital_gain=("capital_gain", "mean"))
                .reset_index())

    answer = normalise(pandas_cold())

    cold_ms, _ = timed(pandas_cold, repeats)
    warm_ms, _ = timed(pandas_warm, repeats)
    results.append({
        "engine": "CSV + pandas", "scale": label, "rows": info["rows"],
        "bytes": info["csv_bytes"], "load_ms": None,
        "query_ms": warm_ms, "query_incl_read_ms": cold_ms,
    })
    print(f"  CSV + pandas       read+query {cold_ms:8.1f} ms   "
          f"query only {warm_ms:8.1f} ms")

    # ---- 2. PostgreSQL ---------------------------------------------------
    import psycopg
    table = f"adult_{label}"
    load_ms, pg_bytes = load_postgres(dsn, csv, table)
    pg_conn = psycopg.connect(dsn, autocommit=True)

    def pg_query():
        with pg_conn.cursor() as cur:
            cur.execute(q(table))
            return cur.fetchall()

    pg_rows = pg_query()
    pg_df = normalise(pd.DataFrame(
        pg_rows,
        columns=["occupation", "education_group", "candidates",
                 "mean_hours", "mean_capital_gain"]))
    pg_ms, _ = timed(pg_query, repeats)
    pg_conn.close()
    results.append({
        "engine": "PostgreSQL", "scale": label, "rows": info["rows"],
        "bytes": pg_bytes, "load_ms": load_ms,
        "query_ms": pg_ms, "query_incl_read_ms": None,
    })
    print(f"  PostgreSQL         load       {load_ms:8.1f} ms   "
          f"query only {pg_ms:8.1f} ms")

    # ---- 3. DuckDB + Parquet --------------------------------------------
    pq = info["parquet"].as_posix()
    ddb = duckdb.connect()

    def duck_query():
        return ddb.execute(q(f"read_parquet('{pq}')")).fetchall()

    duck_df = normalise(pd.DataFrame(
        duck_query(),
        columns=["occupation", "education_group", "candidates",
                 "mean_hours", "mean_capital_gain"]))
    duck_ms, _ = timed(duck_query, repeats)
    ddb.close()
    results.append({
        "engine": "DuckDB + Parquet", "scale": label, "rows": info["rows"],
        "bytes": info["parquet_bytes"], "load_ms": None,
        "query_ms": duck_ms, "query_incl_read_ms": None,
    })
    print(f"  DuckDB + Parquet   {'':19}query      {duck_ms:8.1f} ms")

    # ---- correctness: a timing is worthless if the answers differ --------
    same_pg = answer.equals(pg_df)
    same_duck = answer.equals(duck_df)
    print(f"\n  same answer as pandas:  PostgreSQL {same_pg}   DuckDB {same_duck}"
          f"   ({len(answer)} groups)")
    if not (same_pg and same_duck):
        for name, other in [("postgres", pg_df), ("duckdb", duck_df)]:
            if not answer.equals(other):
                print(f"\n  MISMATCH vs {name}:")
                print(answer.compare(other).head(10).to_string())
        raise SystemExit("engines disagree, timings are meaningless")

    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Lab 4 benchmark.")
    ap.add_argument("--repeats", type=int, default=7)
    ap.add_argument("--scale", type=int, default=100,
                    help="also benchmark an N times larger copy (0 to skip)")
    ap.add_argument("--pg-dsn", default=DEFAULT_DSN)
    args = ap.parse_args(argv)

    if not DB_PATH.exists():
        print("warehouse not found, run: python scripts/ingest.py", file=sys.stderr)
        return 1

    print("preparing inputs")
    con = duckdb.connect(str(DB_PATH))
    inputs = build_inputs(con, args.scale)
    con.close()

    all_results = []
    for label, info in inputs.items():
        all_results += bench_scale(label, info, args.pg_dsn, args.repeats)

    out = ROOT / "warehouse" / "bench" / "results.json"
    out.write_text(json.dumps(all_results, indent=2, default=str), encoding="utf-8")
    print(f"\nresults written to {out.relative_to(ROOT)}")

    print("\n" + "=" * 74)
    print("COMPARISON TABLE")
    print("=" * 74)
    for label in inputs:
        rs = [r for r in all_results if r["scale"] == label]
        print(f"\n{label}, {rs[0]['rows']:,} rows")
        print(f"  {'engine':<20} {'size':>10} {'load':>12} {'query':>12} {'read+query':>13}")
        for r in rs:
            size = f"{r['bytes']/1e6:.1f} MB"
            load = f"{r['load_ms']:.0f} ms" if r["load_ms"] else "n/a"
            qms = f"{r['query_ms']:.1f} ms"
            tot = f"{r['query_incl_read_ms']:.0f} ms" if r["query_incl_read_ms"] else "n/a"
            print(f"  {r['engine']:<20} {size:>10} {load:>12} {qms:>12} {tot:>13}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
