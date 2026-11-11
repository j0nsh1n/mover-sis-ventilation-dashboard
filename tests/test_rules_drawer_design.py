"""The rule reference drawer in the Quiet Record design: what each rule row says and draws, how it is read and reached."""

from __future__ import annotations

import os
import re
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QAccessible  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from src.desktop.marks import SEVERITY_GLYPHS  # noqa: E402
from src.desktop.research_workspace import ResearchWorkspace  # noqa: E402
from src.desktop.rules_drawer import (  # noqa: E402
    GROUP_ORDER,
    OTHER_GROUP,
    PATTERN_GROUP,
    RulesDrawer,
    group_of,
    hold_text,
    rule_sentence,
    threshold_parts,
)
from src.desktop.theme import apply_theme, token_color  # noqa: E402
from src.desktop.threshold_ruler import ruler_for  # noqa: E402
from src.rules import load_rule_catalog  # noqa: E402
from src.services.case_fetch import FetchResult  # noqa: E402
from src.services.retrieval import RetrievalResult, SearchCandidate  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "src" / "desktop"
PRESETS = ("default", "strict", "lenient")


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    apply_theme(app, "light")
    return app


def _dispose(widget, qapp):
    widget.close()
    widget.deleteLater()
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def drawer(qapp):
    widget = RulesDrawer()
    widget.setFixedWidth(420)
    widget.resize(420, 900)
    widget.set_catalog(load_rule_catalog("default"))
    widget.open()
    widget.show()
    widget.activateWindow()
    qapp.processEvents()
    yield widget
    _dispose(widget, qapp)


def _texts(row) -> list[str]:
    return [label.text() for label in row.findChildren(QLabel)]


def _detail(row, caption: str) -> str:
    """The text beside a caption (Signals, Thresholds, Hold time) in a rule row."""
    labels = row.findChildren(QLabel)
    for head, body in zip(labels, labels[1:], strict=False):
        if head.objectName() == "ruleCaption" and head.text() == caption:
            return body.text()
    raise AssertionError(f"no {caption!r} line in {_texts(row)}")


# The hold times the default rule file sets, worked out from config/thresholds.yaml's min_duration_min values.
HOLDS = {
    "pip_high": "none, each minute is checked on its own",
    "tv_abs_extreme": "none, each minute is checked on its own",
    "compliance_concern": "set by the rules it combines",
    "pip_rising": "at least 5 min in a row",
    "peep_zero_long": "at least 15 min in a row",
    "etco2_zero_vent": "at least 3 min in a row",
    "fio2_high_long": "at least 30 min in a row",
    "fio2_room_air_vent": "at least 5 min in a row",
    "agent_drift": "at least 5 min in a row",
    "agent_fi_et_gap": "at least 10 min in a row",
}


@pytest.mark.parametrize(("rule_id", "expected"), HOLDS.items())
def test_each_rules_hold_time_is_the_one_its_file_sets(drawer, rule_id, expected):
    assert _detail(drawer._rows[rule_id], "Hold time") == expected


@pytest.mark.parametrize("preset", PRESETS)
def test_every_rule_has_a_row_with_its_shapes_words_name_signals_limits_and_hold(qapp, preset):
    catalog = load_rule_catalog(preset)
    widget = RulesDrawer()
    widget.set_catalog(catalog)
    widget.open()
    try:
        assert set(widget._rows) == {spec.id for spec in catalog.specs}
        for spec in catalog.specs:
            row = widget._rows[spec.id]
            texts = _texts(row)
            assert spec.label in texts and spec.id in texts
            for word in spec.severities:
                assert f"{SEVERITY_GLYPHS[word]} {word}" in "   ".join(texts), (spec.id, word)
            assert _detail(row, "Signals") == ", ".join(spec.signals)
            assert _detail(row, "Hold time") == hold_text(spec)
            parts = threshold_parts(spec, skip=("min_duration_min",))
            if parts:
                assert _detail(row, "Thresholds") == " · ".join(parts)
            else:
                assert "Thresholds" not in texts
    finally:
        _dispose(widget, qapp)


def test_the_drawer_says_the_limits_of_pip_high_as_the_rule_defines_them(drawer):
    row = drawer._rows["pip_high"]
    assert _detail(row, "Thresholds") == "warn at least 30 cmH2O · critical at least 35 cmH2O"
    assert "△ warn   ◆ critical" in _texts(row)


def test_a_hold_time_is_never_a_number_the_rule_does_not_carry():
    catalog = load_rule_catalog("default")
    duration = catalog.get("peep_zero_long")
    assert hold_text(replace(duration, params={k: v for k, v in duration.params.items() if k != "min_duration_min"})) == (
        "not set in the rule file")
    assert hold_text(replace(duration, params={**duration.params, "min_duration_min": 7})) == "at least 7 min in a row"


def test_threshold_parts_can_leave_a_key_out_and_otherwise_lists_all():
    spec = load_rule_catalog("default").get("pip_rising")
    assert "lasting at least 5 min" in threshold_parts(spec)
    assert threshold_parts(spec, skip=("min_duration_min",)) == [
        part for part in threshold_parts(spec) if part != "lasting at least 5 min"]
    assert len(threshold_parts(spec, skip=("min_duration_min",))) == len(spec.params) - 1


@pytest.mark.parametrize("preset", PRESETS)
def test_a_rule_gets_a_ruler_exactly_when_ruler_for_can_draw_one_and_says_so_when_it_cannot(qapp, preset):
    catalog = load_rule_catalog(preset)
    widget = RulesDrawer()
    widget.set_catalog(catalog)
    widget.open()
    try:
        drawn = 0
        for spec in catalog.specs:
            row = widget._rows[spec.id]
            wanted = ruler_for(spec, None, episode=False)
            if wanted is None:
                assert row.ruler is None
                notes = [label.text() for label in row.findChildren(QLabel) if label.objectName() == "ruleNoRuler"]
                assert len(notes) == 1 and notes[0].startswith("No ruler:"), spec.id
            else:
                drawn += 1
                assert row.ruler.spec() == wanted
                assert row.ruler.isVisibleTo(row)
                assert not [label for label in row.findChildren(QLabel) if label.objectName() == "ruleNoRuler"]
        assert drawn == 15  # thirteen limit rules, pip_rising and agent_low_maint (see threshold_ruler's docstring)
    finally:
        _dispose(widget, qapp)


@pytest.mark.parametrize("preset", PRESETS)
def test_no_ruler_shows_a_number_its_rule_does_not_define(preset):
    for spec in load_rule_catalog(preset).specs:
        ruler = ruler_for(spec, None, episode=False)
        if ruler is None:
            continue
        defined = {float(v) for v in spec.params.values() if isinstance(v, (int, float))}
        assert {mark.value for mark in ruler.marks} <= defined, spec.id
        assert ruler.observed is None and ruler.observed_label == ""
        # The rule's own name may hold a digit ("CO2"); only the limits are numbers the rule defines.
        numbers = {float(n) for n in re.findall(r"\d+(?:\.\d+)?", ruler.description().replace(ruler.rule, ""))}
        assert numbers <= defined, (spec.id, numbers - defined)


def test_the_rule_reference_ruler_describes_limits_in_words_and_says_nothing_of_an_episode():
    spec = load_rule_catalog("default").get("pip_high")
    assert ruler_for(spec, None, episode=False).description() == (
        "Threshold ruler for High PIP. Limits: warn at 30 cmH₂O or more; critical at 35 cmH₂O or more.")
    # The case page keeps saying what the episode reached.
    assert ruler_for(spec, None).description().endswith("No value was recorded for this signal during the episode.")
    assert ruler_for(spec, 31.0).description().endswith("Observed maximum during the episode: 31 cmH₂O.")
    assert ruler_for(spec, 31.0, episode=False).observed == 31.0


def test_the_no_ruler_sentences_follow_the_rule_kind(drawer):
    said = {kind: next(label.text() for label in drawer._rows[rule].findChildren(QLabel)
                       if label.objectName() == "ruleNoRuler")
            for kind, rule in (("duration", "peep_zero_long"), ("band", "tv_abs_extreme"),
                               ("composite", "compliance_concern"))}
    assert said["duration"] == "No ruler: this rule fires on how long a condition lasts, not at one limit."
    assert said["band"] == "No ruler: this rule flags values outside a band, not beyond one limit."
    assert said["composite"] == "No ruler: this rule is made of other rules."


# —— grouping ——

def test_every_rule_of_every_preset_is_in_a_named_group_and_the_counts_add_up(drawer):
    for preset in PRESETS:
        catalog = load_rule_catalog(preset)
        assert {group_of(spec) for spec in catalog.specs} <= set(GROUP_ORDER) - {OTHER_GROUP}
    catalog = load_rule_catalog("default")
    headings = [label.text() for label in drawer._list.findChildren(QLabel) if label.objectName() == "ruleGroup"]
    assert headings == [
        "Airway pressure · 4", "Volume and rate · 5", "Gas · 5", "Anesthetic agent · 4", "Circulation · 4",
        "Patterns · 3"]
    assert sum(int(text.rsplit(" ", 1)[1]) for text in headings) == len(catalog.specs) == 25


def test_composites_are_patterns_and_an_unknown_signal_is_not_lost():
    catalog = load_rule_catalog("default")
    assert group_of(catalog.get("compliance_concern")) == PATTERN_GROUP
    assert group_of(replace(catalog.get("pip_high"), signals=("NOVEL",))) == OTHER_GROUP
    assert group_of(replace(catalog.get("pip_high"), signals=())) == OTHER_GROUP


def test_a_rule_outside_every_group_is_listed_last_under_other_rules(qapp):
    catalog = load_rule_catalog("default")
    odd = replace(catalog.get("pip_high"), id="odd_rule", label="Odd rule", signals=("NOVEL",))
    widget = RulesDrawer()
    widget.set_catalog(replace(catalog, specs=(odd, *catalog.specs[1:3])))
    widget.open()
    try:
        assert widget.visible_rule_ids() == ["pip_rising", "peep_high", "odd_rule"]
        headings = [label.text() for label in widget._list.findChildren(QLabel) if label.objectName() == "ruleGroup"]
        assert headings == ["Airway pressure · 2", "Other rules · 1"]
    finally:
        _dispose(widget, qapp)


def test_group_headings_follow_the_search(drawer):
    drawer.search_edit.setText("etco2")
    heads = {label.text(): label.isVisibleTo(drawer) for label in drawer._list.findChildren(QLabel)
             if label.objectName() == "ruleGroup"}
    assert heads["Gas · 3 of 5"] is True
    assert heads["Patterns · 2 of 3"] is True  # hypoventilation_pattern and desat_with_vent_issue read ETCO2
    shown = [text for text, visible in heads.items() if visible]
    assert sorted(shown) == ["Gas · 3 of 5", "Patterns · 2 of 3"]
    drawer.search_edit.setText("")
    assert all(label.isVisibleTo(drawer) for label in drawer._list.findChildren(QLabel)
               if label.objectName() == "ruleGroup")


# —— the current rule ——

def test_the_current_rule_is_marked_in_words_and_shape_as_well_as_tint(drawer):
    drawer.show_rule("map_low")
    row = drawer._rows["map_low"]
    assert row.property("highlight") is True
    assert row.current_label.isVisibleTo(row) and row.current_label.text().startswith("▸ Current rule")
    assert row.accessibleName().startswith("Current rule. Low mean arterial pressure.")
    other = drawer._rows["pip_high"]
    assert not other.current_label.isVisibleTo(other) and not other.accessibleName().startswith("Current rule")
    drawer.show_rule("pip_high")
    assert not row.current_label.isVisibleTo(row) and not row.accessibleName().startswith("Current rule")
    assert drawer._rows["pip_high"].current_label.isVisibleTo(drawer._rows["pip_high"])


def test_a_rule_that_names_itself_in_its_description_shows_the_text_once(drawer):
    spec = load_rule_catalog("default").get("spo2_low")
    assert spec.description == spec.label  # the rule file's own wording
    assert _texts(drawer._rows["spo2_low"]).count(spec.label) == 1
    assert load_rule_catalog().get("pip_high").description in _texts(drawer._rows["pip_high"])


# —— no colours of its own ——

def test_the_drawer_carries_no_colors_of_its_own(drawer):
    assert drawer.styleSheet() == ""
    source = (SRC / "rules_drawer.py").read_text(encoding="utf-8")
    assert re.findall(r"#[0-9a-fA-F]{3,8}\b|QColor\(\"|rgba?\(\d|color\s*:", source) == []


# —— reading and reaching ——

def test_the_drawer_and_its_list_have_names_and_roles_a_screen_reader_announces(drawer):
    iface = QAccessible.queryAccessibleInterface(drawer)
    assert iface.text(QAccessible.Text.Name) == "Rule reference" and iface.role() == QAccessible.Role.Dialog
    shown = QAccessible.queryAccessibleInterface(drawer._list)
    assert shown.text(QAccessible.Text.Name) == "Rules" and shown.role() == QAccessible.Role.List


def test_each_rule_is_one_sentence_with_name_severity_threshold_and_hold(drawer):
    assert drawer._rows["pip_high"].accessibleName() == (
        "High PIP. Severity: warn, critical. Thresholds: warn at least 30 cmH2O; critical at least 35 cmH2O. "
        "Hold time: none, each minute is checked on its own.")
    assert drawer._rows["pip_rising"].accessibleName() == (
        "Rising PIP. Severity: warn. Thresholds: window 10 min; slope at least 0.5 cmH2O/min; PIP at least 20 cmH2O. "
        "Hold time: at least 5 min in a row.")
    pattern = drawer._rows["compliance_concern"].accessibleName()
    assert pattern.startswith("Compliance pattern. Severity: warn. Combines: rising PIP (pip_rising)")
    assert pattern.endswith("Hold time: set by the rules it combines.")
    assert "Thresholds" not in pattern
    for rule_id, row in drawer._rows.items():
        iface = QAccessible.queryAccessibleInterface(row)
        assert iface.role() == QAccessible.Role.ListItem, rule_id
        assert iface.text(QAccessible.Text.Name) == rule_sentence(load_rule_catalog("default").get(rule_id))


def test_each_ruler_has_a_name_a_role_and_its_numbers_in_words(drawer):
    ruler = drawer._rows["spo2_low"].ruler
    iface = QAccessible.queryAccessibleInterface(ruler)
    assert iface.role() == QAccessible.Role.Graphic
    assert iface.text(QAccessible.Text.Name) == "Threshold ruler, Low oxygen saturation"
    assert iface.text(QAccessible.Text.Description) == (
        "Threshold ruler for Low oxygen saturation. Limits: warn at 92 % or less; critical at 88 % or less.")


def test_tab_runs_down_the_screen_through_every_visible_rule_in_order(drawer, qapp):
    drawer.close_button.setFocus()
    seen = [drawer.close_button]
    for _ in range(len(drawer._rows) + 4):
        assert drawer.focusNextPrevChild(True)
        seen.append(QApplication.focusWidget())
        if seen[-1] is drawer.mac_button:
            break
    assert seen == [drawer.close_button, drawer.search_edit, *drawer._rows.values(), drawer.scoring_button,
                    drawer.mac_button]
    assert [row.property("ruleId") for row in drawer._rows.values()][:3] == ["pip_high", "pip_rising", "peep_high"]


def test_a_rule_the_search_hides_is_not_a_tab_stop(drawer):
    drawer.search_edit.setText("etco2")
    drawer.search_edit.setFocus()
    seen = []
    for _ in range(3):
        drawer.focusNextPrevChild(True)
        seen.append(QApplication.focusWidget())
    assert [row.property("ruleId") for row in seen] == drawer.visible_rule_ids()[:3]


def test_the_arrow_keys_move_between_rules_and_stop_at_the_ends(drawer, qapp):
    first, second = list(drawer._rows.values())[:2]
    first.setFocus()
    QTest.keyClick(first, Qt.Key.Key_Up)
    assert first.hasFocus()
    QTest.keyClick(first, Qt.Key.Key_Down)
    assert second.hasFocus()
    QTest.keyClick(second, Qt.Key.Key_Up)
    assert first.hasFocus()
    last = list(drawer._rows.values())[-1]
    last.setFocus()
    QTest.keyClick(last, Qt.Key.Key_Down)
    assert last.hasFocus()


def test_a_focused_rule_scrolls_into_view(drawer, qapp):
    last = list(drawer._rows.values())[-1]
    assert drawer.rule_scroll.verticalScrollBar().value() == 0
    last.setFocus(Qt.FocusReason.TabFocusReason)
    qapp.processEvents()
    viewport = drawer.rule_scroll.viewport()
    top = last.mapTo(viewport, last.rect().topLeft()).y()
    assert drawer.rule_scroll.verticalScrollBar().value() > 0
    assert 0 <= top < viewport.height()


def test_the_list_is_not_a_second_tab_stop_beside_its_rules(drawer):
    assert drawer.rule_scroll.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert all(row.focusPolicy() == Qt.FocusPolicy.StrongFocus for row in drawer._rows.values())


# —— the theme ——

def _has_pixel(widget, color) -> bool:
    image = widget.grab().toImage()
    wanted = color.rgb()
    return any(image.pixel(x, y) == wanted for x in range(image.width()) for y in range(image.height()))


def test_a_theme_switch_while_the_drawer_is_open_repaints_the_rulers_and_the_rows(drawer, qapp):
    ruler = drawer._rows["pip_high"].ruler
    drawer.show_rule("pip_high")
    qapp.processEvents()
    try:
        apply_theme(qapp, "light")
        qapp.processEvents()
        assert _has_pixel(ruler, token_color("ink", "light"))
        assert not _has_pixel(ruler, token_color("ink", "dark"))
        sheet_light = drawer.grab().toImage().pixelColor(2, 400)
        apply_theme(qapp, "dark")
        qapp.processEvents()
        assert _has_pixel(ruler, token_color("ink", "dark"))
        assert not _has_pixel(ruler, token_color("ink", "light"))
        assert drawer.grab().toImage().pixelColor(2, 400) != sheet_light
        assert drawer.grab().toImage().pixelColor(2, 400).name() == token_color("sheet", "dark").name()
    finally:
        apply_theme(qapp, "light")


# —— from the case page ——

def _case_page(qapp, width=1316):
    minutes = 40
    start = pd.Timestamp("2020-01-01 08:00")
    ts = pd.DataFrame({
        "PID": ["caseA"] * minutes, "t_min": list(range(minutes)), "PIP": [20.0] * minutes,
        "ETCO2": [35.0] * minutes, "HR": [70.0] * minutes,
        "SPO2": [{20: 92.0, 21: 91.0, 22: 90.0}.get(m, 98.0) for m in range(minutes)], "nMAP": [70.0] * minutes,
        "Surgery_start": [start] * minutes, "Surgery_end": [start + pd.Timedelta(minutes=35)] * minutes,
    })
    flags = pd.DataFrame([
        {"PID": "caseA", "t_min": m, "rule_id": "spo2_low", "severity": "critical", "value": 90.0,
         "Obs_time": start + pd.Timedelta(minutes=m), "message": "synthetic", "phase": "maintenance"}
        for m in range(20, 23)
    ])
    catalog = load_rule_catalog("default")
    view = ResearchWorkspace()
    view.resize(width, 900)
    view.set_rule_catalog(catalog)
    view.set_results(RetrievalResult(candidates=(SearchCandidate(
        "caseA", "analyzed case", True, "Laparoscopic cholecystectomy", 55, "F", "PID caseA.", "keyword", 0.5),)))
    view.set_case_data("caseA", ts, flags, fetched=FetchResult(
        cases=pd.DataFrame({"PID": ["caseA"], "Age": [55]}), timeseries=ts, flags=flags, events=pd.DataFrame()),
        thresholds_sha256=catalog.thresholds_sha256)
    view.show()
    view.activateWindow()
    view.open_detail("caseA")
    QTest.qWait(150)
    return view


def test_the_rule_reference_link_opens_on_the_selected_episodes_rule_and_escape_returns_to_it(qapp):
    view = _case_page(qapp)
    try:
        view.episode_list.setCurrentRow(0)
        assert view.selected_rule_id == "spo2_low"
        view.rule_link.setFocus()
        view.rule_link.click()
        QTest.qWait(100)
        drawer = view.rules_drawer
        row = drawer._rows["spo2_low"]
        assert drawer.is_open and row.property("highlight") is True and row.current_label.isVisibleTo(row)
        viewport = drawer.rule_scroll.viewport()
        top = row.mapTo(viewport, row.rect().topLeft()).y()
        assert 0 <= top < viewport.height()
        assert row.ruler.isVisibleTo(row) and row.ruler.spec().marks[0].value == 92.0
        QTest.keyClick(drawer.search_edit, Qt.Key.Key_Escape)
        assert not drawer.is_open and view.rule_link.hasFocus()
    finally:
        _dispose(view, qapp)


def test_in_a_narrow_window_the_drawer_rows_and_rulers_fit_the_list(qapp):
    view = _case_page(qapp, width=700)
    try:
        view.episode_list.setCurrentRow(0)
        view.rule_link.click()
        QTest.qWait(150)
        drawer = view.rules_drawer
        viewport = drawer.rule_scroll.viewport()
        assert not drawer.rule_scroll.horizontalScrollBar().isVisible()
        assert drawer._list.width() <= viewport.width()
        for rule_id in ("spo2_low", "pip_high", "agent_drift"):
            row = drawer._rows[rule_id]
            drawer.rule_scroll.ensureWidgetVisible(row)
            qapp.processEvents()
            assert row.mapTo(viewport, row.rect().topRight()).x() <= viewport.width(), rule_id
            for label in row.findChildren(QLabel):
                if label.isVisibleTo(row) and label.wordWrap():
                    assert label.height() >= label.heightForWidth(label.width()) - 1, (rule_id, label.text())
            if row.ruler is not None:
                assert row.ruler.width() <= row.width()
    finally:
        _dispose(view, qapp)
