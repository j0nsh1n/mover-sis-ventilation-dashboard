"""Waveform decoding (Bernoulli / GE S5 cpcArchive)."""

from __future__ import annotations

import base64
import struct

import numpy as np
import pytest

from src import wave_decode


def _reference_decode(raw: bytes, gain: float, offset: float) -> list[float]:
    """
    Dataset-provider reference algorithm (Feb 2023), byte-pair form.

    Kept here so the vectorised implementation in ``src.wave_decode`` is checked
    against the decoder the data owners validated against the monitors. The old
    root ``waveform_decode.py`` snippet was incomplete (not importable) and was
    removed; this is the algorithmic substance that mattered.
    """

    def set_bit(v, index, x):
        mask = 1 << index
        v &= ~mask
        if x:
            v |= mask
        return v

    out = []
    for i in range(0, len(raw) - 1, 2):
        t = raw[i] + raw[i + 1] * 256
        t = set_bit(t, 15, 0) + (-32768) * (t >> 15)
        out.append(t * gain + offset)
    return out


def _b64_int16(values: list[int]) -> str:
    return base64.b64encode(struct.pack("<" + "h" * len(values), *values)).decode()


def _archive_xml(signal: str, values: list[int], *, gain: str = "1.0", hz: int = 180) -> str:
    return f"""<cpcArchive duration="30">
      <cpc seq="1" datetime="2017-06-23T22:30:09Z">
        <device type="VitalSigns"><status>OK</status>
          <measurements>
            <mg name="{signal}">
              <m name="Wave">{_b64_int16(values)}</m>
              <m name="Points">{len(values)}</m>
              <m name="PointsBytes">2</m>
              <m name="Min">-500</m><m name="Max">1500</m>
              <m name="Offset">0</m>
              <m name="Gain">{gain}</m>
              <m name="Hz">{hz}</m>
            </mg>
          </measurements>
        </device>
      </cpc>
    </cpcArchive>"""


def test_matches_reference_byte_pair_decoder():
    """
    Identical to the reference decoder on real measurements.

    Sentinel codes are excluded here: the reference predates the no-data handling
    and scales them like any other sample, so they are covered separately by
    ``test_nodata_sentinels_decode_to_nan``.
    """
    values = [0, 1, -1, 32767, 1234, -4321, 500, -500, 2047, -2048]
    raw = struct.pack("<" + "h" * len(values), *values)
    got = wave_decode.decode_samples(base64.b64encode(raw).decode(), gain=0.25, offset=3.0)
    assert got.tolist() == pytest.approx(_reference_decode(raw, 0.25, 3.0))


def test_signed_values_round_trip():
    # full 12-bit sample span plus non-sentinel 16-bit extremes
    values = [-2048, -1, 0, 1, 2047, 32767]
    got = wave_decode.decode_samples(_b64_int16(values), gain=1.0, offset=0.0)
    assert got.tolist() == [float(v) for v in values]


def test_gain_and_offset_are_applied():
    got = wave_decode.decode_samples(_b64_int16([100, 200]), gain=0.5, offset=10.0)
    assert got.tolist() == [60.0, 110.0]


def test_pressure_gain_overrides_beat_the_xml_value():
    """The XML Gain is wrong for pressures — GE_ART must be forced to 0.25."""
    assert wave_decode.resolve_gain("GE_ART", 999.0) == 0.25
    assert wave_decode.resolve_gain("INVP1", 999.0) == 0.01
    # anything else trusts the file
    assert wave_decode.resolve_gain("ECG_II", 2.5) == 2.5
    assert wave_decode.resolve_gain("ECG_II", None) == 1.0

    raw = _b64_int16([400])
    assert wave_decode.decode_samples(raw, signal="GE_ART", gain=999.0).tolist() == [100.0]
    assert wave_decode.decode_samples(raw, signal="INVP1", gain=999.0).tolist() == [4.0]


def test_decode_archive_reads_groups_and_timestamps(tmp_path):
    f = tmp_path / "22-30-09-000Z.xml"
    f.write_text(_archive_xml("GE_ART", [400, 800, 1200]), encoding="utf-8")
    blocks = wave_decode.decode_archive(f)
    assert len(blocks) == 1
    b = blocks[0]
    assert b.signal == "GE_ART"
    assert b.hz == 180
    assert b.values.tolist() == [100.0, 200.0, 300.0]  # gain override applied
    assert str(b.start) == "2017-06-23 22:30:09+00:00"
    assert b.duration_s == pytest.approx(3 / 180)


def test_datadown_block_yields_nothing(tmp_path):
    f = tmp_path / "empty.xml"
    f.write_text(
        """<cpcArchive duration="30"><cpc seq="1" datetime="2017-06-23T22:30:09Z">
        <device><status>DATADOWN</status><measurements /><settings /></device>
        </cpc></cpcArchive>""",
        encoding="utf-8",
    )
    assert wave_decode.decode_archive(f) == []


def test_malformed_inputs_are_survivable(tmp_path):
    assert wave_decode.decode_samples("").size == 0
    assert wave_decode.decode_samples("!!!not base64!!!").size == 0
    bad = tmp_path / "bad.xml"
    bad.write_text("<cpcArchive><cpc", encoding="utf-8")
    assert wave_decode.decode_archive(bad) == []
    assert wave_decode.decode_archive(tmp_path / "missing.xml") == []


def test_odd_length_payload_drops_partial_sample():
    raw = struct.pack("<h", 1000) + b"\x01"  # trailing half-sample
    got = wave_decode.decode_samples(base64.b64encode(raw).decode(), gain=1.0)
    assert got.tolist() == [1000.0]


def test_decode_case_builds_time_indexed_series(tmp_path):
    case = tmp_path / "Waveforms" / "00" / "pid123"
    case.mkdir(parents=True)
    (case / "a.xml").write_text(_archive_xml("GE_ART", [400] * 5), encoding="utf-8")

    def fake_case_dir(pid, root):
        return case if str(pid) == "pid123" else None

    import src.wave_decode as wd

    orig = wd.waveform_case_dir
    wd.waveform_case_dir = fake_case_dir
    try:
        out = wd.decode_case("pid123", wdir=tmp_path)
    finally:
        wd.waveform_case_dir = orig

    assert set(out) == {"GE_ART"}
    series = out["GE_ART"]
    assert len(series) == 5
    assert series.iloc[0] == 100.0
    assert isinstance(series.index, __import__("pandas").DatetimeIndex)


def test_wave_summary_reports_no_data_for_unknown_case(tmp_path):
    info = wave_decode.wave_summary("nope", wdir=tmp_path)
    assert info["available"] is False
    assert info["n_files"] == 0


_REAL_WAVE_ROOT = __import__("pathlib").Path(
    "/var/mnt/games/sis_wave_v2/UCI_deidentified_part3_SIS_11_07/Waveforms"
)

_needs_real_waves = pytest.mark.skipif(
    not _REAL_WAVE_ROOT.is_dir(), reason="real MOVER wave tree not present"
)


def _real_case_dirs(limit: int = 40, per_bucket: int = 6):
    """
    Distinct case folders spread across buckets.

    Globbing files alone keeps re-picking one case, and draining a single bucket
    biases the sample — live arterial lines are rare, so coverage matters.
    """
    out = []
    for bucket in sorted(_REAL_WAVE_ROOT.iterdir()):
        if not bucket.is_dir():
            continue
        taken = 0
        for case in sorted(bucket.iterdir()):
            if case.is_dir() and any(case.glob("*.xml")):
                out.append(case)
                taken += 1
                if len(out) >= limit:
                    return out
                if taken >= per_bucket:
                    break
    return out


def _first_real_art_blocks():
    for case in _real_case_dirs():
        for xml in sorted(case.glob("*.xml"))[:1]:
            blocks = [b for b in wave_decode.decode_archive(xml) if b.signal == "GE_ART"]
            if blocks:
                return xml, blocks
    return None, []


_state_cache: dict[str, tuple] | None = None


def _art_by_state():
    """
    Map channel state -> (file, samples) across a spread of real cases.

    Scans several files per case: archives holding no measurement groups are
    interleaved with the ones carrying real traces, so checking only the first
    file reports "no GE_ART" for cases that plainly have it.
    """
    global _state_cache
    if _state_cache is not None:
        return _state_cache
    found: dict[str, tuple] = {}
    for case in _real_case_dirs():
        for xml in sorted(case.glob("*.xml"))[:4]:
            blocks = [b for b in wave_decode.decode_archive(xml) if b.signal == "GE_ART"]
            if not blocks:
                continue
            vals = np.concatenate([b.values for b in blocks])
            found.setdefault(wave_decode.channel_state("GE_ART", vals), (xml, vals))
            break
        if {"idle", "active", "implausible"} <= set(found):
            break
    _state_cache = found
    return found


@_needs_real_waves
def test_real_archive_decodes_structurally():
    """Real files must parse into finite samples with a usable time base."""
    xml, blocks = _first_real_art_blocks()
    if not blocks:
        pytest.skip("no GE_ART blocks found in sampled files")
    vals = np.concatenate([b.values for b in blocks])
    assert vals.size > 0
    assert np.isfinite(vals).all()
    assert blocks[0].hz == 180
    assert blocks[0].start is not None


@_needs_real_waves
def test_active_arterial_line_decodes_to_physiological_pressures():
    """A genuinely transducing A-line must land in a real blood-pressure range."""
    states = _art_by_state()
    if "active" not in states:
        pytest.skip("no active GE_ART channel in the sampled cases")
    xml, vals = states["active"]
    median = float(np.median(vals))
    assert 20 < median < 150, f"{xml}: median {median:.1f} not a blood pressure"
    inside = float(((vals >= -20) & (vals <= 300)).mean())
    assert inside > 0.95, f"{xml}: only {inside:.1%} of samples physiological"
    assert vals.max() > 40, f"{xml}: no systolic excursion"


@_needs_real_waves
def test_idle_channel_is_not_mistaken_for_data():
    """Most cases carry a GE_ART group that never transduced — flat near zero."""
    states = _art_by_state()
    if "idle" not in states:
        pytest.skip("no idle GE_ART channel in the sampled cases")
    xml, vals = states["idle"]
    assert abs(float(np.median(vals))) < 20, f"{xml}: not actually idle"
    assert wave_decode.channel_state("GE_ART", vals) == "idle"


@_needs_real_waves
def test_saturated_channel_is_rejected_as_implausible():
    """
    A disconnected/saturated transducer swings +-450 with a median near zero.

    It passes a span check, so only the physiological test keeps it off a chart
    labelled 'arterial pressure'.
    """
    states = _art_by_state()
    if "implausible" not in states:
        pytest.skip("no saturated GE_ART channel in the sampled cases")
    xml, vals = states["implausible"]
    assert wave_decode.looks_transducing(vals), "span check alone would accept this"
    assert not wave_decode.looks_physiological_pressure(vals), f"{xml}: should be rejected"


def test_nodata_sentinels_decode_to_nan():
    """
    -32767/-32768 are 'no data' codes, not measurements.

    Samples are 12-bit (-2048..2047), so these are unreachable as real values; an
    unused channel streams them continuously and would otherwise report a median
    of -32767.
    """
    got = wave_decode.decode_samples(_b64_int16([-32767, 100, -32768, 200]), gain=1.0)
    assert np.isnan(got[0]) and np.isnan(got[2])
    assert got[1] == 100.0 and got[3] == 200.0


def test_all_sentinel_channel_is_nodata_not_idle():
    all_missing = wave_decode.decode_samples(_b64_int16([-32767] * 50), gain=1.0)
    assert wave_decode.channel_state("AWP", all_missing) == "nodata"


def test_channel_state_classifies_synthetic_examples():
    idle = np.full(500, -2.0) + np.random.default_rng(0).normal(0, 0.3, 500)
    saturated = np.tile([-450.0, 450.0], 250)
    arterial = 80 + 30 * np.sin(np.linspace(0, 40 * np.pi, 500))
    assert wave_decode.channel_state("GE_ART", idle) == "idle"
    assert wave_decode.channel_state("GE_ART", saturated) == "implausible"
    assert wave_decode.channel_state("GE_ART", arterial) == "active"
    # non-pressure channels only need to have moved
    assert wave_decode.channel_state("PLETH", saturated) == "active"


@_needs_real_waves
def test_v2_xml_gains_match_overrides():
    """
    sis_wave_v2 corrected the gains the Feb-2023 reference had to hard-code.

    If a future release diverges from GAIN_OVERRIDES, this fails and the
    override-first policy must be revisited.
    """
    from xml.etree import ElementTree as ET

    seen: dict[str, set[str]] = {}
    for case in _real_case_dirs(12):
        for xml in sorted(case.glob("*.xml"))[:1]:
            try:
                root = ET.parse(xml).getroot()
            except ET.ParseError:
                continue
            for mg in root.iter("mg"):
                name = mg.get("name") or ""
                for m in mg.findall("m"):
                    if m.get("name") == "Gain" and (m.text or "").strip():
                        seen.setdefault(name, set()).add(m.text.strip())
    if not seen:
        pytest.skip("no gain metadata found")
    for signal, override in wave_decode.GAIN_OVERRIDES.items():
        for value in seen.get(signal, set()):
            assert float(value) == pytest.approx(override), (
                f"{signal}: v2 XML gain {value} differs from override {override}"
            )
