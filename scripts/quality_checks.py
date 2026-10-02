"""
Lab 6: executable data quality checks for the Adult census warehouse.

Unit 6 names five dimensions of quality: complete, valid, fresh, unique and
consistent. The checks are split across two places, because the two kinds of
failure need different handling.

  Row level, in sql/03_load.sql
      Ten rules decide whether an individual record may enter the star. A row
      that fails is copied whole into `quarantine` with a reason code and is
      excluded from the fact load. Eight rules test validity, one tests
      completeness, one tests uniqueness.

  Table level, in this script
      Properties of the warehouse as a whole, which no single row can be
      judged against: that no surrogate key is duplicated, that every foreign
      key resolves, that the published table agrees with the fact table, and
      that the data is fresh enough to use. These cannot quarantine anything.
      They either pass or the build is wrong, so a failure exits non-zero.

Every verdict is written to `quality_check_result`, so "were the checks green
when this warehouse was last built?" is a query rather than a memory.

    python scripts/quality_checks.py
    python scripts/quality_checks.py --max-age-hours 24
    python scripts/quality_checks.py --quiet      # only failures

Exit code 0 if every check passed, 1 if any failed.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "warehouse" / "adult.duckdb"

# The eight dimensions, as (table, surrogate key column, label column).
DIMENSIONS = [
    ("dim_workclass", "workclass_sk", "workclass"),
    ("dim_education", "education_sk", "education"),
    ("dim_marital_status", "marital_status_sk", "marital_status"),
    ("dim_occupation", "occupation_sk", "occupation"),
    ("dim_relationship", "relationship_sk", "relationship"),
    ("dim_race", "race_sk", "race"),
    ("dim_sex", "sex_sk", "sex"),
    ("dim_native_country", "native_country_sk", "native_country"),
]

# The three measures Lab 1 found to be top-coded, with the sentinel the source
# used. Loading the sentinel as a number would corrupt every mean drawn from
# the table, so the loader stores NULL beside a flag. This asserts it held.
TOPCODED = [
    ("age", "age_is_topcoded", 90),
    ("hours_per_week", "hours_is_topcoded", 99),
    ("capital_gain", "capital_gain_is_topcoded", 99999),
]


class Checks:
    """Collects verdicts so every check runs even after one fails.

    A suite that stops at the first failure tells you about one problem. A
    suite that runs them all tells you the shape of the damage, which is the
    more useful thing at three in the morning.
    """

    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.con = con
        self.results: list[dict] = []

    def record(self, name, dimension, passed, observed, expected, detail=""):
        self.results.append({
            "check_name": name, "dimension": dimension, "passed": bool(passed),
            "observed": str(observed), "expected": str(expected),
            "detail": detail,
        })

    def _scalar(self, sql):
        return self.con.execute(sql).fetchone()[0]

    def expect_zero(self, name, dimension, sql, detail=""):
        """The common shape: a query counting violations, which must return 0."""
        try:
            n = self._scalar(sql)
        except Exception as exc:
            # A check that cannot run has not passed. Crashing here would stop
            # every later check and report nothing, which is the worst of both:
            # no verdict and no list. Record it as a failure and carry on.
            self.record(name, dimension, False, "error", 0,
                        f"{type(exc).__name__}: {str(exc).splitlines()[0]}")
            return None
        self.record(name, dimension, n == 0, n, 0, detail)
        return n

    def expect_equal(self, name, dimension, sql_a, sql_b, detail=""):
        try:
            a, b = self._scalar(sql_a), self._scalar(sql_b)
        except Exception as exc:
            self.record(name, dimension, False, "error", "error",
                        f"{type(exc).__name__}: {str(exc).splitlines()[0]}")
            return None, None
        self.record(name, dimension, a == b, a, b, detail)
        return a, b

    @property
    def failed(self) -> list[dict]:
        return [r for r in self.results if not r["passed"]]


# ------------------------------------------------------------------- the suite

def run_checks(con, max_age_hours: float) -> Checks:
    c = Checks(con)

    # ----------------------------------------------------------- UNIQUENESS
    # The source has no business key and its 47 duplicate rows are kept on
    # purpose, so uniqueness here is about the keys the warehouse itself
    # creates, never about the content of a record.

    c.expect_zero(
        "fact_record_key_unique", "unique",
        """SELECT count(*) FROM (
               SELECT record_key FROM fact_person
               GROUP BY record_key HAVING count(*) > 1)""",
        "record_key identifies one fact row",
    )

    c.expect_zero(
        "fact_person_sk_unique", "unique",
        """SELECT count(*) FROM (
               SELECT person_sk FROM fact_person
               GROUP BY person_sk HAVING count(*) > 1)""",
        "the surrogate key is not reused",
    )

    for table, sk, label in DIMENSIONS:
        c.expect_zero(
            f"{table}_label_unique", "unique",
            f"""SELECT count(*) FROM (
                    SELECT {label} FROM {table}
                    GROUP BY {label} HAVING count(*) > 1)""",
            "one member per distinct label",
        )

    # A row cannot be both refused and loaded. If this ever fires, the
    # quarantine is decorative and the star is holding data it rejected.
    c.expect_zero(
        "quarantined_rows_never_loaded", "unique",
        """SELECT count(*) FROM quarantine q
           WHERE EXISTS (SELECT 1 FROM fact_person f
                         WHERE f.record_key = q.record_key)""",
        "quarantine and the star are disjoint",
    )

    # --------------------------------------------------------- COMPLETENESS
    # Lab 2 decided no foreign key may ever be NULL: a missing value maps to a
    # reserved member instead, so a GROUP BY accounts for every row rather
    # than silently dropping the gaps. These two checks are that decision made
    # enforceable.

    fk_null = " OR ".join(f"{sk} IS NULL" for _, sk, _ in DIMENSIONS)
    c.expect_zero(
        "no_null_foreign_keys", "complete",
        f"SELECT count(*) FROM fact_person WHERE {fk_null}",
        "missing values use a reserved member, never NULL",
    )

    for table, sk, _ in DIMENSIONS:
        c.expect_zero(
            f"{table}_fk_resolves", "complete",
            f"""SELECT count(*) FROM fact_person f
                WHERE NOT EXISTS (SELECT 1 FROM {table} d WHERE d.{sk} = f.{sk})""",
            "every key points at a real member",
        )

    # Every fact row can be traced to the file and line that produced it.
    # Lab 1 found a decision with no auditable lineage; this is the assertion
    # that the fix is still in place.
    c.expect_zero(
        "lineage_columns_populated", "complete",
        """SELECT count(*) FROM fact_person
           WHERE load_run_id IS NULL OR source_file IS NULL
              OR source_row IS NULL OR record_key IS NULL""",
        "source file, row and run are recorded on every fact row",
    )

    # ------------------------------------------------------------- VALIDITY
    # The row-level rules already refused anything malformed. What is left to
    # assert is that the cleaning decisions actually took effect.

    for measure, flag, sentinel in TOPCODED:
        c.expect_zero(
            f"{measure}_topcode_is_null", "valid",
            f"SELECT count(*) FROM fact_person WHERE {flag} AND {measure} IS NOT NULL",
            f"the {sentinel} sentinel loads as NULL beside its flag",
        )
        c.expect_zero(
            f"{measure}_sentinel_absent", "valid",
            f"SELECT count(*) FROM fact_person WHERE {measure} = {sentinel}",
            f"no {measure} is stored as the raw sentinel {sentinel}",
        )

    c.expect_zero(
        "age_within_plausible_range", "valid",
        "SELECT count(*) FROM fact_person WHERE age IS NOT NULL AND age NOT BETWEEN 1 AND 120",
        "ages outside this never reach the star",
    )

    # ---------------------------------------------------------- CONSISTENCY
    # Unit 6: the same total appears in every report that claims it. When two
    # tables disagree, trust in both dies.

    c.expect_equal(
        "analytic_matches_fact", "consistent",
        "SELECT count(*) FROM analytic_person",
        "SELECT count(*) FROM fact_person",
        "the published table is the star, flattened, and nothing else",
    )

    # Staged rows are either loaded or quarantined. Nothing may simply vanish,
    # which is the failure mode of the pipeline Assignment 1 Question 1 was
    # written about.
    c.expect_equal(
        "staged_rows_all_accounted", "consistent",
        "SELECT count(*) FROM stg_adult",
        """SELECT (SELECT count(*) FROM fact_person)
                + (SELECT count(DISTINCT record_key) FROM quarantine)""",
        "staged = loaded + quarantined, with no remainder",
    )

    # duplicate_seq numbers the members of a duplicate group from 1. A group
    # that does not start at 1 means the numbering drifted across runs.
    c.expect_zero(
        "duplicate_groups_numbered_from_one", "consistent",
        """SELECT count(*) FROM (
               SELECT duplicate_group_id FROM fact_person
               WHERE duplicate_group_id IS NOT NULL
               GROUP BY duplicate_group_id HAVING min(duplicate_seq) <> 1)""",
        "every duplicate group starts at sequence 1",
    )

    # -------------------------------------------------------------- FRESHNESS
    # Unit 6: fresh enough for the decision it serves. There is no live source
    # behind a 1994 extract, so this measures how long ago the warehouse was
    # last built, which is the honest version of the question here.

    try:
        row = con.execute(
            """SELECT max(finished_at) FROM load_run WHERE status = 'ok'"""
        ).fetchone()
        last = row[0] if row else None
    except Exception as exc:
        last = None
        c.record("warehouse_freshness", "fresh", False, "error",
                 f"<= {max_age_hours} h", f"{type(exc).__name__}")
        return c
    if last is None:
        c.record("warehouse_freshness", "fresh", False, "never", f"< {max_age_hours} h",
                 "no successful load run recorded")
    else:
        age_h = (datetime.now() - last).total_seconds() / 3600
        c.record("warehouse_freshness", "fresh", age_h <= max_age_hours,
                 f"{age_h:.1f} h", f"<= {max_age_hours} h",
                 f"last successful load {last:%Y-%m-%d %H:%M}")
    return c


# ------------------------------------------------------------------ reporting

def persist(con, checks: Checks) -> None:
    stamp = datetime.now()
    con.execute("""
        CREATE TABLE IF NOT EXISTS quality_check_result (
            checked_at TIMESTAMP NOT NULL, check_name VARCHAR NOT NULL,
            dimension VARCHAR NOT NULL, passed BOOLEAN NOT NULL,
            observed VARCHAR, expected VARCHAR, detail VARCHAR,
            PRIMARY KEY (checked_at, check_name))
    """)
    con.executemany(
        """INSERT OR REPLACE INTO quality_check_result
           (checked_at, check_name, dimension, passed, observed, expected, detail)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [(stamp, r["check_name"], r["dimension"], r["passed"],
          r["observed"], r["expected"], r["detail"]) for r in checks.results],
    )


def report(checks: Checks, quiet: bool) -> None:
    rows = checks.failed if quiet else checks.results
    width = max((len(r["check_name"]) for r in checks.results), default=20)
    print()
    print("=" * (width + 46))
    print(f"  {'check':<{width}}  {'dimension':<11} {'observed':>10} {'expected':>10}  ")
    print("=" * (width + 46))
    for r in rows:
        mark = "pass" if r["passed"] else "FAIL"
        print(f"  {r['check_name']:<{width}}  {r['dimension']:<11} "
              f"{r['observed']:>10} {r['expected']:>10}  {mark}")
    print("=" * (width + 46))

    by_dim: dict[str, list[bool]] = {}
    for r in checks.results:
        by_dim.setdefault(r["dimension"], []).append(r["passed"])
    summary = ", ".join(
        f"{d} {sum(v)}/{len(v)}" for d, v in sorted(by_dim.items())
    )
    print(f"  {len(checks.results)} checks: {summary}")
    if checks.failed:
        print(f"  {len(checks.failed)} FAILED")
        for r in checks.failed:
            print(f"      {r['check_name']}: {r['detail']}")
    else:
        print("  all green")
    print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--max-age-hours", type=float, default=168.0,
                    help="freshness threshold, default 168 (seven days)")
    ap.add_argument("--quiet", action="store_true", help="print only failures")
    args = ap.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        sys.exit(f"no warehouse at {db}. Run: python scripts/ingest.py")

    con = duckdb.connect(str(db))
    try:
        checks = run_checks(con, args.max_age_hours)
        persist(con, checks)
    finally:
        con.close()                      # never hold the handle, see Gotchas

    report(checks, args.quiet)
    return 1 if checks.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
