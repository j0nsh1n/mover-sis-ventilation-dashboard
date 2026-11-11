"""First-time setup fills the window, stores what the old wizard stored, and offers fictional demo data safely."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.desktop import demo_data
from src.desktop.app import MainWindow
from src.desktop.demo_data import DemoDataError, DemoDataWorker, build_demo_data, check_demo_folder
from src.desktop.setup_flow import DATA, DEMO, DONE, WELCOME, SetupFlow
from src.runtime_paths import (
    DEMO_MARKER,
    clear_session_path_overrides,
    configure_emr_directory,
    demo_data_dir,
    demo_data_in_use,
    emr_dir,
    processed_dir,
)
from src.user_settings import load_settings, needs_first_run_setup


_ORIGINAL_STARTUP = MainWindow._startup


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_DEMO_DATA_DIR", str(tmp_path / "demo-data"))
    for name in ("MOVER_EMR_DIR", "MOVER_PROCESSED_DIR", "MOVER_WAVE_DIR"):
        monkeypatch.delenv(name, raising=False)
    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setenv("OLLAMA_MODELS", str(models))
    monkeypatch.setattr("src.llm.service.ensure_ollama", lambda **kw: SimpleNamespace(models=[]))
    # three fictional cases keep the pipeline run short
    monkeypatch.setattr(demo_data, "DEMO_CASES", 3)
    clear_session_path_overrides()
    yield tmp_path
    clear_session_path_overrides()


def _pump(qapp, until, seconds=60):
    import time

    deadline = time.monotonic() + seconds
    while not until() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    return until()


# ----- demo data: folder safety -----
def test_the_demo_folder_is_never_inside_the_repo_or_program_folder(monkeypatch, tmp_path):
    from src.runtime_paths import app_dir

    monkeypatch.delenv("MOVER_DEMO_DATA_DIR")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = demo_data_dir()
    assert not root.is_relative_to(app_dir()) and root == (tmp_path / "xdg" / "mover-suite" / "demo-data").resolve()
    monkeypatch.setenv("MOVER_DEMO_DATA_DIR", str(app_dir() / "data" / "demo"))
    with pytest.raises(DemoDataError, match="inside the program folder"):
        check_demo_folder(demo_data_dir())


def test_a_folder_the_app_did_not_make_is_never_touched(tmp_path):
    root = tmp_path / "demo-data"
    (root / "EMR").mkdir(parents=True)
    (root / "EMR" / "patient_information.csv").write_text("PID\nREAL-1\n", encoding="utf-8")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(DemoDataError, match="was not made by MOVER Suite"):
        build_demo_data(root)
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert not (root / DEMO_MARKER).exists()


def test_a_file_in_the_place_of_the_folder_is_refused(tmp_path):
    root = tmp_path / "demo-data"
    root.write_text("not a folder", encoding="utf-8")
    with pytest.raises(DemoDataError, match="is not a folder"):
        build_demo_data(root)
    assert root.read_text(encoding="utf-8") == "not a folder"


def test_demo_data_is_fictional_complete_and_reused_when_built_twice(tmp_path, monkeypatch):
    root = tmp_path / "demo-data"
    steps: list[int] = []
    emr = build_demo_data(root, steps.append)
    assert emr == root / "EMR" and steps == [0, 1, 2]
    marker = json.loads((root / DEMO_MARKER).read_text(encoding="utf-8"))
    assert marker["made_by"] == "MOVER Suite" and marker["complete"] is True
    assert (root / "processed" / "cases.parquet").is_file()
    truth = json.loads((emr / "synthetic_truth.json").read_text(encoding="utf-8"))
    assert truth["n_cases"] == 3 and all(pid.startswith("SYN") for pid in truth["cases"])
    stamp = (root / "processed" / "cases.parquet").stat().st_mtime_ns
    monkeypatch.setattr("src.pipeline.synthetic.generate_synthetic_emr",
                        lambda *a, **k: pytest.fail("a complete demo must be reused, not made again"))
    again: list[int] = []
    assert build_demo_data(root, again.append) == emr
    assert again == [2] and (root / "processed" / "cases.parquet").stat().st_mtime_ns == stamp


def test_an_unfinished_demo_is_made_again_over_its_own_files(tmp_path):
    root = tmp_path / "demo-data"
    root.mkdir()
    (root / DEMO_MARKER).write_text(json.dumps({"made_by": "MOVER Suite", "complete": False}), encoding="utf-8")
    (root / "EMR").mkdir()
    (root / "EMR" / "patient_information.csv").write_text("PID\nhalf\n", encoding="utf-8")
    build_demo_data(root)
    assert json.loads((root / DEMO_MARKER).read_text(encoding="utf-8"))["complete"] is True
    assert "half" not in (root / "EMR" / "patient_information.csv").read_text(encoding="utf-8")


def test_a_failed_build_says_so_in_words_and_keeps_the_exception_text_in_the_log(tmp_path, monkeypatch, synthetic_emr):
    def boom(*args, **kwargs):
        raise RuntimeError("PRIVATE-DETAIL /secret/path")

    monkeypatch.setattr("src.pipeline.run.run_pipeline", boom)
    configure_emr_directory(synthetic_emr, persist=False)
    before = (emr_dir(), processed_dir())
    worker = DemoDataWorker()
    failures: list[tuple[str, str]] = []
    worker.failed.connect(lambda message, detail: failures.append((message, detail)))
    worker.run()
    assert len(failures) == 1
    message, detail = failures[0]
    assert message == "Demo data could not be made."
    assert "demo-data:RuntimeError" in detail and "PRIVATE-DETAIL" not in detail and "PRIVATE-DETAIL" not in message
    log = (tmp_path / "config" / "app-errors.log").read_text(encoding="utf-8")
    assert "PRIVATE-DETAIL" in log
    # the folders in use are put back: the pipeline run had selected the demo folder
    assert (emr_dir(), processed_dir()) == before


def test_demo_data_in_use_is_true_only_while_the_emr_is_inside_a_demo_folder_the_app_made(tmp_path, synthetic_emr):
    assert not demo_data_in_use()
    root = tmp_path / "demo-data"
    build_demo_data(root)
    demo_data.use_demo_data(root)
    assert demo_data_in_use() and emr_dir() == (root / "EMR").resolve()
    assert load_settings()["setup_complete"] is True
    configure_emr_directory(synthetic_emr, persist=True)
    assert not demo_data_in_use()
    assert root.is_dir() and (root / DEMO_MARKER).is_file(), "the demo folder is left in place"
    # the same folder without the marker is not demo data, whatever it holds
    (root / DEMO_MARKER).unlink()
    configure_emr_directory(root / "EMR", persist=True)
    assert not demo_data_in_use()


# ----- the flow -----
@pytest.fixture
def flow(qapp):
    widget = SetupFlow("9.9.9")
    widget.resize(1100, 760)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.shutdown()
    widget.close()


def test_the_flow_has_three_steps_and_says_what_the_app_is(flow):
    assert flow.current_page() == WELCOME
    assert flow.side.findChild(type(flow.welcome_kicker), "setupName").text() == "MOVER Suite"
    texts = " ".join(label.text() for label in flow.findChildren(type(flow.welcome_kicker)))
    for claim in ("For research and education", "Not for patient care", "Local only"):
        assert claim in texts, claim
    assert flow.demo_button_welcome.text() == "Try with fictional demo data"
    assert "not patient records" in flow.demo_button_welcome.accessibleDescription()
    assert flow.welcome_kicker.text() == "Step 1 of 3"
    assert [flow.side._rows[i][1].text() for i in range(3)] == ["Welcome", "Data folders", "Done"]
    assert "current step" in flow.side._rows[0][1].accessibleName()


def test_continue_checks_the_emr_folder_and_stores_what_the_old_wizard_stored(flow, qapp, env, synthetic_emr):
    flow.start_button.click()
    assert flow.current_page() == DATA and flow.side._rows[1][1].property("state") == "current"
    flow.row_emr.set_text(str(env / "nowhere"))
    flow.continue_button.click()
    assert flow.current_page() == DATA and flow.data_status.text().startswith("Not saved.")
    assert needs_first_run_setup() is True
    flow.row_emr.set_text(str(synthetic_emr))
    assert "required tables found" in flow.row_emr.note.text()
    flow.combo_preset.setCurrentIndex(flow.combo_preset.findData("strict"))
    flow.combo_theme.setCurrentIndex(flow.combo_theme.findData("dark"))
    flow.continue_button.click()
    assert flow.current_page() == DONE
    saved = load_settings()
    assert saved["emr_dir"] == str(synthetic_emr.resolve()) and saved["setup_complete"] is True
    assert saved["preset"] == "strict" and saved["theme"] == "dark"
    assert saved["processed_dir"] == str(synthetic_emr.resolve().parent / "processed")
    assert saved["ollama_embed_model"] == "nomic-embed-text"
    done = []
    flow.completed.connect(done.append)
    flow.finish_button.click()
    assert done and done[0]["emr_dir"] == str(synthetic_emr.resolve()) and done[0]["preset"] == "strict"


def test_back_and_continue_have_keyboard_keys_and_enter_in_the_folder_field_continues(flow, synthetic_emr):
    assert flow.back_button.shortcut() == QKeySequence("Alt+Left")
    assert flow.continue_button.shortcut() == QKeySequence("Alt+Right")
    flow.start_button.click()
    flow.row_emr.set_text(str(synthetic_emr))
    QTest.keyClick(flow.row_emr.edit, Qt.Key.Key_Return)
    assert flow.current_page() == DONE
    flow.back_from_done.click()
    assert flow.current_page() == DATA


def test_not_now_and_escape_leave_without_saving_anything(flow, env):
    left = []
    flow.dismissed.connect(lambda: left.append(True))
    flow.not_now_welcome.click()
    flow.start_button.click()
    flow.not_now_data.click()
    QTest.keyClick(flow, Qt.Key.Key_Escape)
    assert left == [True, True, True]
    assert not (env / "config" / "settings.json").exists() or "emr_dir" not in load_settings()


def test_tab_order_on_the_data_step_follows_the_screen(flow, qapp):
    flow.start_button.click()
    qapp.processEvents()
    names = []
    for _ in range(9):
        widget = qapp.focusWidget()
        assert widget is not None
        names.append(widget.accessibleName() or widget.text())
        QTest.keyClick(widget, Qt.Key.Key_Tab)
    assert names[:5] == ["EMR folder", "Choose emr folder", "Processed folder", "Choose processed folder", "Rule preset"]
    assert names[5] == "Advanced options"


# ----- the demo through the flow -----
def test_the_demo_button_is_safe_to_press_twice_and_shows_named_steps(flow, qapp, env, monkeypatch):
    release = threading.Event()
    started: list[int] = []
    real_build = demo_data.build_demo_data

    def slow_build(root, progress=lambda _s: None):
        started.append(1)
        progress(0)
        release.wait(30)
        return real_build(root, progress)

    monkeypatch.setattr("src.desktop.demo_data.build_demo_data", slow_build)
    done = []
    flow.completed.connect(done.append)
    flow.start_demo()
    first = flow._demo_worker
    flow.start_demo()
    flow.demo_button_welcome.click()
    assert flow._demo_worker is first and flow.current_page() == DEMO
    assert _pump(qapp, lambda: started == [1])
    assert _pump(qapp, lambda: flow.demo_steps[0].text().endswith("…"))
    assert not flow.demo_back.isEnabled()
    release.set()
    assert _pump(qapp, lambda: bool(done), seconds=120)
    assert started == [1], "a second press must not start a second build"
    assert demo_data_in_use()
    assert all(label.text().startswith("✓ ") for label in flow.demo_steps)
    assert done[0]["emr_dir"] == str((env / "demo-data" / "EMR").resolve())


def test_a_demo_that_cannot_be_made_says_why_and_leaves_a_way_back(flow, qapp, env, monkeypatch):
    (env / "demo-data").mkdir()
    (env / "demo-data" / "mine.txt").write_text("not yours", encoding="utf-8")
    done = []
    flow.completed.connect(done.append)
    flow.start_demo()
    assert _pump(qapp, lambda: flow.demo_back.isEnabled())
    assert "was not made by MOVER Suite" in flow.demo_status.text() and "Nothing else was changed" in flow.demo_status.text()
    assert not done and not demo_data_in_use()
    assert (env / "demo-data" / "mine.txt").read_text(encoding="utf-8") == "not yours"
    flow.demo_back.click()
    assert flow.current_page() == WELCOME


# ----- in the window -----
@pytest.fixture
def window(qapp, monkeypatch, env):
    reloads: list[bool] = []
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setattr(MainWindow, "_autoload_processed", lambda self: reloads.append(True))
    monkeypatch.setattr(MainWindow, "_refresh_ollama_models", lambda self, **kw: None)
    win = MainWindow()
    win.reloads = reloads
    win.resize(1380, 860)
    win.show()
    qapp.processEvents()
    yield win
    win._close_setup_flow()
    page = win._settings_page()
    if page is not None:
        page.discard()  # a failing test must not leave a leave prompt open on close
    win.close()
    qapp.processEvents()


def test_first_run_leads_to_setup_that_fills_the_window_and_not_now_leaves_the_app_usable(window, qapp, monkeypatch):
    monkeypatch.setattr(MainWindow, "_setup_bypassed", lambda self: False)
    _ORIGINAL_STARTUP(window)
    flow = window._setup_flow
    assert flow is not None and window._root.currentWidget() is flow
    assert flow.isVisible() and not window.rail.isVisibleTo(window)
    assert window.windowTitle().startswith("First-time setup")
    # commands that act on pages the flow hides do nothing while it shows
    window._module_actions["handoff"].trigger()
    window._nav_cases.trigger()
    assert window._current_module == "monitoring" and window.module_stack.currentWidget() is window.research
    assert "handoff" not in window._suite_pages
    window._open_palette()
    assert window._palette is None
    flow.not_now_welcome.click()
    assert window._setup_flow is None and window._root.currentIndex() == 0
    assert window.rail.isVisibleTo(window)
    assert not window.research.question_edit.isEnabled(), "research stays in the setup-required state"
    window._go("handoff")
    assert window.module_stack.currentWidget() is window._suite_pages["handoff"]
    assert window.windowTitle().startswith("Handoff practice")


def test_data_setup_from_settings_returns_to_research_and_loads_the_data(window, qapp, synthetic_emr):
    window._open_settings()
    window._suite_pages["settings"].setup_button.click()
    flow = window._setup_flow
    assert flow is not None and window.windowTitle().startswith("Data setup")
    flow.start_button.click()
    flow.row_emr.set_text(str(synthetic_emr))
    flow.continue_button.click()
    flow.finish_button.click()
    assert window._setup_flow is None and window.reloads == [True]
    assert window.module_stack.currentWidget() is window.research
    assert window.research.question_edit.isEnabled()


def test_demo_data_is_named_as_fictional_everywhere_the_source_is_named_and_real_data_removes_it(
        window, qapp, env, synthetic_emr):
    footer = window._footer
    assert footer.source.text() == "MOVER SIS local copy"
    window._open_setup_wizard()
    window._setup_flow.start_demo()
    assert _pump(qapp, lambda: window._setup_flow is None, seconds=120)
    assert window.reloads == [True], "the same loading the normal path runs"
    assert "Fictional demo data" in footer.source.text()
    assert "not patient records" in footer.source.accessibleName() and "not patient records" in footer.source.toolTip()
    window._open_settings()
    page = window._suite_pages["settings"]
    assert "fictional demo data made by MOVER Suite, not patient records" in page.demo_note.text()
    assert str(env / "demo-data") in page.demo_note.text() and not page.demo_note.isHidden()
    # switching to real data in Settings removes the fiction label and leaves the demo folder where it is
    page.row_emr.set_text(str(synthetic_emr))
    assert page.row_processed.text() == str(synthetic_emr.resolve().parent / "processed")
    assert page.save() is True
    assert not demo_data_in_use() and page.demo_note.isHidden()
    window._close_settings()
    assert footer.source.text() == "MOVER SIS local copy"
    assert (env / "demo-data" / DEMO_MARKER).is_file()

