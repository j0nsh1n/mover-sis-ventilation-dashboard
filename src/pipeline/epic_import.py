"""
Convert a MOVER Epic folder once into the SIS layout the rest of the app reads.

Epic keeps intra-operative monitoring in long flowsheet tables (one row per reading, some files over
50 GB); SIS keeps one wide row per minute. The importer streams the flowsheets in two passes with bounded
memory, so the pipeline, phases, flags, Monitoring view, medication explorer and co-pilot then work on Epic
cases unchanged:

1. Each flowsheet part is read in chunks. Intra-op rows whose name is mapped are written to shard files by
   case (``LOG_ID``), and every intra-op name is counted per case. A part is marked done only when it is
   complete, so an interrupted import resumes from the next unfinished part.
2. One shard at a time is turned into per-minute ventilator and vital-sign rows.

Then the small tables follow, keeping only cases with intra-op rows (owner, 2026-11-26): case information
(``PID`` = ``LOG_ID``; age, height, weight and sex converted; surgery start and end from a skin-incision event
where one exists, else anesthesia start and stop), procedure events and intra-op medications.

Patient identifiers stay out of the case tables: the link from a case to its waveform files (the Epic
``PAT_ID``) goes to ``epic_wave_ids.csv``, readable by the owner only, and never into ``patient_information``,
whose columns the pipeline copies into its outputs. Progress lines and the name report carry counts only.

    PYTHONPATH=. python -m src.pipeline.epic_import --epic-root /var/mnt/games/mover-epic --out /var/mnt/games/mover-epic-sis
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
import zlib
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

EPIC_INFO = "patient_information.csv"
EPIC_EVENTS = "patient_procedure events.csv"
EPIC_MEDICATIONS = "patient_medications.csv"
ID_MAP = "EPIC_MRN_PAT_ID.csv"
MARKER = ".mover-epic-import"
WAVE_IDS = "epic_wave_ids.csv"
NAMES_REPORT = "epic_import_names.json"
IMPORTER_VERSION = 1
CHUNK_ROWS = 1_000_000
N_SHARDS = 128
MIN_CELL = 11

# Flowsheet display name (spaces collapsed, lower case) -> (table, SIS column).
# Agent rows carry their agent letter; RR keeps both sources until the per-minute choice.
EPIC_FLOWSHEET_MAP: dict[str, tuple[str, str]] = {
    "etco2 (mmhg)": ("vent", "ETC02"),
    "etco2": ("vent", "ETC02"),
    "fico2": ("vent", "FIC02"),
    "exhaled tv (ml)": ("vent", "TV"),
    "etco2rr": ("vent", "RR_measured"),
    "set resp rate": ("vent", "RR_set"),
    "set peep": ("vent", "PEEP"),
    "pip observed": ("vent", "PIP"),
    "fio2 (%)": ("vent", "FI02"),
    "eto2": ("vent", "ET02"),
    "fin20 (%)": ("vent", "FIN20"),
    "etn2o": ("vent", "ETN20"),
    "sev et": ("vent", "Agent_Et:S"),
    "des et": ("vent", "Agent_Et:D"),
    "iso et": ("vent", "Agent_Et:I"),
    "sev in": ("vent", "Agent_Fi:S"),
    "des in": ("vent", "Agent_Fi:D"),
    "iso in": ("vent", "Agent_Fi:I"),
    "heart rate": ("vitals", "HRe"),
    "pulse": ("vitals", "HRp"),
    "spo2": ("vitals", "SP02"),
    "nibp": ("vitals", "NIBP"),
    "nibp - map": ("vitals", "nMAP"),
}
VENT_COLS = ["PID", "Obs_time", "Agent", "Agent_Fi", "Agent_Et", "ETC02", "FIC02", "TV",
             "RR", "PEEP", "PIP", "FIN20", "ETN20", "FI02", "ET02"]
VITALS_COLS = ["PID", "Obs_time", "HRe", "HRp", "nSBP", "nMAP", "nDBP", "SP02"]
INFO_COLS = ["PID", "Age", "Ht", "Wt", "Gender", "OR_start", "OR_end", "Surgery_start", "Surgery_end",
             "Procedure"]
MED_COLS = ["PID", "Drug_name", "Dose", "Drug_units", "Start_time", "End_time"]
# Medication actions that record a dose going in; holds, cancellations and missed doses are not doses.
GIVEN_ACTIONS = re.compile(r"^(given|new bag|bolus|bolus from bag|restarted|rate change|rate change dual sign|"
                           r"fluid - given by anesthesia|given by other|patch applied)$", re.I)
# The incision itself, not a closure or a pre-incision timeout that also names it.
INCISION = re.compile(r"^(?!.*\b(?:clos(?:e|ed|ing|ure)|pre[- ]|time[- ]?out)\b).*\bskin incision\b", re.I)
OUT_TIME = "%Y-%m-%d %H:%M:%S"
FLOW_COLS = ["LOG_ID", "FLO_DISPLAY_NAME", "RECORD_TYPE", "RECORDED_TIME", "MEAS_VALUE"]

Progress = Callable[[str], None]


_OUTPUTS = ("patient_information.csv", "patient_procedure_events.csv", "patient_medication.csv",
            "patient_ventilator.csv", "patient_vitals.csv", WAVE_IDS, NAMES_REPORT)
# What an earlier run of this importer may leave in its output folder, so a rerun is not refused over its own files.
_OWN_NAMES = {".work", MARKER, f"{MARKER}.tmp",
              *_OUTPUTS, *(f"{n}.tmp" for n in _OUTPUTS)}


class EpicImportError(RuntimeError):
    """The folder is not an Epic export the importer can read, or the output place is unsafe."""


def normalise_name(name: object) -> str:
    return re.sub(r"\s+", " ", str(name)).strip().lower()


def _header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", errors="replace") as fh:
        line = fh.readline()
    return [c.strip().strip('"') for c in line.rstrip("\r\n").split(",")]


def looks_like_epic_emr(path: Path | str) -> bool:
    """An Epic EMR folder: case information keyed by LOG_ID and MRN (no SIS PID) beside the Epic event file."""
    p = Path(path)
    info = p / EPIC_INFO
    if not info.is_file() or not (p / EPIC_EVENTS).is_file():
        return False
    try:
        cols = {c.upper() for c in _header(info)}
    except OSError:
        return False
    return {"LOG_ID", "MRN"} <= cols and "PID" not in cols


def _walk(root: Path, max_depth: int = 4):
    """Folders under ``root`` to ``max_depth``, skipping waveform trees (hundreds of thousands of files)."""
    for dirpath, dirs, files in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        dirs[:] = [] if depth >= max_depth else sorted(d for d in dirs if d != "Waveforms" and not d.startswith("."))
        yield Path(dirpath), files


def find_epic_emr(root: Path | str) -> Path | None:
    """The Epic EMR folder at or below ``root`` (up to four levels), or None."""
    for folder, _files in _walk(Path(root)):
        if looks_like_epic_emr(folder):
            return folder
    return None


def epic_root_for(emr: Path | str) -> Path | None:
    """The Epic export's top folder for an Epic EMR folder: the nearest parent (up to three up) with flowsheets."""
    p = Path(emr).resolve()
    for parent in [p, *list(p.parents)[:3]]:
        if find_flowsheets(parent):
            return parent
    return None


def default_output_for(epic_root: Path | str) -> Path:
    """Where a conversion goes unless the person picks another place: beside the export, never inside it."""
    root = Path(epic_root).resolve()
    return root.with_name(f"{root.name}-sis")


def _part_number(path: Path) -> int:
    m = re.search(r"(\d+)$", path.stem)
    return int(m.group(1)) if m else 0


def find_flowsheets(root: Path | str) -> list[Path]:
    """The cleaned flowsheet parts under ``root``, in part order (the raw exports are not used)."""
    found = [folder / f for folder, files in _walk(Path(root)) for f in files
             if re.fullmatch(r"flowsheet_part\d+\.csv", f)]
    return sorted(found, key=_part_number)


def _find_file(root: Path, name: str) -> Path | None:
    for folder, files in _walk(root):
        if name in files:
            return folder / name
    return None


def height_cm(value: object) -> float:
    """Epic heights are feet and inches (for example 5' 7 or 5' 7"); plain numbers over 100 are cm."""
    text = str(value).strip()
    m = re.fullmatch(r"(\d)\s*'\s*(\d{1,2}(?:\.\d+)?)?\s*\"?", text)
    if m:
        return round((int(m.group(1)) * 12 + float(m.group(2) or 0)) * 2.54, 1)
    try:
        number = float(text)
    except ValueError:
        return float("nan")
    return number if number > 100 else round(number * 2.54, 1)


def weight_kg(ounces: object) -> float:
    try:
        return round(float(str(ounces)) * 0.028349523125, 2)
    except ValueError:
        return float("nan")


def _gender(sex: object) -> str:
    return {"male": "M", "female": "F"}.get(str(sex).strip().lower(), "U")


def _fmt(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, format="mixed", errors="coerce").dt.strftime(OUT_TIME)


def _shard(log_id: pd.Series) -> pd.Series:
    return log_id.map(lambda v: zlib.crc32(str(v).encode()) % N_SHARDS)


class _State:
    """The importer's resume marker in the output folder."""

    def __init__(self, out: Path, sources: dict[str, Any]):
        self.path = out / MARKER
        self.data: dict[str, Any] = {"made_by": "MOVER Suite", "importer_version": IMPORTER_VERSION,
                                     "sources": sources, "parts_done": [], "finished": False}
        if self.path.is_file():
            old = json.loads(self.path.read_text(encoding="utf-8"))
            if old.get("sources") == sources and old.get("importer_version") == IMPORTER_VERSION:
                self.data = old

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)


def _pass1_part(part: Path, work: Path, name_cases: dict[str, set[str]], chunk_rows: int,
                progress: Progress, label: str = "", should_stop: Callable[[], bool] | None = None) -> int | None:
    """Shard one part's mapped intra-op rows; count intra-op names per case. Returns rows kept, or None when told
    to stop (checked every chunk, so stopping takes seconds; the unfinished part is read again on resume)."""
    target = work / part.stem
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    kept = 0
    reader = pd.read_csv(part, usecols=lambda c: c in FLOW_COLS, dtype=str, keep_default_na=False,
                         chunksize=chunk_rows, encoding="utf-8-sig", encoding_errors="replace", on_bad_lines="skip")
    for chunk in reader:
        if should_stop and should_stop():
            reader.close()
            return None
        intra = chunk[chunk["RECORD_TYPE"].str.strip().str.upper() == "INTRA-OP"]
        if intra.empty:
            continue
        names = intra["FLO_DISPLAY_NAME"].map(normalise_name)
        for name, ids in intra.groupby(names)["LOG_ID"]:
            name_cases[str(name)].update(ids.unique())
        mapped = intra[names.isin(EPIC_FLOWSHEET_MAP.keys())]
        if mapped.empty:
            continue
        mapped = mapped.assign(name=names[mapped.index])[["LOG_ID", "name", "RECORDED_TIME", "MEAS_VALUE"]]
        for k, rows in mapped.groupby(_shard(mapped["LOG_ID"])):
            f = target / f"shard_{int(k):03d}.csv"
            rows.to_csv(f, mode="a", header=not f.exists(), index=False)
        kept += len(mapped)
    progress(f"{label or 'Flowsheet part'}: {kept:,} intra-op readings kept")
    return kept


def _wide_minutes(long: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-minute SIS ventilator and vital-sign rows from one shard of mapped readings (last reading wins)."""
    long = long.copy()
    long["ts"] = pd.to_datetime(long["RECORDED_TIME"], format="mixed", errors="coerce")
    long = long.dropna(subset=["ts"]).sort_values("ts", kind="stable")
    long["t"] = long["ts"].dt.floor("min")
    target = long["name"].map(lambda n: EPIC_FLOWSHEET_MAP[n][1])
    nibp = target == "NIBP"
    parts = []
    if nibp.any():
        bp = long.loc[nibp, "MEAS_VALUE"].str.extract(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)")
        for i, col in ((0, "nSBP"), (1, "nDBP")):
            parts.append(pd.DataFrame({"PID": long.loc[nibp, "LOG_ID"], "t": long.loc[nibp, "t"],
                                       "col": col, "value": pd.to_numeric(bp[i], errors="coerce")}))
    rest = long[~nibp]
    parts.append(pd.DataFrame({"PID": rest["LOG_ID"], "t": rest["t"], "col": target[~nibp],
                               "value": pd.to_numeric(rest["MEAS_VALUE"], errors="coerce")}))
    values = pd.concat(parts, ignore_index=True).dropna(subset=["value"])
    if values.empty:
        return pd.DataFrame(columns=VENT_COLS), pd.DataFrame(columns=VITALS_COLS)
    # Stable sort keeps the recorded order inside a minute, so the last reading is the one kept.
    wide = values.groupby(["PID", "t", "col"], sort=False)["value"].last().unstack("col")
    wide = wide.reset_index().sort_values(["PID", "t"])
    wide["Obs_time"] = wide["t"].dt.strftime(OUT_TIME)
    for col in ("RR_measured", "RR_set"):
        if col not in wide:
            wide[col] = np.nan
    wide["RR"] = wide["RR_measured"].where(wide["RR_measured"].notna(), wide["RR_set"])
    # The agent is the one with the highest end-tidal reading that minute, else the highest inspired one.
    et_map = {str(c).split(":")[1]: c for c in wide.columns if str(c).startswith("Agent_Et:")}
    fi_map = {str(c).split(":")[1]: c for c in wide.columns if str(c).startswith("Agent_Fi:")}

    def strongest(cols: dict[str, Any]) -> pd.Series:
        if not cols:
            return pd.Series(np.nan, index=wide.index, dtype=object)
        block = wide[list(cols.values())]
        letters = block.fillna(-np.inf).idxmax(axis=1).map({c: a for a, c in cols.items()})
        return letters.where(block.notna().any(axis=1))

    agent = strongest(et_map)
    agent = agent.where(agent.notna(), strongest(fi_map))
    wide["Agent"] = agent
    for col, cols in (("Agent_Et", et_map), ("Agent_Fi", fi_map)):
        wide[col] = [wide.at[i, cols[a]] if isinstance(a, str) and a in cols else np.nan
                     for i, a in zip(wide.index, agent, strict=True)]
    vent_cols = [c for c in VENT_COLS if c not in ("PID", "Obs_time", "Agent", "Agent_Fi", "Agent_Et")]
    vit_cols = [c for c in VITALS_COLS if c not in ("PID", "Obs_time")]
    vent = wide.reindex(columns=VENT_COLS)
    vent = vent[vent[["Agent_Et", "Agent_Fi", *vent_cols]].notna().any(axis=1)]
    vitals = wide.reindex(columns=VITALS_COLS)
    vitals = vitals[vitals[vit_cols].notna().any(axis=1)]
    return vent, vitals


def _names_report(name_cases: dict[str, set[str]], min_cell: int) -> dict[str, Any]:
    listed = {n: len(ids) for n, ids in sorted(name_cases.items(), key=lambda kv: -len(kv[1])) if len(ids) >= min_cell}
    return {
        "min_cell": min_cell,
        "names": [{"name": n, "cases": c, "mapped": n in EPIC_FLOWSHEET_MAP} for n, c in listed.items()],
        "pooled_names": len(name_cases) - len(listed),
    }


def _info(emr: Path, keep: set[str], events: pd.DataFrame) -> pd.DataFrame:
    df = pd.read_csv(emr / EPIC_INFO, dtype=str, keep_default_na=False, encoding="utf-8-sig", encoding_errors="replace")
    df = df[df["LOG_ID"].isin(keep)].drop_duplicates("LOG_ID")
    incision = events[events["Event_name"].map(lambda n: bool(INCISION.search(str(n))))]
    first_cut = incision.groupby("PID")["Event_time"].min()
    surgery_start = df["LOG_ID"].map(first_cut)
    start = _fmt(df["AN_START_DATETIME"])
    return pd.DataFrame({
        "PID": df["LOG_ID"],
        "Age": pd.to_numeric(df.get("BIRTH_DATE"), errors="coerce"),
        "Ht": df.get("HEIGHT", pd.Series("", index=df.index)).map(height_cm),
        "Wt": df.get("WEIGHT", pd.Series("", index=df.index)).map(weight_kg),
        "Gender": df.get("SEX", pd.Series("", index=df.index)).map(_gender),
        "OR_start": _fmt(df["IN_OR_DTTM"]),
        "OR_end": _fmt(df["OUT_OR_DTTM"]),
        "Surgery_start": surgery_start.where(surgery_start.notna(), start),
        "Surgery_end": _fmt(df["AN_STOP_DATETIME"]),
        "Procedure": df.get("PRIMARY_PROCEDURE_NM", pd.Series("", index=df.index)),
    })[INFO_COLS]


def _events(emr: Path, keep: set[str]) -> pd.DataFrame:
    df = pd.read_csv(emr / EPIC_EVENTS, dtype=str, keep_default_na=False, encoding="utf-8-sig", encoding_errors="replace",
                     usecols=["LOG_ID", "EVENT_DISPLAY_NAME", "EVENT_TIME"])
    df = df[df["LOG_ID"].isin(keep)]
    return pd.DataFrame({"PID": df["LOG_ID"], "Event_time": _fmt(df["EVENT_TIME"]),
                         "Event_name": df["EVENT_DISPLAY_NAME"].str.strip()})


def _medications(emr: Path, keep: set[str], chunk_rows: int) -> pd.DataFrame:
    path = emr / EPIC_MEDICATIONS
    if not path.is_file():
        return pd.DataFrame(columns=MED_COLS)
    want = ["LOG_ID", "MEDICATION_NM", "ADMIN_SIG", "DOSE_UNIT_NM", "MED_ACTION_TIME", "MAR_ACTION_NM", "RECORD_TYPE"]
    out = []
    for chunk in pd.read_csv(path, dtype=str, keep_default_na=False, usecols=lambda c: c in want,
                             chunksize=chunk_rows, encoding="utf-8-sig", encoding_errors="replace"):
        rows = chunk[chunk["LOG_ID"].isin(keep) & (chunk["RECORD_TYPE"].str.strip().str.upper() == "INTRA-OP")
                     & chunk["MAR_ACTION_NM"].str.strip().str.match(GIVEN_ACTIONS)]
        t = _fmt(rows["MED_ACTION_TIME"])
        out.append(pd.DataFrame({"PID": rows["LOG_ID"], "Drug_name": rows["MEDICATION_NM"].str.strip(),
                                 "Dose": pd.to_numeric(rows["ADMIN_SIG"], errors="coerce"),
                                 "Drug_units": rows["DOSE_UNIT_NM"].str.strip(), "Start_time": t, "End_time": t}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=MED_COLS)


def _wave_ids(epic_root: Path, keep: set[str]) -> pd.DataFrame | None:
    found = _find_file(epic_root, ID_MAP)
    if found is None:
        return None
    ids = pd.read_csv(found, dtype=str, keep_default_na=False, usecols=["LOG_ID", "PAT_ID"], encoding="utf-8-sig",
                      encoding_errors="replace")
    ids = ids[ids["LOG_ID"].isin(keep)].drop_duplicates("LOG_ID")
    return pd.DataFrame({"PID": ids["LOG_ID"], "Wave_ID": ids["PAT_ID"].str.strip().str.lower()})


def _write(df: pd.DataFrame, path: Path, *, private: bool = False) -> None:
    """Write through a temporary file; a private one is created owner-only, so it is never readable by others."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        if private:
            tmp.unlink(missing_ok=True)  # a new file, so it is owner-only from the first byte and never a symlink
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                fh = os.fdopen(fd, "w", encoding="utf-8", newline="")
            except BaseException:
                os.close(fd)
                raise
            with fh:
                df.to_csv(fh, index=False)
        else:
            df.to_csv(tmp, index=False)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def import_epic(epic_root: Path | str, out: Path | str, *, chunk_rows: int = CHUNK_ROWS, min_cell: int = MIN_CELL,
                progress: Progress | None = None, should_stop: Callable[[], bool] | None = None,
                on_step: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """Convert ``epic_root`` into a SIS-layout EMR folder at ``out``; resumable. Returns the marker data."""
    say = progress or (lambda _msg: None)
    root = Path(epic_root).expanduser().resolve()
    dest = Path(out).expanduser().resolve()
    if dest == root or dest.is_relative_to(root) or root.is_relative_to(dest):
        raise EpicImportError("Choose an output folder outside the Epic folder; the Epic data stays read-only.")
    emr = find_epic_emr(root)
    if emr is None:
        raise EpicImportError(f"No Epic records found under {root} (expected {EPIC_INFO} with LOG_ID and MRN "
                              f"beside '{EPIC_EVENTS}').")
    parts = find_flowsheets(root)
    if not parts:
        raise EpicImportError(f"No Epic flowsheets (flowsheet_part*.csv) found under {root}.")
    if dest.exists() and not (dest / MARKER).is_file() and any(p.name not in _OWN_NAMES for p in dest.iterdir()):
        raise EpicImportError("Choose an empty folder for the converted records; this one already holds files.")
    dest.mkdir(parents=True, exist_ok=True)
    sources: dict[str, Any] = {"epic_root": str(root), "emr": str(emr.relative_to(root)),
               "flowsheets": [str(p.relative_to(root)) for p in parts]}
    state = _State(dest, sources)
    if state.data["finished"]:
        say("already converted")
        return state.data
    work = dest / ".work"
    names_path = work / "names.json"
    if state.data["parts_done"] and not names_path.is_file():
        state.data["parts_done"] = []  # the counts behind the finished parts are gone, so start over
    if not state.data["parts_done"] and work.exists():
        shutil.rmtree(work)  # left by an older or different run; its shards must not mix into this one
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(work, 0o700)  # it holds case ids per name and raw readings until the import ends
    name_cases: dict[str, set[str]] = defaultdict(set)
    if names_path.is_file():
        for n, ids in json.loads(names_path.read_text(encoding="utf-8")).items():
            name_cases[n] = set(ids)
    started = time.time()
    steps = len(parts) + 1  # one step per flowsheet part, one for building the tables
    step = on_step or (lambda _done, _total: None)
    step(len(state.data["parts_done"]), steps)
    for number, part in enumerate(parts, start=1):
        rel = str(part.relative_to(root))
        if rel in state.data["parts_done"]:
            if (work / part.stem).is_dir():
                continue
            state.data["parts_done"].remove(rel)  # its shards are gone: read the part again
        if should_stop and should_stop():
            say("stopped; run again to resume")
            return state.data
        if _pass1_part(part, work, name_cases, chunk_rows, say, f"Flowsheet part {number} of {len(parts)}",
                       should_stop) is None:
            say("stopped; run again to resume")
            return state.data
        tmp_names = names_path.with_suffix(".json.tmp")
        tmp_names.write_text(json.dumps({n: sorted(ids) for n, ids in name_cases.items()}), encoding="utf-8")
        tmp_names.replace(names_path)
        state.data["parts_done"].append(rel)
        state.save()
        step(len(state.data["parts_done"]), steps)
    if should_stop and should_stop():
        say("stopped; run again to resume")
        return state.data
    say("pass 1 done; building per-minute rows")
    vent_path, vit_path = dest / "patient_ventilator.csv", dest / "patient_vitals.csv"
    tmp_vent, tmp_vit = vent_path.with_suffix(".csv.tmp"), vit_path.with_suffix(".csv.tmp")
    pd.DataFrame(columns=VENT_COLS).to_csv(tmp_vent, index=False)
    pd.DataFrame(columns=VITALS_COLS).to_csv(tmp_vit, index=False)
    cases: set[str] = set()
    for k in range(N_SHARDS):
        if should_stop and should_stop():
            tmp_vent.unlink(missing_ok=True)
            tmp_vit.unlink(missing_ok=True)
            say("stopped; run again to resume")
            return state.data
        # Part order, not name order: flowsheet_part10 must come after flowsheet_part2.
        files = [work / p.stem / f"shard_{k:03d}.csv" for p in parts if (work / p.stem / f"shard_{k:03d}.csv").is_file()]
        if not files:
            continue
        long = pd.concat((pd.read_csv(f, dtype=str, keep_default_na=False) for f in files), ignore_index=True)
        vent, vitals = _wide_minutes(long)
        vent.to_csv(tmp_vent, mode="a", header=False, index=False)
        vitals.to_csv(tmp_vit, mode="a", header=False, index=False)
        cases.update(vent["PID"].unique())
        cases.update(vitals["PID"].unique())
    tmp_vent.replace(vent_path)
    tmp_vit.replace(vit_path)
    say(f"{len(cases)} cases with intra-op readings")
    events = _events(emr, cases)
    _write(_info(emr, cases, events), dest / "patient_information.csv")
    _write(events, dest / "patient_procedure_events.csv")
    _write(_medications(emr, cases, chunk_rows), dest / "patient_medication.csv")
    waves = _wave_ids(root, cases)
    if waves is not None:
        _write(waves, dest / WAVE_IDS, private=True)
    (dest / NAMES_REPORT).write_text(json.dumps(_names_report(name_cases, min_cell), indent=2), encoding="utf-8")
    # Mark the import finished before removing the work folder: a crash in between must not leave a marker that
    # lists every part done with no shards left, which a rerun would turn into empty tables.
    state.data.update(finished=True, cases=len(cases), seconds=round(time.time() - started, 1),
                      wave_ids=waves is not None)
    state.save()
    shutil.rmtree(work, ignore_errors=True)
    step(steps, steps)
    say("done")
    return state.data


def read_import_marker(folder: Path | str) -> dict[str, Any] | None:
    """The marker of a finished import this app made, or None."""
    try:
        data = json.loads((Path(folder) / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("made_by") == "MOVER Suite" and data.get("finished") else None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Convert a MOVER Epic folder into the SIS layout, once")
    parser.add_argument("--epic-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--chunk-rows", type=int, default=CHUNK_ROWS)
    args = parser.parse_args(argv)
    result = import_epic(args.epic_root, args.out, chunk_rows=args.chunk_rows,
                         progress=lambda m: print(f"[epic-import] {m}", flush=True))
    print(f"[epic-import] cases: {result.get('cases')}, finished: {result.get('finished')}")


if __name__ == "__main__":
    main()
