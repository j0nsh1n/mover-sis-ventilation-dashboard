# MOVER SIS — Intraoperative Ventilation & Anesthesia Monitor

Research **desktop app** (and optional web UI) for the [MOVER](https://mover.ics.uci.edu/) **SIS** perioperative dataset (UC Irvine, 2015–2017). Visualizes ventilator settings, ETCO₂, and volatile anesthetic concentration over each surgery, with transparent rule-based anomaly flags.

> De-identified public research data. **Not for clinical care.**

## What you get

1. **Data pipeline** — load/clean SIS tables, time-align ventilator + vitals per surgery (`PID`), derive TV mL/kg & MAC, write parquet caches  
2. **Anomaly flags** — rising PIP, ETCO₂ out of range, agent drift/high MAC, SpO₂, composite patterns (see `docs/DESIGN.md` and `src/config/thresholds.yaml`)  
3. **Desktop app (primary)** — native PySide6 window: summary charts, case timeline, rule reference  
4. **Streamlit UI (optional)** — same views in the browser if you prefer  

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

## Launch the desktop app

```bash
source .venv/bin/activate
pip install -r requirements.txt

# From repo root
./scripts/run_desktop.sh
# or
PYTHONPATH=. python -m src.desktop
```

Optional: install a Linux app-menu launcher for your user:

```bash
./scripts/install_desktop_entry.sh
```

Then open **MOVER SIS Ventilation Monitor** from your application menu.

The desktop app:

- Opens a **native window** (not a browser tab)
- Lets you **choose any SIS data/EMR folder** (Browse… / File → Open data folder); choice is remembered
- Loads existing processed caches on startup when present
- Runs the pipeline in a **background thread** when you click **Run / reload pipeline**
- Shows organized Summary, Case timeline, and Rule reference tabs with metric cards and path status

### Optional: Streamlit (browser)

```bash
PYTHONPATH=. streamlit run src/dashboard/app.py
```

## Build a Linux executable (Nobara / Fedora x86_64)

Produces a standalone **folder** (recommended for QtWebEngine) plus a `.tar.gz`:

```bash
source .venv/bin/activate
./scripts/build_executable.sh
```

Artifacts:

| Path | Description |
|------|-------------|
| `dist/MOVER-SIS-Monitor/MOVER-SIS-Monitor` | GUI binary |
| `dist/MOVER-SIS-Monitor/data/raw/EMR/` | Drop SIS CSVs here |
| `dist/MOVER-SIS-Monitor-linux-x86_64.tar.gz` | Portable archive |

```bash
# After build
cd dist/MOVER-SIS-Monitor
# copy your EMR CSVs into data/raw/EMR/
./MOVER-SIS-Monitor
```

Optional desktop menu entry from the built folder:

```bash
cp dist/MOVER-SIS-Monitor/mover-sis-monitor.desktop ~/.local/share/applications/
# edit Exec=/Path= if you move the folder
```

If the binary fails to start on a minimal install, install common GUI deps:

```bash
sudo dnf install -y mesa-libGL libxkbcommon xcb-util-cursor \
  xcb-util-wm xcb-util-keysyms xcb-util-image xcb-util-renderutil \
  libnsl libxcrypt-compat
```

## Project layout

```
docs/DESIGN.md              # Pipeline design, thresholds, UI plan
src/config/thresholds.yaml  # Tunable clinical ranges
src/pipeline/               # load → clean → merge → features → flags
src/services/               # UI-agnostic data loading
src/desktop/                # PySide6 desktop application
src/dashboard/app.py        # Optional Streamlit UI
scripts/run_desktop.sh      # Desktop launcher
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
PYTHONPATH=. QT_QPA_PLATFORM=offscreen pytest
# with coverage:
PYTHONPATH=. QT_QPA_PLATFORM=offscreen pytest --cov=src --cov-report=term-missing
```

CI runs the same suite on every push/PR (`.github/workflows/ci.yml`), including:

- Pipeline / guardrail unit + integration tests  
- **Desktop app** offscreen smoke (window construct, data load, chart render, startup budget)  
- **Packaging** structure checks (spec, entrypoint, build script); frozen binary smoke runs when `dist/` exists  

`./scripts/build_executable.sh` also smoke-tests the frozen binary under offscreen Qt after build.

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
