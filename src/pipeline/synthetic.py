"""
Synthetic SIS EMR generator for development and tests without the real data.

Writes ``patient_information.csv``, ``patient_ventilator.csv``,
``patient_vitals.csv`` and ``patient_procedure_events.csv`` in the layout the
pipeline loads, reproducing the quirks of the MOVER SIS dump:

- ``\\N`` for nulls; ``0``-for-``O`` column names (``ETC02``, ``SP02``, ...)
- mixed timestamp formats (``M/D/YY H:MM`` and ``YYYY-MM-DD HH:MM:SS``)
- ventilator dropouts, duplicate samples, sparse NIBP (every 3-5 minutes)
- missing or implausible height; vitals-only (no ventilator) cases
- an optional row cap on the ventilator file, like the real export ceiling
- Intubation / Incision / Extubation procedure events, and agent wash-in and
  wash-out ramps after intubation and before extubation (phase context)

Every surgery is fictional; PIDs start with ``SYN``. Injected anomalies are
recorded in ``synthetic_truth.json`` next to the CSVs so flag recall can be
checked. Every sixth ventilated case also gets a brief desaturation right after
intubation (recorded with ``"phase": "induction"``).

    PYTHONPATH=. python -m src.pipeline.synthetic --out data/synthetic/EMR --n-cases 30
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

NULL = "\\N"

# Raw SIS column names, typos included
VENT_COLS = [
    "PID", "Obs_time", "Agent", "Agent_Fi", "Agent_Et", "ETC02", "FIC02", "TV",
    "RR", "PEEP", "PIP", "FIN20", "ETN20", "FI02", "ET02",
]
VITALS_COLS = ["PID", "Obs_time", "HRe", "HRp", "nSBP", "nMAP", "nDBP", "SP02"]

MAC40 = {"S": 1.8, "D": 6.6, "I": 1.2}
N2O_MAC40 = 104.0
MAC_AGE_COEF = -0.00269

PROCEDURES = [
    "Laparoscopic Cholecystectomy",
    "Laparoscopic Appendectomy",
    "Total Knee Arthroplasty",
    "Total Hip Arthroplasty",
    "Lumbar Laminectomy",
    "Thyroidectomy",
    "Robotic Prostatectomy",
    "Open Reduction Internal Fixation Radius",
    "Hysterectomy Abdominal",
    "Craniotomy",
]

# kind -> rules the pipeline should raise inside the anomaly window
ANOMALY_RULES: dict[str, list[str]] = {
    "pip_ramp": ["pip_rising", "pip_high"],
    "hypoventilation": ["etco2_high", "rr_low", "hypoventilation_pattern"],
    "disconnect": ["etco2_zero_vent"],
    "desaturation": ["spo2_low"],
    "hypotension": ["map_low"],
    "agent_overdose": ["agent_high"],
}
ANOMALY_MINUTES = {
    "pip_ramp": 16,
    "hypoventilation": 15,
    "disconnect": 4,
    "desaturation": 6,
    "hypotension": 12,
    "agent_overdose": 10,
}


@dataclass
class _Case:
    pid: str
    age: int
    gender: str
    ht: float | None
    wt: float
    procedure: str
    or_start: pd.Timestamp
    surgery_start: pd.Timestamp
    surgery_end: pd.Timestamp
    or_end: pd.Timestamp
    agent: str
    uses_n2o: bool
    ventilated: bool
    intubation: pd.Timestamp
    extubation: pd.Timestamp
    anomalies: list[dict] = field(default_factory=list)


def _age_mac(mac40: float, age: float) -> float:
    return mac40 * 10 ** (MAC_AGE_COEF * (age - 40.0))


def _ibw(ht: float | None, gender: str) -> float:
    if ht is None or not 140 <= ht <= 215:
        return 70.0
    base = 50.0 if gender == "M" else 45.5
    return base + 2.3 * (ht / 2.54 - 60.0)


def _fmt_time(t: pd.Timestamp, rng: np.random.Generator, seconds: bool) -> str:
    if rng.random() < 0.3:
        # Short US format, minute resolution
        return f"{t.month}/{t.day}/{t.strftime('%y')} {t.hour}:{t.strftime('%M')}"
    if seconds:
        t = t + pd.Timedelta(seconds=int(rng.integers(0, 50)))
    return t.strftime("%Y-%m-%d %H:%M:%S")


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return NULL
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


def _make_case(i: int, rng: np.random.Generator, day0: pd.Timestamp) -> _Case:
    gender = str(rng.choice(["M", "F"]))
    age = int(rng.integers(18, 86))
    ht_roll = rng.random()
    if ht_roll < 0.08:
        ht = None
    elif ht_roll < 0.10:
        ht = 0.0  # implausible, as seen in the real corpus
    else:
        ht = float(round(rng.normal(176 if gender == "M" else 163, 7), 1))
    wt = float(round(rng.normal(85 if gender == "M" else 72, 14), 1))
    surgery_min = int(rng.integers(60, 241))
    or_start = day0 + pd.Timedelta(days=i, hours=int(rng.integers(7, 14)))
    surgery_start = or_start + pd.Timedelta(minutes=int(rng.integers(12, 26)))
    surgery_end = surgery_start + pd.Timedelta(minutes=surgery_min)
    or_end = surgery_end + pd.Timedelta(minutes=int(rng.integers(10, 21)))
    agent = str(rng.choice(["S", "D", "I", "N"], p=[0.65, 0.2, 0.05, 0.1]))
    ventilated = rng.random() >= 0.05
    # Induction after OR entry, before incision; emergence before OR exit
    intubation = or_start + pd.Timedelta(minutes=int(rng.integers(4, 10)))
    extubation = surgery_end + pd.Timedelta(minutes=int(rng.integers(3, 9)))
    return _Case(
        pid=f"SYN{i:04d}{rng.integers(0, 16**6):06x}",
        age=age,
        gender=gender,
        ht=ht,
        wt=wt,
        procedure=str(rng.choice(PROCEDURES)),
        or_start=or_start,
        surgery_start=surgery_start,
        surgery_end=surgery_end,
        or_end=or_end,
        agent=agent if ventilated else "N",
        uses_n2o=bool(ventilated and agent != "N" and rng.random() < 0.15),
        ventilated=ventilated,
        intubation=intubation,
        extubation=extubation,
    )


def _plan_anomalies(case: _Case, rng: np.random.Generator, rate: float) -> None:
    """Place 0-2 non-overlapping anomaly windows inside the surgery."""
    if not case.ventilated:
        return
    kinds = [k for k in ANOMALY_RULES if case.agent != "N" or k != "agent_overdose"]
    n = int(rng.random() < rate) + int(rng.random() < rate / 3)
    surgery_len = int((case.surgery_end - case.surgery_start) / pd.Timedelta(minutes=1))
    used: list[tuple[int, int]] = []
    for kind in rng.choice(kinds, size=min(n, len(kinds)), replace=False):
        dur = ANOMALY_MINUTES[str(kind)]
        for _ in range(20):
            start = int(rng.integers(20, max(21, surgery_len - dur - 20)))
            if all(start + dur + 10 < a or start > b + 10 for a, b in used):
                used.append((start, start + dur))
                t0 = case.surgery_start + pd.Timedelta(minutes=start)
                case.anomalies.append({
                    "kind": str(kind),
                    "start": t0.isoformat(),
                    "end": (t0 + pd.Timedelta(minutes=dur - 1)).isoformat(),
                    "expected_rules": ANOMALY_RULES[str(kind)],
                })
                break


def _add_induction_desaturation(case: _Case) -> None:
    """A brief desaturation right after intubation, from the case's own times (no random draws).

    Phase context must never hide it: spo2_low has to fire during induction.
    """
    t0 = case.intubation + pd.Timedelta(minutes=2)
    case.anomalies.append({
        "kind": "desaturation",
        "start": t0.isoformat(),
        "end": (t0 + pd.Timedelta(minutes=3)).isoformat(),
        "expected_rules": ANOMALY_RULES["desaturation"],
        "phase": "induction",
    })


def _active(case: _Case, kind: str, t: pd.Timestamp) -> tuple[bool, int]:
    for a in case.anomalies:
        if a["kind"] == kind:
            start = pd.Timestamp(a["start"])
            if start <= t <= pd.Timestamp(a["end"]):
                return True, int((t - start) / pd.Timedelta(minutes=1))
    return False, 0


def _case_rows(case: _Case, rng: np.random.Generator) -> tuple[list[dict], list[dict]]:
    vent_rows: list[dict] = []
    vit_rows: list[dict] = []
    ibw = _ibw(case.ht, case.gender)
    tv_set = round(ibw * rng.uniform(6.5, 7.5) / 10) * 10
    rr_set = int(rng.integers(10, 15))
    peep = float(rng.choice([5, 5, 5, 6, 8]))
    pip_base = float(rng.uniform(17, 22))
    etco2_base = float(rng.uniform(34, 40))
    fio2 = float(rng.choice([40, 50, 50, 60]))
    hr_base = float(rng.uniform(58, 88))
    map_base = float(rng.uniform(72, 92))
    maint_mac = 0.6 if case.uses_n2o else float(rng.uniform(0.9, 1.1))
    et_maint = _age_mac(MAC40[case.agent], case.age) * maint_mac if case.agent in MAC40 else 0.0
    n2o_et = float(rng.uniform(48, 60)) if case.uses_n2o else 0.0

    # Ventilator: intubation shortly after OR entry until shortly before exit
    intub = case.intubation
    extub = case.extubation
    dropouts: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    if rng.random() < 0.35:
        for _ in range(int(rng.integers(1, 3))):
            d0 = intub + pd.Timedelta(minutes=int(rng.integers(10, 60)))
            dropouts.append((d0, d0 + pd.Timedelta(minutes=int(rng.integers(3, 21)))))

    t = intub
    while case.ventilated and t <= extub:
        in_dropout = any(a <= t < b for a, b in dropouts)
        in_anomaly = any(
            pd.Timestamp(a["start"]) <= t <= pd.Timestamp(a["end"]) for a in case.anomalies
        )
        if in_dropout and not in_anomaly:
            t += pd.Timedelta(minutes=1)
            continue
        mins_in = (t - intub) / pd.Timedelta(minutes=1)
        mins_left = (extub - t) / pd.Timedelta(minutes=1)
        ramp = min(1.0, mins_in / 8.0) * min(1.0, max(0.1, mins_left / 8.0))
        et = et_maint * ramp + rng.normal(0, 0.03 * max(et_maint, 0.1))
        tv = tv_set + rng.normal(0, 8)
        rr = float(rr_set)
        pip = pip_base + rng.normal(0, 0.6)
        etco2 = etco2_base + rng.normal(0, 1.0)
        mac = _age_mac(MAC40[case.agent], case.age) if case.agent in MAC40 else 0.0

        on, k = _active(case, "pip_ramp", t)
        if on:
            pip = pip_base + 1.0 * k
        on, _ = _active(case, "hypoventilation", t)
        if on:
            etco2, rr = 56.0 + rng.normal(0, 0.8), 5.0
        on, _ = _active(case, "disconnect", t)
        if on:
            etco2 = 0.0
        on, _ = _active(case, "agent_overdose", t)
        if on and mac:
            et = mac * 2.2

        row = {
            "PID": case.pid,
            "Obs_time": _fmt_time(t, rng, seconds=True),
            "Agent": case.agent,
            "Agent_Fi": et + 0.3 if case.agent in MAC40 else None,
            "Agent_Et": et if case.agent in MAC40 else None,
            "ETC02": etco2,
            "FIC02": 0.0,
            "TV": tv,
            "RR": rr,
            "PEEP": peep,
            "PIP": pip,
            "FIN20": n2o_et + 3 if case.uses_n2o else 0.0,
            "ETN20": n2o_et * ramp if case.uses_n2o else 0.0,
            "FI02": fio2,
            "ET02": fio2 - 5,
        }
        vent_rows.append(row)
        if rng.random() < 0.01:
            vent_rows.append(dict(row))  # duplicate sample
        t += pd.Timedelta(minutes=1)

    # Vitals: whole OR stay; NIBP cycles every 3-5 minutes
    next_nibp = case.or_start
    t = case.or_start
    while t <= case.or_end:
        hr = hr_base + rng.normal(0, 2)
        spo2 = float(min(100, round(rng.normal(98.5, 0.8))))
        on, _ = _active(case, "desaturation", t)
        if on:
            spo2 = 89.0
        nibp = t >= next_nibp
        on_hypo, _ = _active(case, "hypotension", t)
        if on_hypo:
            nibp = True
        mean_ap = (55.0 if on_hypo else map_base + rng.normal(0, 3)) if nibp else None
        if nibp:
            next_nibp = t + pd.Timedelta(minutes=int(rng.integers(3, 6)))
        vit_rows.append({
            "PID": case.pid,
            "Obs_time": _fmt_time(t, rng, seconds=False),
            "HRe": hr if rng.random() > 0.02 else None,
            "HRp": hr + rng.normal(0, 1),
            "nSBP": mean_ap + 35 if mean_ap else None,
            "nMAP": mean_ap,
            "nDBP": mean_ap - 15 if mean_ap else None,
            "SP02": spo2,
        })
        t += pd.Timedelta(minutes=1)
    return vent_rows, vit_rows


def generate_synthetic_emr(
    out_dir: Path | str,
    n_cases: int = 20,
    seed: int = 0,
    anomaly_rate: float = 0.6,
    vent_row_cap: int | None = None,
) -> Path:
    """
    Write a synthetic SIS EMR directory and return its path.

    ``anomaly_rate`` is the chance a ventilated case gets at least one injected
    anomaly. ``vent_row_cap`` truncates the ventilator file after that many
    rows, as the real export does at 1,048,575.
    """
    if n_cases < 1:
        raise ValueError("n_cases must be >= 1")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    day0 = pd.Timestamp("2016-03-01")

    cases: list[_Case] = []
    vent_rows: list[dict] = []
    vit_rows: list[dict] = []
    events: list[dict] = []
    for i in range(n_cases):
        case = _make_case(i, rng, day0)
        _plan_anomalies(case, rng, anomaly_rate)
        if case.ventilated and i % 6 == 4:
            _add_induction_desaturation(case)
        v, w = _case_rows(case, rng)
        vent_rows.extend(v)
        vit_rows.extend(w)
        cases.append(case)
        if case.ventilated:
            for name, when in (
                ("Intubation", case.intubation),
                ("Incision", case.surgery_start),
                ("Extubation", case.extubation),
            ):
                events.append({
                    "PID": case.pid,
                    "Event_time": _fmt_time(when, rng, seconds=False),
                    "Event_name": name,
                })

    truncated_pids: set[str] = set()
    if vent_row_cap is not None and len(vent_rows) > vent_row_cap:
        truncated_pids = {r["PID"] for r in vent_rows[vent_row_cap:]}
        vent_rows = vent_rows[:vent_row_cap]

    info = pd.DataFrame([
        {
            "PID": c.pid,
            "Age": c.age,
            "Ht": c.ht,
            "Wt": c.wt,
            "Gender": c.gender,
            "OR_start": _fmt_time(c.or_start, rng, seconds=False),
            "OR_end": _fmt_time(c.or_end, rng, seconds=False),
            "Surgery_start": _fmt_time(c.surgery_start, rng, seconds=False),
            "Surgery_end": _fmt_time(c.surgery_end, rng, seconds=False),
            "Procedure": c.procedure,
        }
        for c in cases
    ])
    _write(info, out / "patient_information.csv")
    _write(pd.DataFrame(vent_rows, columns=VENT_COLS), out / "patient_ventilator.csv")
    _write(pd.DataFrame(vit_rows, columns=VITALS_COLS), out / "patient_vitals.csv")
    _write(
        pd.DataFrame(events, columns=["PID", "Event_time", "Event_name"]),
        out / "patient_procedure_events.csv",
    )

    truth = {
        "generator": "src.pipeline.synthetic",
        "seed": seed,
        "n_cases": n_cases,
        "vent_row_cap": vent_row_cap,
        "cases": {
            c.pid: {
                "ventilated": c.ventilated,
                "agent": c.agent,
                "uses_n2o": c.uses_n2o,
                "height_usable": c.ht is not None and 140 <= c.ht <= 215,
                "vent_truncated": c.pid in truncated_pids,
                "anomalies": c.anomalies,
            }
            for c in cases
        },
    }
    (out / "synthetic_truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
    return out


def _write(df: pd.DataFrame, path: Path) -> None:
    df.map(_fmt).to_csv(path, index=False)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Write a synthetic MOVER SIS EMR directory")
    parser.add_argument("--out", type=str, default="data/synthetic/EMR")
    parser.add_argument("--n-cases", type=int, default=30)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--anomaly-rate", type=float, default=0.6)
    parser.add_argument("--vent-row-cap", type=int, default=None)
    args = parser.parse_args(argv)
    path = generate_synthetic_emr(
        args.out,
        n_cases=args.n_cases,
        seed=args.seed,
        anomaly_rate=args.anomaly_rate,
        vent_row_cap=args.vent_row_cap,
    )
    print(f"[synthetic] wrote {args.n_cases} fictional cases to {path}")


if __name__ == "__main__":
    main()
