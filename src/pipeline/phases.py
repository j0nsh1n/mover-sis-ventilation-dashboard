"""Anesthesia phase per minute: pre_induction, induction, maintenance, emergence, post_emergence.

Boundaries come from procedure events (intubation / extubation, matched by the
patterns in ``thresholds.yaml`` ``phases``) and fall back to the case times
when a surgery has no usable event. ``phase_source`` records which.

The event-name patterns are unverified against the real MOVER data; see the
"Procedure event names" table of ``python -m src.pipeline.profile``.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

PHASES = ("pre_induction", "induction", "maintenance", "emergence", "post_emergence")
PHASE_SOURCES = ("events", "mixed", "fallback")

# Signals that show the ventilator circuit is connected (mask or tube).
VENT_SIGNAL_COLS = ["TV", "RR", "PEEP", "PIP", "ETCO2", "FIO2", "Agent_Et", "Agent_Fi"]


def compile_patterns(patterns: list[str]) -> re.Pattern[str] | None:
    """Join patterns into one case-insensitive regex (None when there are none)."""
    patterns = [p for p in patterns if isinstance(p, str) and p.strip()]
    if not patterns:
        return None
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


def classify_event_names(names: pd.Series, phases_cfg: dict) -> pd.Series:
    """Label each event name ``intubation``, ``extubation`` or ``""`` by the configured patterns.

    A name that matches both lists counts as an extubation.
    """
    intub = compile_patterns(phases_cfg.get("intubation_patterns") or [])
    extub = compile_patterns(phases_cfg.get("extubation_patterns") or [])
    text = names.astype(str)
    is_ext = (
        text.map(lambda n: bool(extub.search(n))) if extub else pd.Series(False, index=names.index)
    )
    is_int = (
        text.map(lambda n: bool(intub.search(n))) if intub else pd.Series(False, index=names.index)
    )
    out = pd.Series("", index=names.index, dtype="object")
    out[is_int & ~is_ext] = "intubation"
    out[is_ext] = "extubation"
    return out


def _event_times(
    events: pd.DataFrame | None, phases_cfg: dict
) -> dict[str, tuple[list[pd.Timestamp], list[pd.Timestamp]]]:
    """PID -> (intubation times, extubation times), sorted, from matching events."""
    if events is None or events.empty or not {"PID", "Event_time", "Event_name"} <= set(events.columns):
        return {}
    ev = events.dropna(subset=["Event_time"]).copy()
    ev["PID"] = ev["PID"].astype(str)
    ev["_role"] = classify_event_names(ev["Event_name"], phases_cfg)
    ev = ev[ev["_role"] != ""]
    out: dict[str, tuple[list[pd.Timestamp], list[pd.Timestamp]]] = {}
    for (pid, role), g in ev.groupby(["PID", "_role"]):
        times = sorted(pd.Timestamp(t).floor("min") for t in g["Event_time"])
        ints, exts = out.setdefault(str(pid), ([], []))
        (ints if role == "intubation" else exts).extend(times)
    return out


def _case_boundaries(
    g: pd.DataFrame,
    event_times: tuple[list[pd.Timestamp], list[pd.Timestamp]] | None,
    cfg: dict,
) -> dict:
    """Phase boundaries (minute-floored timestamps or NaT) and source for one surgery."""
    obs = g["Obs_time"]
    first, last = obs.min(), obs.max()
    pad = pd.Timedelta(minutes=float(cfg.get("event_pad_min", 5)))
    after = pd.Timedelta(minutes=float(cfg.get("induction_after_min", 10)))
    before = pd.Timedelta(minutes=float(cfg.get("emergence_before_min", 10)))

    def _floor(col: str) -> pd.Timestamp:
        if col not in g.columns:
            return pd.NaT
        v = g[col].dropna()
        return pd.Timestamp(v.iloc[0]).floor("min") if len(v) else pd.NaT

    case_start, case_end = _floor("case_start"), _floor("case_end")

    ints, exts = event_times if event_times else ([], [])
    ints = [t for t in ints if first - pad <= t <= last + pad]
    exts = [t for t in exts if first - pad <= t <= last + pad]
    intub = ints[0] if ints else pd.NaT  # earliest: a later re-intubation does not move induction
    if pd.notna(intub):
        exts = [t for t in exts if t > intub]
    extub = exts[-1] if exts else pd.NaT  # latest extubation after the intubation

    have_int, have_ext = pd.notna(intub), pd.notna(extub)
    source = "events" if have_int and have_ext else "mixed" if have_int or have_ext else "fallback"

    if have_int:
        pre_end = intub
        ind_end = intub + after
    else:
        # Airway start unknown: first ventilator minute, but never after incision
        cols = [c for c in VENT_SIGNAL_COLS if c in g.columns]
        vent_minutes = obs[g[cols].notna().any(axis=1)] if cols else obs.iloc[0:0]
        proxy = vent_minutes.min() if len(vent_minutes) else first
        pre_end = min(proxy, case_start) if pd.notna(case_start) else proxy
        ind_end = case_start if pd.notna(case_start) else pre_end
    if have_ext:
        emerg_start = extub - before
        post_start = extub
    else:
        emerg_start = case_end
        post_start = pd.NaT

    # Keep the boundaries ordered (short cases, odd events)
    ind_end = max(ind_end, pre_end)
    if pd.notna(emerg_start):
        emerg_start = max(emerg_start, ind_end)
    if pd.notna(post_start):
        post_start = max(post_start, emerg_start if pd.notna(emerg_start) else ind_end)
    return {
        "_pre_end": pre_end,
        "_ind_end": ind_end,
        "_emerg_start": emerg_start,
        "_post_start": post_start,
        "phase_source": source,
    }


def assign_phases(
    ts: pd.DataFrame,
    events: pd.DataFrame | None,
    thresholds: dict,
) -> pd.DataFrame:
    """Add ``phase`` (per minute) and ``phase_source`` (per surgery) to *ts*.

    *events* is the cleaned procedure-event table (``PID``, ``Event_time``,
    ``Event_name``) and may be empty or None. Returns *ts* unchanged when
    thresholds have no ``phases`` section.
    """
    cfg = thresholds.get("phases")
    if not cfg or ts.empty or "Obs_time" not in ts.columns:
        return ts

    event_map = _event_times(events, cfg)
    rows = []
    for pid, g in ts.groupby("PID", sort=False):
        b = _case_boundaries(g, event_map.get(str(pid)), cfg)
        b["PID"] = pid
        rows.append(b)
    bounds = pd.DataFrame(rows)
    for c in ("_pre_end", "_ind_end", "_emerg_start", "_post_start"):
        bounds[c] = pd.to_datetime(bounds[c])

    out = ts.drop(columns=[c for c in ("phase", "phase_source") if c in ts.columns])
    m = out[["PID", "Obs_time"]].merge(bounds, on="PID", how="left")
    t = m["Obs_time"]
    # NaT comparisons are False, so an unknown boundary simply never triggers
    conds = [
        t >= m["_post_start"],
        t >= m["_emerg_start"],
        t >= m["_ind_end"],
        t >= m["_pre_end"],
    ]
    phase = np.select(conds, ["post_emergence", "emergence", "maintenance", "induction"], "pre_induction")
    out["phase"] = phase
    out["phase_source"] = m["phase_source"].to_numpy()
    return out
