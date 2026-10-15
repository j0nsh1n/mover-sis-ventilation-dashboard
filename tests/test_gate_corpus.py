"""The directive check against a fixed corpus of descriptive and directive sentences."""

from pathlib import Path

import yaml

from src.llm.agent import find_directive_phrases

_CORPUS = yaml.safe_load(
    (Path(__file__).parent / "fixtures" / "gate_drafts.yaml").read_text(encoding="utf-8")
)


def test_corpus_has_enough_of_each_kind():
    assert len(_CORPUS["descriptive"]) >= 30
    assert len(_CORPUS["directive"]) >= 30


def test_gate_passes_descriptive_and_catches_directive_sentences():
    false_positives = {
        s: hits for s in _CORPUS["descriptive"] if (hits := find_directive_phrases(s))
    }
    false_negatives = [s for s in _CORPUS["directive"] if not find_directive_phrases(s)]
    print(f"false positives: {len(false_positives)}  false negatives: {len(false_negatives)}")
    assert not false_positives, false_positives
    assert not false_negatives, false_negatives
