"""
MOVER SIS — Intraoperative Ventilation & Anesthesia Monitor

Run from repo root:
  streamlit run src/dashboard/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Ensure repo root is on path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_thresholds
from src.dashboard.components import case_timeline_figure, flag_bar_by_rule, top_cases_bar
from src.pipeline.run import run_pipeline

PROCESSED = ROOT / "data" / "processed"


@st.cache_data(show_spinner=False)
def load_processed(processed_dir: str):
    p = Path(processed_dir)
    cases = pd.read_parquet(p / "cases.parquet")
    ts = pd.read_parquet(p / "timeseries.parquet")
    flags = pd.read_parquet(p / "flags.parquet")
    episodes = pd.read_parquet(p / "episodes.parquet") if (p / "episodes.parquet").exists() else pd.DataFrame()
    events = pd.read_parquet(p / "events.parquet") if (p / "events.parquet").exists() else pd.DataFrame()
    return cases, ts, flags, episodes, events


def ensure_data(n_cases: int, preset: str, force: bool = False):
    need = force or not (PROCESSED / "cases.parquet").exists()
    if need:
        with st.spinner(f"Running pipeline on {n_cases} cases (preset={preset})…"):
            run_pipeline(n_cases=n_cases, preset=preset, output_dir=PROCESSED)
        load_processed.clear()
    return load_processed(str(PROCESSED))


def main():
    st.set_page_config(
        page_title="MOVER SIS Ventilation Monitor",
        page_icon="🫁",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.title("MOVER SIS — Intraoperative Ventilation & Anesthesia")
    st.caption(
        "Research dashboard on de-identified UC Irvine perioperative data (2015–2017). "
        "Rule-based flags are screening aids, not clinical diagnoses."
    )

    # ----- Sidebar -----
    st.sidebar.header("Data & filters")
    n_cases = st.sidebar.slider("Sample size (cases)", 10, 200, 50, 10)
    preset = st.sidebar.selectbox("Threshold preset", ["default", "strict", "lenient"])
    force_rerun = st.sidebar.button("Re-run pipeline")

    try:
        cases, ts, flags, episodes, events = ensure_data(n_cases, preset, force=force_rerun)
    except Exception as e:
        st.error(f"Pipeline failed: {e}")
        st.info(
            "Ensure SIS CSVs are at `data/raw/EMR/` "
            "(patient_information.csv, patient_ventilator.csv, patient_vitals.csv)."
        )
        return

    # Filters
    agents = ["(all)"] + sorted(
        [a for a in cases.get("primary_agent_name", pd.Series(dtype=str)).dropna().unique()]
    )
    agent_f = st.sidebar.selectbox("Primary agent", agents)
    min_score = st.sidebar.number_input("Min anomaly score", min_value=0, value=0, step=1)
    proc_q = st.sidebar.text_input("Procedure contains", "")

    view = st.sidebar.radio("View", ["Summary", "Case timeline", "Rule reference"])

    filtered = cases.copy()
    if agent_f != "(all)" and "primary_agent_name" in filtered.columns:
        filtered = filtered[filtered["primary_agent_name"] == agent_f]
    if "anomaly_score" in filtered.columns:
        filtered = filtered[filtered["anomaly_score"] >= min_score]
    if proc_q and "Procedure_short" in filtered.columns:
        filtered = filtered[
            filtered["Procedure_short"].fillna("").str.contains(proc_q, case=False, regex=False)
        ]

    pid_list = filtered["PID"].tolist()

    # ----- Summary -----
    if view == "Summary":
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Cases", len(filtered))
        c2.metric(
            "With any flag",
            int(((filtered.get("n_warn", 0) + filtered.get("n_critical", 0)) > 0).sum())
            if len(filtered) else 0,
        )
        c3.metric(
            "Median duration (min)",
            f"{filtered['case_duration_min'].median():.0f}" if len(filtered) and "case_duration_min" in filtered else "—",
        )
        c4.metric(
            "Max anomaly score",
            int(filtered["anomaly_score"].max()) if len(filtered) and "anomaly_score" in filtered else 0,
        )

        col_a, col_b = st.columns(2)
        with col_a:
            st.plotly_chart(top_cases_bar(filtered, n=min(15, max(1, len(filtered)))), use_container_width=True)
        with col_b:
            fsub = flags[flags["PID"].isin(pid_list)] if len(pid_list) else flags.iloc[0:0]
            st.plotly_chart(flag_bar_by_rule(fsub), use_container_width=True)

        st.subheader("Case table")
        show_cols = [
            c for c in [
                "PID", "Age", "Gender", "Procedure_short", "primary_agent_name",
                "case_duration_min", "median_TV", "median_PIP", "median_ETCO2",
                "n_warn", "n_critical", "anomaly_score", "top_rules",
            ]
            if c in filtered.columns
        ]
        st.dataframe(
            filtered[show_cols].reset_index(drop=True),
            use_container_width=True,
            height=420,
        )
        st.info("Switch to **Case timeline** in the sidebar and pick a PID to inspect.")

    # ----- Case timeline -----
    elif view == "Case timeline":
        if not pid_list:
            st.warning("No cases match filters.")
            return

        # Default to highest scoring
        default_pid = filtered.sort_values("anomaly_score", ascending=False)["PID"].iloc[0]
        pid = st.selectbox(
            "Surgery (PID)",
            pid_list,
            index=pid_list.index(default_pid) if default_pid in pid_list else 0,
            format_func=lambda p: (
                f"{p[:10]}… | score={int(filtered.loc[filtered.PID==p, 'anomaly_score'].iloc[0]) if (filtered.PID==p).any() else 0}"
                f" | {filtered.loc[filtered.PID==p, 'Procedure_short'].iloc[0] if 'Procedure_short' in filtered.columns and (filtered.PID==p).any() else ''}"
            )[:100],
        )
        show_vitals = st.checkbox("Show vitals panel", value=True)

        case_row = filtered[filtered["PID"] == pid].iloc[0]
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Age / Sex", f"{case_row.get('Age', '—')} / {case_row.get('Gender', '—')}")
        m2.metric("Agent", case_row.get("primary_agent_name", "—"))
        m3.metric("Duration (min)", f"{case_row.get('case_duration_min', float('nan')):.0f}")
        m4.metric("Warn / Critical min", f"{int(case_row.get('n_warn', 0))} / {int(case_row.get('n_critical', 0))}")
        m5.metric("Anomaly score", int(case_row.get("anomaly_score", 0)))

        if "Procedure_short" in case_row:
            st.write(f"**Procedure:** {case_row.get('Procedure_short', '')}")

        cts = ts[ts["PID"] == pid].sort_values("t_min")
        cflags = flags[flags["PID"] == pid]
        cevents = events[events["PID"] == pid] if events is not None and not events.empty else pd.DataFrame()

        if cts.empty:
            st.warning("No timeseries rows for this case.")
            return

        fig = case_timeline_figure(cts, cflags, cevents if not cevents.empty else None, show_vitals=show_vitals)
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Flag episodes")
        if episodes is not None and not episodes.empty:
            ep = episodes[episodes["PID"] == pid].sort_values("t_start_min")
            st.dataframe(ep.drop(columns=["PID"], errors="ignore"), use_container_width=True, height=280)
        else:
            st.dataframe(cflags, use_container_width=True, height=280)

        with st.expander("Raw minute flags"):
            st.dataframe(cflags, use_container_width=True, height=240)

    # ----- Rule reference -----
    else:
        cfg = load_thresholds(preset)
        st.subheader(f"Threshold rules (preset: {preset})")
        st.write(
            "Flags are research/education tools on public de-identified data. "
            "They are **not** validated clinical alarms."
        )
        rules = cfg.get("rules", {})
        rows = []
        for rid, spec in rules.items():
            rows.append({
                "rule_id": rid,
                "description": spec.get("description", ""),
                "details": {k: v for k, v in spec.items() if k != "description"},
            })
        for r in rows:
            st.markdown(f"**`{r['rule_id']}`** — {r['description']}")
            st.code(str(r["details"]), language="yaml")

        st.markdown("### Scoring")
        st.code(str(cfg.get("scoring", {})), language="yaml")
        st.markdown("### Age-40 MAC (vol %)")
        st.code(str(cfg.get("mac_age40", {})), language="yaml")


if __name__ == "__main__":
    main()
