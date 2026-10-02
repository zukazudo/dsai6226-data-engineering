"""
Lab 7: the consumer view over mart_segment_allocation.

One screen answering one question: where in the eligible pool are the
candidates, and which of those segments can actually carry a decision?

    python -m streamlit run app.py

Run it through the module rather than the bare `streamlit` command. pip
installs a streamlit.exe into the interpreter's Scripts directory, and on a
default Windows Python that directory is often not on PATH, so `streamlit run`
reports that it is not recognised while the package is installed and working.
`python -m` uses the interpreter you already have.

It reads only the serving table and computes no metric of its own. Every figure
on the page is a column defined in metrics.md and computed in sql/07_mart.sql,
which is the rule Unit 7 exists to enforce: a chart that derives its own numbers
from raw data is how two dashboards start disagreeing.

--------------------------------------------------------------------------
Why this app never opens warehouse/adult.duckdb directly
--------------------------------------------------------------------------
DuckDB allows many readers or one writer, not both. A Streamlit server is a
long-lived process, so a connection held here, even a read-only one, would take
a lock and make the next `python scripts/ingest.py` fail with:

    IOException: Could not set lock on file ... Conflicting lock is held

That is already recorded in the project's notes from a Lab 3 notebook hitting
it. It matters more here than anywhere else, because the Lab 7 demonstration is
precisely to break the refresh, RUN THE PIPELINE, and watch the banner clear. A
dashboard that blocks the pipeline could not perform its own demonstration.

So the app copies the warehouse to a temporary file, reads the copy, and closes
it immediately. The copy is keyed on the warehouse's modification time, so a
pipeline run invalidates the cache and the next interaction picks up new data.
Reading is plain file I/O and takes no DuckDB lock at all.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
DB = ROOT / "warehouse" / "adult.duckdb"

# The promise from metrics.md. Kept in one place, with dashboard.py importing
# these rather than keeping a second copy.
FRESH_HOURS = 24
AGEING_HOURS = 24 * 7

ACCENT = "#1F3864"
GOOD = "#2E6F5E"
WARN = "#B8860B"
BAD = "#9C3A2E"


# ----------------------------------------------------------------- loading

@st.cache_data(show_spinner=False)
def load(mtime: float):
    """Copy the warehouse, read the serving table from the copy, close it.

    mtime is not used in the body. It is the cache key: when the pipeline
    rewrites the warehouse the modification time changes, this function re-runs,
    and the page shows the new data without anyone restarting the server.

    The name must NOT begin with an underscore. Streamlit treats a leading
    underscore as "do not hash this argument", so calling it _mtime silently
    excluded the one value the cache key exists for, and the page kept serving
    the first snapshot it ever read. It looked correct until the pipeline ran
    and the banner failed to move.
    """
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "snapshot.duckdb"
        shutil.copy2(DB, snapshot)
        con = duckdb.connect(str(snapshot), read_only=True)
        try:
            segments = con.execute("""
                SELECT occupation, education_group, candidates,
                       pct_of_eligible_pool, mean_hours, mean_capital_gain,
                       hours_measured_on, capital_gain_measured_on,
                       segment_is_sparse, occupation_is_assignable
                FROM mart_segment_allocation
                ORDER BY candidates DESC, occupation, education_group
            """).df()
            refreshed, run_id = con.execute(
                "SELECT max(refreshed_at), max(source_load_run_id) "
                "FROM mart_segment_allocation"
            ).fetchone()
        finally:
            con.close()
    return segments, refreshed, run_id


def freshness(age_hours: float) -> tuple[str, str, str]:
    """Computed from the data every run. A hand-typed label rots."""
    if age_hours < FRESH_HOURS:
        return ("FRESH", GOOD,
                f"Data as of {age_hours:.1f} hours ago. The pipeline refreshed this "
                f"table within its promise of every run.")
    if age_hours < AGEING_HOURS:
        return ("AGEING", WARN,
                f"Data is {age_hours / 24:.1f} days old. The table has not been rebuilt "
                f"recently. Check that the pipeline is still running.")
    return ("STALE", BAD,
            f"Data is {age_hours / 24:.1f} days old. This view is not current and should "
            f"not be used for a decision until the pipeline has run.")


# -------------------------------------------------------------------- page

st.set_page_config(page_title="Segment allocation · Team E",
                   page_icon="•", layout="wide")

st.markdown(
    "<style>"
    "#MainMenu,footer{visibility:hidden}"
    "div[data-testid='stMetricValue']{font-size:30px}"
    "</style>", unsafe_allow_html=True)

st.title("Where are the eligible candidates?")
st.caption("Working age, below the income threshold, by occupation and education. "
           "One view, one question. Team E · DSAI 6226.")

if not DB.exists():
    st.error(f"No warehouse at `{DB}`. Build it first:  `python scripts/ingest.py`")
    st.stop()

try:
    segments, refreshed, run_id = load(DB.stat().st_mtime)
except Exception as exc:                 # a load may be running right now
    st.warning(
        "Could not read the warehouse, which usually means the pipeline is "
        f"writing to it at this moment. Try again in a few seconds.\n\n`{exc}`")
    if st.button("Retry"):
        st.rerun()
    st.stop()

if segments.empty:
    st.error("`mart_segment_allocation` is empty. Run `python scripts/ingest.py`.")
    st.stop()

# ---------------------------------------------------------------- freshness
age_hours = (datetime.now() - refreshed).total_seconds() / 3600
state, colour, sentence = freshness(age_hours)

st.markdown(
    f"<div style='border-left:5px solid {colour};background:rgba(128,128,128,.07);"
    f"padding:12px 16px;border-radius:5px;margin:6px 0 18px'>"
    f"<div style='color:{colour};font-weight:700;font-size:12px;letter-spacing:.5px'>{state}</div>"
    f"<div style='margin-top:4px'>{sentence}</div>"
    f"<div style='margin-top:6px;font-size:12px;opacity:.7'>Serving table "
    f"<code>mart_segment_allocation</code>, rebuilt {refreshed:%Y-%m-%d %H:%M} by load run "
    f"{run_id}. Label computed from <code>refreshed_at</code>, never typed.</div>"
    f"</div>", unsafe_allow_html=True)

# ------------------------------------------------------------------ filters
# The drill path Unit 7 asks for: summary first, detail on demand, and the
# viewer chooses the depth. This is the rule a static page was weakest on.
with st.sidebar:
    st.header("Drill")
    occupations = sorted(segments["occupation"].unique())
    chosen = st.multiselect("Occupation", occupations, default=[])
    groups = sorted(segments["education_group"].unique())
    chosen_grp = st.multiselect("Education group", groups, default=[])
    st.divider()
    hide_sparse = st.checkbox("Hide sparse segments (under 30 people)", value=False)
    only_assignable = st.checkbox("Only assignable occupations", value=False)
    st.divider()
    st.caption("Every column is defined in `metrics.md` and computed in "
               "`sql/07_mart.sql`. This page computes nothing of its own.")

view = segments
if chosen:
    view = view[view["occupation"].isin(chosen)]
if chosen_grp:
    view = view[view["education_group"].isin(chosen_grp)]
if hide_sparse:
    view = view[~view["segment_is_sparse"]]
if only_assignable:
    view = view[view["occupation_is_assignable"]]

filtered = len(view) != len(segments)

# -------------------------------------------------------------------- tiles
pool = int(segments["candidates"].sum())
shown = int(view["candidates"].sum())
actionable = int(segments.loc[~segments["segment_is_sparse"]
                              & segments["occupation_is_assignable"], "candidates"].sum())

c = st.columns(5)
c[0].metric("In the eligible pool", f"{pool:,}",
            delta=(f"{shown:,} shown" if filtered else None), delta_color="off")
c[1].metric("Segments", f"{len(segments)}",
            delta=(f"{len(view)} shown" if filtered else None), delta_color="off")
c[2].metric("In segments safe to act on", f"{actionable:,}")
c[3].metric("Sparse, under 30 people", f"{int(segments['segment_is_sparse'].sum())}")
c[4].metric("No assignable occupation",
            f"{int((~segments['occupation_is_assignable']).sum())}")

st.divider()

# -------------------------------------------------------------------- chart
left, right = st.columns([1, 1], gap="large")

with left:
    st.subheader("Candidates by occupation")
    by_occ = (view.groupby("occupation", as_index=False)["candidates"].sum()
                  .sort_values("candidates", ascending=False))
    st.bar_chart(by_occ.set_index("occupation")["candidates"],
                 height=380, color=ACCENT)

with right:
    st.subheader("Hours worked against the segment's size")
    scat = view.dropna(subset=["mean_hours"]).copy()
    scat["flag"] = scat.apply(
        lambda r: "sparse" if r["segment_is_sparse"]
        else ("not assignable" if not r["occupation_is_assignable"] else "actionable"),
        axis=1)
    st.scatter_chart(scat, x="candidates", y="mean_hours", color="flag",
                     size="candidates", height=380)
    st.caption("Sparse segments cluster at the left. A mean drawn from a handful "
               "of people sits as far from the axis as one drawn from a thousand, "
               "which is exactly why the flag travels with the row.")

# -------------------------------------------------------------------- table
st.subheader(f"Every segment{' (filtered)' if filtered else ''}")

display = view.copy()
display["flags"] = display.apply(
    lambda r: " ".join(
        (["sparse"] if r["segment_is_sparse"] else [])
        + ([] if r["occupation_is_assignable"] else ["not assignable"])) or "—",
    axis=1)
display = display[["occupation", "education_group", "candidates",
                   "pct_of_eligible_pool", "mean_hours", "hours_measured_on",
                   "mean_capital_gain", "capital_gain_measured_on", "flags"]]

st.dataframe(
    display, hide_index=True, use_container_width=True, height=430,
    column_config={
        "occupation": "Occupation",
        "education_group": "Education group",
        "candidates": st.column_config.NumberColumn("Candidates", format="%d"),
        "pct_of_eligible_pool": st.column_config.NumberColumn("Share", format="%.2f%%"),
        "mean_hours": st.column_config.NumberColumn("Mean hours", format="%.1f"),
        "hours_measured_on": st.column_config.NumberColumn("n", format="%d",
            help="Rows the mean was computed on. Lower than candidates where values were top-coded."),
        "mean_capital_gain": st.column_config.NumberColumn("Mean cap. gain", format="%.0f"),
        "capital_gain_measured_on": st.column_config.NumberColumn("n", format="%d",
            help="Rows the mean was computed on."),
        "flags": "Flags",
    })

st.caption(
    "Means exclude top-coded values, which is why the n beside each mean can be lower "
    "than the candidate count. Rows flagged sparse or not assignable are shown rather "
    "than hidden, so the segment counts still sum to the pool. "
    f"Read from a snapshot of `warehouse/adult.duckdb` taken at "
    f"{datetime.fromtimestamp(DB.stat().st_mtime):%Y-%m-%d %H:%M}, so this app never holds "
    "a lock on the warehouse the pipeline writes to.")
