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


@pytest.mark.parametrize("spelling", ["{v} cmH2O", "{v} cm H₂O", "{v} cm H2O", "{v}cmH2O", "{v}%"])
def test_unit_spellings_do_not_matter(extract, spelling):
    peak = _value(extract, "Peak inspiratory pressure")
    assert find_ungrounded_numbers("It was " + spelling.format(v=peak), [extract]) == []


@pytest.mark.parametrize("unit", ["mmHg", "/min", "bpm", "mL", "vol%"])
def test_a_value_in_another_unit_is_not_grounded(extract, unit):
    peak = _value(extract, "Peak inspiratory pressure")
    if any(_value(extract, label, bound) == peak for label in ("End-tidal CO2", "Heart rate", "Tidal volume",
           "Respiratory rate", "Noninvasive MAP") for bound in ("min", "max")):
        pytest.skip("the PIP figure also appears in a signal measured in another unit")
    assert find_ungrounded_numbers(f"It was {peak} {unit}", [extract]) == [peak]


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
        ("FiO2 was 1.0", "FiO2 weaned over 20 minutes to 1.0.", True),
        ("FiO2 was 20", "FiO2 weaned over 20 minutes to 1.0.", False),
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
    # About 17 ms locally; shared CI runners run about three times slower. A per-number rescan of
    # the whole answer once took this to 118 ms on CI, so 100 ms still catches a blow-up.
    assert best < 0.1, f"{best * 1000:.1f} ms"


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


@pytest.mark.parametrize("title,year", [
    ("Protective ventilation in adults", 2019),
    ("P.I.P. and PEEP in adults", 1888),
])
@pytest.mark.parametrize("value,removed", [(5, False), (13, True)])
def test_article_answers_keep_the_value_check_with_catalog_headers(title, year, value, removed):
    from src.llm.retrieve import answer_from_articles
    from src.services.articles import Article

    article = Article(
        id="ventilation", title=title, year=year, authors="Smith et al.",
        abstract="PEEP of 5 cmH2O was used. PIP of 20 cmH2O was used.",
        doi_or_url="https://example.org/ventilation", source="synthetic",
        rights_note="fictional test article", imported_at="2026-10-17",
    )
    draft = f"PIP was {value} cmH2O [A1]."
    client = Drafts(draft, draft)
    result = answer_from_articles(
        question="Describe airway pressure in the articles", model="fake",
        articles=[article], client=client,
    )
    if removed:
        assert f"PIP was {REMOVED_MARK} cmH2O [A1]." in result.answer
        assert result.answer.count(REMOVED_NOTE) == 1
    else:
        assert draft in result.answer
        assert REMOVED_MARK not in result.answer
        assert len(client.sent) == 1


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


CASE = (
    "SOURCE EXTRACT (analyzed case) PID=SYN0000085e40\n"
    "- Age: 23\n"
    "- Case duration (min): 67.0\n"
    "- Peak inspiratory pressure: median 18.6 cmH2O, IQR [18.1–18.9] cmH2O, min–max [16.9–19.9] cmH2O (n=64)\n"
    "- PEEP: median 8.0 cmH2O, IQR [8.0–8.0] cmH2O, min–max [8.0–8.0] cmH2O (n=64)\n"
    "- Heart rate: median 66.6 bpm, IQR [65.3–68.2] bpm, min–max [60.1–71.0] bpm (n=98)\n"
    "- SpO2: median 98.5 %, IQR [98.0–99.0] %, min–max [89.0–100.0] % (n=98)\n"
    "- spo2_low [warn]: t=32–37 min, during maintenance (Low oxygen saturation)\n"
    "- 2016-03-01 07:08:00: Intubation\n"
)
MINUTE = (
    "SOURCE EXTRACT (analyzed case) PID=SYN0000085e40\n"
    "At the selected minute: PIP 22; ETCO2 38; HR 71\n"
    "SOURCE ROWS from 30 to 34 min\n"
    "t_min | PIP (cmH₂O) | ETCO2 (mmHg) | HR (bpm)\n"
    "30 | 21 | 37 | 70\n"
    "31 | 24 | 39 | 74\n"
)


@pytest.mark.parametrize(
    "answer",
    [
        "PIP reached 23 cmH2O.",  # the age, not a PIP value
        "Peak pressure peaked at 67 cmH2O.",  # the case duration
        "The heart rate rose to 98.5 bpm.",  # the SpO2 median
        "PIP reached 66.6.",  # the heart-rate median, no unit
        "A pressure of 66.6 cmH2O was recorded.",  # no signal named: compared with cmH2O values only
        "SpO2 fell to 93%.",  # a percent is not excused by some ratio of two source numbers
        "PIP was 19.9 cmH2O at minute 32 and 67 at minute 33.",  # 67 is the case duration
    ],
)
def test_a_value_of_another_signal_is_not_grounded(answer):
    assert find_ungrounded_numbers(answer, [CASE]) != []


@pytest.mark.parametrize(
    "answer",
    [
        "PIP and PEEP were 18.6 and 8.0 cmH2O.",
        "Heart rate was 66.6 bpm while PIP stayed at 18.6 cmH2O.",
        "PIP ranged from 16.9 to 19.9 cmH2O (IQR 18.1–18.9).",
        "SpO2 fell to 89% during the spo2_low episode at t=32–37 min.",
        "PIP rose after intubation at 07:08 and stayed near 19 cmH2O for 67 minutes.",
        "PIP was recorded in 64 of 98 minutes.",
        "The case was 23 years old; PIP peaked at 19.9 cmH2O.",
        "PIP rose by 3.0 cmH2O from its minimum.",
    ],
)
def test_values_of_the_named_signal_and_other_numbers_are_grounded(answer):
    assert find_ungrounded_numbers(answer, [CASE]) == []


def test_a_pipe_table_attributes_values_by_column():
    assert find_ungrounded_numbers("PIP was 24 cmH₂O at minute 31.", [MINUTE]) == []
    assert find_ungrounded_numbers("PIP was 22 at the selected minute and ETCO2 38.", [MINUTE]) == []
    assert find_ungrounded_numbers("PIP was 39 at minute 31.", [MINUTE]) == ["39"]  # the ETCO2 cell
    assert find_ungrounded_numbers("HR was 37 bpm.", [MINUTE]) == ["37"]


def test_the_question_grounds_a_value_for_any_signal():
    assert find_ungrounded_numbers("PIP never exceeded 35 cmH2O.", [CASE], "Was PIP above 35?") == []


def test_question_values_are_not_operands_for_a_signal_difference():
    assert find_ungrounded_numbers("PIP rose by 33 cmH2O.", ["PIP 22 cmH2O"], "Was the patient aged 55?") == ["33"]
    assert find_ungrounded_numbers("PIP rose by 3 cmH2O.", ["PIP 22 then PIP 25 cmH2O"], "Was PIP 25?") == []


@pytest.mark.parametrize(("answer", "value"), [
    ("ETCO2 reached 61 mmHg.", "61"),
    ("SpO2 fell to 88%.", "88"),
    ("PEEP was 22.", "22"),
    ("ETCO2 rose by 28 mmHg.", "28"),
])
def test_absent_signals_cannot_borrow_other_source_numbers(answer, value):
    source = "Age 61. Heart rate: min 60, max 88 bpm. Peak inspiratory pressure: max 22 cmH2O."
    assert find_ungrounded_numbers(answer, [source]) == [value]


@pytest.mark.parametrize("unit", ["bpm", "/min"])
def test_heart_rate_thresholds_accept_both_rate_units(unit):
    source = "Heart rate: min 60, max 88 bpm.\nwarn at least 100 /min"
    assert find_ungrounded_numbers(f"Heart rate crossed its warn threshold of 100 {unit}.", [source]) == []


@pytest.mark.parametrize(("source_unit", "answer_unit"), [("bpm", "/min"), ("/min", "bpm")])
def test_heart_rate_values_accept_equivalent_units_without_borrowing_rr(source_unit, answer_unit):
    source = f"Heart rate: max 101 {source_unit}.\nRR: max 22 /min."
    assert find_ungrounded_numbers(f"HR peaked at 101 {answer_unit}.", [source]) == []
    assert find_ungrounded_numbers(f"HR peaked at 22 {answer_unit}.", [source]) == ["22"]


def test_sources_without_a_labeled_signal_fall_back_to_any_value():
    abstract = "[A1] Smith et al. 2019. Airway pressure stayed at 25 in lean adults."
    assert find_ungrounded_numbers("PIP was 25 cmH2O in lean adults [A1].", [abstract]) == []
    assert find_ungrounded_numbers("PIP was 27 cmH2O in lean adults [A1].", [abstract]) == ["27"]


EPISODE = (
    "SOURCE EXTRACT (analyzed case) PID=SYN0000085e40\n"
    "SOURCE ROWS from 30 to 31 min\n"
    "t_min | PIP | ETCO2 (mmHg) | nMAP\n"
    "30 | 21 | 37 | 75\n"
    "31 | 24 | 39 | 62\n"
    "\n"
    "EPISODE COVERING THE SELECTED MINUTE: High PIP (pip_high), warn, 30 to 34 min\n"
    "Mean flagged value (PIP): 27.5\n"
    "PIP: min 20, max 25, mean 22.1; 0 missing minutes\n"
    "ETCO2: min 35, max 40, mean 37.2; 0 missing minutes\n"
    "HR: min 61.5, max 70, mean 65; 0 missing minutes\n"
    "warn at least 25 cmH2O\n"
    "critical at least 30 cmH2O\n"
)


@pytest.mark.parametrize(
    ("answer", "sources"),
    [
        ("The minimum PIP in the episode was 20 cmH2O.", [EPISODE]),
        ("ETCO2 min was 35 and max 40 mmHg.", [EPISODE]),
        ("Minimum HR was 61.5 bpm.", [EPISODE]),
        ("PIP ranged from 20 to 25 cmH2O.", [EPISODE]),
        ("PIP at 21 is below the critical 30 cmH2O.", [EPISODE]),
        ("The mean flagged PIP was 27.5 cmH2O.", [EPISODE]),
        ("MAP was 75 mmHg, and mean arterial pressure was 62 mmHg a minute later.", [EPISODE]),
        ("PIP was 21 cmH2O during minutes 30 and 31.", [EPISODE]),
        ("PIP was 18.6 cmH2O; SpO2 later reached 100.", [CASE]),
        ("PIP peaked at 19.9 cmH2O in a 23-year-old.", [CASE]),
        ("HR was 66.6 bpm in 2016.", [CASE]),
        ("SpO2 fell to 89% in 6 of 98 minutes (6%).", [CASE + "- spo2_low [warn]: 6 minute-flags\n"]),
        ("PIP climbed by 3.0 cmH2O from its minimum.", [CASE]),
        ("HR median 66.6 bpm, with 98 recorded minutes.", [CASE]),
        ("PIP was 18.6 cmH2O across 64 valid one-minute recordings.", [CASE]),
        ("PEEP of 8.0 cmH2O was kept during 98 recorded minutes.", [CASE]),
        ("PIP at min 31 was 24 cmH2O.", [MINUTE]),
        ("HR was 71 at the selected minute.", ["At the selected minute: PIP 22; ETCO2 38; HR 71\nSOURCE ROWS from 30 to 31 min\n"]),
        ("SpO2 was below 90% for 6% of minutes.", [CASE + "- spo2_low [warn]: 6 minute-flags\n- SpO2 below 90 %\n"]),
        ("She received 500 mL of Lactated Ringer.", [CASE + "- Lactated Ringer dose samples: 500, 1000 mL\n"]),
        ("Heart rate was 120 bpm.", ["Heart rates were 120,130 bpm."]),
        ("PEEP of 10 cmH2O was used [A2].", [
            "[A1] Lee et al. 2020. PEEP of 5 cmH2O was used.",
            "[A2] Kim et al. 2021. Patients received 10 cmH2O of PEEP or 4 cmH2O PEEP.",
        ]),
    ],
)
def test_correct_figures_stay_grounded_when_tied_to_signals(answer, sources):
    assert find_ungrounded_numbers(answer, sources) == []


def test_tying_numbers_to_signals_grows_linearly_with_source_length():
    import time

    def best_time(n_events: int) -> float:
        events = "".join(f"- 2016-03-01 {7 + i // 60:02d}:{i % 60:02d}:00: Event {i}\n" for i in range(n_events))
        sources = [CASE + events] * 3
        answer = "PIP peaked at 19.9 cmH2O and PEEP stayed at 8.0 cmH2O. " * 10
        times = []
        for _ in range(3):
            start = time.perf_counter()
            find_ungrounded_numbers(answer, sources)
            times.append(time.perf_counter() - start)
        return min(times)

    # Four times the events: linear work takes about four times as long, quadratic sixteen
    assert best_time(1200) < 8 * best_time(300)



# ----- a minute extract, as the Signal page sends it (invented values) -----

_MINUTE_SOURCE = "\n".join([
    "SOURCE EXTRACT (MOVER SIS) PID=SYN0000000001",
    "Age: 58 · Sex: F",
    "SELECTED MINUTE: -40 min relative to incision",
    "SOURCE ROWS from -44 to -39 min (the minutes this case has within 10 of the cursor)",
    "t_min | PIP (cmH2O) | ETCO2 (mmHg) | HR (bpm)",
    "-44 | 39 | 31.79 | 66",
    "-43 | missing | 37.85 | 62",
    "-42 | 1 | 0 | 68",
    "-41 | 41 | missing | 73",
    "-40 | 45 | 35.58 | 66",
    "-39 | 44 | 35 | 65",
])


def test_a_minute_that_starts_a_table_row_keeps_its_minus_sign():
    answer = "| t_min | PIP (cmH2O) |\n|---|---|\n| -44 | 39 |\n| -42 | 1 |\n| -41 | 41 |"
    assert find_ungrounded_numbers(answer, [_MINUTE_SOURCE]) == []
    # The row after a row ending in a digit: "66\n-43" is a new row's minus, not a subtraction
    assert find_ungrounded_numbers("At -43 ETCO2 was 37.85.", [_MINUTE_SOURCE]) == []
    assert find_ungrounded_numbers("| -47 | 39 |", [_MINUTE_SOURCE]) == ["-47"], "a minute the rows lack"


def test_a_negative_minute_beside_a_signal_is_checked_as_a_minute():
    answer = "PIP was 39 and 41 at -44 and -41, and 1 at -42."
    assert find_ungrounded_numbers(answer, [_MINUTE_SOURCE]) == []
    assert find_ungrounded_numbers("PIP was 39 at -48.", [_MINUTE_SOURCE]) == ["-48"]
    assert find_ungrounded_numbers("PIP reached 52 at -41.", [_MINUTE_SOURCE]) == ["52"], "readings still checked"


def test_a_heading_number_is_a_label():
    answer = "## 1. Cases used\nSYN0000000001\n\n## 2. What the extract shows\nPIP 45 at -40."
    assert find_ungrounded_numbers(answer, [_MINUTE_SOURCE]) == []
    assert find_ungrounded_numbers("## 2. PIP reached 77", [_MINUTE_SOURCE]) == ["77"]


def test_an_age_band_holding_the_stated_age_is_grounded_and_one_missing_it_is_not():
    assert find_ungrounded_numbers("SYN0000000001: age 55-59, sex F.", [_MINUTE_SOURCE]) == []
    assert find_ungrounded_numbers("SYN0000000001: age 55\u201359.", [_MINUTE_SOURCE]) == []
    assert find_ungrounded_numbers("SYN0000000001: age 60-64.", [_MINUTE_SOURCE]) == ["60", "64"]
    assert find_ungrounded_numbers("SYN0000000001: age 56-60.", [_MINUTE_SOURCE]) == ["56", "60"], "not a band"


def test_an_age_band_uses_the_named_cases_age_and_any_source_only_when_unnamed():
    from src.llm.grounding import _true_age_bands

    younger = "SOURCE EXTRACT PID=ab0123456789cdef\nAge: 50"
    older = "SOURCE EXTRACT PID=fedcba9876543210\nAge: 60"
    answer = "ab0123456789cdef was aged 60-64. fedcba9876543210 was aged 60-64."
    spans = _true_age_bands(answer, [younger, older])
    assert len(spans) == 1
    assert answer[spans[0][0]:spans[0][1]] == "60-64"
    assert spans[0][0] == answer.rindex("60-64")
    named_only_in_the_previous_sentence = "ab0123456789cdef had a stable course. The patient was aged 60-64."
    assert _true_age_bands(named_only_in_the_previous_sentence, [younger, older]) == []
    assert _true_age_bands("aged 55-59.", ["Age: 58"])
    assert _true_age_bands("aged 60-64.", ["Age: 58"]) == []
