"""OpenAI-style tool schemas + text recovery (Daily Scheduler pattern).

Tools are *declared* here and *executed* in ``tools.py``. The agent loop prefers
Ollama's native ``tool_calls`` channel; ``extract_tool_calls`` recovers when a
light model prints JSON instead.
"""

from __future__ import annotations

import json
import re
from typing import Any

MAX_TOOL_ROUNDS = 8

# Shared by every tool that ranks or searches surgeries
SCOPE_PARAM: dict[str, Any] = {
    "type": "string",
    "enum": ["auto", "full", "sample"],
    "description": (
        "'full' = every ventilated surgery from the full-EMR scan; 'sample' = the "
        "loaded analyzed sample only; 'auto' (default) = full when a scan is loaded."
    ),
}

AI_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "corpus_overview",
            "description": (
                "Scope (full-EMR scan vs loaded sample and EMR totals), counts, agent "
                "mix, anomaly-score range, active PID and focus list. Call first when "
                "the user has not named a case or asks about the whole dataset."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_cases",
            "description": (
                "Keyword search over procedures, PID, agent, and flag rule IDs. "
                "Sets the focus list to matching PIDs (sorted by anomaly score)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords/phrases (e.g. 'hysterectomy sevoflurane pip_high')",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max cases to return (1–30, default 12)",
                    },
                    "scope": SCOPE_PARAM,
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_similar_cases",
            "description": (
                "Find cases similar to a vignette: procedure keywords + optional agent "
                "and flag terms. Preferred for 'theoretical patient' / teaching vignettes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "procedure": {
                        "type": "string",
                        "description": "Procedure keywords (e.g. laparoscopic cholecystectomy)",
                    },
                    "agent": {
                        "type": "string",
                        "description": "Primary volatile/agent name fragment (e.g. sevoflurane)",
                    },
                    "flags": {
                        "type": "string",
                        "description": "Flag rule keywords (e.g. pip_high etco2)",
                    },
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                    "scope": SCOPE_PARAM,
                },
                "required": ["procedure"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "top_anomaly_cases",
            "description": (
                "Highest anomaly_score cases (or per observed hour); sets focus list. "
                "The result's SCOPE line says whether it covers every ventilated surgery."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "How many (default 10)"},
                    "scope": SCOPE_PARAM,
                    "per_hour": {
                        "type": "boolean",
                        "description": "Rank by score per observed hour instead of total",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "filter_by_agent",
            "description": "Cases whose primary agent name contains the given string.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string"},
                    "limit": {"type": "integer"},
                    "scope": SCOPE_PARAM,
                },
                "required": ["agent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rule_case_counts",
            "description": (
                "For each flag rule: how many surgeries it fired in (and %), episodes, "
                "and flagged minutes. Use for 'how common is …' questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": SCOPE_PARAM,
                    "severity": {
                        "type": "string",
                        "enum": ["", "info", "warn", "critical"],
                        "description": "Only count episodes of this severity",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_case",
            "description": (
                "Select the active surgery by PID (prefix allowed if unique) and return "
                "the full grounded briefing. REQUIRED before discussing management detail."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pid": {"type": "string", "description": "Surgery PID or unique prefix"},
                },
                "required": ["pid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_case_briefing",
            "description": "Grounded briefing for pid (or the active case if pid omitted).",
            "parameters": {
                "type": "object",
                "properties": {"pid": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_management",
            "description": (
                "Structured management snapshot for a case: agent strategy, vent ranges, "
                "flags, meds, events — what was *documented* in the extract (not advice)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pid": {"type": "string", "description": "PID or omit for active case"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_cases",
            "description": (
                "Side-by-side management snapshots for 2–5 PIDs (comma-separated or list)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pids": {
                        "type": "string",
                        "description": "Comma-separated PIDs (2–5)",
                    },
                },
                "required": ["pids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_case_flags",
            "description": "Rule flag counts for a case (research screens, not diagnoses).",
            "parameters": {
                "type": "object",
                "properties": {"pid": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rule_reference",
            "description": "Threshold rule definitions from app config.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wave_status",
            "description": "Whether a waveform folder exists for a PID.",
            "parameters": {
                "type": "object",
                "properties": {"pid": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_selection",
            "description": (
                "VERIFICATION tool (like list_blocks in the scheduler). Re-reads active "
                "PID + focus list with short factual lines. Call once before the final "
                "answer after multi-step retrieval."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

AI_TOOL_NAMES = {t["function"]["name"] for t in AI_TOOLS}


def strip_think(s: str) -> str:
    """Remove reasoning-model <think>…</think> blocks (DeepSeek-R1 style)."""
    if not s:
        return s
    return re.sub(r"<think>[\s\S]*?</think>", "", s, flags=re.I).strip()


def _json_spans(s: str):
    depth, start = 0, None
    for i, ch in enumerate(s):
        if ch in "{[":
            if depth == 0:
                start = i
            depth += 1
        elif ch in "}]" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                yield s[start : i + 1]
                start = None


def looks_like_tool_text(s: str) -> bool:
    t = (s or "").lstrip()
    return (
        t.startswith("{")
        or t.startswith("[")
        or t.startswith("<|python_tag|>")
        or t.startswith("```")
        or t.startswith("TOOL:")
        or "<|python_tag|>" in (s or "")[:40]
    )


def extract_tool_calls(text: str) -> list[dict[str, Any]]:
    """
    Recover tool calls printed as content instead of native tool_calls.

    Returns list of {"name", "args"} for known tools only.
    Also recovers legacy TOOL:/END blocks.
    """
    if not text:
        return []
    found: list[dict[str, Any]] = []

    # Legacy light-model format
    for m in re.finditer(
        r"TOOL:\s*(?P<name>[A-Za-z0-9_]+)\s*\n(?P<body>.*?)(?:\nEND\b|\Z)",
        text,
        re.I | re.S,
    ):
        name = m.group("name").strip()
        if name not in AI_TOOL_NAMES:
            continue
        args: dict[str, Any] = {}
        for line in (m.group("body") or "").splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            k, v = line.split(":", 1)
            key = k.strip().lower()
            val = v.strip().strip('"').strip("'")
            if key in {"limit", "n", "max"}:
                try:
                    args["limit" if key != "limit" else key] = int(val)
                except ValueError:
                    args[key] = val
            else:
                if key == "q":
                    key = "query"
                args[key] = val
        found.append({"name": name, "args": args})

    s = text.replace("<|python_tag|>", " ").replace("<|eom_id|>", " ")
    for fence in (
        "```json",
        "```tool_code",
        "```python",
        "```tool_call",
        "```",
    ):
        s = s.replace(fence, " ")
    for span in _json_spans(s):
        try:
            obj = json.loads(span)
        except Exception:
            continue
        for it in obj if isinstance(obj, list) else [obj]:
            if not isinstance(it, dict):
                continue
            if isinstance(it.get("function"), dict):
                it = it["function"]
            name = it.get("name")
            if name not in AI_TOOL_NAMES:
                continue
            args = it.get("arguments")
            if args is None:
                args = it.get("parameters", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            if not isinstance(args, dict):
                args = {}
            found.append({"name": name, "args": args})

    # Dedupe consecutive identical calls
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for f in found:
        key = f"{f['name']}:{json.dumps(f['args'], sort_keys=True)}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)
    return deduped
