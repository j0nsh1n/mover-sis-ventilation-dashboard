# MOVER SIS — Intraoperative Ventilation & Anesthesia Monitor

**Version 0.9.0**

Research **desktop app** (and optional web UI) for the [MOVER](https://mover.ics.uci.edu/) **SIS** perioperative dataset (UC Irvine; OR dates largely **2015–2018**). Visualizes ventilator settings, ETCO₂, and volatile anesthetic concentration over each surgery, with transparent rule-based anomaly flags.

> De-identified public research data. **Not for clinical care.**

## What you get

1. **Data pipeline** — load/clean SIS tables, time-align ventilator + vitals per surgery (`PID`), derive TV mL/kg & MAC, write parquet caches  
2. **Anomaly flags** — rising PIP, ETCO₂ out of range, agent drift/high MAC, SpO₂, composite patterns (see `docs/DESIGN.md` and `src/config/thresholds.yaml`)  
3. **Desktop app (primary)** — native PySide6 window with setup-first entry, AI research search, focused signal inspection, summary charts, case timeline, and rule reference
4. **Streamlit UI (optional)** — browser views for the existing pipeline

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
# Python 3.14 (see .python-version / spec.md)
python3.14 -m venv .venv   # or: python3 -m venv .venv if 3.14 is default
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
| `timeseries.parquet` | One row per minute (gap minutes empty): vent + vitals + features |
| `cases.parquet` | Per-surgery summary + anomaly scores (total and per observed hour) |
| `flags.parquet` | Long-form minute flags |
| `episodes.parquet` | Contiguous flag runs |
| `events.parquet` | Optional procedure events aligned to a surgery |
| `run_meta.json` | Sample PID list and counts |

### Score every surgery (full-EMR scan)

The pipeline above analyzes a sample and keeps its minute-level data for charts.
To score every surgery with ventilator data, use **File → Scan full EMR** or:

```bash
PYTHONPATH=. python -m src.pipeline.corpus_scan --preset default
```

It processes the export in batches and keeps only per-case scores and flag episodes
(`corpus_cases.parquet`, `corpus_episodes.parquet`, `corpus_meta.json`), so memory
stays bounded. On the full MOVER SIS export expect it to take over an hour. The
co-pilot then answers whole-dataset questions from the scan and says so.

### Profile the output (threshold calibration)

```bash
PYTHONPATH=. python -m src.pipeline.profile --processed-dir data/processed --out profile.md
```

Writes aggregates only: ventilator/vitals gap statistics, signal percentiles, and for
each rule how often it fired and what share of observed minutes lie past its warn and
critical thresholds. No PIDs or timestamps; case counts under 11 print as `<11`.
Check your MOVER DUA before sharing even aggregate output.

### No MOVER data yet?

Generate fictional cases with the same file layout and quirks (`\N` nulls, `ETC02`
column names, mixed timestamps, ventilator gaps). `synthetic_truth.json` lists the
anomalies injected into each case.

```bash
PYTHONPATH=. python -m src.pipeline.synthetic --out data/synthetic/EMR --n-cases 30
PYTHONPATH=. python -m src.pipeline.run --emr-dir data/synthetic/EMR \
    --output-dir data/synthetic/processed --n-cases 30
```

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
- Opens **Setup** before loading data if the EMR folder is missing or invalid
- Starts in **Research**, where a question retrieves indexed surgeries using local embeddings or keyword fallback. A selected local chat model can answer from bounded case extracts.
- Lets you select a retrieved case and open **F signal detail** with PIP, ETCO₂, heart rate, and source rows. Returning preserves the question and comparison.
- Keeps the **Ask** co-pilot for case analysis, plus Summary, Case timeline, and Rule reference
- Provides **Settings** (`Ctrl+,`) for EMR, Wave, Processed, chat and embedding models, and theme
- **Theme:** light, dark, or system  
- Loads processed caches after setup; **File → Run pipeline** for rebuilds
- Research-only (not clinical care)

### Optional: Streamlit (browser)

```bash
PYTHONPATH=. streamlit run src/dashboard/app.py
```

## Versioning and releases

- Source of truth: top-level [`VERSION`](VERSION) file (`0.9.0`, semver `x.y.z`)
- Agent policy: [`agents.md`](agents.md) + project contract [`spec.md`](spec.md); state in [`context.md`](context.md); history in [`CHANGELOG.md`](CHANGELOG.md)
- UI **About** and window title show the same version
- **On a `VERSION` change merged to `main`** (or a manual run): GitHub Actions workflow
  [`.github/workflows/release.yml`](.github/workflows/release.yml) builds Linux
  and Windows packages and publishes both in a GitHub Release (Python **3.14**).
- [Desktop updates](docs/UPDATES.md): the installed app checks a signed
  manifest on GitHub Pages about once a minute and downloads verified packages
  from public GitHub Releases. Install v0.9.0 manually once if you have v0.8.0
  or an older build. Set `MOVER_UPDATE_MANIFEST_URL` to an empty string to
  disable automatic checks.

### Local LLM (Ollama)

The **Research** and **Ask** tabs use Ollama on this machine. Research searches indexed surgeries even when Ollama is stopped. Select a local chat model to generate an answer from retrieved case extracts. The Ask tab keeps the existing case-analysis tools. Models stay on the host, outside the executable.

Configure the **models directory** (e.g. `/var/mnt/games/LLM_Models`) via the **⚙ Settings** icon (`Ctrl+,`) or first-run **Setup**; the app exports `OLLAMA_MODELS` when starting the server.

```bash
# Install Ollama once: https://ollama.com
# Pull at least one model (examples already on many machines):
ollama pull gemma4
```

Then in the app:
1. Complete Setup with a valid EMR folder.
2. In **Research**, ask a question and inspect a retrieved case. Start and select a local chat model for an answer.
3. For the older case-analysis flow, open **2 · Ask** and ask about a selected surgery.

Answers are grounded in a structured case briefing. Research use only — not for clinical care.

### Build & install on this computer (Nobara)

```bash
source .venv/bin/activate
# Build + install frozen app to ~/.local/share/mover-sis-monitor
./scripts/install_local.sh
# Launch
mover-sis-monitor
```

Or build only:

```bash
./scripts/build_executable.sh
# → dist/MOVER-SIS-Monitor-v0.9.0-linux-x86_64.tar.gz
```

| Path | Description |
|------|-------------|
| `dist/MOVER-SIS-Monitor/MOVER-SIS-Monitor` | GUI binary |
| `dist/MOVER-SIS-Monitor/VERSION` | Stamped `0.9.0` |
| `dist/MOVER-SIS-Monitor-v0.9.0-linux-x86_64.tar.gz` | Portable archive |
| `~/.local/share/mover-sis-monitor/` | Local install (via `install_local.sh`) |

If the binary fails to start on a minimal install:

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

The desktop updater has a separate Rust test crate. It drives the Python
updater and Qt controller through a small probe, with scenarios and assertions
in Rust. Run it on Linux after installing the Python dependencies:

```bash
cd rust-updater-tests
PYTHONPATH=.. QT_QPA_PLATFORM=offscreen cargo test --workspace --locked
```

CI runs the same suite on every push/PR (`.github/workflows/ci.yml`), including:

- Pipeline / guardrail unit + integration tests  
- **Desktop app** offscreen smoke (window construct, data load, chart render, startup budget)  
- **Updater** Rust contract tests for manifest checks, staging, rollback, and Qt polling
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

## License

Copyright (C) 2026 Jonathan Shin

This program is free software: you can redistribute it and/or modify it under the
terms of the **GNU General Public License v3.0** as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but **WITHOUT ANY
WARRANTY**; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the [GNU General Public License](LICENSE) for more details.

GPL-3.0 is compatible with the app's dependencies (PySide6 is available under the
LGPL). Note the separate, stronger restriction that applies regardless of licence:

> **Research and education only — not a medical device, and not for clinical care.**

The MOVER SIS dataset itself is *not* covered by this licence; it remains subject to
the [MOVER data use agreement](https://mover.ics.uci.edu/) from UC Irvine.
