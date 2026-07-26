"""Local LLM helpers for case Q&A (Ollama)."""

from src.llm.case_context import build_case_context
from src.llm.ollama_client import OllamaClient, OllamaError

__all__ = ["OllamaClient", "OllamaError", "build_case_context"]
