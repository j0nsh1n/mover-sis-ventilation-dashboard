"""PyInstaller entry point for the desktop app."""

from __future__ import annotations

import argparse
import os
import sys


def selftest_scan() -> int:
    from pathlib import Path
    from tempfile import TemporaryDirectory

    try:
        with TemporaryDirectory(prefix="mover-selftest-scan-") as directory:
            root = Path(directory)
            os.environ["MOVER_CONFIG_DIR"] = str(root / "config")
            os.environ["OLLAMA_MODELS"] = str(root / "ollama-models")
            os.environ["MOVER_ALLOW_EXTERNAL_OUTPUT"] = "1"
            from src.pipeline.synthetic import generate_synthetic_emr
            from src.pipeline.corpus_scan import scan_corpus

            emr = generate_synthetic_emr(root / "EMR", n_cases=20, seed=11)
            result = scan_corpus(emr, root / "processed", workers=2, cases_per_shard=10)
            if result.meta["failed_shards"]:
                raise RuntimeError(f"Failed scan shards: {result.meta['failed_shards']}")
            count = result.meta["n_scanned_cases"]
            if count != result.meta["n_ventilated_surgeries"] or result.meta["workers"] != 2:
                raise RuntimeError(f"Incomplete two-worker scan: {result.meta}")
        print(f"selftest-scan ok {count} cases")
        return 0
    except Exception as exc:
        print(f"selftest-scan failed: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="MOVER SIS Monitor desktop app")
    parser.add_argument("--selftest-scan", action="store_true", help=argparse.SUPPRESS)
    options, _qt_args = parser.parse_known_args()
    if options.selftest_scan:
        return selftest_scan()
    # Must set platform env BEFORE importing Qt
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and not os.environ.get(
        "QT_QPA_PLATFORM"
    ):
        os.environ["QT_QPA_PLATFORM"] = "xcb"

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

    from src.desktop.app import main as desktop_main

    return desktop_main()


if __name__ == "__main__":
    # First thing in the frozen app: the full-EMR scan starts worker processes
    # that re-launch this executable, and freeze_support() lets those children
    # run their job instead of opening a second window.
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
