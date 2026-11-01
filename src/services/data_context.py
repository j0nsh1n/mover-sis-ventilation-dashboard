"""Shared source, preset, and background-job generation for real-data modules.

One context records the selected EMR, the processed directory, the preset,
and the threshold identity. It is not a second data loader. A source or
threshold change bumps a generation and drops memoised fetches. Jobs stamp
the generation they started in, and a later generation rejects them even
when the folders have been switched back.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.services.case_fetch import cache_lock, discard_stored_caches


@dataclass(frozen=True)
class JobStamp:
    """Generation captured when a background job starts."""

    generation: int


class DataContext:
    def __init__(self) -> None:
        self.generation = 0
        self.emr: Path | None = None
        self.processed: Path | None = None
        self.preset = "default"
        self.preset_recorded = True
        self.thresholds_sha256: str | None = None
        self.chat_model: str | None = None
        self.embed_model = "nomic-embed-text"
        self._paths_bound = False
        self._preset_bound = False
        self._models_bound = False

    def note_paths(self, emr: Path | str, processed: Path | str) -> bool:
        """Record the selected source. The first bind does not invalidate."""
        emr_path = Path(emr).expanduser().resolve()
        processed_path = Path(processed).expanduser().resolve()
        if not self._paths_bound:
            self.emr = emr_path
            self.processed = processed_path
            self._paths_bound = True
            return False
        if (emr_path, processed_path) == (self.emr, self.processed):
            return False
        self.emr = emr_path
        self.processed = processed_path
        self._bump()
        return True

    def note_preset(
        self,
        preset: str,
        *,
        recorded: bool,
        thresholds_sha256: str | None,
    ) -> bool:
        """Record the preset. A new preset name or a new real threshold hash invalidates.

        A missing hash is not an identity. Clearing the loaded sample, and the
        first recorded hash after that, do not bump the generation.
        """
        incoming = _real_threshold_hash(thresholds_sha256)
        preset_changed = self._preset_bound and preset != self.preset
        hash_changed = (
            self._preset_bound
            and incoming is not None
            and self.thresholds_sha256 is not None
            and incoming != self.thresholds_sha256
        )
        self.preset = preset
        self.preset_recorded = recorded
        if incoming is not None:
            self.thresholds_sha256 = incoming
        self._preset_bound = True
        if preset_changed or hash_changed:
            self._bump()
        return preset_changed or hash_changed

    def note_models(self, *, chat_model: str | None, embed_model: str) -> bool:
        """Record the saved local models. Only an embedding-model change is reported.

        The chat model does not change analyzed data. Embedding vectors are
        already stored under their model id, so this does not bump the
        data generation or clear case fetches.
        """
        chat = (chat_model or "").strip() or None
        embed = (embed_model or "").strip() or "nomic-embed-text"
        embed_changed = self._models_bound and embed != self.embed_model
        self.chat_model = chat
        self.embed_model = embed
        self._models_bound = True
        return embed_changed

    def stamp(self) -> JobStamp:
        return JobStamp(self.generation)

    def accepts(self, stamp: JobStamp) -> bool:
        return stamp.generation == self.generation

    def _bump(self) -> None:
        with cache_lock:
            self.generation += 1
            discard_stored_caches()


_current: DataContext | None = None


def _real_threshold_hash(value: str | None) -> str | None:
    if isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value):
        return value
    return None


def current_context() -> DataContext:
    global _current
    if _current is None:
        _current = DataContext()
    return _current


def reset_context() -> DataContext:
    """Replace the process context. Tests use this so generations do not leak."""
    global _current
    _current = DataContext()
    return _current
