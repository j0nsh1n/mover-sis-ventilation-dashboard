"""The searchable rules drawer inside the research workspace (offscreen Qt, no real data)."""

from __future__ import annotations

import os
import statistics
import time
from collections import Counter
from dataclasses import replace

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton
from shiboken6 import delete

from src.desktop.research_workspace import ResearchWorkspace
from src.desktop.rules_drawer import threshold_parts
from src.rules import load_rule_catalog
from src.services.retrieval import RetrievalResult, SearchCandidate


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _dispose(widget, qapp):
    """Delete now: live top-level widgets make every later apply_theme repolish them."""
    widget.close()
    widget.deleteLater()
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def view(qapp):
    workspace = ResearchWorkspace()
    workspace.resize(1100, 800)
    workspace.set_rule_catalog(load_rule_catalog("default"))
    workspace.show()
    workspace.activateWindow()
    qapp.processEvents()
    yield workspace
    _dispose(workspace, qapp)


def _type(drawer, text):
    drawer.search_edit.setText(text)


def _ids(text):
    return [spec.id for spec in load_rule_catalog().search(text)]


def _row_text(drawer, rule_id):
    return "\n".join(label.text() for label in drawer._rows[rule_id].findChildren(QLabel))


def _luminance(color: QColor) -> float:
    def channel(value: int) -> float:
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(color.red()) + 0.7152 * channel(color.green()) + 0.0722 * channel(color.blue())


def _contrast(a: QColor, b: QColor) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_count_text_for_empty_and_filtered_search(view):
    view.rules_button.click()
    drawer = view.rules_drawer
    total = len(load_rule_catalog().specs)
    assert drawer.count_label.text() == f"{total} rules"
    _type(drawer, "etco2")
    assert drawer.count_label.text() == f"{len(_ids('etco2'))} of {total} rules"
    _type(drawer, "   ")
    assert drawer.count_label.text() == f"{total} rules"


def test_count_follows_catalog_size_not_a_constant(view):
    catalog = load_rule_catalog()
    view.set_rule_catalog(replace(catalog, specs=catalog.specs[:3]))
    view.rules_button.click()
    drawer = view.rules_drawer
    assert drawer.count_label.text() == "3 rules"
    _type(drawer, catalog.specs[0].id)
    assert drawer.count_label.text() == f"{len(replace(catalog, specs=catalog.specs[:3]).search(catalog.specs[0].id))} of 3 rules"


def test_etco2_filter_shows_exactly_the_catalog_matches(view):
    view.rules_button.click()
    drawer = view.rules_drawer
    _type(drawer, "etco2")
    assert drawer.visible_rule_ids() == _ids("etco2")
    assert {"etco2_high", "etco2_low", "etco2_zero_vent"} <= set(drawer.visible_rule_ids())
    assert "spo2_low" not in drawer.visible_rule_ids()
    _type(drawer, "")
    assert drawer.visible_rule_ids() == [spec.id for spec in load_rule_catalog().specs]


def test_empty_result_message_and_clear_search(view):
    view.rules_button.click()
    drawer = view.rules_drawer
    _type(drawer, "zzz")
    assert drawer.visible_rule_ids() == []
    assert drawer.count_label.text() == f"0 of {len(load_rule_catalog().specs)} rules"
    assert drawer.empty_state.isVisible()
    assert "No rule matches" in drawer.empty_label.text()
    drawer.clear_button.click()
    assert drawer.search_edit.text() == ""
    assert not drawer.empty_state.isVisible()
    assert drawer.visible_rule_ids() == [spec.id for spec in load_rule_catalog().specs]


def test_rows_show_label_signals_thresholds_with_units_and_phase_skips(view):
    view.rules_button.click()
    drawer = view.rules_drawer
    text = _row_text(drawer, "pip_high")
    assert "High PIP" in text and "pip_high" in text
    assert "Signals" in text and "PIP" in text
    assert "warn at least 30 cmH2O" in text and "critical at least 35 cmH2O" in text
    assert "Skipped in" not in text
    skipped = _row_text(drawer, "peep_zero_long")
    assert "pre_induction" in skipped and "Skipped in" in skipped
    assert "{" not in text and "mappingproxy" not in _row_text(drawer, "agent_drift")
    assert "S 0.5" in _row_text(drawer, "agent_drift")
    for rule_id in ("compliance_concern", "hypoventilation_pattern", "desat_with_vent_issue"):
        row = _row_text(drawer, rule_id)
        assert "Combines" in row and load_rule_catalog().get(rule_id).composition in row
        assert "none of its own" not in row and "Thresholds" not in row
    assert "at most 5 mL/min" in _row_text(drawer, "compliance_concern")
    assert "Combines" not in text


def test_every_numeric_parameter_appears_in_a_readable_phrase():
    for spec in load_rule_catalog("default").specs:
        assert len(threshold_parts(spec)) == len(spec.params)


def test_show_rule_opens_clears_a_hiding_search_and_scrolls(view):
    drawer = view.rules_drawer
    _type(drawer, "zzz")
    assert not drawer.is_open
    view.show_rule("map_low")
    QApplication.processEvents()
    assert drawer.is_open
    assert drawer.search_text() == ""
    assert "map_low" in drawer.visible_rule_ids()
    row = drawer._rows["map_low"]
    assert row.property("highlight") is True
    assert drawer.rule_scroll.verticalScrollBar().value() > 0
    top = row.mapTo(drawer.rule_scroll.viewport(), row.rect().topLeft()).y()
    assert 0 <= top and top + row.height() <= drawer.rule_scroll.viewport().height() + 1
    view.show_rule("pip_high")
    QApplication.processEvents()
    assert drawer._rows["map_low"].property("highlight") is False
    assert drawer._rows["pip_high"].property("highlight") is True


def test_show_rule_keeps_a_search_that_still_shows_the_rule(view):
    view.show_rule("etco2_low")
    _type(view.rules_drawer, "etco2")
    view.show_rule("etco2_low")
    assert view.rules_drawer.search_text() == "etco2"
    assert view.rules_drawer.visible_rule_ids() == _ids("etco2")


def test_unknown_rule_id_shows_all_rules_with_a_notice(view):
    drawer = view.rules_drawer
    _type(drawer, "etco2")
    view.show_rule("not_a_rule")
    assert drawer.is_open
    assert drawer.visible_rule_ids() == [spec.id for spec in load_rule_catalog().specs]
    assert "not_a_rule" in drawer.notice_label.text()
    assert drawer.notice_label.isVisible()
    _type(drawer, "pip")
    assert not drawer.notice_label.isVisible()


def test_escape_closes_and_returns_focus_to_the_rules_button(view, qapp):
    view.rules_button.click()
    drawer = view.rules_drawer
    assert drawer.search_edit.hasFocus()
    QTest.keyClick(drawer.search_edit, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert not drawer.is_open
    assert view.rules_button.hasFocus()
    image = view.rules_button.grab().toImage()
    ring = QColor("#8c739f")
    assert image.pixelColor(0, image.height() // 2) == ring
    assert image.pixelColor(image.width() // 2, 0) == ring


def test_escape_returns_focus_to_whatever_had_it_for_ctrl_r(view, qapp):
    view.question_edit.setFocus()
    assert view.question_edit.hasFocus()
    QTest.keyClick(view.question_edit, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    assert view.rules_drawer.is_open
    assert view.rules_drawer.search_edit.hasFocus()
    QTest.keyClick(view.rules_drawer.search_edit, Qt.Key.Key_Escape)
    assert not view.rules_drawer.is_open
    assert view.question_edit.hasFocus()


def test_escape_from_the_list_and_the_close_button_also_close(view, qapp):
    view.rules_button.click()
    QTest.keyClick(view.rules_drawer.rule_scroll, Qt.Key.Key_Escape)
    assert not view.rules_drawer.is_open
    view.rules_button.click()
    view.rules_drawer.close_button.click()
    assert not view.rules_drawer.is_open
    assert view.rules_button.hasFocus()


def test_ctrl_r_opens_the_drawer_on_the_canvas_and_on_f(view, qapp):
    assert not view.rules_drawer.is_open
    QTest.keyClick(view, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    assert view.rules_drawer.is_open
    view.rules_drawer.close_drawer()

    view.stack.setCurrentIndex(1)
    view.back_button.setFocus()
    QTest.keyClick(view.back_button, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    assert view.rules_drawer.is_open
    QTest.keyClick(view.rules_drawer.search_edit, Qt.Key.Key_Escape)
    assert not view.rules_drawer.is_open
    assert view.back_button.hasFocus()


def test_escape_on_f_without_an_opener_falls_back_to_a_visible_button(view, qapp):
    view.stack.setCurrentIndex(1)
    view.show_rule("pip_high")
    QTest.keyClick(view.rules_drawer.search_edit, Qt.Key.Key_Escape)
    assert not view.rules_drawer.is_open
    assert view.back_button.hasFocus() or view.window().focusWidget() is view.back_button


@pytest.mark.parametrize("preset", ["default", "lenient"])
def test_header_names_the_preset_and_hash_prefix(view, preset):
    catalog = load_rule_catalog(preset)
    view.set_rule_catalog(catalog)
    view.rules_button.click()
    header = view.rules_drawer.header_label.text()
    assert header == f"Preset: {preset} · thresholds {catalog.thresholds_sha256[:12]}"
    assert len(catalog.thresholds_sha256[:12]) == 12


def test_header_says_when_the_preset_was_not_recorded(view):
    catalog = load_rule_catalog("default")
    view.set_rule_catalog(catalog, recorded=False)
    view.rules_button.click()
    assert view.rules_drawer.header_label.text().endswith(", not recorded with this data")
    view.set_rule_catalog(catalog, recorded=True)
    assert "not recorded" not in view.rules_drawer.header_label.text()


def test_catalog_replacement_while_open_reapplies_the_search(view):
    view.rules_button.click()
    drawer = view.rules_drawer
    _type(drawer, "etco2")
    default_text = _row_text(drawer, "etco2_high")
    lenient = load_rule_catalog("lenient")
    view.set_rule_catalog(lenient)
    assert drawer.is_open
    assert drawer.search_text() == "etco2"
    assert drawer.header_label.text().startswith("Preset: lenient")
    assert drawer.count_label.text() == f"{len(lenient.search('etco2'))} of {len(lenient.specs)} rules"
    assert drawer.visible_rule_ids() == [spec.id for spec in lenient.search("etco2")]
    assert _row_text(drawer, "etco2_high") != default_text
    assert f"warn at least {lenient.get('etco2_high').params['warn']:g} mmHg" in _row_text(drawer, "etco2_high")


def test_catalog_replaced_while_closed_is_used_on_the_next_open(view):
    view.set_rule_catalog(load_rule_catalog("strict"))
    view.rules_button.click()
    assert view.rules_drawer.header_label.text().startswith("Preset: strict")
    assert f"warn at least {load_rule_catalog('strict').get('pip_high').params['warn']:g} cmH2O" in _row_text(
        view.rules_drawer, "pip_high"
    )


def test_invalid_config_is_stated_in_the_drawer(view):
    view.set_rule_catalog(None, recorded=True, error="rules.pip_high.warn must be a number")
    view.rules_button.click()
    drawer = view.rules_drawer
    assert "invalid" in drawer.header_label.text().lower()
    assert "rules.pip_high.warn must be a number" in drawer.empty_label.text()
    assert drawer.visible_rule_ids() == []
    assert not drawer.search_edit.isEnabled()
    view.set_rule_catalog(load_rule_catalog("default"))
    assert drawer.search_edit.isEnabled()
    assert drawer.visible_rule_ids() == [spec.id for spec in load_rule_catalog().specs]


def test_drawer_opens_while_a_search_is_running_and_without_a_model(view):
    view.set_searching(True)
    view.rules_button.click()
    assert view.rules_drawer.is_open
    assert view.cancel_button.isVisible()
    assert view.selected_model() is None
    _type(view.rules_drawer, "pip")
    assert view.rules_drawer.visible_rule_ids() == _ids("pip")
    view.set_searching(False)


def test_drawer_fits_at_900_px_and_the_board_stays_usable(view, qapp):
    view.resize(900, 900)
    view.rules_button.click()
    qapp.processEvents()
    drawer = view.rules_drawer
    assert drawer.width() <= int(view.width() * 0.42)
    assert view.minimumSizeHint().width() <= 900
    assert view.width() == 900
    scroll = view.board_splitter.widget(0)
    assert not scroll.horizontalScrollBar().isVisible()
    assert scroll.viewport().width() >= 480
    assert view.question_edit.isVisible() and view.question_edit.width() >= 300
    for button in (view.rules_button, view.reset_button, view.add_comparison_button):
        origin = button.mapTo(view, button.rect().topLeft())
        assert origin.x() >= 0 and origin.x() + button.width() <= view.width() - drawer.width()
    assert not drawer.rule_scroll.horizontalScrollBar().isVisible()
    viewport = drawer.rule_scroll.viewport()
    for label in drawer._list.findChildren(QLabel):
        if not label.isVisible() or not label.wordWrap():
            continue
        assert label.height() >= label.heightForWidth(label.width()) - 1, label.text()
        right = label.mapTo(viewport, label.rect().topRight()).x()
        assert right <= viewport.width(), label.text()
    for widget in (drawer.header_label, drawer.count_label):
        assert widget.width() <= drawer.width()
    view.question_edit.setFocus()
    QTest.keyClicks(view.question_edit, "pressure")
    assert view.question_edit.text() == "pressure"


def test_header_buttons_wrap_beside_the_drawer_at_900_px(view, qapp):
    view.resize(900, 900)
    view.rules_button.click()
    qapp.processEvents()
    viewport = view.board_splitter.widget(0).viewport()
    buttons = view._header_buttons
    for button in buttons:
        origin = button.mapTo(viewport, button.rect().topLeft())
        assert origin.x() >= 0 and origin.x() + button.width() <= viewport.width(), button.text()
    assert buttons[-1].y() > buttons[0].y()
    view.rules_drawer.close_drawer()
    qapp.processEvents()
    view.resize(1400, 900)
    qapp.processEvents()
    assert len({button.y() for button in buttons}) == 1


def test_the_board_regains_its_layout_when_the_drawer_closes(view, qapp):
    view.resize(1300, 900)
    qapp.processEvents()
    assert view._layout_mode == "wide"
    view.rules_button.click()
    assert view._layout_mode == "narrow"
    view.rules_drawer.close_drawer()
    assert view._layout_mode == "wide"


def test_no_comparison_hint_overlaps_a_card_when_narrow(view, qapp):
    view.resize(900, 900)
    qapp.processEvents()
    grid = view._evidence_grid
    hint = grid.getItemPosition(grid.indexOf(view.comparison_hint))
    card = grid.getItemPosition(grid.indexOf(view.comparison_card))
    assert hint == card
    for other in view._evidence_cards:
        if other is not view.comparison_card:
            assert grid.getItemPosition(grid.indexOf(other)) != hint


def test_text_contrast_is_enough_under_the_light_and_dark_app_themes(view, qapp):
    from src.desktop.theme import apply_theme
    from src.user_settings import THEME_DARK, THEME_LIGHT

    view.resize(900, 900)
    seen = {}
    try:
        for theme in (THEME_LIGHT, THEME_DARK):
            apply_theme(qapp, theme)
            view.show_rule("pip_rising")
            qapp.processEvents()
            drawer = view.rules_drawer
            frame = drawer.grab().toImage()
            checked = 0
            widgets = [
                widget for kind in (QLabel, QLineEdit, QPushButton) for widget in drawer.findChildren(kind)
            ]
            for widget in widgets:
                if not widget.isVisible() or widget.width() < 5:
                    continue
                origin = widget.mapTo(drawer, widget.rect().topLeft())
                area = QRect(origin, widget.size())
                viewport = drawer.rule_scroll.viewport()
                bounds = (QRect(viewport.mapTo(drawer, viewport.rect().topLeft()), viewport.size())
                          if drawer._list.isAncestorOf(widget) else frame.rect())
                if not bounds.contains(area):
                    continue
                crop = frame.copy(area).convertToFormat(QImage.Format.Format_RGB32)
                pixels = Counter(memoryview(bytes(crop.constBits())).cast("I"))
                if not pixels:
                    continue
                background = QColor.fromRgb(pixels.most_common(1)[0][0])
                text = max((QColor.fromRgb(pixel) for pixel in pixels), key=lambda color: _contrast(color, background))
                if text == background:
                    continue
                checked += 1
                assert _contrast(text, background) >= 4.5, (theme, widget.objectName(), background.name(), text.name())
            assert checked >= 10
            seen[theme] = qapp.palette().window().color().name()
    finally:
        apply_theme(qapp, THEME_LIGHT)
    assert seen[THEME_LIGHT] != seen[THEME_DARK]


def test_opening_and_filtering_are_fast(view, qapp):
    view.rules_button.click()
    view.rules_drawer.close_drawer()
    fresh = ResearchWorkspace()
    fresh.set_rule_catalog(load_rule_catalog("default"))
    fresh.resize(1100, 800)
    fresh.show()
    qapp.processEvents()
    try:
        opens = []
        for _ in range(5):
            started = time.perf_counter()
            fresh.rules_button.click()
            qapp.processEvents()
            opens.append(time.perf_counter() - started)
            fresh.rules_drawer.close_drawer()
        assert statistics.median(opens) < 0.15
        fresh.rules_button.click()
        keys = []
        for char in "etco2 high":
            started = time.perf_counter()
            QTest.keyClick(fresh.rules_drawer.search_edit, char)
            qapp.processEvents()
            keys.append(time.perf_counter() - started)
        assert statistics.median(keys) < 0.03
    finally:
        _dispose(fresh, qapp)


def _main_window(monkeypatch, tmp_path, preset="default", reloads=None):
    from src.desktop.app import MainWindow

    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("MOVER_EMR_DIR", str(tmp_path / "EMR"))
    monkeypatch.setattr("src.desktop.app.load_settings", lambda: {"preset": preset})
    for method in (
        "_load_corpus_scan", "_refresh_tables_and_selectors",
        "_refresh_charts_if_needed", "_refresh_ollama_models",
    ):
        monkeypatch.setattr(MainWindow, method, lambda *a, **k: None)
    monkeypatch.setattr(MainWindow, "_autoload_processed", lambda self: reloads.append(1) if reloads is not None else None)
    return MainWindow()


def test_drawer_follows_the_loaded_preset_not_the_saved_one(qapp, monkeypatch, tmp_path):
    window = _main_window(monkeypatch, tmp_path, preset="default")
    try:
        window.cases = pd.DataFrame()
        drawer = window.research.rules_drawer
        window.research.rules_button.click()
        assert drawer.header_label.text().startswith("Preset: default")
        window._set_loaded_preset("strict", recorded=True)
        assert drawer.header_label.text().startswith("Preset: strict")
        window._on_settings_applied({"preset": "lenient"})
        assert drawer.header_label.text().startswith("Preset: strict")
        window._set_loaded_preset("default", recorded=False)
        assert drawer.header_label.text().endswith(", not recorded with this data")
    finally:
        _dispose(window, qapp)


def test_invalid_thresholds_reach_the_drawer_from_the_window(qapp, monkeypatch, tmp_path):
    from src.guardrails.exceptions import ConfigValidationError

    window = _main_window(monkeypatch, tmp_path)
    try:
        def fail(preset, **_kwargs):
            raise ConfigValidationError("thresholds are broken")

        monkeypatch.setattr("src.config.load_thresholds", fail)
        window._refresh_rule_reference()
        drawer = window.research.rules_drawer
        window.research.rules_button.click()
        assert "thresholds are broken" in drawer.empty_label.text()
        assert "invalid" in drawer.header_label.text().lower()
    finally:
        _dispose(window, qapp)


def test_ctrl_r_in_the_workspace_opens_rules_and_elsewhere_still_reloads(qapp, monkeypatch, tmp_path):
    reloads = []
    window = _main_window(monkeypatch, tmp_path, reloads=reloads)
    try:
        window.show()
        window.activateWindow()
        window.tabs.setCurrentWidget(window.research)
        window.research.question_edit.setFocus()
        qapp.processEvents()
        before = len(reloads)
        QTest.keyClick(window.research.question_edit, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
        assert window.research.rules_drawer.is_open
        assert len(reloads) == before
        QTest.keyClick(window.tabs.tabBar(), Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
        assert len(reloads) == before + 1
    finally:
        _dispose(window, qapp)


@pytest.fixture
def workspace():
    app = QApplication.instance() or QApplication([])
    view = ResearchWorkspace()
    view.resize(900, 860)
    view.set_rule_catalog(load_rule_catalog())
    view.show()
    view.activateWindow()
    app.processEvents()
    yield view
    view.close()
    view.deleteLater()
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_invalid_catalog_focuses_close_and_escape_restores_opener(workspace):
    workspace.set_rule_catalog(None, error="invalid thresholds")
    workspace.rules_button.click()
    drawer = workspace.rules_drawer
    assert drawer.close_button.hasFocus()
    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Escape)
    assert not drawer.is_open
    assert workspace.rules_button.hasFocus()


@pytest.mark.parametrize("location", ["question", "canvas", "detail"])
def test_escape_anywhere_in_workspace_closes_drawer(workspace, location):
    workspace.rules_button.click()
    if location == "question":
        target = workspace.question_edit
    elif location == "detail":
        workspace.stack.setCurrentIndex(1)
        target = workspace.back_button
    else:
        target = workspace.candidate_table
    target.setFocus()
    QTest.keyClick(target, Qt.Key.Key_Escape)
    assert not workspace.rules_drawer.is_open
    expected = workspace.back_button if location == "detail" else workspace.rules_button
    assert expected.hasFocus()


@pytest.mark.parametrize("opener_state", ["hidden", "deleted"])
def test_escape_with_unavailable_opener_uses_fallback(workspace, opener_state):
    opener = QLineEdit(workspace)
    opener.show()
    workspace.open_rules(opener)
    if opener_state == "hidden":
        opener.hide()
    else:
        delete(opener)
    workspace.question_edit.setFocus()
    QTest.keyClick(workspace.question_edit, Qt.Key.Key_Escape)
    assert not workspace.rules_drawer.is_open
    assert workspace.rules_button.hasFocus()


def test_closed_drawer_leaves_escape_to_the_workspace(workspace):
    class EscapeReceiver(QLineEdit):
        received = False

        def keyPressEvent(self, event):  # noqa: N802
            if event.key() == Qt.Key.Key_Escape:
                self.received = True
            super().keyPressEvent(event)

    field = EscapeReceiver(workspace)
    field.show()
    workspace.open_rules(field)
    workspace.rules_drawer.close_drawer()
    field.setFocus()
    QTest.keyClick(field, Qt.Key.Key_Escape)
    assert field.received
    assert not workspace.rules_drawer.is_open


@pytest.mark.parametrize(("rule_id", "phrase"), [
    ("agent_low_maint", "exempt when end-tidal N2O is at least 30 %"),
    ("agent_low_maint", "below 0.3 MAC"),
    ("agent_low_maint", "case fraction at least 0.2"),
    ("agent_low_maint", "case fraction at most 0.8"),
    ("agent_drift", "drift at least by agent S 0.5 vol %"),
    ("agent_fi_et_gap", "gap at least by agent S 1 vol %"),
    ("pip_high", "warn at least 30 cmH2O"),
    ("etco2_low", "warn at most 28 mmHg"),
    ("fio2_high_long", "from minute 15 min"),
])
def test_parameter_phrases_match_engine_comparisons(rule_id, phrase):
    assert any(phrase in part for part in threshold_parts(load_rule_catalog().get(rule_id)))


def test_narrow_shortlist_fits_all_columns_and_keeps_full_text(workspace):
    candidate = SearchCandidate(
        pid="SYN0000000001", source_label="analyzed case", analyzed=True,
        procedure="Laparoscopic procedure with a long synthetic description",
        age=55, gender="M", source_text="Synthetic case", rank_method="keyword", score=0.5,
    )
    workspace.set_results(RetrievalResult(candidates=(candidate,)))
    table = workspace.candidate_table
    wide_widths = [table.columnWidth(i) for i in range(3)]
    workspace.rules_button.click()
    for _ in range(3):
        QApplication.processEvents()
    assert not table.horizontalScrollBar().isVisible()
    assert sum(table.columnWidth(i) for i in range(3)) <= table.viewport().width()
    for column in range(3):
        item = table.item(0, column)
        assert item.toolTip() == item.text()
        assert table.visualItemRect(item).right() < table.viewport().width()
    assert table.textElideMode() == Qt.TextElideMode.ElideRight
    assert table.fontMetrics().horizontalAdvance(candidate.procedure) > table.columnWidth(1)
    workspace.rules_drawer.close_drawer()
    for _ in range(3):
        QApplication.processEvents()
    assert [table.columnWidth(i) for i in range(3)] == wide_widths
