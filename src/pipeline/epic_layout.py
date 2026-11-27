"""
Summary-only layout report of a MOVER Epic folder, so a loader can be written without reading rows.

For every CSV under the root it reports the columns and, from a sample of rows read from the
start of the file, what kind of values each column holds:

- ``id``: patient, case or record keys (by column name, or values that look like keys). Only the
  number of distinct values is reported.
- ``number``: share empty and the 5th, 50th and 95th percentiles, when at least ``min_cell``
  patients have a value.
- ``datetime``: the shape of the values (for example ``YYYY-MM-DD HH:MM:SS``), never a value.
- ``label``: a short repeated name such as an event or measurement name. Names recorded for at
  least ``min_cell`` patients are listed with their patient count; the rest are pooled. A file
  with no patient key column lists no names at all.
- ``text``: anything else. Only the distinct count and the mean length are reported.

For waveform roots it reports the folder shape (names replaced by their shape, such as
``<2 hex>/<16 hex>``), file counts, and the XML element, attribute and signal-group names of a
few files, never sample values.

No row, key, date or free-text value is written. Check your MOVER data use agreement before
sharing even aggregate output.

    PYTHONPATH=. python -m src.pipeline.epic_layout --root /var/mnt/games/mover-epic --out layout.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import numpy as np
import pandas as pd

SAMPLE_ROWS = 200_000
CHUNK_ROWS = 50_000
MAX_LABEL_DISTINCT = 500
MAX_LABEL_LENGTH = 80
MAX_NAMES = 200
XML_FILES = 3

# A column whose name says it is a key is reported as an id whatever its values look like.
_ID_NAME = re.compile(r"(^|_)(pat|patient|mrn|pid|log|csn|enc|encounter|visit|hsp|acct|account|order|id)(_|$)|_id$|^id_", re.I)
# The patient key, in order of preference, used to count patients per name.
# People's names are never listed, even when many patients share one.
_PERSON_NAME = re.compile(r"name|provider|surgeon|physician|anesthesiologist|staff|author|user|employee|nurse|resident", re.I)
_LABEL_HINT = re.compile(r"(event|flo|meas|measure|type|unit|desc|status|category|class|route|code)", re.I)
_PATIENT_KEYS = ("PAT_ID", "MRN", "PID", "PATIENT_ID", "LOG_ID", "PAT_ENC_CSN_ID", "CSN")
_KEYISH = re.compile(r"^(?:[0-9A-Fa-f]{8,}|\d{6,}|[A-Za-z]{0,3}\d{6,}|[0-9a-f-]{32,36})$")
_DATE_SHAPES = [
    (re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?$"), "YYYY-MM-DD HH:MM:SS"),
    (re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}$"), "YYYY-MM-DD HH:MM"),
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "YYYY-MM-DD"),
    (re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4} \d{1,2}:\d{2}(:\d{2})?( ?[AP]M)?$", re.I), "M/D/Y H:MM"),
    (re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$"), "M/D/Y"),
    (re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{2,4}( \d{1,2}:\d{2}(:\d{2})?)?$"), "D-Mon-Y"),
]


def _count(n: int, min_cell: int) -> int | str:
    return f"<{min_cell}" if 0 < n < min_cell else int(n)


def _date_shape(values: pd.Series) -> str | None:
    """The shape most values share, when at least 90% of them match one date shape."""
    sample = values.head(2000)
    for pattern, shape in _DATE_SHAPES:
        if sample.str.match(pattern).mean() >= 0.9:
            return shape
    return None


def _patient_key(columns: list[str]) -> str | None:
    upper = {c.upper(): c for c in columns}
    for key in _PATIENT_KEYS:
        if key in upper:
            return upper[key]
    return None


def _read_sample(path: Path, sample_rows: int) -> pd.DataFrame:
    parts = []
    reader = pd.read_csv(path, dtype=str, keep_default_na=False, chunksize=CHUNK_ROWS,
                         on_bad_lines="skip", encoding_errors="replace", low_memory=True)
    seen = 0
    for chunk in reader:
        parts.append(chunk)
        seen += len(chunk)
        if seen >= sample_rows:
            break
    reader.close()
    return pd.concat(parts, ignore_index=True).head(sample_rows) if parts else pd.DataFrame()


def _column_report(name: str, values: pd.Series, patients: pd.Series | None, min_cell: int) -> dict[str, Any]:
    present = values[values.str.strip() != ""]
    out: dict[str, Any] = {"name": name, "empty_pct": round(100 * (1 - len(present) / max(len(values), 1)), 2)}
    distinct = present.nunique()
    out["distinct"] = int(distinct)
    if len(present) == 0:
        out["kind"] = "empty"
        return out
    keyish = present.head(2000).str.match(_KEYISH).mean() >= 0.9
    if _ID_NAME.search(name) or (keyish and distinct > MAX_LABEL_DISTINCT) or (
            _PERSON_NAME.search(name) and not _LABEL_HINT.search(name)):
        out["kind"] = "id"
        return out
    shape = _date_shape(present)
    if shape:
        out["kind"] = "datetime"
        out["shape"] = shape
        return out
    numbers = pd.to_numeric(present, errors="coerce")
    if numbers.notna().mean() >= 0.98:
        out["kind"] = "number"
        n_pat = int(patients[numbers.notna().reindex(patients.index, fill_value=False)].nunique()) if patients is not None else 0
        if n_pat >= min_cell:
            finite = numbers.dropna().to_numpy(dtype=float)
            out["p5_p50_p95"] = [round(float(v), 4) for v in np.percentile(finite, [5, 50, 95])]
        out["patients"] = _count(n_pat, min_cell) if patients is not None else None
        return out
    lengths = present.str.len()
    # A label repeats: free text that happens to be short is mostly one-off values.
    if distinct <= MAX_LABEL_DISTINCT and lengths.max() <= MAX_LABEL_LENGTH and distinct <= len(present) / 2:
        out["kind"] = "label"
        if patients is None:
            out["names"] = "not listed: no patient key column"
            return out
        per_name = patients[present.index].groupby(present).nunique().sort_values(ascending=False)
        listed = {
            str(label): int(n) for label, n in per_name.items()
            if n >= min_cell and not _KEYISH.match(str(label)) and _date_shape(pd.Series([str(label)])) is None
        }
        out["names"] = dict(list(listed.items())[:MAX_NAMES])
        out["pooled_names"] = int(len(per_name) - len(out["names"]))
        return out
    out["kind"] = "text"
    out["mean_length"] = round(float(lengths.mean()), 1)
    return out


def profile_csv(path: Path, *, sample_rows: int = SAMPLE_ROWS, min_cell: int = 11) -> dict[str, Any]:
    """Layout of one CSV from the first ``sample_rows`` rows; no value is copied out."""
    df = _read_sample(path, sample_rows)
    columns = [str(c) for c in df.columns]
    key = _patient_key(columns)
    patients = df[key] if key else None
    size = path.stat().st_size
    with path.open("rb") as fh:
        header_bytes = len(fh.readline())
    return {
        "file": path.name,
        "bytes": size,
        "sampled_rows": int(len(df)),
        "estimated_rows": int(len(df) and (size - header_bytes) / max(_mean_row_bytes(path, header_bytes, len(df)), 1)),
        "patient_key": key,
        "sampled_patients": _count(int(patients.nunique()), min_cell) if patients is not None else None,
        "columns": [_column_report(c, df[c], patients, min_cell) for c in columns],
    }


def _mean_row_bytes(path: Path, header_bytes: int, n_rows: int) -> float:
    """Average bytes per data row over the sampled rows, to estimate the file's row count."""
    with path.open("rb") as fh:
        fh.readline()
        total = 0
        for i, line in enumerate(fh):
            if i >= n_rows:
                break
            total += len(line)
    return total / max(n_rows, 1)


def _name_shape(name: str) -> str:
    if re.fullmatch(r"[0-9a-fA-F]+", name) and not name.isdigit():
        return f"<{len(name)} hex>"
    if name.isdigit():
        return f"<{len(name)} digits>"
    if re.fullmatch(r"[0-9a-fA-F]{2}", name):
        return "<2 hex>"
    return name if not re.search(r"\d{4,}", name) else re.sub(r"\d", "9", name)


def profile_waves(root: Path, *, xml_files: int = XML_FILES) -> dict[str, Any]:
    """Folder shape, file counts and XML element names of a waveform root; no sample values."""
    shapes: Counter[str] = Counter()
    extensions: Counter[str] = Counter()
    per_dir: list[int] = []
    empty = 0
    xml_paths: list[Path] = []
    for dirpath, _dirs, files in os.walk(root):
        if not files:
            continue
        rel = Path(dirpath).relative_to(root)
        shapes["/".join(_name_shape(p) for p in rel.parts) or "."] += 1
        per_dir.append(len(files))
        for f in files:
            p = Path(dirpath) / f
            extensions[p.suffix.lower() or "(none)"] += 1
            try:
                if p.stat().st_size == 0:
                    empty += 1
                elif p.suffix.lower() == ".xml" and len(xml_paths) < xml_files:
                    xml_paths.append(p)
            except OSError:
                continue
    tags: Counter[str] = Counter()
    attributes: Counter[str] = Counter()
    groups: Counter[str] = Counter()
    measures: Counter[str] = Counter()
    for p in xml_paths:
        seen_tags: set[str] = set()
        seen_attr: set[str] = set()
        seen_groups: set[str] = set()
        seen_measures: set[str] = set()
        for _event, el in ET.iterparse(p, events=("start",)):
            seen_tags.add(el.tag)
            seen_attr.update(f"{el.tag}@{a}" for a in el.attrib)
            if el.tag == "mg" and "name" in el.attrib:
                seen_groups.add(el.attrib["name"])
            if el.tag == "m" and "name" in el.attrib:
                seen_measures.add(el.attrib["name"])
        tags.update(seen_tags)
        attributes.update(seen_attr)
        groups.update(seen_groups)
        measures.update(seen_measures)
    counts = np.array(per_dir) if per_dir else np.array([0])
    return {
        "root": root.name,
        "folder_shapes": dict(shapes.most_common(20)),
        "files": int(sum(extensions.values())),
        "empty_files": empty,
        "extensions": dict(extensions.most_common(10)),
        "files_per_folder_p5_p50_p95": [int(v) for v in np.percentile(counts, [5, 50, 95])],
        "xml_files_read": len(xml_paths),
        "xml_elements": dict(tags.most_common()),
        "xml_attributes": dict(attributes.most_common()),
        "signal_groups": dict(groups.most_common()),
        "measure_names": dict(measures.most_common()),
    }


def profile_root(root: Path, *, wave_roots: list[Path] | None = None, sample_rows: int = SAMPLE_ROWS,
                 min_cell: int = 11) -> dict[str, Any]:
    csvs = []
    for dirpath, dirs, files in os.walk(root):
        # Waveform trees hold hundreds of thousands of files and no tables.
        dirs[:] = sorted(d for d in dirs if d != "Waveforms" and not d.startswith("."))
        csvs += [Path(dirpath) / f for f in sorted(files) if f.lower().endswith(".csv")]
    return {
        "min_cell": min_cell,
        "sample_rows": sample_rows,
        "tables": [{"folder": str(p.parent.relative_to(root)), **profile_csv(p, sample_rows=sample_rows, min_cell=min_cell)}
                   for p in csvs],
        "waves": [profile_waves(w) for w in (wave_roots or [])],
    }


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MOVER Epic layout (summary only)", "",
        f"No row, key, date or free-text value is shown. Patient counts under {report['min_cell']} show as "
        f"<{report['min_cell']}; names recorded for fewer patients are pooled. Each table is profiled from its "
        f"first {report['sample_rows']} rows.", "",
    ]
    for t in report["tables"]:
        lines += [
            f"## {t['folder']}/{t['file']}", "",
            f"{t['bytes'] / 1e9:.2f} GB, about {t['estimated_rows']:,} rows; patient key: {t['patient_key'] or 'none found'}; "
            f"patients in the sample: {t['sampled_patients']}.", "",
            "| Column | Kind | Empty % | Distinct | Detail |", "|---|---|---|---|---|",
        ]
        for c in t["columns"]:
            detail = ""
            if c.get("kind") == "datetime":
                detail = c["shape"]
            elif c.get("kind") == "number":
                detail = f"p5/p50/p95 {c['p5_p50_p95']}" if "p5_p50_p95" in c else "percentiles suppressed"
            elif c.get("kind") == "text":
                detail = f"mean length {c['mean_length']}"
            elif c.get("kind") == "label":
                detail = (c["names"] if isinstance(c["names"], str)
                          else f"{len(c['names'])} names listed below, {c['pooled_names']} pooled")
            lines.append(f"| {_cell(c['name'])} | {c.get('kind')} | {c['empty_pct']} | {c['distinct']} | {_cell(detail)} |")
        for c in t["columns"]:
            if c.get("kind") == "label" and isinstance(c.get("names"), dict) and c["names"]:
                lines += ["", f"### {t['file']}: {c['name']} (patients per name)", "", "| Name | Patients |", "|---|---|"]
                lines += [f"| {_cell(k)} | {v} |" for k, v in c["names"].items()]
        lines.append("")
    for w in report["waves"]:
        lines += [f"## Waveforms: {w['root']}", "",
                  f"{w['files']:,} files ({w['empty_files']} empty); files per folder p5/p50/p95 "
                  f"{w['files_per_folder_p5_p50_p95']}; extensions {w['extensions']}.", "",
                  "Folder shapes:", ""]
        lines += [f"- `{k}`: {v}" for k, v in w["folder_shapes"].items()]
        lines += ["", f"From {w['xml_files_read']} XML files: elements {w['xml_elements']}; "
                  f"attributes {w['xml_attributes']}; signal groups {w['signal_groups']}; "
                  f"measure names {w['measure_names']}.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Summary-only layout of a MOVER Epic folder")
    parser.add_argument("--root", type=Path, required=True, help="folder holding the Epic CSVs (searched recursively)")
    parser.add_argument("--wave-root", type=Path, action="append", default=[], help="a waveform root; repeatable")
    parser.add_argument("--sample-rows", type=int, default=SAMPLE_ROWS)
    parser.add_argument("--min-cell", type=int, default=11)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.min_cell < 1:
        parser.error("--min-cell must be at least 1")
    report = profile_root(args.root, wave_roots=args.wave_root, sample_rows=args.sample_rows, min_cell=args.min_cell)
    args.out.write_text(render_markdown(report), encoding="utf-8")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[epic-layout] wrote {args.out}")


if __name__ == "__main__":
    main()
