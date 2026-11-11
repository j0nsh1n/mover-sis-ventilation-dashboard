"""The record as a timeline: vital tracks and drug doses on one minute axis, and the same numbers in words.

It is built from the table's own rows (``align.DisplayRow``), so the chart, the table and the description cannot
drift apart. A reading recorded as missing stays missing: it has no value, and nothing here fills it in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.modules.medication_vitals.align import END_BEFORE_START, DisplayRow, _number

MEASUREMENT = "measurement"
BOLUS = "bolus"
INFUSION = "infusion"


@dataclass(frozen=True)
class TrackSpec:
    label: str
    unit: str
    token: str  # colour token of the trace: a signal hue is identity only, the name is always written
    limits: tuple[float, float]  # the least y range, so two records compare by eye


# Keyed by the subject the table writes. Arterial MAP comes first, as it does on the Handoff page.
TRACK_SPECS: dict[str, TrackSpec] = {
    "Arterial MAP": TrackSpec("Arterial MAP", "mmHg", "s_pip", (50.0, 90.0)),
    "Noninvasive MAP": TrackSpec("Noninvasive MAP", "mmHg", "s_map", (50.0, 90.0)),
    "Heart rate": TrackSpec("Heart rate", "bpm", "s_hr", (50.0, 110.0)),
    "SpO2": TrackSpec("SpO₂", "%", "s_spo2", (85.0, 100.0)),
}
_FALLBACK_TOKEN = "ink_2"


@dataclass(frozen=True)
class Reading:
    minute: int
    value: float | None  # None: the record has the minute and says the value is missing


@dataclass(frozen=True)
class Track:
    subject: str
    label: str
    unit: str
    token: str
    limits: tuple[float, float]
    readings: tuple[Reading, ...]

    def present(self) -> dict[int, float]:
        return {item.minute: item.value for item in self.readings if item.value is not None}

    def missing(self) -> tuple[int, ...]:
        return tuple(item.minute for item in self.readings if item.value is None)


@dataclass(frozen=True)
class Dose:
    name: str
    amount: str | None  # "100 mcg"; None when the dose was never written down
    start: int
    end: int
    kind: str

    @property
    def infusion(self) -> bool:
        return self.kind == INFUSION

    @property
    def reversed_times(self) -> bool:
        """The record gives an end time before the start time; the dose is neither a bolus nor an infusion."""
        return self.kind == END_BEFORE_START

    @property
    def label(self) -> str:
        return f"{self.name} {self.amount}" if self.amount else f"{self.name}, dose not recorded"

    @property
    def line(self) -> str:
        when = f"from minute {self.start} to minute {self.end}" if self.infusion else f"at minute {self.start}"
        return f"{self.label} {when} ({self.kind})"


@dataclass(frozen=True)
class Timeline:
    label: str  # for example "Invented record" or "Open case SYN-DEMO-014"
    axis_label: str
    tracks: tuple[Track, ...]
    doses: tuple[Dose, ...]
    off_axis: int  # drug rows that have only a clock time and so are not on the axis

    def span(self) -> tuple[int, int] | None:
        minutes = [item.minute for track in self.tracks for item in track.readings]
        for dose in self.doses:
            minutes.extend((dose.start, dose.end))
        return (min(minutes), max(minutes)) if minutes else None

    def cursor_minutes(self) -> tuple[int, ...]:
        """The minutes the cursor can stand on: every minute with a reading row or a dose start."""
        found = {item.minute for track in self.tracks for item in track.readings}
        found.update(dose.start for dose in self.doses)
        return tuple(sorted(found))


def build_timeline(label: str, axis_label: str, rows: Sequence[DisplayRow]) -> Timeline:
    readings: dict[str, list[Reading]] = {}
    doses: list[Dose] = []
    off_axis = 0
    for row in rows:
        if row.kind == MEASUREMENT:
            if row.start is not None:
                readings.setdefault(row.subject, []).append(Reading(row.start, row.number))
        elif row.start is None:
            off_axis += 1
        else:
            amount = None if row.number is None else f"{row.number_text} {row.unit}".strip()
            end = row.end if row.kind == INFUSION and row.end is not None else row.start
            doses.append(Dose(row.subject, amount, row.start, end, row.kind))
    tracks = []
    for subject in [*TRACK_SPECS, *(name for name in readings if name not in TRACK_SPECS)]:
        if subject not in readings:
            continue
        spec = TRACK_SPECS.get(subject) or TrackSpec(subject, "", _FALLBACK_TOKEN, (0.0, 1.0))
        tracks.append(
            Track(subject, spec.label, spec.unit, spec.token, spec.limits,
                  tuple(sorted(readings[subject], key=lambda item: item.minute)))
        )
    return Timeline(label, axis_label, tuple(tracks), tuple(sorted(doses, key=lambda d: (d.start, d.name))), off_axis)


def minutes_text(minutes: Sequence[int]) -> str:
    """'minute 8', 'minutes 4 and 5', 'minutes 4, 5 and 6'; a long run is said by its count and first minute."""
    if len(minutes) == 1:
        return f"minute {minutes[0]}"
    if len(minutes) > 5:
        return f"{len(minutes)} minutes, the first at minute {minutes[0]}"
    return "minutes " + ", ".join(str(m) for m in minutes[:-1]) + f" and {minutes[-1]}"


def track_line(track: Track) -> str:
    """One track in words: how many readings, from when to when, lowest and highest, and where it is missing."""
    found = track.present()
    head = f"{track.label} ({track.unit})" if track.unit else track.label
    if found:
        text = (f"{head}: {len(found)} readings from minute {min(found)} to minute {max(found)}, "
                f"lowest {_number(min(found.values()))}, highest {_number(max(found.values()))}")
    else:
        text = f"{head}: no readings"
    gaps = track.missing()
    return text + (f", missing at {minutes_text(gaps)}." if gaps else ".")


def describe(timeline: Timeline) -> str:
    """What the chart shows, in the numbers the table lists."""
    span = timeline.span()
    parts = [f"{timeline.label}, minutes {span[0]} to {span[1]}." if span else f"{timeline.label}, nothing to draw."]
    parts.extend(track_line(track) for track in timeline.tracks)
    if timeline.doses:
        parts.append("Drugs: " + "; ".join(dose.line for dose in timeline.doses) + ".")
    else:
        parts.append("No drug doses on the axis.")
    if timeline.off_axis:
        parts.append(f"{timeline.off_axis} drug rows have only a clock time and are not placed on the axis.")
    parts.append("The table lists the same rows.")
    return " ".join(parts)


def value_at(track: Track, minute: int) -> tuple[str, float | None]:
    """('value', x), ('missing', None) or ('none', None) when the record has no row for that minute."""
    for item in track.readings:
        if item.minute == minute:
            return ("missing", None) if item.value is None else ("value", item.value)
    return ("none", None)


def cursor_text(timeline: Timeline, minute: int) -> str:
    """The cursor in words: each track at that minute and the doses that start there."""
    parts = []
    for track in timeline.tracks:
        state, value = value_at(track, minute)
        unit = f" {track.unit}" if track.unit else ""
        if value is not None:
            shown = f"{_number(value)}{unit}"
        else:
            shown = "missing" if state == "missing" else "no row"
        parts.append(f"{track.label} {shown}")
    starting = [dose.label for dose in timeline.doses if dose.start == minute]
    if starting:
        parts.append("starting: " + ", ".join(starting))
    return f"Cursor at minute {minute}: " + "; ".join(parts) + "."
