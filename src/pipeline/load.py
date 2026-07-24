"""Load raw SIS EMR CSV tables."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

# MySQL-style null token used throughout MOVER SIS dumps
NA_VALUES = ["\\N", "\\n", "NA", "NaN", "nan", ""]

AGENT_MAP = {
    "S": "sevoflurane",
    "D": "desflurane",
    "I": "isoflurane",
    "N": "none",
}

# Canonical renames (fix 0/O typos and unify casing)
VENT_RENAME = {
    "ETC02": "ETCO2",
    "FIC02": "FICO2",
    "FI02": "FIO2",
    "ET02": "ETO2",
    "FIN20": "FIN2O",
    "ETN20": "ETN2O",
}

VITALS_RENAME = {
    "SP02": "SPO2",
}


def _default_emr_dir(data_root: Path | str | None = None) -> Path:
    if data_root is None:
        # repo_root/data/raw/EMR
        here = Path(__file__).resolve()
        return here.parents[2] / "data" / "raw" / "EMR"
    root = Path(data_root)
    if (root / "patient_information.csv").exists():
        return root
    if (root / "EMR" / "patient_information.csv").exists():
        return root / "EMR"
    if (root / "raw" / "EMR" / "patient_information.csv").exists():
        return root / "raw" / "EMR"
    return root


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    """Read a SIS CSV with consistent NA handling. Fails fast if path missing."""
    path = Path(path)
    if not path.is_file():
        from src.guardrails.exceptions import PipelineError

        raise PipelineError(f"Required CSV not found: {path}")
    defaults = dict(
        na_values=NA_VALUES,
        keep_default_na=True,
        low_memory=False,
    )
    defaults.update(kwargs)
    try:
        return pd.read_csv(path, **defaults)
    except pd.errors.EmptyDataError as e:
        from src.guardrails.exceptions import PipelineError

        raise PipelineError(f"CSV is empty or unreadable: {path}") from e
    except Exception as e:
        from src.guardrails.exceptions import PipelineError

        raise PipelineError(f"Failed to read CSV {path}: {e}") from e


def load_case_info(emr_dir: Path | str | None = None) -> pd.DataFrame:
    path = _default_emr_dir(emr_dir) / "patient_information.csv"
    df = read_csv(path)
    df.columns = [c.strip().strip('"') for c in df.columns]
    if "PID" not in df.columns:
        from src.guardrails.exceptions import DataValidationError

        raise DataValidationError(
            f"patient_information missing PID column; got {list(df.columns)}"
        )
    return df


def load_ventilator(
    emr_dir: Path | str | None = None,
    pids: Iterable[str] | None = None,
    nrows: int | None = None,
) -> pd.DataFrame:
    path = _default_emr_dir(emr_dir) / "patient_ventilator.csv"
    df = read_csv(path, nrows=nrows)
    df.columns = [c.strip().strip('"') for c in df.columns]
    df = df.rename(columns=VENT_RENAME)
    if "PID" not in df.columns or "Obs_time" not in df.columns:
        from src.guardrails.exceptions import DataValidationError

        raise DataValidationError(
            f"ventilator missing PID/Obs_time; got {list(df.columns)}"
        )
    if pids is not None:
        pid_set = set(map(str, pids))
        df = df[df["PID"].astype(str).isin(pid_set)].copy()
    return df


def load_vitals(
    emr_dir: Path | str | None = None,
    pids: Iterable[str] | None = None,
    nrows: int | None = None,
) -> pd.DataFrame:
    path = _default_emr_dir(emr_dir) / "patient_vitals.csv"
    df = read_csv(path, nrows=nrows)
    df.columns = [c.strip().strip('"') for c in df.columns]
    df = df.rename(columns=VITALS_RENAME)
    if "PID" not in df.columns or "Obs_time" not in df.columns:
        from src.guardrails.exceptions import DataValidationError

        raise DataValidationError(
            f"vitals missing PID/Obs_time; got {list(df.columns)}"
        )
    if pids is not None:
        pid_set = set(map(str, pids))
        df = df[df["PID"].astype(str).isin(pid_set)].copy()
    return df


def load_procedure_events(
    emr_dir: Path | str | None = None,
    pids: Iterable[str] | None = None,
) -> pd.DataFrame:
    path = _default_emr_dir(emr_dir) / "patient_procedure_events.csv"
    if not path.exists():
        return pd.DataFrame(columns=["PID", "Event_time", "Event_name"])
    df = read_csv(path)
    df.columns = [c.strip().strip('"') for c in df.columns]
    if pids is not None:
        pid_set = set(map(str, pids))
        df = df[df["PID"].astype(str).isin(pid_set)].copy()
    return df


def load_observations(
    emr_dir: Path | str | None = None,
    pids: Iterable[str] | None = None,
    nrows: int | None = None,
) -> pd.DataFrame:
    path = _default_emr_dir(emr_dir) / "patient_observations.csv"
    if not path.exists():
        return pd.DataFrame()
    df = read_csv(path, nrows=nrows)
    df.columns = [c.strip() for c in df.columns]
    if pids is not None:
        pid_set = set(map(str, pids))
        df = df[df["PID"].astype(str).isin(pid_set)].copy()
    return df


def sample_pids_with_ventilator(
    emr_dir: Path | str | None = None,
    n: int = 50,
    min_vent_rows: int = 30,
    seed: int = 42,
    vent_nrows_scan: int | None = 400_000,
) -> list[str]:
    """
    Pick PIDs that have substantial ventilator data.

    Scans a prefix of the ventilator file (which may be export-truncated)
    and returns up to n PIDs with at least min_vent_rows samples.
    """
    vent = load_ventilator(emr_dir, nrows=vent_nrows_scan)
    counts = vent.groupby("PID").size()
    eligible = counts[counts >= min_vent_rows].index.astype(str).tolist()
    if not eligible:
        return []
    rs = pd.Series(eligible).sample(n=min(n, len(eligible)), random_state=seed)
    return rs.tolist()
