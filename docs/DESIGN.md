# MOVER SIS Ventilation & Anesthesia Depth Dashboard — Design

> **Historical design document** (kept for pipeline/flag rationale and data quirks).  
> It describes the original Streamlit + Plotly plan. **Primary UI is now the PySide6
> desktop app** with an optional Streamlit view; charts are matplotlib.  
> Dataset years: EMR `OR_start` mass is largely **2015–2018** (not only 2015–2017).  
> Product contract: `spec.md`. Current state: `context.md`. Thresholds of record:
> `src/config/thresholds.yaml`.

## Goals

Build a Python dashboard that:

1. Loads and cleans SIS perioperative tables (ventilator, vitals, case metadata).
2. Time-aligns ventilator + vitals per surgery (`PID` = surgery ID, not patient).
3. Plots a per-case timeline of ventilation and anesthetic agent concentration.
4. Flags simple rule-based anomalies (compliance, ETCO₂, agent drift, etc.).
5. Aggregates flags across cases for a multi-case summary view.

Original stack in this doc: **pandas** + **Streamlit** + **Plotly** (see banner above).

---

## 1. Data cleaning / merging pipeline

### Source tables (SIS EMR)

| File | Role | Key columns |
|------|------|-------------|
| `patient_information.csv` | Case metadata | `PID`, `Age`, `Ht`, `Wt`, `Gender`, `OR_start`, `OR_end`, `Surgery_start`, `Surgery_end`, `Procedure` |
| `patient_ventilator.csv` | Vent + agent (≈1 min) | `PID`, `Obs_time`, `Agent`, `Agent_Fi`, `Agent_Et`, `ETC02`, `TV`, `RR`, `PEEP`, `PIP`, `FI02`, `ET02`, `FIN20`, `ETN20`, `FIC02` |
| `patient_vitals.csv` | Vitals (≈1 min) | `PID`, `Obs_time`, `HRe`, `HRp`, `nSBP`, `nMAP`, `nDBP`, `SP02` |
| `patient_observations.csv` (optional) | Art line / SVV / rSO₂ | `PID`, `Obs_time`, `SVV`, `SBP_ART`, `MAP_ART`, `DBP_ART`, `Cer_ox_*`, `Temp` |
| `patient_procedure_events.csv` (optional) | Phase markers | `PID`, `Event_time`, `Event_name` (Intubation, Extubation, …) |
| `patient_a_line.csv` (optional) | Line placement | `PID`, `PlacementTime`, `Location`, `Laterality` |

**Important quirks observed in the actual files:**

- Nulls are the literal string `\N` (MySQL dump style), not empty cells.
- Column names use `0` for `O` in several places: `ETC02`, `SP02`, `FI02`, `ET02`, `FIN20`, `ETN20` (treat as ETCO₂, SpO₂, FiO₂, EtO₂, FiN₂O, EtN₂O).
- Timestamps are mixed formats (`M/D/YY H:MM` vs `YYYY-MM-DD HH:MM:SS`).
- `patient_ventilator.csv` has **1,048,575 rows** (Excel/export row ceiling) — may be truncated relative to all cases; vitals has ~3.6M rows.
- `PID` uniquely identifies a **surgery** (19,114 cases); no longitudinal patient ID in SIS.
- `Agent` codes: `S` = sevoflurane, `D` = desflurane, `I` = isoflurane, `N` = none / no volatile.
- Agent concentrations are vol % (typical sevo maintenance ~1–3%, iso ~0.5–1.5%, des ~3–7%).
- TV is in **mL**, pressures in **cmH₂O**, gas fractions mostly **%** (FiO₂ ~21–100), ETCO₂ in **mmHg**.
- Sampling is typically **1 sample/minute** for both vent and vitals.

### Pipeline stages

```
raw CSVs
  → load_raw (na_values=["\\N"], dtype hints)
  → clean_case_info (parse times, duration, BMI, filter absurd durations)
  → clean_ventilator (numeric coerce, rename columns, physiologic clamps)
  → clean_vitals (numeric coerce, rename, clamps)
  → filter_to_case_window (clip to OR_start..OR_end or Surgery_*)
  → time_align_merge (outer asof / minute-bin join on PID + time)
  → derive_features (PIP slope, TV/IBW, age-adjusted MAC, minutes from start)
  → flag_anomalies (rule engine)
  → case_flag_summary (per-PID aggregates)
  → optional parquet cache for dashboard
```

### Cleaning rules (numeric)

| Signal | Keep range (soft physiologic) | Notes |
|--------|-------------------------------|-------|
| TV (mL) | 50–1500 | Clip/null outside; flag high/low separately |
| RR | 4–40 | |
| PIP (cmH₂O) | 0–50 | Negative PIP → null |
| PEEP (cmH₂O) | 0–25 | Negative → 0 or null |
| ETCO₂ (mmHg) | 5–80 | 0 often means disconnected / pre-intubation |
| FiO₂ (%) | 21–100 | |
| Agent_Et / Agent_Fi (%) | 0–12 | >12 almost certainly artifact |
| HR | 30–200 | |
| SpO₂ (%) | 50–100 | |
| NIBP MAP | 30–160 | |

Also:

1. Parse all datetimes with `pd.to_datetime(..., format="mixed")`.
2. Sort by `(PID, Obs_time)`; drop exact duplicate timestamps (keep last).
3. Restrict rows to `[OR_start − 5 min, OR_end + 5 min]` when metadata exists.
4. Build `t_min` = minutes from case start (prefer `Surgery_start`, else `OR_start`, else first vent sample).
5. Ideal body weight (Devine) for mL/kg TV when height available.

### Time-aligned merge strategy

Because both tables are ~1 min cadence:

1. Floor each observation to the nearest minute: `time_bin = Obs_time.dt.floor("min")`.
2. Aggregate duplicates within a bin (median for continuous signals; mode for Agent).
3. Outer-merge ventilator and vitals on `(PID, time_bin)`.
4. Optionally left-join observations on the same bin (sparse).
5. Left-join case metadata once per PID.

This is simpler and more robust than `merge_asof` for equal-rate streams, and preserves gaps.

### Intermediate products

| Artifact | Description |
|----------|-------------|
| `data/processed/cases.parquet` | One row per PID (demographics, times, duration, agent mode, flag counts) |
| `data/processed/timeseries/{PID}.parquet` **or** single `timeseries.parquet` partitioned / filtered | Minute-aligned multi-signal frame |
| `data/processed/flags.parquet` | Long-form flag events: PID, time, rule_id, severity, value |

For interactive use, default path: process a **sample of N cases** (or all if filtered) into one `timeseries.parquet` + `cases.parquet` + `flags.parquet`.

---

## 2. Anomaly flag threshold rules

Rules are intentionally simple, transparent, and grounded in common intraoperative ranges. They are **screening flags**, not diagnoses. All thresholds live in `src/config/thresholds.yaml` so clinicians can tune them.

### Severity

- **info**: worth noting, often context-dependent  
- **warn**: outside typical maintenance range  
- **critical**: extreme / sustained / multi-signal concern  

A flag fires on a **minute** if the condition is true. Case-level summary counts minutes and episodes (contiguous runs).

### Ventilation rules

| Rule ID | Condition | Default thresholds | Rationale |
|---------|-----------|--------------------|-----------|
| `pip_high` | PIP ≥ high | **warn ≥ 30**, **critical ≥ 35** cmH₂O | Barotrauma risk; common alarm band |
| `pip_rising` | Rolling slope of PIP over W minutes ≥ δ | W=10 min, δ=**0.5 cmH₂O/min** sustained ≥ 5 min, and PIP ≥ 20 | Suggests decreasing compliance / tube kink / light anesthesia / abdominal insufflation |
| `peep_high` | PEEP ≥ high | **warn ≥ 12**, **critical ≥ 15** cmH₂O | Unusual outside ARDSnet-style strategies |
| `peep_zero_long` | PEEP ≤ 0 for ≥ N min during mechanical ventilation | N=**15** min, requires TV≥200 and RR≥6 | Prolonged ZEEP |
| `tv_low_mlkg` | TV / IBW < low | **warn < 4**, **critical < 3** mL/kg | Hypoventilation / disconnect / pediatric mix-up |
| `tv_high_mlkg` | TV / IBW > high | **warn > 10**, **critical > 12** mL/kg | Volutrauma risk (protective target often 6–8) |
| `tv_abs_extreme` | TV outside absolute band | <100 or >1000 mL (adults Age≥16) | Artifact or severe mis-setting |
| `rr_high` / `rr_low` | RR outside band | low **< 6**, high **> 20** (warn); **> 30** critical | While mechanically ventilated |
| `etco2_high` | ETCO₂ ≥ high | **warn ≥ 50**, **critical ≥ 60** mmHg | Hypoventilation, MH early sign context, CO₂ insufflation |
| `etco2_low` | ETCO₂ ≤ low (and ETCO₂ > 0) | **warn ≤ 28**, **critical ≤ 22** mmHg | Hyperventilation, low CO₂ production, PE consideration |
| `etco2_zero_vent` | ETCO₂ == 0 while TV≥200 & RR≥8 for ≥ 3 min | — | Circuit disconnect / esophageal intubation / sensor fail |
| `fio2_high_long` | FiO₂ ≥ 90% for ≥ 30 min after first 15 min of case | — | Prolonged high FiO₂ |
| `fio2_room_air_vent` | FiO₂ ≤ 25% while TV≥200 & RR≥6 for ≥ 5 min | — | Unexpected on ventilator |

### Anesthetic agent rules

MAC equivalents (approx., age 40): sevo **1.8%**, iso **1.2%**, des **6.6%**. Age-adjust: MAC_age ≈ MAC_40 × 10^(−0.00269 × (age−40)).

| Rule ID | Condition | Default | Rationale |
|---------|-----------|---------|-----------|
| `agent_high` | age-adj MAC_Et ≥ | **warn ≥ 1.5 MAC**, **critical ≥ 2.0 MAC** | Overdose / vaporizer error |
| `agent_low_maint` | During mid-case window (20–80% of duration), MAC_Et < 0.3 and Agent ≠ N, and no N₂O Et ≥ 30% | — | Possible awareness risk if GA intended (crude) |
| `agent_drift` | \|Agent_Et − rolling median(Agent_Et, 15 min)\| ≥ | **0.5 vol%** (sevo/iso) or **1.5 vol%** (des) for ≥ 5 min | Unexpected concentration drift |
| `agent_fi_et_gap` | Agent_Fi − Agent_Et ≥ | **1.0 vol%** (sevo/iso) or **3.0** (des) for ≥ 10 min mid-case | Uptake / leak / fresh circuit |
| `agent_switch` | Agent code changes mid-case more than once after first non-N | info | Document agent changes |

### Vitals (supporting, not primary)

| Rule ID | Condition | Default |
|---------|-----------|---------|
| `spo2_low` | SpO₂ ≤ | **warn ≤ 92**, **critical ≤ 88** |
| `hr_high` / `hr_low` | HR | **> 120** / **< 45** warn; **> 140** / **< 40** critical |
| `map_low` | nMAP ≤ | **warn ≤ 60**, **critical ≤ 50** mmHg |

### Composite / pattern flags

| Rule ID | Logic |
|---------|--------|
| `compliance_concern` | `pip_rising` AND (TV stable or falling) within same 10-min window |
| `hypoventilation_pattern` | `etco2_high` AND (`tv_low_mlkg` OR `rr_low`) |
| `desat_with_vent_issue` | `spo2_low` AND (`pip_high` OR `etco2_zero_vent` OR `tv_low_mlkg`) |

### Case-level score

```
anomaly_score = 1*n_warn_minutes + 3*n_critical_minutes + 5*n_composite_episodes
```

Rank cases by score for the summary view. Also report top rule_ids by frequency.

---

## 3. Dashboard layout (Streamlit)

### Global chrome

- **Title**: MOVER SIS — Intraoperative Ventilation & Anesthesia Monitor  
- **Sidebar**:
  - Data path / “use sample cache”
  - Case filters: age band, procedure text search, duration, agent type, min anomaly score
  - Threshold preset: Default / Strict / Lenient (loads YAML)
  - Sample size control for first-time processing

### Page A — Multi-case summary (landing)

1. **KPI row**: # cases loaded, # with any warn/critical, median duration, most common agent  
2. **Bar chart**: top 15 cases by `anomaly_score`  
3. **Stacked bar / heatmap**: flag counts by `rule_id` across cohort  
4. **Table**: sortable case list (PID, procedure snippet, duration, agent, #flags, score) → click selects case for Page B  
5. **Scatter** (optional): duration vs anomaly_score, color by agent  

### Page B — Single-case timeline

1. **Header**: PID, age/sex/BMI, procedure, OR times, primary agent, score  
2. **Multi-panel Plotly figure** (shared x = minutes from start):
   - Panel 1: TV (mL) + TV mL/kg  
   - Panel 2: PIP + PEEP  
   - Panel 3: ETCO₂ (+ optional FiCO₂)  
   - Panel 4: Agent_Et (+ Agent_Fi), MAC guide lines  
   - Panel 5 (optional toggle): HR, SpO₂, nMAP  
3. **Flag markers**: vertical spans or scatter markers on panels where rules fire; color by severity  
4. **Event markers**: Intubation / Extubation from procedure_events  
5. **Flag table** for the case: time, rule, severity, value, message  
6. **Episode summary**: contiguous runs collapsed (“PIP rising 12:04–12:19”)

### Page C — Rule reference

- Human-readable list of all rules + current thresholds (from YAML)  
- Disclaimer: research/education tool on de-identified public data; not for clinical care  

### UX notes

- Cache processed parquets with `@st.cache_data`.  
- Default open on Summary; deep-link case via query params if easy.  
- Prefer dark-friendly clinical palette: cyan TV, amber PIP, green ETCO₂, purple agent, red flags.

---

## 4. Project layout

```
Monitoring Dashboard (MOVER SIS)/
├── data/
│   ├── raw/EMR/                 # extracted SIS CSVs
│   ├── sample/                  # small PID subset for fast iteration
│   └── processed/               # parquet outputs
├── docs/
│   └── DESIGN.md                # this file
├── src/
│   ├── config/
│   │   └── thresholds.yaml
│   ├── pipeline/
│   │   ├── load.py
│   │   ├── clean.py
│   │   ├── merge.py
│   │   ├── features.py
│   │   ├── flags.py
│   │   └── run.py
│   └── dashboard/
│       ├── app.py
│       ├── pages/               # optional multipage
│       └── components.py
├── notebooks/                   # optional EDA
├── requirements.txt
└── README.md
```

## 5. Build order

1. ✅ Design (this doc)  
2. Scaffold package + thresholds YAML  
3. Implement load → clean → merge on a **sample of real PIDs**  
4. Implement flag engine + case summary  
5. Streamlit summary + case timeline  
6. Iterate thresholds against real distributions  

---

## References (clinical ranges — standard teaching values)

- Lung-protective TV often 6–8 mL/kg IBW; PIP commonly kept <30 cmH₂O when feasible.  
- ETCO₂ maintenance often ~35–45 mmHg; alerts commonly around <30 or >50.  
- MAC values: sevoflurane ~1.8%, isoflurane ~1.2%, desflurane ~6.6% (age 40).  
- SpO₂ alarms commonly 92% / 88% in many OR protocols (context-dependent).  

These are **defaults for research visualization**, not institutional protocol.
