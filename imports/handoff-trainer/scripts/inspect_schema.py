#!/usr/bin/env python3
"""Read-only schema inspector for the MOVER SIS EMR CSVs.

PRIVACY: this script prints NO patient values. It only prints file sizes, row
counts, column names, inferred dtypes, null fractions, distinct-value counts,
timestamp *shape* patterns (digits -> 9), aggregate relative-time statistics
(minutes from Surgery_start), coarse scale bands, and the labels of code-like
category columns (drug, unit, event, device and agent names).
It never writes anything; it only opens the CSVs for reading.

Usage: python3 scripts/inspect_schema.py --raw-dir /var/mnt/games/EMR
"""
import argparse
import os
import re
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

FILES = {
    "patient_information": ("patient_information.csv", ["OR_start", "OR_end", "Surgery_start", "Surgery_end"]),
    "a_line": ("patient_a_line.csv", ["PlacementTime"]),
    "input_output": ("patient_input_output.csv", ["IO_datetime"]),
    "labs": ("patient_labs.csv", ["Obs_time"]),
    "medication": ("patient_medication.csv", ["Start_time", "End_time"]),
    "observations": ("patient_observations.csv", ["Obs_time"]),
    "procedure_events": ("patient_procedure_events.csv", ["Event_time"]),
    "ventilator": ("patient_ventilator.csv", ["Obs_time"]),
    "vitals": ("patient_vitals.csv", ["Obs_time"]),
}
# Only these columns get their labels printed (drug/unit/event/device names, not patient data).
LABEL_COLS = {
    "medication": ["Drug_name", "Drug_units"],
    "input_output": ["Type"],
    "procedure_events": ["Event_name"],
    "a_line": ["Location", "Laterality"],
    "ventilator": ["Agent"],
}
# Never print labels or scale for these, only counts.
NO_SCALE = {"PID", "Procedure", "Gender", "Age", "Ht", "Wt"}
VASO_RE = re.compile(
    r"phenyl|neo-?syn|ephed|norepi|levophed|epineph|adrenal|vasopres|pitressin|"
    r"dopamin|dobutam|milrinon|isoprot|angiotens|giapreza", re.I)
PLAUSIBLE = {  # (lo, hi) to report % of non-null values in a plausible range
    "nMAP": (20, 200), "MAP_ART": (20, 200), "MAP_FEM": (20, 200),
    "nSBP": (30, 300), "SBP_ART": (30, 300), "nDBP": (10, 200), "DBP_ART": (10, 200),
    "SP02": (50, 100), "HRe": (20, 250), "HRp": (20, 250),
    "Temp": (25, 45), "Temp2": (25, 45),
}


def p(*a):
    print(*a)
    sys.stdout.flush()


def shape(s):
    return re.sub(r"[A-Za-z]", "A", re.sub(r"\d", "9", s))


def band(x):
    if x is None or not np.isfinite(x):
        return "n/a"
    if x == 0:
        return "0"
    e = int(np.floor(np.log10(abs(x))))
    return f"{'-' if x < 0 else ''}1e{e}..1e{e+1}"


def q(series, qs=(0.01, 0.05, 0.5, 0.95, 0.99), nd=1):
    s = pd.Series(series).dropna()
    if s.empty:
        return "n/a"
    return ", ".join(f"p{int(x*100)}={s.quantile(x):.{nd}f}" for x in qs)


def to_minutes(col):
    """Return (float minutes series, kind, parse_rate). Absolute datetimes -> minutes since epoch."""
    s = col.dropna().astype(str).str.strip()
    s = s[s != ""]
    if s.empty:
        return pd.Series(np.nan, index=col.index), "empty", 0.0
    num = pd.to_numeric(col, errors="coerce")
    if num.notna().sum() >= 0.95 * len(s):
        return num.astype(float), "numeric", num.notna().sum() / len(s)
    dt = pd.to_datetime(col, errors="coerce")
    rate = dt.notna().sum() / len(s)
    if rate < 0.95:
        dt2 = pd.to_datetime(col, errors="coerce", format="mixed")
        if dt2.notna().sum() / len(s) > rate:
            dt, rate = dt2, dt2.notna().sum() / len(s)
    mins = (dt - pd.Timestamp("1970-01-01")).dt.total_seconds() / 60.0
    return mins, "datetime", rate


def infer_dtype(col):
    s = col.dropna().astype(str).str.strip()
    s = s[s != ""]
    if s.empty:
        return "empty"
    num = pd.to_numeric(s, errors="coerce")
    r = num.notna().mean()
    if r >= 0.99:
        return "int" if (num.dropna() % 1 == 0).all() else "float"
    if r >= 0.5:
        return f"mostly numeric ({r:.1%} parse)"
    return "string"


def safe(fn, label):
    try:
        fn()
    except Exception as e:  # never echo exception text (could contain values)
        p(f"  !! {label} failed: {type(e).__name__}")


def runs_below(df, tcol, vcol, thr, min_minutes):
    """PIDs having a run of consecutive readings < thr lasting > min_minutes."""
    d = df[["PID", tcol, vcol]].dropna().sort_values(["PID", tcol])
    if d.empty:
        return set()
    flag = d[vcol] < thr
    newrun = (flag != flag.shift()) | (d["PID"] != d["PID"].shift())
    rid = newrun.cumsum()
    g = d[flag].groupby(rid[flag]).agg(PID=("PID", "first"), t0=(tcol, "min"), t1=(tcol, "max"))
    return set(g.loc[(g.t1 - g.t0) > min_minutes, "PID"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--max-labels", type=int, default=400)
    args = ap.parse_args()
    raw = args.raw_dir

    p("# MOVER SIS schema inspection (no patient values)\n")
    data = {}
    for key, (fname, tcols) in FILES.items():
        path = os.path.join(raw, fname)
        if not os.path.exists(path):
            p(f"## {key}: MISSING ({fname})")
            continue
        df = pd.read_csv(path, dtype=str, keep_default_na=True, na_values=["", "NULL", "NaN", "nan"])
        df.columns = [c.strip() for c in df.columns]
        data[key] = df
        p(f"## {key}  ({fname})")
        p(f"- size_bytes={os.path.getsize(path)}  rows={len(df)}  cols={len(df.columns)}")
        p(f"- full-row duplicates: {int(df.duplicated().sum())}")
        p("| column | dtype | null % | distinct |")
        p("|---|---|---|---|")
        for c in df.columns:
            dtp = "time" if c in tcols else infer_dtype(df[c])
            p(f"| {c} | {dtp} | {df[c].isna().mean():.2%} | {df[c].nunique(dropna=True)} |")
        for c in tcols:
            if c in df:
                sh = df[c].dropna().astype(str).map(shape).value_counts()
                tops = "; ".join(f"'{k}' x{v}" for k, v in sh.head(5).items())
                p(f"- time col {c}: shapes(9=digit) {tops}")
        p("")

    pt = data.get("patient_information")
    if pt is None:
        p("No patient_information; stopping.")
        return
    p("## patient_information checks")
    p(f"- distinct PID={pt.PID.nunique()}  rows={len(pt)}  rows with duplicated PID={int(pt.PID.duplicated(keep=False).sum())}")
    tm = {}
    for c in ["OR_start", "OR_end", "Surgery_start", "Surgery_end"]:
        tm[c], kind, rate = to_minutes(pt[c])
        p(f"- {c}: kind={kind} parse_rate={rate:.2%} missing={int(tm[c].isna().sum())}")
    P = pd.DataFrame({"PID": pt.PID, **tm})
    surg = P.Surgery_end - P.Surgery_start
    orl = P.OR_end - P.OR_start
    p(f"- surgery duration min: {q(surg)}")
    p(f"- OR duration min: {q(orl)}")
    p(f"- Surgery_start - OR_start (min): {q(P.Surgery_start - P.OR_start)}")
    p(f"- OR_end - Surgery_end (min): {q(P.OR_end - P.Surgery_end)}")
    p(f"- cases surgery_end<surgery_start: {int((surg < 0).sum())}; OR_end<OR_start: {int((orl < 0).sum())}")
    inside = (P.Surgery_start >= P.OR_start) & (P.Surgery_end <= P.OR_end)
    p(f"- surgery window inside OR window: {inside.mean():.2%}")
    for c in ["Ht", "Wt", "Age"]:
        num = pd.to_numeric(pt[c], errors="coerce")
        p(f"- {c}: numeric parse {num.notna().sum()}/{pt[c].notna().sum()} non-null")
    p("")
    ref = P.drop_duplicates("PID").set_index("PID")
    pids = set(ref.index)

    for key, (fname, tcols) in FILES.items():
        if key == "patient_information" or key not in data:
            continue
        df = data[key]
        p(f"## {key}: linkage and time")

        def link():
            u = set(df.PID.dropna().unique())
            p(f"- distinct PID={len(u)}; rows whose PID in patient_information={df.PID.isin(pids).mean():.2%}; "
              f"cases covered={len(u & pids)}/{len(pids)} ({len(u & pids)/max(len(pids),1):.1%})")
        safe(link, "linkage")
        for c in tcols:
            def tstats(c=c):
                m, kind, rate = to_minutes(df[c])
                df["_t_" + c] = m
                j = df[["PID"]].join(ref[["Surgery_start", "OR_start", "OR_end"]], on="PID")
                rel = m - j.Surgery_start
                df["_rel_" + c] = rel
                p(f"- {c}: kind={kind} parse_rate={rate:.2%}")
                p(f"  minutes from Surgery_start: {q(rel)}")
                ok = m.notna() & j.OR_start.notna() & j.OR_end.notna()
                out = ok & ((m < j.OR_start) | (m > j.OR_end))
                p(f"  rows outside [OR_start, OR_end]: {int(out.sum())}/{int(ok.sum())} ({out.sum()/max(ok.sum(),1):.2%}); "
                  f"before Surgery_start: {(rel < 0).mean():.2%}")
                span = df.groupby("PID")["_t_" + c].agg(lambda s: s.max() - s.min())
                p(f"  per-case span (min): {q(span, (0.5, 0.95))}")
            safe(tstats, f"time {c}")
        p("")

    # Labels for code-like category columns
    for key, cols in LABEL_COLS.items():
        if key not in data:
            continue
        for c in cols:
            vc = data[key][c].fillna("<null>").value_counts()
            p(f"## labels {key}.{c}  ({len(vc)} distinct; showing up to {args.max_labels})")
            for k, v in vc.head(args.max_labels).items():
                p(f"- {k} | {v}")
            p("")

    # Wide measurement tables: sampling interval and scale
    for key in ["vitals", "observations", "ventilator", "labs"]:
        if key not in data or "_t_Obs_time" not in data[key]:
            continue
        df = data[key]
        p(f"## {key}: per-measure coverage, sampling interval, scale")
        p(f"- duplicate (PID, Obs_time) rows: {int(df.duplicated(['PID', 'Obs_time']).sum())}")
        p("| measure | non-null % | cases with any | median gap min | p90 gap min | median band | % plausible |")
        p("|---|---|---|---|---|---|---|")
        for c in df.columns:
            if c in ("PID", "Obs_time") or c.startswith("_"):
                continue
            num = pd.to_numeric(df[c], errors="coerce")
            sub = pd.DataFrame({"PID": df.PID, "t": df["_t_Obs_time"]})[num.notna()].sort_values(["PID", "t"])
            gaps = sub.groupby("PID")["t"].diff()
            med = num.median()
            pl = ""
            if c in PLAUSIBLE and num.notna().any():
                lo, hi = PLAUSIBLE[c]
                pl = f"{num.between(lo, hi).sum()/num.notna().sum():.1%}"
            p(f"| {c} | {num.notna().mean():.1%} | {sub.PID.nunique()} | "
              f"{gaps.median() if gaps.notna().any() else float('nan'):.2f} | "
              f"{gaps.quantile(0.9) if gaps.notna().any() else float('nan'):.2f} | {band(med)} | {pl} |")
        p("")

    med = data.get("medication")
    vaso_pids = set()
    if med is not None:
        p("## medication structure")
        dose = pd.to_numeric(med.Dose, errors="coerce")
        p(f"- Dose numeric parse: {dose.notna().sum()}/{med.Dose.notna().sum()}")
        st, en = med.get("_t_Start_time"), med.get("_t_End_time")
        if st is not None and en is not None:
            d = en - st
            p(f"- End_time null: {en.isna().mean():.2%}; End==Start: {(d == 0).mean():.2%}; "
              f"End>Start: {(d > 0).mean():.2%}; End<Start: {(d < 0).mean():.2%}")
            p(f"- duration where End>Start (min): {q(d[d > 0])}")
        isv = med.Drug_name.fillna("").str.contains(VASO_RE)
        vaso_pids = set(med.loc[isv, "PID"])
        p(f"- vasopressor/inotrope-like rows: {int(isv.sum())}; cases: {len(vaso_pids)}")
        v = med[isv].copy()
        if st is not None and en is not None:
            dd = (en - st)[isv]
            v["_kind"] = np.where(dd.isna() | (dd == 0), "point(bolus?)", np.where(dd > 0, "interval(infusion?)", "neg"))
        else:
            v["_kind"] = "?"
        p("| Drug_name | Drug_units | kind | rows | cases |")
        p("|---|---|---|---|---|")
        g = v.fillna({"Drug_units": "<null>"}).groupby(["Drug_name", "Drug_units", "_kind"]).agg(rows=("PID", "size"), cases=("PID", "nunique"))
        for (dn, du, k), r in g.sort_values("rows", ascending=False).iterrows():
            p(f"| {dn} | {du} | {k} | {r.rows} | {r.cases} |")
        p("")

    # Demo-case candidate counts
    p("## demo case criteria (counts only)")
    crit = {}
    crit["surgery 60-240 min"] = set(ref.index[((ref.Surgery_end - ref.Surgery_start).between(60, 240))])
    if "a_line" in data:
        crit["has a_line"] = set(data["a_line"].PID.dropna())
    crit["has vasopressor"] = vaso_pids
    obs = data.get("observations")
    vit = data.get("vitals")
    if obs is not None and "_t_Obs_time" in obs:
        o = pd.DataFrame({"PID": obs.PID, "t": obs["_t_Obs_time"], "v": pd.to_numeric(obs.MAP_ART, errors="coerce")})
        cnt = o.dropna().groupby("PID").size()
        crit["MAP_ART >=30 readings"] = set(cnt.index[cnt >= 30])
        o = o[o.v.between(20, 200)]
        crit["art MAP<65 >5min"] = runs_below(o, "t", "v", 65, 5)
    if vit is not None and "_t_Obs_time" in vit:
        n = pd.DataFrame({"PID": vit.PID, "t": vit["_t_Obs_time"], "v": pd.to_numeric(vit.nMAP, errors="coerce")})
        n = n[n.v.between(20, 200)]
        crit["nibp MAP<65 >5min"] = runs_below(n, "t", "v", 65, 5)
        s = pd.DataFrame({"PID": vit.PID, "t": vit["_t_Obs_time"], "v": pd.to_numeric(vit.SP02, errors="coerce")})
        s = s[s.v.between(50, 100)]
        crit["any SpO2<90"] = set(s.loc[s.v < 90, "PID"])
        crit["SpO2<90 >1min"] = runs_below(s, "t", "v", 90, 1)
    for k, sset in crit.items():
        p(f"- {k}: {len(sset & pids)}")
    combo = [
        ["surgery 60-240 min", "has a_line", "has vasopressor"],
        ["surgery 60-240 min", "has a_line", "has vasopressor", "art MAP<65 >5min"],
        ["surgery 60-240 min", "has a_line", "has vasopressor", "art MAP<65 >5min", "any SpO2<90"],
        ["surgery 60-240 min", "MAP_ART >=30 readings", "has vasopressor", "art MAP<65 >5min", "SpO2<90 >1min"],
    ]
    for cmb in combo:
        if all(c in crit for c in cmb):
            inter = pids.copy()
            for c in cmb:
                inter &= crit[c]
            p(f"- ALL[{' + '.join(cmb)}]: {len(inter)}")
    p("\ndone.")


if __name__ == "__main__":
    main()
