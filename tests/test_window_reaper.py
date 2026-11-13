"""The clean-up in conftest deletes windows a test leaves open, keeps windows that existed before the test, and
waits for a window's running thread before deleting it.

These tests run in file order and each looks at what the clean-up did after the one before it.
"""

from __future__ import annotations

import threading
import time

import pytest
import shiboken6
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QWidget

_left: dict = {}
_release = threading.Event()


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module", autouse=True)
def _release_the_thread_at_the_end():
    """Run on its own, the thread test leaves its thread waiting; a QThread still running at exit aborts Python."""
    yield
    _release.set()
    busy = _left.get("busy")
    if busy is not None and shiboken6.isValid(busy[1]):
        busy[1].wait(5000)


@pytest.fixture(scope="module")
def module_window(qapp):
    from PySide6.QtCore import QEvent

    window = QWidget()
    window.show()
    qapp.processEvents()
    yield window
    window.close()
    window.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_a_window_from_a_module_fixture_is_alive_in_the_first_test(module_window):
    assert shiboken6.isValid(module_window) and module_window.isVisible()
    _left["module"] = module_window


def test_a_window_that_existed_before_the_test_is_kept(module_window):
    assert module_window is _left["module"] and shiboken6.isValid(module_window)


def test_leave_a_window_open(qapp):
    window = QWidget()
    window.show()
    qapp.processEvents()
    _left["leaked"] = window


def test_the_window_left_open_by_the_test_before_was_deleted():
    assert not shiboken6.isValid(_left["leaked"])


class _Waits(QThread):
    def run(self):
        _release.wait(10)


def test_leave_a_window_whose_thread_still_runs(qapp):
    window = QWidget()
    thread = _Waits(window)
    window.show()
    thread.start()
    qapp.processEvents()
    _left["busy"] = (window, thread)


def test_a_window_with_a_running_thread_is_kept_until_the_thread_ends():
    window, thread = _left["busy"]
    assert shiboken6.isValid(window) and thread.isRunning()
    _release.set()
    assert thread.wait(5000)
    time.sleep(0.05)


def test_once_its_thread_has_ended_the_window_is_deleted():
    window, _thread = _left["busy"]
    assert not shiboken6.isValid(window)
