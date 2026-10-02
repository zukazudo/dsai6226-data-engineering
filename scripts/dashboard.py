"""
Lab 7: a static export of the consumer view.

The dashboard is app.py, run with `python -m streamlit run app.py`. This renders
the same serving table to a single HTML file for the cases the app cannot
cover: reading it without a Python environment, attaching it to a submission,
or committing a dated snapshot of what the numbers were on a given day.

It is not a second dashboard. Unit 7 warns about dashboard sprawl, and the
defence here is that both views read the same table and neither computes a
metric of its own, so they cannot disagree. The freshness thresholds are
imported from app.py rather than duplicated, for the same reason.

Every figure comes from a column defined in metrics.md.

    python scripts/dashboard.py
    python scripts/dashboard.py --out docs/dashboard.html
    python scripts/dashboard.py --stale-hours 0   # force the stale banner

Writes a single self-contained HTML file with no external requests, so it opens
from disk and keeps working offline.
"""

from __future__ import annotations

import argparse
import html
import sys
from datetime import datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "warehouse" / "adult.duckdb"
DEFAULT_OUT = ROOT / "docs" / "dashboard.html"

# Imported from the dashboard so the two views cannot drift apart. If app.py is
# unavailable for any reason, fall back to the values metrics.md publishes.
try:
    sys.path.insert(0, str(ROOT))
    from app import FRESH_HOURS, AGEING_HOURS
except Exception:                        # pragma: no cover - app.py is present
    FRESH_HOURS, AGEING_HOURS = 24, 24 * 7


def freshness(age_hours: float) -> tuple[str, str, str]:
    """Return (state, colour, sentence) for the banner.

    Computed from the data every time this runs. Unit 7: a hand-typed
    freshness label rots, and stale data presented as live is a quiet lie.
    """
    if age_hours < FRESH_HOURS:
        return ("fresh", "#2E6F5E",
                f"Data as of {age_hours:.1f} hours ago. The pipeline refreshed this table "
                f"within its promise of every run.")
    if age_hours < AGEING_HOURS:
        return ("ageing", "#B8860B",
                f"Data is {age_hours / 24:.1f} days old. The table has not been rebuilt "
                f"recently. Check that the pipeline is still running.")
    return ("STALE", "#9C3A2E",
            f"Data is {age_hours / 24:.1f} days old. This view is not current and should "
            f"not be used for a decision until the pipeline has run.")


def fetch(con, stale_hours: float | None):
    mart = "mart_segment_allocation"
    if not con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [mart]
    ).fetchone()[0]:
        sys.exit(f"{mart} does not exist. Run: python scripts/ingest.py")

    rows = con.execute(f"""
        SELECT occupation, education_group, candidates, pct_of_eligible_pool,
               mean_hours, mean_capital_gain, hours_measured_on,
               capital_gain_measured_on, segment_is_sparse, occupation_is_assignable
        FROM {mart}
        ORDER BY candidates DESC, occupation, education_group
    """).fetchall()
    if not rows:
        sys.exit(f"{mart} is empty. Run: python scripts/ingest.py")

    refreshed, run_id = con.execute(
        f"SELECT max(refreshed_at), max(source_load_run_id) FROM {mart}"
    ).fetchone()
    age = (datetime.now() - refreshed).total_seconds() / 3600
    if stale_hours is not None:          # the deliberate break, for the demo
        age = stale_hours
    return rows, refreshed, run_id, age


def render(rows, refreshed, run_id, age_hours) -> str:
    state, colour, sentence = freshness(age_hours)
    pool = sum(r[2] for r in rows)
    sparse = sum(1 for r in rows if r[8])
    unassignable = sum(1 for r in rows if not r[9])
    actionable = sum(r[2] for r in rows if not r[8] and r[9])

    # Top occupations by candidates, for the bar chart. Drawn as inline SVG
    # rather than a charting library, so the file stays self-contained.
    by_occ: dict[str, int] = {}
    for occ, _, n, *_ in rows:
        by_occ[occ] = by_occ.get(occ, 0) + n
    top = sorted(by_occ.items(), key=lambda kv: -kv[1])[:10]
    widest = max(n for _, n in top)

    bars = []
    for i, (occ, n) in enumerate(top):
        w = 560 * n / widest
        y = i * 30
        bars.append(
            f'<text x="0" y="{y + 15}" class="bl">{html.escape(occ)}</text>'
            f'<rect x="170" y="{y + 4}" width="{w:.1f}" height="16" rx="2" class="br"/>'
            f'<text x="{176 + w:.1f}" y="{y + 16}" class="bv">{n:,}</text>'
        )
    chart = (f'<svg viewBox="0 0 760 {len(top) * 30}" role="img" '
             f'aria-label="Eligible candidates by occupation">{"".join(bars)}</svg>')

    trs = []
    for occ, eg, n, pct, mh, mcg, hn, cn, is_sparse, assignable in rows:
        flags = []
        if is_sparse:
            flags.append('<span class="flag sparse">sparse</span>')
        if not assignable:
            flags.append('<span class="flag unassign">not assignable</span>')
        trs.append(
            f"<tr{' class=dim' if (is_sparse or not assignable) else ''}>"
            f"<td>{html.escape(occ)}</td><td>{html.escape(eg)}</td>"
            f"<td class=n>{n:,}</td><td class=n>{pct:.2f}%</td>"
            f"<td class=n>{'' if mh is None else f'{mh:.1f}'}</td>"
            f"<td class=n sub>{hn:,}</td>"
            f"<td class=n>{'' if mcg is None else f'{mcg:,.0f}'}</td>"
            f"<td class=n sub>{cn:,}</td>"
            f"<td>{''.join(flags)}</td></tr>"
        )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Segment allocation · Team E</title>
<style>
  :root {{ color-scheme: light dark;
    --bg:#fbfbfa; --fg:#1d1d1b; --mut:#5f5e5a; --line:#e3e2de; --card:#fff; --accent:#1F3864; }}
  @media (prefers-color-scheme: dark) {{ :root {{
    --bg:#16181a; --fg:#e8e6e3; --mut:#9a9893; --line:#2c2f33; --card:#1d2023; --accent:#8fa8d4; }} }}
  * {{ box-sizing:border-box }}
  body {{ margin:0; padding:28px 22px 60px; background:var(--bg); color:var(--fg);
    font:14px/1.55 "Segoe UI",system-ui,-apple-system,sans-serif; }}
  .wrap {{ max-width:1080px; margin:0 auto }}
  h1 {{ font-size:22px; margin:0 0 2px; letter-spacing:-.2px }}
  .sub {{ color:var(--mut) }}
  header .q {{ color:var(--mut); margin:0 0 18px; font-size:14px }}
  .banner {{ border-left:4px solid {colour}; background:var(--card); border:1px solid var(--line);
    border-left:4px solid {colour}; border-radius:5px; padding:12px 15px; margin:0 0 22px }}
  .banner .state {{ font-weight:700; color:{colour}; letter-spacing:.4px; font-size:12px }}
  .banner p {{ margin:4px 0 0 }}
  .banner .meta {{ color:var(--mut); font-size:12px; margin-top:6px }}
  .tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:0 0 26px }}
  .tile {{ background:var(--card); border:1px solid var(--line); border-radius:5px; padding:13px 15px }}
  .tile .v {{ font-size:25px; font-weight:650; letter-spacing:-.5px }}
  .tile .k {{ color:var(--mut); font-size:12px; margin-top:2px }}
  h2 {{ font-size:15px; margin:28px 0 10px; color:var(--accent) }}
  svg {{ width:100%; height:auto }}
  .bl {{ font:12px "Segoe UI",system-ui,sans-serif; fill:var(--fg) }}
  .bv {{ font:11px "Segoe UI",system-ui,sans-serif; fill:var(--mut) }}
  .br {{ fill:var(--accent); opacity:.82 }}
  .tablewrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:5px; background:var(--card) }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; min-width:760px }}
  th,td {{ padding:7px 11px; text-align:left; border-bottom:1px solid var(--line); white-space:nowrap }}
  th {{ font-size:11px; text-transform:uppercase; letter-spacing:.5px; color:var(--mut); font-weight:600 }}
  td.n {{ text-align:right; font-variant-numeric:tabular-nums }}
  td.sub, th.sub {{ color:var(--mut); font-size:11px }}
  tr.dim td {{ color:var(--mut) }}
  .flag {{ font-size:10.5px; padding:1px 6px; border-radius:9px; margin-right:4px }}
  .sparse {{ background:#f3e6c8; color:#6b5316 }}
  .unassign {{ background:#f0dcd8; color:#7d2f23 }}
  @media (prefers-color-scheme: dark) {{
    .sparse {{ background:#4a3c18; color:#e5cd8e }}
    .unassign {{ background:#4a2420; color:#f0b3a8 }} }}
  footer {{ margin-top:26px; color:var(--mut); font-size:12px; border-top:1px solid var(--line); padding-top:12px }}
  code {{ font:12px ui-monospace,Consolas,monospace; background:var(--bg); padding:1px 4px; border-radius:3px }}
</style></head><body><div class="wrap">

<header>
  <h1>Where are the eligible candidates?</h1>
  <p class="q">Working age, below the income threshold, by occupation and education.
     One view, one question. Team E &middot; DSAI 6226.</p>
</header>

<div class="banner">
  <div class="state">{state.upper()}</div>
  <p>{sentence}</p>
  <div class="meta">Serving table <code>mart_segment_allocation</code>, rebuilt
    {refreshed:%Y-%m-%d %H:%M} by load run {run_id}. Label computed from
    <code>refreshed_at</code>, never typed.</div>
</div>

<div class="tiles">
  <div class="tile"><div class="v">{pool:,}</div><div class="k">in the eligible pool</div></div>
  <div class="tile"><div class="v">{len(rows)}</div><div class="k">segments</div></div>
  <div class="tile"><div class="v">{actionable:,}</div><div class="k">in segments safe to act on</div></div>
  <div class="tile"><div class="v">{sparse}</div><div class="k">sparse, under 30 people</div></div>
  <div class="tile"><div class="v">{unassignable}</div><div class="k">no assignable occupation</div></div>
</div>

<h2>Ten largest occupations in the pool</h2>
{chart}

<h2>Every segment</h2>
<div class="tablewrap"><table>
  <thead><tr>
    <th>Occupation</th><th>Education group</th><th class=n>Candidates</th>
    <th class=n>Share</th><th class=n>Mean hours</th><th class="n sub">n</th>
    <th class=n>Mean cap. gain</th><th class="n sub">n</th><th>Flags</th>
  </tr></thead>
  <tbody>{''.join(trs)}</tbody>
</table></div>

<footer>
  Every column is defined in <code>metrics.md</code> and computed in
  <code>sql/07_mart.sql</code>. This page computes nothing of its own.
  Means exclude top-coded values, which is why the <span class="sub">n</span> beside each mean
  can be lower than the candidate count. Rows flagged sparse or not assignable are shown
  rather than hidden, so the segment counts still sum to the pool.
  <br>Generated {datetime.now():%Y-%m-%d %H:%M} from
  <code>warehouse/adult.duckdb</code>.
</footer>
</div></body></html>
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--stale-hours", type=float, default=None,
                    help="override the computed age, to demonstrate the stale banner")
    args = ap.parse_args(argv)

    con = duckdb.connect(args.db, read_only=True)
    try:
        rows, refreshed, run_id, age = fetch(con, args.stale_hours)
    finally:
        con.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(rows, refreshed, run_id, age), encoding="utf-8")

    state, _, _ = freshness(age)
    print(f"  wrote {out}  ({out.stat().st_size / 1024:.0f} KB)")
    print(f"  {len(rows)} segments, freshness {state}, data {age:.1f} h old")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
