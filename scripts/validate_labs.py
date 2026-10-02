"""
Validate every figure this repository publishes, lab by lab.

The README, five Word deliverables and a 25 slide deck all quote numbers. This
script re-derives them from the warehouse and the source file and fails if any
of them has drifted. It is the answer to "is what we wrote down still true?",
asked in one command rather than by re-reading six documents.

    python scripts/validate_labs.py
    python scripts/validate_labs.py --lab 3        # one lab only
    python scripts/validate_labs.py --quiet        # failures only

Exit code 0 if every claim holds, 1 otherwise.

What is deliberately not covered: the PostgreSQL arm of the Lab 4 benchmark,
which needs a container running, and the Lab 5 sandbox run, which needs a
Google sign-in. Both are reported as skipped rather than quietly omitted. The
Lab 5 prediction that the sandbox confirmed IS checked, because it is
computable from the data.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "warehouse" / "adult.duckdb"
SOURCE = ROOT / "data" / "adult.csv"
SQL_DIR = ROOT / "sql"

# The source file as committed. Every figure below is downstream of these bytes,
# so if the hash moves nothing else in this script means what it used to.
SOURCE_SHA256 = "250e154ed75714ae57a564926d66c6319cd6aac1bcd32774cc76841a88d74e53"
SOURCE_BYTES = 4104734


class Report:
    def __init__(self):
        self.rows: list[tuple[int, str, bool, str, str]] = []

    def check(self, lab, name, got, want, note=""):
        ok = got == want
        self.rows.append((lab, name, ok, f"{got}", f"{want}"))
        return ok

    def skip(self, lab, name, why):
        self.rows.append((lab, name, None, "skipped", why))

    @property
    def failures(self):
        return [r for r in self.rows if r[2] is False]


def q(con, sql):
    return con.execute(sql).fetchone()[0]


# --------------------------------------------------------------------- lab 1

def lab1(con, r: Report):
    """The figures the problem statement and the notebook are built on."""
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    r.check(1, "source file sha256", digest, SOURCE_SHA256)
    r.check(1, "source file bytes", SOURCE.stat().st_size, SOURCE_BYTES)

    r.check(1, "records", q(con, "SELECT count(*) FROM fact_person"), 32561)
    r.check(1, "age minimum is 17, not 19", q(con, "SELECT min(age) FROM fact_person"), 17)
    r.check(1, "zero records at age 89",
            q(con, "SELECT count(*) FROM fact_person WHERE age = 89"), 0)
    r.check(1, "age top-coded at 90",
            q(con, "SELECT count(*) FROM fact_person WHERE age_is_topcoded"), 43)
    r.check(1, "hours top-coded at 99",
            q(con, "SELECT count(*) FROM fact_person WHERE hours_is_topcoded"), 85)
    r.check(1, "capital gain top-coded at 99999",
            q(con, "SELECT count(*) FROM fact_person WHERE capital_gain_is_topcoded"), 159)
    r.check(1, "records carrying a question mark",
            q(con, """SELECT count(*) FROM stg_adult
                      WHERE workclass = '?' OR occupation = '?' OR native_country = '?'"""),
            2399)
    r.check(1, "capital.loss max is not a sentinel",
            q(con, "SELECT max(capital_loss) FROM fact_person"), 4356)
    r.check(1, "fnlwgt is not an identifier",
            q(con, "SELECT count(DISTINCT fnlwgt) FROM fact_person"), 21648)
    # The tie that made idxmax() unreproducible. Three values share the top count.
    r.check(1, "fnlwgt most-frequent is a three-way tie",
            q(con, """SELECT count(*) FROM (
                        SELECT fnlwgt FROM fact_person GROUP BY fnlwgt
                        HAVING count(*) = (SELECT max(n) FROM (
                            SELECT count(*) AS n FROM fact_person GROUP BY fnlwgt)))"""),
            3)


# --------------------------------------------------------------------- lab 2

def lab2(con, r: Report):
    """One fact, eight dimensions, one staging table, and the two departures."""
    dims = q(con, """SELECT count(*) FROM information_schema.tables
                     WHERE table_schema = 'main' AND table_name LIKE 'dim_%'""")
    r.check(2, "eight dimensions", dims, 8)

    for table, n in [("dim_workclass", 9), ("dim_education", 16),
                     ("dim_marital_status", 7), ("dim_occupation", 16),
                     ("dim_relationship", 6), ("dim_race", 5), ("dim_sex", 2),
                     ("dim_native_country", 42)]:
        r.check(2, f"{table} members", q(con, f"SELECT count(*) FROM {table}"), n)

    # Departure 1: duplicates retained and marked, not dropped.
    r.check(2, "rows in a duplicate group",
            q(con, "SELECT count(*) FROM fact_person WHERE duplicate_group_id IS NOT NULL"), 47)
    r.check(2, "rows pandas.duplicated() would report",
            q(con, "SELECT count(*) FROM fact_person WHERE duplicate_seq > 1"), 24)

    # Departure 2: top-coded measures are NULL beside a flag.
    r.check(2, "no top-coded value stored as a number",
            q(con, """SELECT count(*) FROM fact_person
                      WHERE (age_is_topcoded AND age IS NOT NULL)
                         OR (hours_is_topcoded AND hours_per_week IS NOT NULL)
                         OR (capital_gain_is_topcoded AND capital_gain IS NOT NULL)"""), 0)

    # The question mark split into two distinguishable states.
    r.check(2, "occupation unknown",
            q(con, """SELECT count(*) FROM fact_person f JOIN dim_occupation o
                      USING (occupation_sk) WHERE o.is_unknown"""), 1836)
    r.check(2, "occupation not applicable (Never-worked)",
            q(con, """SELECT count(*) FROM fact_person f JOIN dim_occupation o
                      USING (occupation_sk) WHERE o.is_not_applicable"""), 7)
    r.check(2, "no foreign key is NULL",
            q(con, """SELECT count(*) FROM fact_person WHERE workclass_sk IS NULL
                      OR education_sk IS NULL OR marital_status_sk IS NULL
                      OR occupation_sk IS NULL OR relationship_sk IS NULL
                      OR race_sk IS NULL OR sex_sk IS NULL
                      OR native_country_sk IS NULL"""), 0)


# --------------------------------------------------------------------- lab 3

def lab3(r: Report):
    """Idempotency and the reject path, both run against throwaway databases."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "idem.duckdb"
        run = lambda *a: subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "ingest.py"), *a],
            capture_output=True, text=True)

        run(str(SOURCE), "--db", str(db))
        con = duckdb.connect(str(db), read_only=True)
        first = q(con, "SELECT count(*) FROM fact_person")
        con.close()

        run(str(SOURCE), "--db", str(db))            # the whole point: run it twice
        con = duckdb.connect(str(db), read_only=True)
        second = q(con, "SELECT count(*) FROM fact_person")
        staged_on_rerun = q(con, """SELECT rows_staged FROM load_run
                                    WHERE load_run_id = (SELECT max(load_run_id) FROM load_run)""")
        con.close()

        r.check(3, "run 1 loads the file", first, 32561)
        r.check(3, "run 2 changes nothing", second, first)
        r.check(3, "run 2 stages zero rows", staged_on_rerun, 0)

        # The reject path, proven with a second and different file, because
        # re-running the same file proves nothing on its own: a pipeline that
        # silently drops all input passes that test perfectly.
        bad = Path(tmp) / "bad.duckdb"
        run(str(ROOT / "tests" / "fixtures" / "adult_bad_rows.csv"), "--db", str(bad))
        run(str(ROOT / "tests" / "fixtures" / "adult_quarantine_demo.csv"), "--db", str(bad))
        con = duckdb.connect(str(bad), read_only=True)
        r.check(3, "nine distinct rules fire",
                q(con, "SELECT count(DISTINCT reason) FROM quarantine"), 9)
        r.check(3, "nine rows refused",
                q(con, "SELECT count(DISTINCT record_key) FROM quarantine"), 9)
        r.check(3, "the two valid rows load",
                q(con, "SELECT count(*) FROM fact_person"), 2)
        con.close()


# --------------------------------------------------------------------- lab 4

def lab4(con, r: Report):
    """The benchmark's correctness claim, and that Parquet is the smaller file."""
    query = """
        SELECT occupation, education_group, count(*) AS candidates,
               avg(hours_per_week) AS mean_hours, avg(capital_gain) AS mean_capital_gain
        FROM {src}
        WHERE income_gt_50k = FALSE AND age BETWEEN 25 AND 54
        GROUP BY occupation, education_group
    """
    groups = q(con, f"SELECT count(*) FROM ({query.format(src='analytic_person')})")
    r.check(4, "the allocation query returns 87 groups", groups, 87)

    with tempfile.TemporaryDirectory() as tmp:
        csv_p, pq_p = Path(tmp) / "a.csv", Path(tmp) / "a.parquet"
        con.execute(f"COPY (SELECT * FROM analytic_person) TO '{csv_p.as_posix()}' (FORMAT CSV, HEADER)")
        con.execute(f"COPY (SELECT * FROM analytic_person) TO '{pq_p.as_posix()}' (FORMAT PARQUET, COMPRESSION zstd)")

        # Same answer from both formats, which is what makes a timing meaningful.
        a = con.execute(query.format(src=f"read_csv_auto('{csv_p.as_posix()}')")).fetchall()
        b = con.execute(query.format(src=f"read_parquet('{pq_p.as_posix()}')")).fetchall()
        r.check(4, "CSV and Parquet return the same answer",
                sorted(map(str, a)) == sorted(map(str, b)), True)

        ratio = csv_p.stat().st_size / pq_p.stat().st_size
        r.check(4, "Parquet is at least 10x smaller than the CSV", ratio >= 10, True)

    r.skip(4, "PostgreSQL arm", "needs docker compose up -d")


# --------------------------------------------------------------------- lab 5

def lab5(r: Report):
    """The bytes-processed prediction the sandbox confirmed exactly."""
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "bq_bytes.py")],
                         capture_output=True, text=True).stdout
    r.check(5, "six-column scan predicted at 1.81 MB", "1.81 MB" in out, True)
    r.check(5, "all-columns scan predicted at 5.34 MB", "5.34 MB" in out, True)
    r.check(5, "ratio is 3.0x, not the lecture's 100x", "3.0 times" in out, True)
    r.skip(5, "sandbox run", "needs a Google sign-in; measured 1.81 MB on 13 Sep 2026")


# --------------------------------------------------------------------- lab 7

def lab7(con, r: Report):
    """The serving table's published invariants, exactly as metrics.md claims."""
    r.check(7, "87 segments published",
            q(con, "SELECT count(*) FROM mart_segment_allocation"), 87)
    r.check(7, "candidates sum to the eligible pool",
            q(con, "SELECT sum(candidates) FROM mart_segment_allocation"), 16005)
    r.check(7, "share sums to 100 within rounding",
            round(q(con, "SELECT sum(pct_of_eligible_pool) FROM mart_segment_allocation")) , 100)
    r.check(7, "20 sparse segments",
            q(con, "SELECT count(*) FROM mart_segment_allocation WHERE segment_is_sparse"), 20)
    r.check(7, "7 unassignable segments",
            q(con, """SELECT count(*) FROM mart_segment_allocation
                      WHERE NOT occupation_is_assignable"""), 7)
    # A mean must never be computed on more rows than the segment holds, and the
    # gap between them is exactly the top-coded records metrics.md describes.
    r.check(7, "no mean is measured on more rows than exist",
            q(con, """SELECT count(*) FROM mart_segment_allocation
                      WHERE hours_measured_on > candidates
                         OR capital_gain_measured_on > candidates"""), 0)
    r.check(7, "every segment records when it was built",
            q(con, "SELECT count(*) FROM mart_segment_allocation WHERE refreshed_at IS NULL"), 0)


# --------------------------------------------------------------------- lab 6

def lab6(r: Report):
    """The quality suite, run as its own process so its exit code is the verdict."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "quality_checks.py"), "--quiet"],
        capture_output=True, text=True)
    r.check(6, "all quality checks pass", proc.returncode, 0)
    r.check(6, "the suite reports 32 checks", "32 checks" in proc.stdout, True)


# ------------------------------------------------------------------- reporting

LABS = {1: "data problem statement", 2: "star schema", 3: "re-runnable ingester",
        4: "benchmark", 5: "cloud warehouse", 6: "governance and quality",
        7: "serving layer"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lab", type=int, choices=sorted(LABS), action="append")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    wanted = set(args.lab or LABS)

    if not DB.exists():
        sys.exit(f"no warehouse at {DB}. Run: python scripts/ingest.py")

    r = Report()
    started = time.perf_counter()
    con = duckdb.connect(str(DB), read_only=True)
    try:
        if 1 in wanted: lab1(con, r)
        if 2 in wanted: lab2(con, r)
        if 4 in wanted: lab4(con, r)
        if 7 in wanted: lab7(con, r)
    finally:
        con.close()                      # closed before anything shells out
    if 3 in wanted: lab3(r)
    if 5 in wanted: lab5(r)
    if 6 in wanted: lab6(r)
    elapsed = time.perf_counter() - started

    width = max(len(n) for _, n, _, _, _ in r.rows)
    current = None
    print()
    for lab, name, ok, got, want in r.rows:
        if ok is True and args.quiet:
            continue
        if lab != current:
            print(f"\n  Lab {lab}: {LABS[lab]}")
            current = lab
        mark = "skip" if ok is None else ("pass" if ok else "FAIL")
        tail = f"  got {got}, expected {want}" if ok is False else (f"  ({want})" if ok is None else "")
        print(f"    {name:<{width}}  {mark}{tail}")

    passed = sum(1 for x in r.rows if x[2] is True)
    skipped = sum(1 for x in r.rows if x[2] is None)
    print()
    print("  " + "-" * (width + 20))
    print(f"  {passed} passed, {len(r.failures)} failed, {skipped} skipped, "
          f"in {elapsed:.1f}s")
    if r.failures:
        print("\n  FAILURES")
        for lab, name, _, got, want in r.failures:
            print(f"    Lab {lab}  {name}: got {got}, expected {want}")
    else:
        print("  Every published figure still holds.")
    print()
    return 1 if r.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
