"""Keyword search / filtering over case summary tables."""

from __future__ import annotations

import re

import pandas as pd

# Columns scanned for free-text keyword search (if present)
DEFAULT_SEARCH_COLS = (
    "PID",
    "Procedure",
    "Procedure_short",
    "primary_agent",
    "primary_agent_name",
    "Gender",
    "top_rules",
    "Age",
)


def tokenize_query(query: str) -> list[str]:
    """Split query into keywords; supports quoted phrases."""
    q = (query or "").strip()
    if not q:
        return []
    # "exact phrase" tokens + remaining words
    phrases = re.findall(r'"([^"]+)"', q)
    rest = re.sub(r'"[^"]+"', " ", q)
    words = [w for w in re.split(r"\s+", rest.strip()) if w]
    return phrases + words


def filter_cases_by_keywords(
    cases: pd.DataFrame,
    query: str,
    *,
    flags: pd.DataFrame | None = None,
    columns: tuple[str, ...] | list[str] = DEFAULT_SEARCH_COLS,
    match_all: bool = True,
) -> pd.DataFrame:
    """
    Filter case table by keywords.

    - Case-insensitive substring match across selected columns
    - Also matches flag ``rule_id`` values for the case when ``flags`` is provided
    - ``match_all=True``: every keyword must match somewhere (AND)
    - ``match_all=False``: any keyword matches (OR)
    """
    if cases is None or cases.empty:
        return cases.iloc[0:0].copy() if cases is not None else pd.DataFrame()

    tokens = tokenize_query(query)
    if not tokens:
        return cases

    cols = [c for c in columns if c in cases.columns]
    if not cols and (flags is None or flags.empty):
        return cases.iloc[0:0].copy()

    # Pre-build per-case searchable blob
    parts = []
    for c in cols:
        parts.append(cases[c].astype(str).fillna(""))
    if parts:
        blob = parts[0]
        for p in parts[1:]:
            blob = blob + " " + p
    else:
        blob = pd.Series([""] * len(cases), index=cases.index)

    if flags is not None and not flags.empty and "PID" in flags.columns and "rule_id" in flags.columns:
        rule_blob = (
            flags.assign(rule_id=flags["rule_id"].astype(str))
            .groupby("PID")["rule_id"]
            .agg(lambda s: " ".join(sorted(set(s))))
        )
        # map by PID string
        pid_str = cases["PID"].astype(str)
        extra = pid_str.map(rule_blob).fillna("")
        blob = blob + " " + extra.values

    blob_l = blob.str.lower()
    tokens_l = [t.lower() for t in tokens]

    if match_all:
        mask = pd.Series(True, index=cases.index)
        for t in tokens_l:
            mask &= blob_l.str.contains(re.escape(t), regex=True, na=False)
    else:
        mask = pd.Series(False, index=cases.index)
        for t in tokens_l:
            mask |= blob_l.str.contains(re.escape(t), regex=True, na=False)

    return cases.loc[mask].copy()
