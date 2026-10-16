"""The source extract behind a question about one minute of one case."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from src.services.retrieval import SearchCandidate

WINDOW_MINUTES = 10
UNITS = {"PIP": "cmH₂O", "ETCO2": "mmHg", "HR": "bpm"}
# Minute indexes can carry float tails from the incision anchor
_EDGE = 1e-9


def _value(value: object) -> str:
    return "missing" if pd.isna(value) else f"{float(value):g}"  # type: ignore[arg-type]


def _column(rows: pd.DataFrame, signal: str) -> pd.Series:
    if signal in rows:
        return pd.to_numeric(rows[signal], errors="coerce")
    return pd.Series(float("nan"), index=rows.index)


def _between(rows: pd.DataFrame, low: float, high: float) -> pd.DataFrame:
    return rows[(rows.index >= low - _EDGE) & (rows.index <= high + _EDGE)]


def _signal_stats(rows: pd.DataFrame, signal: str, start: float, end: float) -> str:
    window = _column(_between(rows, start, end), signal)
    seen = window.dropna()
    summary = (
        f"min {seen.min():.6g}, max {seen.max():.6g}, mean {seen.mean():.6g}"
        if len(seen) else "min missing, max missing, mean missing"
    )
    return f"{signal}: {summary}; {len(window) - len(seen)} missing minutes"


def build_minute_extract(
    *,
    candidate: SearchCandidate,
    rows: pd.DataFrame,
    minute: float,
    signals: Sequence[str],
    episode: Mapping[str, object] | None,
    episode_label: str = "",
    episode_signals: Sequence[str] = (),
    threshold_lines: Sequence[str] = (),
) -> str:
    """Only the selected case: its search record, the rows within ten minutes of the cursor,
    the episode covering the cursor and that rule's thresholds.

    ``rows`` holds one row per minute indexed by ``t_min``, so a minute with no sample is
    present with missing values.
    """
    window = _between(rows, minute - WINDOW_MINUTES, minute + WINDOW_MINUTES)
    names = list(dict.fromkeys(signals))
    heads = [f"{name} ({UNITS[name]})" if name in UNITS else name for name in names]
    at = _between(rows, minute, minute)
    lines = [
        f"SOURCE EXTRACT ({candidate.source_label}) PID={candidate.pid}",
        candidate.source_text,
        "",
        f"SELECTED MINUTE: {minute:g} min relative to incision",
        "At the selected minute: " + "; ".join(
            f"{name} {_value(_column(at, name).iloc[0]) if len(at) else 'missing'}" for name in names
        ),
    ]
    if len(window):
        lines.append(
            f"SOURCE ROWS from {window.index[0]:g} to {window.index[-1]:g} min "
            f"(the minutes this case has within {WINDOW_MINUTES} of the cursor)"
        )
        lines.append(" | ".join(["t_min", *heads]))
        columns = {name: _column(window, name) for name in names}
        lines.extend(
            " | ".join([f"{t:g}", *(_value(columns[name].iloc[i]) for name in names)])
            for i, t in enumerate(window.index)
        )
    else:
        lines.append("SOURCE ROWS: this case has no rows within the window.")
    lines.append("")
    if episode is None:
        lines.append("No flagged episode covers the selected minute.")
        return "\n".join(lines)
    start, end = float(episode["t_start_min"]), float(episode["t_end_min"])  # type: ignore[arg-type]
    lines.append(
        f"EPISODE COVERING THE SELECTED MINUTE: {episode_label or episode['rule_id']} "
        f"({episode['rule_id']}), {episode['severity']}, {start:g} to {end:g} min"
    )
    mean = episode.get("value_mean")
    if mean is not None and pd.notna(mean):
        lines.append(f"Mean flagged value: {float(mean):g}")  # type: ignore[arg-type]
    lines.extend(_signal_stats(rows, signal, start, end) for signal in episode_signals)
    lines.extend(threshold_lines)
    return "\n".join(lines)
