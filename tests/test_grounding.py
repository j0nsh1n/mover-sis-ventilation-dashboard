"""Every number in a research answer must come from its sources or the question."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.llm.agent import answer_problems, run_agent, verify_answer
from src.llm.grounding import (
    REMOVED_MARK,
    REMOVED_NOTE,
    find_ungrounded_numbers,
    remove_ungrounded_numbers,
)
from src.llm.ollama_client import ChatResult
from src.llm.prompts import VERIFICATION_FOOTER
from src.llm.retrieve import answer_from_retrieval
from src.llm.tools import SessionState
from src.services.retrieval import RetrievalEngine, build_analysis_facts

PROTOTYPE_DRAFTS = Path(__file__).parent / "fixtures" / "drive_prototype.yaml"


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    """A seeded synthetic EMR, its pipeline output, and three real extracts."""
    from src.pipeline.run import run_pipeline
    from src.pipeline.synthetic import generate_synthetic_emr

    root = tmp_path_factory.mktemp("grounding")
    mp = pytest.MonkeyPatch()
    mp.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    mp.setenv("MOVER_EMR_DIR", str(root / "EMR"))
    try:
        emr = generate_synthetic_emr(root / "EMR", n_cases=30, seed=8)
        data = run_pipeline(
            emr_dir=emr, output_dir=root / "processed", n_cases=30, seed=8,
            write_sample_csv=False, validate=True, preset="default",
        )
        engine = RetrievalEngine(
            emr=emr,
            cache_path=root / "cache.sqlite",
            analyzed_pids=data["cases"]["PID"].astype(str).tolist(),
            analysis_facts=build_analysis_facts(data["timeseries"], data["flags"]),
            candidate_limit=500,
        )
        candidates = list(engine.search("high airway pressure").candidates[:3])
        frames = {k: data[k] for k in ("cases", "timeseries", "flags", "episodes", "events")}
        extracts, fetched = engine.fetch_extracts(
            [c.pid for c in candidates], session_frames=frames
        )
        yield {"extracts": list(extracts), "candidates": candidates, "emr": emr}
    finally:
        mp.undo()


def _value(extract: str, label: str, bound: str = "max") -> str:
    """One figure as the extract prints it, for example the maximum PIP."""
    line = next(ln for ln in extract.splitlines() if ln.startswith(f"- {label}:"))
    low, high = re.search(r"min–max \[([-\d.]+)–([-\d.]+)\]", line).groups()
    return high if bound == "max" else low


@pytest.fixture
def extract(synthetic) -> str:
    return synthetic["extracts"][0]


def test_real_extract_figures_are_grounded(extract):
    peak = _value(extract, "Peak inspiratory pressure")
    answer = f"The maximum PIP was {peak} cmH2O and PEEP was a steady 5.0 cmH2O (n=177)."
    assert find_ungrounded_numbers(answer, [extract]) == []


def test_fabricated_value_is_caught(extract):
    assert float(_value(extract, "Peak inspiratory pressure")) < 30
    answer = "PIP reached 45 cmH2O in the first case."
    assert find_ungrounded_numbers(answer, [extract]) == ["45"]


@pytest.mark.parametrize(
    "spelling",
    ["{v} cmH2O", "{v} cm H₂O", "{v} cm H2O", "{v}cmH2O", "{v} mmHg", "{v}%", "{v} /min", "{v} bpm", "{v} mL"],
)
def test_unit_spellings_do_not_matter(extract, spelling):
    peak = _value(extract, "Peak inspiratory pressure")
    assert find_ungrounded_numbers("It was " + spelling.format(v=peak), [extract]) == []


@pytest.mark.parametrize(
    ("answer", "source", "grounded"),
    [
        ("PIP reached 45 cmH2O", "PIP max 22.09 cmH2O", False),
        ("PIP reached 22.09 cmH2O", "PIP max 22.09 cmH2O", True),
        ("peaked at 19.5 cmH2O", "max 19.509 cmH2O", True),
        ("peaked at 19.51 cmH2O", "max 19.509 cmH2O", True),
        ("peaked at 19.6 cmH2O", "max 19.509 cmH2O", False),
        ("peaked at 19.5091 cmH2O", "max 19.509 cmH2O", False),
        ("peaked at 20 cmH2O", "max 19.509 cmH2O", True),
        ("peaked at 19 cmH2O", "max 19.509 cmH2O", False),
        ("peaked at 20.0 cmH2O", "max 19.509 cmH2O", False),
        ("peaked at about 19 cmH2O", "max 19.509 cmH2O", True),
        ("peaked at ~20 cmH2O", "max 19.2 cmH2O", True),
        ("peaked at around 20 cmH2O", "max 19.2 cmH2O", True),
        ("peaked at approximately 20 cmH2O", "max 19.2 cmH2O", True),
        ("peaked at 20 cmH2O", "max 19.2 cmH2O", False),
        ("peaked at about 25 cmH2O", "max 22.09 cmH2O", False),
        ("FiO2 of 40%", "FiO2: median 40.0 %", True),
        ("a volume of 1,200 mL", "volume 1200 mL", True),
        ("a volume of 1200 mL", "volume 1,200 mL", True),
        ("a volume of 1,300 mL", "volume 1200 mL", False),
        ("it fell to −5 mmHg", "delta -5 mmHg", True),
        ("it fell to -5 mmHg", "delta −5 mmHg", True),
        ("it fell by 5 mmHg", "delta -5 mmHg", True),
        ("it fell to -6 mmHg", "delta -5 mmHg", False),
        ("it rose to -5 mmHg", "delta 5 mmHg", False),
        ("between 18 to 22 cmH2O", "range 18-22 cmH2O", True),
        ("between 18–22 cmH2O", "range 18 to 22 cmH2O", True),
        ("between 18 and 25 cmH2O", "range 18–22 cmH2O", False),
    ],
)
def test_allowances_and_limits(answer, source, grounded):
    assert (find_ungrounded_numbers(answer, [source]) == []) is grounded


def test_a_range_is_checked_end_by_end():
    assert find_ungrounded_numbers("between 18 to 25", ["range 18-22"]) == ["25"]
    assert find_ungrounded_numbers("between 17–22", ["range 18-22"]) == ["17"]


def test_the_question_grounds_a_number():
    assert find_ungrounded_numbers("The 58-year-old case", ["no figures"], "a 58-year-old") == []
    assert find_ungrounded_numbers("The 59-year-old case", ["no figures"], "a 58-year-old") == ["59"]


def test_negative_minute_from_a_real_extract(extract):
    assert "t=-" in extract
    start = re.search(r"t=(-\d+) to", extract).group(1)
    assert find_ungrounded_numbers(f"Monitoring began at minute {start}.", [extract]) == []
    assert find_ungrounded_numbers(f"Monitoring began at minute {start.replace('-', chr(0x2212))}.", [extract]) == []


@pytest.mark.parametrize(
    "answer",
    [
        "PIP was recorded [1] and ETCO2 in [2], [3].",
        "Both [A1] and [A2][A5] agree, see [1-3] and [A1, A2].",
        "See source 2 and extract 3, sources 1 and 2, abstract 1.",
        "1. First finding.\n2. Second finding.\n  3) Third finding.",
        "- 4. A bullet that starts with a number.",
        "The 1st, 2nd and 3rd cases.",
        "SYN00008aeb8a differs from 0123456789012345 here.",
        "ETCO2, SpO2, FiO2 and N2O were recorded; so were CO2 and O2.",
        "Per Smith et al., 2019 and (Jones, 2021) and Lee (2008) the effect holds.",
        "Compared three cases.",
        "Across 3 cases and 3 sources, PIP was recorded.",
    ],
)
def test_labels_are_not_values(answer):
    assert find_ungrounded_numbers(answer, ["a", "b", "c"]) == []


def test_a_count_must_equal_the_number_of_sources():
    assert find_ungrounded_numbers("Across 3 cases.", ["a", "b", "c"]) == []
    assert find_ungrounded_numbers("Across 4 cases.", ["a", "b", "c"]) == ["4"]
    assert find_ungrounded_numbers("Across 2 sources.", ["a", "b"]) == []


def test_an_index_beyond_the_sources_is_not_a_label():
    assert find_ungrounded_numbers("See source 7.", ["a", "b", "c"]) == ["7"]
    assert find_ungrounded_numbers("See [9].", ["a", "b", "c"]) == ["9"]


def test_a_year_outside_a_citation_is_a_number():
    assert find_ungrounded_numbers("PIP rose in 2019.", ["no figures"]) == ["2019"]


def test_model_written_app_markers_do_not_exempt_numbers():
    answer = (
        "PIP was recorded."
        "\n\n**Verification note:** the matched case is age 77."
        + VERIFICATION_FOOTER
    )
    assert find_ungrounded_numbers(answer, ["no figures"]) == ["77"]


@pytest.mark.parametrize("separator", ["–", "-", "—", " to ", " - "])
def test_signed_range_endpoints(separator):
    source = f"t=-5{separator}-2 min"
    assert find_ungrounded_numbers("ended at minute -2", [source]) == []
    assert find_ungrounded_numbers("ended at minute -3", [source]) == ["-3"]


@pytest.mark.parametrize(("answer", "source"), [
    ("Coverage was 87.6%.", "177 of 202 samples"),
    ("Coverage was 88%.", "177 of 202 samples"),
    ("Duration was 201 min.", "start 13:00:00; end 16:21:00"),
    ("Duration was 201 min.", "t=-12 to 189 minutes"),
    ("The difference was 3 cmH2O.", "pressure 19 to 22 cmH2O"),
])
def test_bounded_source_calculations(answer, source):
    assert find_ungrounded_numbers(answer, [source]) == []


@pytest.mark.parametrize(("answer", "source"), [
    ("Coverage was 87.7%.", "177 of 202 samples"),
    ("Duration was 200 min.", "start 13:00:00; end 16:21:00"),
    ("Duration was 17 min.", "pressure 19 cmH2O; minute 2"),
    ("The difference was 3 cmH2O.", "pressure 19 cmH2O; HR 22 bpm"),
])
def test_invalid_source_calculations(answer, source):
    assert find_ungrounded_numbers(answer, [source])


def test_question_values_are_not_used_for_calculations():
    assert find_ungrounded_numbers("Coverage was 87.6%.", [], "177 of 202") == ["87.6"]


@pytest.mark.parametrize("token", ["zero", "nine", "ninety-nine", "hundred", "forty-five", ".9", "4\u20095", "4\u202f5", "4 5", "4 5.9"])
def test_alternative_number_spellings(token):
    answer = f"**PIP {token} cmH2O** [1], SYN00008aeb8a."
    assert find_ungrounded_numbers(answer, ["PIP 22"]) == [token]
    replaced, count = remove_ungrounded_numbers(answer, ["PIP 22"])
    assert count == 1
    assert replaced == f"**PIP {REMOVED_MARK} cmH2O** [1], SYN00008aeb8a."


@pytest.mark.parametrize(("answer", "source"), [("PIP forty-five cmH2O", "PIP 45"), ("PIP .9 cmH2O", "PIP 0.9"), ("PIP 4 5 cmH2O", "PIP 45")])
def test_alternative_spellings_match_values(answer, source):
    assert find_ungrounded_numbers(answer, [source]) == []


@pytest.mark.parametrize(("source", "grounded"), [("28.5", True), ("28.499", False), ("28.3", False)])
def test_five_percent_hedge_boundary(source, grounded):
    assert (find_ungrounded_numbers("about 30 cmH2O", [source]) == []) is grounded


def test_each_ungrounded_number_is_replaced_in_its_sentence():
    text, removed = remove_ungrounded_numbers(
        "PIP reached 45 cmH2O. PEEP was 5 cmH2O.", ["PEEP 5 cmH2O, PIP max 22.09"]
    )
    assert removed == 1
    assert text == f"PIP reached {REMOVED_MARK} cmH2O. PEEP was 5 cmH2O."


def test_negative_number_is_replaced_with_its_sign():
    text, _ = remove_ungrounded_numbers("Pressure fell to −9 cmH2O.", ["PIP 22"])
    assert text == f"Pressure fell to {REMOVED_MARK} cmH2O."


def test_check_time_on_a_three_extract_answer(synthetic):
    import time

    answer = " ".join(
        f"PIP peaked at {_value(e, 'Peak inspiratory pressure')} cmH2O and ETCO2 at "
        f"{_value(e, 'End-tidal CO2')} mmHg, with HR {_value(e, 'Heart rate')} bpm."
        for e in synthetic["extracts"]
    )
    answer = (answer + " ") * (4000 // (len(answer) + 1) + 1)
    answer = answer[:4000]
    sources = synthetic["extracts"]
    assert find_ungrounded_numbers(answer, sources) == []
    best = min(
        (lambda t0: (find_ungrounded_numbers(answer, sources), time.perf_counter() - t0)[1])(
            time.perf_counter()
        )
        for _ in range(20)
    )
    assert best < 0.01, f"{best * 1000:.1f} ms"


# The agent gate


def _label_all_description(request: dict) -> ChatResult:
    keys = request["format"]["required"]
    return ChatResult(content=json.dumps(dict.fromkeys(keys, "description")))


class Drafts:
    """Returns the drafts in order; the advice check labels every sentence description."""

    def __init__(self, *drafts: str):
        self.drafts = drafts
        self.sent: list[list[dict]] = []

    def chat_turn(self, model, messages, **kwargs):
        if kwargs.get("format"):
            return _label_all_description(kwargs)
        self.sent.append([dict(m) for m in messages])
        return ChatResult(content=self.drafts[min(len(self.sent), len(self.drafts)) - 1])

    def rewrite_request(self) -> str:
        return self.sent[1][-1]["content"]


def _research(client, synthetic, question="Where does pressure peak?"):
    return answer_from_retrieval(
        question=question,
        model="fake",
        candidates=synthetic["candidates"],
        extracts=synthetic["extracts"],
        client=client,
    )


def _peak(synthetic) -> str:
    return _value(synthetic["extracts"][0], "Peak inspiratory pressure")


def _absent(synthetic, *candidates: str) -> list[str]:
    """Values none of the extracts holds, so a draft stating one is fabricating it."""
    absent = [c for c in candidates if find_ungrounded_numbers(c, synthetic["extracts"]) == [c]]
    assert len(absent) == len(candidates), "pick numbers the synthetic extracts do not hold"
    return absent


def test_fabricated_value_is_rewritten_and_the_grounded_draft_ships(synthetic):
    peak = _peak(synthetic)
    pid = synthetic["candidates"][0].pid
    (fake,) = _absent(synthetic, "73.4")
    client = Drafts(
        f"{pid} had PIP reach {fake} cmH2O.", f"{pid} had PIP reach {peak} cmH2O."
    )
    result = _research(client, synthetic)
    assert len(client.sent) == 2
    request = client.rewrite_request()
    assert f"the number {fake} is not in the source extracts" in request
    assert f"{peak} cmH2O" in result.answer
    assert REMOVED_MARK not in result.answer and REMOVED_NOTE not in result.answer
    assert "rewrite:accepted" in result.tool_trace


def test_value_still_fabricated_after_the_rewrite_is_replaced_with_one_note(synthetic):
    pid = synthetic["candidates"][0].pid
    first, second = _absent(synthetic, "73.4", "81.9")
    draft = f"{pid} had PIP reach {first} cmH2O, then {second} cmH2O."
    client = Drafts(draft, draft)
    result = _research(client, synthetic)
    assert len(client.sent) == 2
    assert "rewrite:still_flagged" in result.tool_trace
    assert result.answer.count(REMOVED_MARK) == 2
    assert first not in result.answer and second not in result.answer
    assert result.answer.count(REMOVED_NOTE) == 1


def test_grounded_answer_costs_no_rewrite_and_carries_no_note(synthetic):
    peak = _peak(synthetic)
    pid = synthetic["candidates"][0].pid
    client = Drafts(f"{pid} peaked at {peak} cmH2O, per source 1 of 3 cases.")
    result = _research(client, synthetic)
    assert len(client.sent) == 1
    assert "Verification note" not in result.answer


def test_other_checks_still_see_the_original_numbers(synthetic):
    candidate = synthetic["candidates"][0]
    wrong_age = next(
        n for n in range(21, 99)
        if n != candidate.age and find_ungrounded_numbers(str(n), synthetic["extracts"]) == [str(n)]
    )
    draft = f"{candidate.pid} is a {wrong_age}-year-old."
    result = _research(Drafts(draft, draft), synthetic)
    notes = result.answer.split("**Verification note:**")[1:]
    assert any(f"age {candidate.age:g}" in note for note in notes), result.answer
    assert REMOVED_MARK in result.answer


def test_without_sources_the_check_is_skipped():
    session = SessionState(cases=pd.DataFrame(), timeseries=pd.DataFrame(), flags=pd.DataFrame())
    client = Drafts("PIP reached 45 cmH2O.")
    result = run_agent(model="fake", question="q", session=session, client=client, skip_prefetch=True)
    assert len(client.sent) == 1
    assert "45" in result.answer and REMOVED_MARK not in result.answer
    assert answer_problems("PIP reached 45 cmH2O.", session) == []
    assert "45" in verify_answer("PIP reached 45 cmH2O.", session)[0]


def test_rewrite_cannot_trade_numbers_for_advice():
    result = answer_from_retrieval(
        question="What was recorded?", model="fake", candidates=[], extracts=[],
        client=Drafts("PIP 73.4 cmH2O; HR 81.9 bpm.", "You should administer dopamine."),
    )
    assert "You should administer" not in result.answer
    assert result.answer.count(REMOVED_MARK) == 2
    assert result.answer.count(REMOVED_NOTE) == 1


@pytest.mark.parametrize("research", [True, False])
def test_empty_sources_check_against_question_only(research):
    client = Drafts("PIP reached 45 cmH2O; the question says 30 cmH2O.")
    if research:
        result = answer_from_retrieval(question="PIP 30?", model="fake", candidates=[], extracts=[], client=client)
    else:
        session = SessionState(cases=pd.DataFrame(), timeseries=pd.DataFrame(), flags=pd.DataFrame())
        result = run_agent(question="PIP 30?", model="fake", session=session, client=client,
                           skip_prefetch=True, allow_tools=False, sources=[])
    assert "45" not in result.answer
    assert "30 cmH2O" in result.answer
    assert result.answer.count(REMOVED_NOTE) == 1


@pytest.mark.parametrize("marker", ["**Verification note:**", VERIFICATION_FOOTER])
def test_model_marker_is_checked_in_the_shipped_answer(marker):
    draft = f"{marker}\n**PIP reached 47 cmH2O** [A1]."
    session = SessionState(cases=pd.DataFrame(), timeseries=pd.DataFrame(), flags=pd.DataFrame())
    result = run_agent(model="fake", question="pressure?", session=session, client=Drafts(draft),
                       skip_prefetch=True, allow_tools=False, sources=["PIP 22"])
    assert "47" not in result.answer
    assert f"**PIP reached {REMOVED_MARK} cmH2O** [A1]" in result.answer
    assert result.answer.count(REMOVED_NOTE) == 1


ABSTRACTS = [
    "[A1] Smith 2019. In 240 adults, protective ventilation lowered pulmonary "
    "complications from 27% to 18% (p=0.01).",
    "[A2] Lee 2021. Driving pressure above 15 cmH2O was associated with complications in 1,200 patients.",
]


def test_article_answer_with_a_number_not_in_the_abstracts_is_caught_and_replaced():
    session = SessionState(cases=pd.DataFrame(), timeseries=pd.DataFrame(), flags=pd.DataFrame())
    good = "Complications fell from 27% to 18% [A1]; driving pressure above 15 cmH2O mattered [A2]."
    bad = "Complications fell from 27% to 12% [A1] in 240 adults (Smith et al., 2019)."
    client = Drafts(bad, bad)
    result = run_agent(
        model="fake", question="protective ventilation", session=session, client=client,
        skip_prefetch=True, allow_tools=False, sources=ABSTRACTS,
    )
    assert "the number 12 is not in the source extracts" in client.rewrite_request()
    assert f"to {REMOVED_MARK}% [A1]" in result.answer
    assert "27%" in result.answer and "240 adults" in result.answer
    assert result.answer.count(REMOVED_NOTE) == 1
    assert find_ungrounded_numbers(good, ABSTRACTS, "protective ventilation") == []


def test_prototype_drafts_and_conversation_answers_have_no_false_positive(extract):
    drafts = yaml.safe_load(PROTOTYPE_DRAFTS.read_text(encoding="utf-8"))
    texts = [d for scenario in drafts.values() for d in scenario["drafts"] if not d.startswith("{")]
    assert len(texts) >= 5
    for scenario in drafts.values():
        for draft in scenario["drafts"]:
            assert find_ungrounded_numbers(draft, [extract], scenario["question"]) == [], draft
    from tools.desktop_drive import DEFAULT_DRAFTS

    for draft in DEFAULT_DRAFTS:
        assert find_ungrounded_numbers(draft, [extract], "Where does pressure peak?") == [], draft
