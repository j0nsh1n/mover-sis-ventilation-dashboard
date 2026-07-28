"""Curated Ollama models + per-family guidance (Daily Scheduler pattern)."""

from __future__ import annotations

from typing import Any

MODEL_PROFILES: dict[str, dict[str, Any]] = {
    "qwen3:14b": {
        "badge": "★ Best everyday",
        "when": (
            "Default for tool-heavy case retrieval. Reliable native tools on 12–16 GB GPUs."
        ),
    },
    "qwen2.5:14b": {
        "badge": "Solid fallback",
        "when": "Previous default — strong tools if qwen3 misbehaves.",
    },
    "mistral-small3.1:24b": {
        "badge": "Strongest (tight fit)",
        "when": "Best quality tool-calling when VRAM allows (~15 GB).",
    },
    "gemma4": {
        "badge": "Try / verify first",
        "when": "Capable; less battle-tested for multi-step tool loops.",
    },
    "deepseek-r1:14b": {
        "badge": "Deep reasoning",
        "when": "May narrate instead of calling tools; app strips <think> blocks.",
    },
    "gpt-oss:20b": {
        "badge": "Open weights",
        "when": "Verify tool-calling before multi-case analysis.",
    },
    "glm-4.7-flash": {
        "badge": "Large MoE",
        "when": "Needs substantial VRAM; verify tools first.",
    },
}

RECOMMENDED_MODELS = list(MODEL_PROFILES.keys())

_R1 = (
    "\n\n══ MODEL-SPECIFIC — DeepSeek-R1 ══\n"
    "Your <think> block is hidden. Only TOOL CALLS change state.\n"
    "1. Think briefly, then call tools — do not narrate the plan instead of acting.\n"
    "2. Prefer native function-calling. If forced to text, emit ONE "
    '{"name":"…","arguments":{…}} object and nothing else.\n'
    "3. After retrieval, call verify_selection once, then answer.\n"
    "4. Never invent PIDs, drugs, or numbers.\n"
)

_QWEN3 = (
    "\n\n══ MODEL-SPECIFIC — Qwen3 ══\n"
    "/no_think\n"
    "Decide fast and call tools. One bulk search/find_similar, then select_case, "
    "then summarize_management / compare_cases. Verify with verify_selection. "
    "Native tool channel only — no printed JSON unless runtime has no tools.\n"
)

_QWEN25 = (
    "\n\n══ MODEL-SPECIFIC — Qwen2.5 ══\n"
    "ALWAYS use tools for corpus facts. Prefer native tools. "
    "find_similar_cases → select_case → summarize_management → verify_selection → answer.\n"
)

_GENERIC = (
    "\n\n══ MODEL-SPECIFIC ══\n"
    "1. Call tools for any case fact — prose alone is not evidence.\n"
    "2. Prefer native tool-calling; fallback single JSON object if needed.\n"
    "3. verify_selection before final multi-case answers.\n"
    "4. Exact arg shapes: pid strings, query strings, limit integers.\n"
)


def model_guidance(model: str) -> str:
    m = (model or "").lower()
    if "deepseek" in m or "r1" in m:
        return _R1
    if "qwen3" in m:
        return _QWEN3
    if "qwen2.5" in m or "qwen2" in m:
        return _QWEN25
    if "gpt-oss" in m or "mistral" in m or "gemma" in m or "glm" in m:
        return _GENERIC
    return _GENERIC


def model_when_text(model: str) -> str:
    tag = (model or "").strip()
    # strip :latest
    key = tag[:-7] if tag.endswith(":latest") else tag
    prof = MODEL_PROFILES.get(key) or MODEL_PROFILES.get(tag)
    if prof:
        return f"{prof.get('badge', '')}: {prof.get('when', '')}".strip()
    return "Installed model — verify tool-calling on a search + select turn."
