"""
Which waveform files belong to a case, for SIS and for converted Epic folders.

SIS keeps one folder per case: ``Waveforms/<first 2 chars of PID>/<PID>/*.xml``.

Epic keeps files per patient, flat in prefix folders across several wave sets:
``wave_N/<set>/Waveforms/<first 2 chars>/<16-hex PAT_ID><IP|CB>-YYYY-MM-DD-HH-MM-SS-mmmZ.xml``. A converted
Epic folder (``src.pipeline.epic_import``) links each case to its patient's wave id in ``epic_wave_ids.csv``;
the case's files are that patient's files whose UTC start time falls in the case's operating-room window.
Epic case times are local hospital time (UC Irvine, America/Los_Angeles).

The wave id is never returned or written: callers get file paths for decoding and counts for display.
"""

from __future__ import annotations

import os
import re
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src.runtime_paths import emr_dir, wave_dir, waveform_case_dir

WAVE_IDS = "epic_wave_ids.csv"
HOSPITAL_TZ = ZoneInfo("America/Los_Angeles")
# A file covers up to 30 minutes, so one that starts this long before the room-in time can still hold the case.
WINDOW_BEFORE = timedelta(minutes=30)
_EPIC_FILE = re.compile(r"^([0-9a-fA-F]{16})(?:IP|CB)-(\d{4})-(\d\d)-(\d\d)-(\d\d)-(\d\d)-(\d\d)-\d{3}Z\.xml$")


def is_epic_converted(emr: Path | str | None) -> bool:
    return emr is not None and (Path(emr) / WAVE_IDS).is_file()


@lru_cache(maxsize=4)
def _epic_cases(emr: str, mtime: float) -> dict[str, tuple[str, pd.Timestamp | None, pd.Timestamp | None]]:
    """PID -> (wave id, OR start UTC, OR end UTC), from a converted Epic folder."""
    ids = pd.read_csv(Path(emr) / WAVE_IDS, dtype=str, keep_default_na=False)
    info = pd.read_csv(Path(emr) / "patient_information.csv", dtype=str, keep_default_na=False,
                       usecols=["PID", "OR_start", "OR_end"]).set_index("PID")

    def utc(value: str, *, earlier: bool) -> pd.Timestamp | None:
        """Local hospital time to UTC. In the repeated autumn hour the start takes the earlier reading and the end
        the later one, so the window covers the whole hour instead of becoming undefined."""
        t = pd.to_datetime(value, errors="coerce")
        if pd.isna(t):
            return None
        local = t.tz_localize(HOSPITAL_TZ, ambiguous=earlier, nonexistent="shift_forward")
        return None if pd.isna(local) else local.tz_convert("UTC")

    out = {}
    for pid, wave_id in zip(ids["PID"], ids["Wave_ID"], strict=True):
        row = info.loc[pid] if pid in info.index else None
        out[pid] = (wave_id.strip().lower(),
                    utc(row["OR_start"], earlier=True) if row is not None else None,
                    utc(row["OR_end"], earlier=False) if row is not None else None)
    return out


@lru_cache(maxsize=4)
def _waveform_dirs(root: str) -> tuple[Path, ...]:
    """Every ``Waveforms`` folder up to three levels below ``root`` (the Epic sets sit two levels down)."""
    found = []
    base = Path(root)
    if base.name == "Waveforms":
        return (base,)
    for dirpath, dirs, _files in os.walk(base):
        depth = len(Path(dirpath).relative_to(base).parts)
        if "Waveforms" in dirs:
            found.append(Path(dirpath) / "Waveforms")
        dirs[:] = [] if depth >= 2 else sorted(d for d in dirs if d != "Waveforms" and not d.startswith("."))
    return tuple(found)


@lru_cache(maxsize=1024)
def _prefix_index(root: str, prefix: str) -> dict[str, tuple[tuple[pd.Timestamp, Path], ...]]:
    """Every file in one prefix folder across all wave sets, by patient id; built once, so ~50k case lookups do not
    list the same folders again for each case."""
    by_id: dict[str, list[tuple[pd.Timestamp, Path]]] = {}
    for wdir in _waveform_dirs(root):
        # Match the prefix folder's name without regard to case, once: on a case-insensitive disk "AB" and "ab"
        # are one folder, and listing both would count every file twice.
        try:
            with os.scandir(wdir) as entries:
                folders = [Path(e.path) for e in entries if e.name.lower() == prefix.lower() and e.is_dir()]
        except OSError:
            continue
        for folder in folders:
            try:
                names = os.listdir(folder)
            except OSError:
                continue
            for name in names:
                m = _EPIC_FILE.match(name)
                if not m:
                    continue
                try:
                    t = pd.Timestamp(f"{m.group(2)}-{m.group(3)}-{m.group(4)} {m.group(5)}:{m.group(6)}:{m.group(7)}",
                                     tz="UTC")
                except ValueError:
                    continue
                by_id.setdefault(m.group(1).lower(), []).append((t, folder / name))
    return {k: tuple(sorted(v, key=lambda item: item[0])) for k, v in by_id.items()}


def _epic_files(pid: str, root: Path, emr: Path) -> list[Path]:
    cases = _epic_cases(str(emr), (emr / WAVE_IDS).stat().st_mtime)
    if pid not in cases:
        return []
    wave_id, start, end = cases[pid]
    # Files are per patient across all their admissions: without both room times there is no safe window.
    if not wave_id or start is None or end is None:
        return []
    files = []
    for t, path in _prefix_index(str(root), wave_id[:2]).get(wave_id, ()):
        if t < start - WINDOW_BEFORE or t > end:
            continue
        try:
            if path.stat().st_size == 0:
                continue
        except OSError:
            continue
        files.append(path)
    return files


def case_wave_files(pid: str, wdir: Path | str | None = None, emr: Path | str | None = None) -> list[Path]:
    """The case's waveform files, oldest first; empty files and other patients' files are left out."""
    root = Path(wdir) if wdir is not None else wave_dir()
    if root is None or not root.is_dir():
        return []
    emr_path = Path(emr) if emr is not None else emr_dir()
    if is_epic_converted(emr_path):
        return _epic_files(str(pid), root, emr_path)
    case_dir = waveform_case_dir(str(pid), root)
    if case_dir is None:
        return []
    return sorted(p for p in Path(case_dir).glob("*.xml") if p.stat().st_size > 0)


def has_case_waves(pid: str, wdir: Path | str | None = None, emr: Path | str | None = None) -> bool:
    """Whether the case has waveform data; for SIS this only checks that its folder exists."""
    root = Path(wdir) if wdir is not None else wave_dir()
    if root is None:
        return False
    emr_path = Path(emr) if emr is not None else emr_dir()
    if is_epic_converted(emr_path):
        return bool(_epic_files(str(pid), root, emr_path))
    return waveform_case_dir(str(pid), root) is not None


def wave_presence_text(pid: str, wdir: Path | str | None = None, emr: Path | str | None = None) -> str | None:
    """One line on the case's waveform files for text contexts, or None when there are none.

    Epic files are named by patient id, so for Epic only the count is given, never a path.
    """
    root = Path(wdir) if wdir is not None else wave_dir()
    if root is None:
        return None
    emr_path = Path(emr) if emr is not None else emr_dir()
    if is_epic_converted(emr_path):
        n = len(_epic_files(str(pid), root, emr_path))
        return f"{n} waveform files for this case's operating-room time" if n else None
    case_dir = waveform_case_dir(str(pid), root)
    if case_dir is None:
        return None
    try:
        n_files = sum(1 for p in case_dir.rglob("*") if p.is_file())
    except OSError:
        n_files = -1
    return f"{case_dir} ({n_files} files)"
