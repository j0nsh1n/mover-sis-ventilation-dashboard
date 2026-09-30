"""Build grounded text context for a single surgery (PID) for local LLM use."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from src.runtime_paths import emr_dir, wave_dir, waveform_case_dir


def phase_note(row: pd.Series) -> str:
    """Suffix such as ", during induction" for a flag or episode row; "" when the phase is unknown."""
    start = row.get("phase")
    if start is None or pd.isna(start):
        return ""
    end = row.get("phase_end")
    start_txt = str(start).replace("_", " ")
    if end is None or pd.isna(end) or end == start:
        return f", during {start_txt}"
    return f", from {start_txt} to {str(end).replace('_', ' ')}"


def phase_counts_line(flags: pd.DataFrame) -> str:
    """One line of flagged minutes per phase; empty when the flags carry no phase."""
    if "phase" not in flags.columns or flags["phase"].isna().all():
        return ""
    minutes = flags.dropna(subset=["phase"]).groupby("phase")["Obs_time"].nunique()
    order = ["pre_induction", "induction", "maintenance", "emergence", "post_emergence"]
    parts = [f"{p}={int(minutes[p])}" for p in order if p in minutes.index]
    return "Flagged minutes by phase: " + ", ".join(parts) if parts else ""


def _fmt_num(x: Any, nd: int = 1) -> str:
    try:
        if pd.isna(x):
            return "n/a"
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return str(x)


def _series_stats(s: pd.Series, name: str, unit: str = "") -> str:
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return f"- {name}: no data"
    u = f" {unit}" if unit else ""
    return (
        f"- {name}: median {_fmt_num(s.median())}{u}, "
        f"IQR [{_fmt_num(s.quantile(0.25))}–{_fmt_num(s.quantile(0.75))}]{u}, "
        f"min–max [{_fmt_num(s.min())}–{_fmt_num(s.max())}]{u} "
        f"(n={len(s)})"
    )


def load_case_medications(pid: str, emr: Path | str | None = None) -> pd.DataFrame:
    from src.pipeline.load import read_csv, _default_emr_dir

    path = _default_emr_dir(emr) / "patient_medication.csv"
    if not path.is_file():
        return pd.DataFrame()
    df = read_csv(path)
    df.columns = [c.strip().strip('"') for c in df.columns]
    if "PID" not in df.columns:
        return pd.DataFrame()
    return df[df["PID"].astype(str) == str(pid)].copy()


def load_case_events(pid: str, emr: Path | str | None = None) -> pd.DataFrame:
    from src.pipeline.load import load_procedure_events

    return load_procedure_events(emr, pids=[str(pid)])


def build_case_context(
    pid: str,
    *,
    cases: pd.DataFrame,
    timeseries: pd.DataFrame,
    flags: pd.DataFrame | None = None,
    episodes: pd.DataFrame | None = None,
    events: pd.DataFrame | None = None,
    medications: pd.DataFrame | None = None,
    include_emr_extras: bool = True,
    max_meds: int = 40,
    max_flag_lines: int = 25,
) -> str:
    """
    Assemble a plain-text case briefing for the LLM.

    Prefer preloaded frames from the dashboard; optionally loads meds/events from EMR.
    """
    pid = str(pid)
    lines: list[str] = []
    lines.append(f"CASE PID: {pid}")
    lines.append(
        "Note: PID identifies a surgery record in SIS (not a lifelong patient ID)."
    )

    # --- demographics / procedure ---
    crow = None
    if cases is not None and not cases.empty and "PID" in cases.columns:
        sub = cases[cases["PID"].astype(str) == pid]
        if not sub.empty:
            crow = sub.iloc[0]
    if crow is not None:
        lines.append("\n## Demographics & procedure")
        for label, key in [
            ("Age", "Age"),
            ("Sex", "Gender"),
            ("Procedure", "Procedure_short"),
            ("Primary agent", "primary_agent_name"),
            ("Agent code", "primary_agent"),
            ("Case duration (min)", "case_duration_min"),
            ("OR / data span start", "t_start"),
            ("OR / data span end", "t_end"),
            ("Minute samples", "n_minutes"),
        ]:
            if key in crow.index and pd.notna(crow[key]):
                lines.append(f"- {label}: {crow[key]}")
        if "anomaly_score" in crow.index:
            lines.append(
                f"- Anomaly score: {crow.get('anomaly_score')} "
                f"(warn min={crow.get('n_warn', 'n/a')}, "
                f"critical min={crow.get('n_critical', 'n/a')}, "
                f"info min={crow.get('n_info', 'n/a')})"
            )
        if "top_rules" in crow.index and pd.notna(crow.get("top_rules")):
            lines.append(f"- Top flag rules: {crow['top_rules']}")

    # --- timeseries summary ---
    ts = pd.DataFrame()
    if timeseries is not None and not timeseries.empty:
        ts = timeseries[timeseries["PID"].astype(str) == pid].copy()
    if not ts.empty:
        lines.append("\n## Intraoperative signals (minute-level summary)")
        if "t_min" in ts.columns:
            from src.pipeline.features import OBSERVED_SIGNAL_COLS

            # The timeseries has a row for every minute; gap minutes have no signals
            signal_cols = [c for c in OBSERVED_SIGNAL_COLS if c in ts.columns]
            n_obs = int(ts[signal_cols].notna().any(axis=1).sum()) if signal_cols else len(ts)
            lines.append(
                f"- Monitoring window: t={_fmt_num(ts['t_min'].min(), 0)} to "
                f"{_fmt_num(ts['t_min'].max(), 0)} minutes from case start "
                f"({n_obs} of {len(ts)} minutes with data)"
            )
        for col, name, unit in [
            ("TV", "Tidal volume", "mL"),
            ("TV_mlkg", "TV per IBW", "mL/kg"),
            ("PIP", "Peak inspiratory pressure", "cmH2O"),
            ("PEEP", "PEEP", "cmH2O"),
            ("RR", "Respiratory rate", "/min"),
            ("ETCO2", "End-tidal CO2", "mmHg"),
            ("FIO2", "FiO2", "%"),
            ("Agent_Et", "End-tidal agent conc.", "vol%"),
            ("Agent_Fi", "Inspired agent conc.", "vol%"),
            ("MAC_Et", "Age-adj MAC (Et)", ""),
            ("HR", "Heart rate", "bpm"),
            ("SPO2", "SpO2", "%"),
            ("nMAP", "Noninvasive MAP", "mmHg"),
        ]:
            if col in ts.columns:
                lines.append(_series_stats(ts[col], name, unit))
        if "Agent" in ts.columns:
            agents = ts["Agent"].dropna().astype(str)
            if not agents.empty:
                mode = agents.mode().iloc[0] if not agents.mode().empty else agents.iloc[0]
                lines.append(
                    f"- Agent codes present: {', '.join(sorted(agents.unique()))} "
                    f"(modal={mode})"
                )

    # --- flags / episodes ---
    if flags is not None and not flags.empty:
        fsub = flags[flags["PID"].astype(str) == pid]
        if not fsub.empty:
            lines.append("\n## Rule-based anomaly flags")
            counts = fsub.groupby(["rule_id", "severity"]).size().reset_index(name="n")
            counts = counts.sort_values("n", ascending=False)
            for _, r in counts.head(max_flag_lines).iterrows():
                lines.append(
                    f"- {r['rule_id']} [{r['severity']}]: {int(r['n'])} minute-flags"
                )
            by_phase = phase_counts_line(fsub)
            if by_phase:
                lines.append(f"- {by_phase}")
    if episodes is not None and not episodes.empty:
        esub = episodes[episodes["PID"].astype(str) == pid]
        if not esub.empty and "rule_id" in esub.columns:
            lines.append("\n## Flag episodes (contiguous runs)")
            show = esub
            if "t_start_min" in esub.columns:
                show = esub.sort_values("t_start_min")
            for _, r in show.head(20).iterrows():
                t0 = r.get("t_start_min", "?")
                t1 = r.get("t_end_min", "?")
                lines.append(
                    f"- {r.get('rule_id')} [{r.get('severity')}]: "
                    f"t={_fmt_num(t0, 0)}–{_fmt_num(t1, 0)} min"
                    + phase_note(r)
                    + (
                        f" ({r.get('message')})"
                        if "message" in r.index and pd.notna(r.get("message"))
                        else ""
                    )
                )

    # --- procedure events ---
    ev = events
    if (ev is None or ev.empty) and include_emr_extras:
        try:
            ev = load_case_events(pid, emr_dir())
        except Exception:
            ev = pd.DataFrame()
    if ev is not None and not ev.empty:
        es = ev[ev["PID"].astype(str) == pid] if "PID" in ev.columns else ev
        if not es.empty:
            lines.append("\n## Procedure events")
            name_col = "Event_name" if "Event_name" in es.columns else es.columns[-1]
            time_col = "Event_time" if "Event_time" in es.columns else None
            for _, r in es.iterrows():
                t = r[time_col] if time_col else ""
                lines.append(f"- {t}: {r[name_col]}")

    # --- medications ---
    meds = medications
    if (meds is None or meds.empty) and include_emr_extras:
        try:
            meds = load_case_medications(pid, emr_dir())
        except Exception:
            meds = pd.DataFrame()
    if meds is not None and not meds.empty:
        m = meds[meds["PID"].astype(str) == pid] if "PID" in meds.columns else meds
        if not m.empty:
            lines.append("\n## Medications documented in SIS")
            # Aggregate by drug for readability
            if "Drug_name" in m.columns:
                g = (
                    m.groupby("Drug_name", dropna=False)
                    .agg(
                        n=("Drug_name", "size"),
                        dose_examples=(
                            "Dose",
                            lambda s: ", ".join(
                                str(x) for x in s.dropna().unique()[:5]
                            ),
                        )
                        if "Dose" in m.columns
                        else ("Drug_name", "size"),
                        units=(
                            "Drug_units",
                            lambda s: ", ".join(
                                str(x) for x in s.dropna().unique()[:3]
                            ),
                        )
                        if "Drug_units" in m.columns
                        else ("Drug_name", "size"),
                    )
                    .reset_index()
                    .sort_values("n", ascending=False)
                )
                for _, r in g.head(max_meds).iterrows():
                    dose = r.get("dose_examples", "")
                    units = r.get("units", "")
                    lines.append(
                        f"- {r['Drug_name']}: {int(r['n'])} record(s)"
                        + (f"; dose samples: {dose} {units}".rstrip() if dose else "")
                    )
            else:
                lines.append(f"- {len(m)} medication rows (columns: {list(m.columns)})")

    # --- wave availability ---
    wdir = wave_dir()
    wcase = waveform_case_dir(pid, wdir)
    lines.append("\n## Waveform data")
    if wdir is None:
        lines.append("- Wave root not configured in the app.")
    elif wcase is not None:
        try:
            n_files = sum(1 for _ in wcase.rglob("*") if _.is_file())
        except OSError:
            n_files = -1
        lines.append(f"- Waveform folder present: {wcase} ({n_files} files)")
        lines.append(
            "- High-fidelity waveforms are not decoded in this text context; "
            "only path presence is noted."
        )
    else:
        lines.append(f"- No matching Waveforms folder found under {wdir} for this PID.")

    lines.append(
        "\n## Assistant instructions reminder\n"
        "- Base answers only on the sections above.\n"
        "- If asked about outcomes (mortality, complications), state SIS does not "
        "include postoperative outcomes in this dashboard extract."
    )
    return "\n".join(lines)
