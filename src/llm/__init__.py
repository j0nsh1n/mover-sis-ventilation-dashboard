"""Local LLM helpers for case Q&A (Ollama)."""

from src.llm.case_context import build_case_context
from src.llm.ollama_client import OllamaClient, OllamaError
from src.llm.service import (
    ensure_ollama,
    find_ollama_binary,
    probe_ollama,
    start_ollama,
    stop_ollama,
    unload_ollama_model,
)

__all__ = [
    "OllamaClient",
    "OllamaError",
    "build_case_context",
    "ensure_ollama",
    "find_ollama_binary",
    "probe_ollama",
    "start_ollama",
    "stop_ollama",
    "unload_ollama_model",
]
