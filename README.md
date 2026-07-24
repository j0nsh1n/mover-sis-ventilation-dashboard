# MOVER SIS — Intraoperative Ventilation & Anesthesia Dashboard

Research dashboard for the [MOVER](https://mover.ics.uci.edu/) **SIS** perioperative dataset (UC Irvine, 2015–2017). Visualizes ventilator settings, ETCO₂, and volatile anesthetic concentration over each surgery, with transparent rule-based anomaly flags.

> De-identified public research data. **Not for clinical care.**

## What you get

1. **Data pipeline** — load/clean SIS tables, time-align ventilator + vitals per surgery (`PID`), derive TV mL/kg & MAC, write parquet caches  
2. **Anomaly flags** — rising PIP, ETCO₂ out of range, agent drift/high MAC, SpO₂, composite patterns (see `docs/DESIGN.md` and `src/config/thresholds.yaml`)  
3. **Streamlit app** — multi-case summary + per-case multi-panel timeline  

## Data location

Place or extract SIS EMR CSVs here:

```
data/raw/EMR/
  patient_information.csv
  patient_ventilator.csv
  patient_vitals.csv
  patient_procedure_events.csv   # optional
  patient_observations.csv       # optional
  ...
```

From the MOVER archive:

```bash
mkdir -p data/raw
tar -xzf /path/to/sis_emr.tar.gz -C data/raw
```

**Note:** `patient_ventilator.csv` in the public dump has **1,048,575** rows (classic export ceiling) and may not cover every case that appears in vitals.

## Setup

```bash
cd "Monitoring Dashboard (MOVER SIS)"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run the pipeline (sample of cases)

```bash
# From repo root, with PYTHONPATH=.
PYTHONPATH=. python -m src.pipeline.run --n-cases 50 --preset default
```

Outputs under `data/processed/`:

| File | Content |
|------|---------|
| `timeseries.parquet` | Minute-aligned vent + vitals + features |
| `cases.parquet` | Per-surgery summary + anomaly scores |
| `flags.parquet` | Long-form minute flags |
| `episodes.parquet` | Contiguous flag runs |
| `run_meta.json` | Sample PID list and counts |

## Run the dashboard

```bash
PYTHONPATH=. streamlit run src/dashboard/app.py
```

The app will run the pipeline automatically if processed files are missing.

## Project layout

```
docs/DESIGN.md              # Pipeline design, thresholds, UI plan
src/config/thresholds.yaml  # Tunable clinical ranges
src/pipeline/               # load → clean → merge → features → flags
src/dashboard/app.py        # Streamlit UI
```

## Key SIS column quirks handled

- Null token `\N`
- Typos `ETC02` / `SP02` / `FI02` → ETCO₂ / SpO₂ / FiO₂
- Agent codes: `S` sevoflurane, `D` desflurane, `I` isoflurane, `N` none
- Mixed datetime formats
- `PID` = surgery ID (not longitudinal patient ID)

## Testing & guardrails

Safety is enforced in code, not only by convention.

### Run tests

```bash
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. pytest
# with coverage:
PYTHONPATH=. pytest --cov=src --cov-report=term-missing
```

CI runs the same suite on every push/PR (`.github/workflows/ci.yml`).

### What is guarded

| Layer | Behavior |
|-------|----------|
| **Config** | `thresholds.yaml` schema, warn/critical ordering, clean range low&lt;high, MAC values |
| **CLI params** | `n_cases`, `preset`, `vent_scan_rows`, `min_vent_rows`, `pad_minutes` bounds |
| **Paths** | Required EMR files present & non-empty; refuse writes outside the repo unless `MOVER_ALLOW_EXTERNAL_OUTPUT=1` |
| **Stage checks** | Raw/cleaned schemas, non-empty vent after filter, sorted unique minute timeseries |
| **Outputs** | Case/flag/episode invariants; scores non-negative; flag PIDs ⊆ case PIDs |
| **Writes** | Atomic parquet/JSON (temp + replace) to avoid half-written caches |
| **Dashboard** | Re-validates processed parquets before serving; catches guardrail errors in UI |

Custom exceptions live under `src/guardrails/` (`ConfigValidationError`, `DataValidationError`, `SafetyLimitError`, `PathSafetyError`, `PipelineError`).

Disable stage validation only if you must: `python -m src.pipeline.run --no-validate` (not recommended).

### Next steps

1. Increase `--n-cases` or pass explicit PIDs once you are happy with thresholds  
2. Tune `src/config/thresholds.yaml` (or use `strict` / `lenient` presets)  
3. Optionally join `patient_observations` (art line, SVV, cerebral oximetry) on the same minute grid  
4. Add export of flag reports for offline review  

See **`docs/DESIGN.md`** for full design rationale and rule definitions.
