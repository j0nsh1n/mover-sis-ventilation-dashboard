"""
Decode MOVER SIS high-fidelity waveforms (Bernoulli / GE S5 ``cpcArchive`` XML).

Layout: one file per 30-minute window, holding a sequence of ``<cpc>`` blocks. Each
block carries a UTC ``datetime`` and, when the monitor was streaming, one ``<mg>``
measurement group per signal::

    <mg name="GE_ART">
      <m name="Wave">…base64…</m>   <m name="Points">180</m>
      <m name="PointsBytes">2</m>   <m name="Hz">180</m>
      <m name="Gain">…</m>          <m name="Offset">0</m>
    </mg>

``Wave`` is little-endian signed integers, ``PointsBytes`` wide, scaled to physical
units by ``value = sample * gain + offset``.

**Gain provenance.** The algorithm validated against the monitors (dataset provider
snippet, Feb 2023) hard-codes ``GE_ART`` → 0.25 and ``INVP1`` → 0.01 because the *v1*
XML gains were wrong for the pressure channels. The dataset README then shipped a v2
release (2024-05) whose stated reason was "some of the wave gains were off" — and in
``sis_wave_v2`` the XML now carries exactly those values. Preferring
:data:`GAIN_OVERRIDES` over the XML is therefore correct for v1 *and* a no-op for v2;
``test_v2_xml_gains_match_overrides`` fails loudly if a future release diverges.
The byte-pair signed decode is also checked in tests against that provider algorithm.

Signals seen in ``sis_wave_v2``: ``GE_ART``, ``INVP1`` (pressures), ``GE_ECG``/``ECG1``,
``AWP`` (airway pressure), ``FLOW``, ``CO2``, ``PLETH``, ``RESP``.

**A present channel is not a live channel.** Sampling real cases turns up three states,
and only the last is safe to label as a pressure:

======================  ==========================================================
State                   Signature in ``sis_wave_v2``
======================  ==========================================================
``idle``                flat near zero (min −4.5, max 0.0); the channel existed but
                        never transduced — this is the common case
``implausible``         saturated/disconnected: swings ±450 with a median near 0,
                        ~70% of samples outside physiological bounds
``active``              a real arterial line: min 44, max 136, median 77 mmHg
======================  ==========================================================

Use :func:`channel_state` (or :func:`looks_physiological_pressure` directly) before
plotting or summarising. :func:`looks_transducing` alone is not enough — a saturated
transducer moves plenty.

Blocks with ``<status>DATADOWN</status>`` carry no measurements and are skipped.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pandas as pd

from src.runtime_paths import wave_dir, waveform_case_dir

# Signals whose XML Gain cannot be trusted (see module docstring).
GAIN_OVERRIDES: dict[str, float] = {
    "GE_ART": 0.25,
    "INVP1": 0.01,
}

# Guard against a pathological file consuming the UI thread
MAX_FILES_PER_CASE = 64
MAX_SAMPLES_PER_SIGNAL = 8_000_000

# Raw codes the monitor emits for "no data". Payload samples are 12-bit
# (-2048..2047), so these are unreachable as measurements — an unused channel
# streams them continuously. Decoded to NaN rather than scaled into a value:
# left as-is, an idle AWP channel reports a median of -32767.
NODATA_SENTINELS = (-32768, -32767)


@dataclass
class WaveBlock:
    """One decoded ``<mg>`` group: samples in physical units, plus its time base."""

    signal: str
    hz: int
    start: pd.Timestamp | None
    values: np.ndarray

    @property
    def duration_s(self) -> float:
        return len(self.values) / self.hz if self.hz else 0.0


def _dtype_for(points_bytes: int) -> np.dtype:
    if points_bytes == 1:
        return np.dtype("i1")
    if points_bytes == 4:
        return np.dtype("<i4")
    return np.dtype("<i2")  # PointsBytes=2 is what SIS emits


def looks_transducing(values: np.ndarray | pd.Series, *, min_span: float = 10.0) -> bool:
    """
    True when a channel actually carried a signal rather than sitting idle.

    An unused monitor channel still emits samples — a flat trace near zero, but often
    with brief artifact excursions (handling, flush, disconnection), so peak-to-peak
    is easily fooled. The 5th-95th percentile span is not: an idle channel stays within
    a couple of units, while a pulsatile arterial line spans tens of mmHg between
    diastole and systole.
    """
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 2:
        return False
    lo, hi = np.percentile(arr, [5, 95])
    return float(hi - lo) >= float(min_span)


def looks_physiological_pressure(
    values: np.ndarray | pd.Series,
    *,
    bounds: tuple[float, float] = (-20.0, 300.0),
    median_range: tuple[float, float] = (20.0, 150.0),
    min_inside: float = 0.95,
) -> bool:
    """
    True when a pressure channel carries a believable blood-pressure trace.

    :func:`looks_transducing` only asks whether a channel moved, which a saturated
    or disconnected transducer does too — those swing roughly ±450 with a median
    near zero. A real arterial line keeps a median around 60-100 mmHg with nearly
    every sample inside physiological bounds, so require both.
    """
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 2:
        return False
    lo, hi = bounds
    inside = float(((arr >= lo) & (arr <= hi)).mean())
    if inside < min_inside:
        return False
    median = float(np.median(arr))
    return median_range[0] <= median <= median_range[1]


def channel_state(signal: str, values: np.ndarray | pd.Series) -> str:
    """
    Classify a decoded channel: ``nodata`` | ``idle`` | ``implausible`` | ``active``.

    ``nodata`` means every sample was a sentinel (the channel was never streaming);
    ``idle`` means it streamed a flat trace. Pressure channels additionally get the
    physiological test — everything else only has to show that it moved. Callers
    should refuse to plot anything but ``active`` under a clinical label.
    """
    arr = np.asarray(values, dtype=float)
    if arr.size == 0 or not np.isfinite(arr).any():
        return "nodata"
    if not looks_transducing(values):
        return "idle"
    if signal.upper() in GAIN_OVERRIDES and not looks_physiological_pressure(values):
        return "implausible"
    return "active"


def resolve_gain(signal: str, xml_gain: float | None) -> float:
    """Gain for *signal*, preferring the override table over the XML value."""
    if signal in GAIN_OVERRIDES:
        return GAIN_OVERRIDES[signal]
    if xml_gain is None:
        return 1.0
    return float(xml_gain)


def decode_samples(
    wave_b64: str,
    *,
    signal: str = "",
    gain: float | None = None,
    offset: float = 0.0,
    points_bytes: int = 2,
) -> np.ndarray:
    """
    Base64 payload → samples in physical units.

    Vectorised equivalent of the reference decoder's byte-pair loop: little-endian
    two's-complement integers, then ``sample * gain + offset``.
    """
    if not wave_b64:
        return np.empty(0, dtype=float)
    try:
        raw = base64.b64decode(wave_b64, validate=False)
    except (ValueError, TypeError):
        return np.empty(0, dtype=float)

    dt = _dtype_for(points_bytes)
    usable = len(raw) - (len(raw) % dt.itemsize)
    if usable <= 0:
        return np.empty(0, dtype=float)

    samples = np.frombuffer(raw[:usable], dtype=dt).astype(float)
    missing = np.isin(samples, NODATA_SENTINELS)
    values = samples * resolve_gain(signal, gain) + float(offset)
    if missing.any():
        values = values.copy()
        values[missing] = np.nan
    return values


def _decode_group(mg: ET.Element, block_start: pd.Timestamp | None) -> WaveBlock | None:
    signal = mg.get("name") or ""
    wave_b64 = ""
    xml_gain: float | None = None
    offset = 0.0
    hz = 0
    points_bytes = 2

    for m in mg.findall("m"):
        name = m.get("name")
        text = (m.text or "").strip()
        if not name or not text:
            continue
        try:
            if name == "Wave":
                wave_b64 = text
            elif name == "Gain":
                xml_gain = float(text)
            elif name == "Offset":
                offset = float(text)
            elif name == "Hz":
                hz = int(float(text))
            elif name == "PointsBytes":
                points_bytes = int(float(text))
        except ValueError:
            continue

    if not wave_b64:
        return None
    values = decode_samples(
        wave_b64, signal=signal, gain=xml_gain, offset=offset, points_bytes=points_bytes
    )
    if values.size == 0:
        return None
    return WaveBlock(signal=signal, hz=hz, start=block_start, values=values)


def _block_time(cpc: ET.Element) -> pd.Timestamp | None:
    raw = cpc.get("datetime")
    if not raw:
        return None
    ts = pd.to_datetime(raw, errors="coerce", utc=True)
    return None if pd.isna(ts) else ts


def decode_archive(path: Path | str) -> list[WaveBlock]:
    """Decode every measurement group in one ``cpcArchive`` file."""
    blocks: list[WaveBlock] = []
    try:
        tree = ET.parse(str(path))
    except (ET.ParseError, OSError):
        return blocks
    for cpc in tree.getroot().iter("cpc"):
        start = _block_time(cpc)
        for mg in cpc.iter("mg"):
            decoded = _decode_group(mg, start)
            if decoded is not None:
                blocks.append(decoded)
    return blocks


def _case_files(pid: str, wdir: Path | str | None = None) -> list[Path]:
    root = Path(wdir) if wdir is not None else wave_dir()
    if root is None:
        return []
    case_dir = waveform_case_dir(pid, root)
    if case_dir is None:
        return []
    return sorted(Path(case_dir).glob("*.xml"))


def decode_case(
    pid: str,
    *,
    wdir: Path | str | None = None,
    signals: list[str] | None = None,
    max_files: int = MAX_FILES_PER_CASE,
) -> dict[str, pd.Series]:
    """
    All waveform samples for *pid*, as one time-indexed Series per signal.

    ``signals`` restricts decoding (e.g. ``["GE_ART"]``) — worth using, since a
    case can hold millions of samples across every channel the monitor streamed.
    """
    wanted = {s.upper() for s in signals} if signals else None
    chunks: dict[str, list[tuple[pd.Timestamp | None, int, np.ndarray]]] = {}

    for file in _case_files(pid, wdir)[: max(1, int(max_files))]:
        for block in decode_archive(file):
            if wanted is not None and block.signal.upper() not in wanted:
                continue
            chunks.setdefault(block.signal, []).append(
                (block.start, block.hz, block.values)
            )

    out: dict[str, pd.Series] = {}
    for signal, parts in chunks.items():
        parts.sort(key=lambda p: (p[0] is None, p[0]))
        stamps: list[pd.DatetimeIndex] = []
        values: list[np.ndarray] = []
        total = 0
        for start, hz, vals in parts:
            if total + vals.size > MAX_SAMPLES_PER_SIGNAL:
                vals = vals[: max(0, MAX_SAMPLES_PER_SIGNAL - total)]
            if vals.size == 0:
                continue
            total += vals.size
            if start is not None and hz:
                stamps.append(
                    pd.date_range(start=start, periods=vals.size, freq=pd.Timedelta(seconds=1 / hz))
                )
            values.append(vals)
            if total >= MAX_SAMPLES_PER_SIGNAL:
                break
        if not values:
            continue
        data = np.concatenate(values)
        if len(stamps) == len(values) and stamps:
            index = pd.DatetimeIndex(np.concatenate([s.values for s in stamps]))
            series = pd.Series(data, index=index, name=signal).sort_index()
        else:
            series = pd.Series(data, name=signal)
        out[signal] = series
    return out


def wave_summary(pid: str, *, wdir: Path | str | None = None, scan_files: int = 4) -> dict:
    """
    Cheap coverage facts for a case — signal names, rates, state, sample counts.

    Scans the first few files rather than just one: a case commonly interleaves
    archives that carry no measurement groups at all with archives that hold the
    real traces, so ``files[0]`` alone can report "no signals" for a case with a
    perfectly good arterial line.
    """
    files = _case_files(pid, wdir)
    info: dict = {
        "pid": str(pid),
        "n_files": len(files),
        "available": bool(files),
        "signals": {},
    }
    if not files:
        return info

    per_signal: dict[str, dict] = {}
    collected: dict[str, list[np.ndarray]] = {}
    scanned = 0
    for file in files[: max(1, int(scan_files))]:
        scanned += 1
        for block in decode_archive(file):
            entry = per_signal.setdefault(
                block.signal,
                {"hz": block.hz, "samples": 0, "gain_overridden": block.signal in GAIN_OVERRIDES},
            )
            entry["samples"] += int(block.values.size)
            collected.setdefault(block.signal, []).append(block.values)
        if per_signal:
            break  # first archive with measurements is enough for a summary
    info["files_scanned"] = scanned

    for name, entry in per_signal.items():
        hz = entry.get("hz") or 0
        entry["seconds_scanned"] = round(entry["samples"] / hz, 1) if hz else 0.0
        vals = np.concatenate(collected[name]) if collected.get(name) else np.empty(0)
        entry["state"] = channel_state(name, vals)
        entry["transducing"] = entry["state"] in {"active", "implausible"}
        finite = vals[np.isfinite(vals)] if vals.size else vals
        entry["valid_fraction"] = (
            round(float(finite.size / vals.size), 3) if vals.size else 0.0
        )
        if finite.size:
            entry["min"] = round(float(finite.min()), 2)
            entry["max"] = round(float(finite.max()), 2)
            entry["median"] = round(float(np.median(finite)), 2)
    info["signals"] = per_signal
    info["active_signals"] = sorted(
        name for name, e in per_signal.items() if e.get("state") == "active"
    )
    return info
