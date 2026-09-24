# spec.md — MOVER SIS Monitor

## Problem

Researchers need a **local, offline desktop tool** to explore de-identified MOVER SIS
perioperative records (UC Irvine): align ventilator/vitals per surgery (`PID`), surface
transparent rule-based anomaly flags, and discuss **documented management patterns** via
a local LLM—without sending case text to the cloud or implying clinical decision support.

## Intended Users

- Solo / lab researchers and students on a local Linux (or Windows package) workstation
- Users with SIS EMR/wave data on disk and optional Ollama for private Q&A
- **Not** clinicians making real-time care decisions

## Required Behavior

- Load SIS EMR tables from a user-chosen folder; optional wave root; write/read processed
  parquet (`cases`, `timeseries`, `flags`, `episodes`).
- Desktop UI is primary: LLM-first **Ask** tab, plus Summary, Case timeline, Rule reference.
- Case analysis mode: user describes a patient/scenario; app pre-fetches tools then local
  model answers from **grounded** extracts only.
- Ollama lifecycle: Start / Stop / Unload / Refresh; models dir via settings → `OLLAMA_MODELS`.
- Ollama HTTP must default to **loopback only** (case text stays local unless an explicit
  env override is set for remote).
- Answer gate rejects prescriptive clinical directives and unstated case facts where
  implemented in `src/llm/agent.py` / `prompts.py`.
- Pipeline and guardrails validate config/IO; empty or missing processed data fails clearly.
- Each case's data window is the whole OR stay (`OR_start`..`OR_end` ± pad), so induction
  and emergence are included; `t_min` is minutes from incision (negative before it).
- The timeseries has one row per minute per case; minutes without data are empty rows.
  Duration, slope and rolling-window rules are measured in real minutes, never rows.
- Anomaly score counts each flagged minute once at its worst severity and composites per
  episode; cases also carry a score per observed hour.
- Synthetic data generator writes fictional cases (`SYN*` PIDs) in the SIS layout, with
  a truth file of injected anomalies; it never reads real data.
- Profile report is **aggregate only**: no PIDs, timestamps or row-level values, and case
  counts below the small-cell threshold (default 11) are suppressed.
- Optional Streamlit dashboard remains available for browser exploration of the same pipeline.

## User Experience

- **Desktop (primary):** `PYTHONPATH=. python -m src.desktop` or `./scripts/run_desktop.sh`
  or installed `mover-sis-monitor` after `./scripts/install_local.sh`.
- Example: Ask tab (Case analysis) → type `55y woman, hysterectomy, sevoflurane, elevated PIP`
  → co-pilot finds similar PIDs, shows documented management/flags, research disclaimer.
- **Pipeline CLI:** `PYTHONPATH=. python -m src.pipeline.run --n-cases 50 --preset default`
- **Profile (threshold calibration):**
  `PYTHONPATH=. python -m src.pipeline.profile --processed-dir data/processed --out profile.md`
- **Synthetic data (no MOVER access needed):**
  `PYTHONPATH=. python -m src.pipeline.synthetic --out data/synthetic/EMR --n-cases 30`,
  then run the pipeline with `--emr-dir data/synthetic/EMR --output-dir data/synthetic/processed`
- **Streamlit (optional):** `PYTHONPATH=. streamlit run src/dashboard/app.py`
- Settings: gear icon / `Ctrl+,` (paths, Ollama models dir, theme). First-run Setup wizard.
- Themes: light / dark / system. Charts are matplotlib (theme-aware); tall timelines scroll.

## Architecture

- Language/runtime: **Python 3.14** — PINNED for agents and CI/release. (Local verified 3.14.x.)
- Frameworks (minimum versions in `requirements.txt`; not fully upper-bound locked):
  - PySide6 ≥ 6.6 (desktop)
  - pandas / numpy / pyarrow / scipy / matplotlib / PyYAML
  - streamlit ≥ 1.28 (optional UI)
  - pytest / pyinstaller (dev & packaging)
- Storage: local filesystem — EMR CSVs, optional waves, parquet cache; prefs in
  `~/.config/mover-sis-monitor/settings.json` (override with `MOVER_CONFIG_DIR`).
- Major components:
  - `src/pipeline/` — load → clean → merge → features → flags → run
  - `src/pipeline/synthetic.py` — fictional SIS EMR generator; `profile.py` — aggregate report
  - `src/services/` — `ensure_data` / `load_processed`; `case_fetch.py` on-demand shortlist+flags
  - `src/desktop/` — PySide6 app, theme, charts, settings UI
  - `src/llm/` — Ollama client, tools, agent loop, prompts, service lifecycle
  - `src/guardrails/` — limits, config/data/IO validation
  - `src/wave_decode.py` — Bernoulli/GE S5 cpcArchive decode (research waveforms)
  - `src/runtime_paths.py`, `search.py`, `user_settings.py`
  - `packaging/`, `scripts/build_executable.sh`, `scripts/install_local.sh`
- External services:
  - **Ollama** (optional, local HTTP, default `http://127.0.0.1:11434`)
  - No cloud LLM/email required for core product

## Security & Privacy

- No secrets in source. Prefer environment variables / local settings JSON for paths.
- `.streamlit/secrets.toml` is gitignored (Streamlit only if used).
- Case text must not leave the machine via remote Ollama unless explicit override env is set
  (see `src/llm/ollama_client.py` and `tests/test_local_only.py`).
- **Research / education only.** Not a medical device; not clinical decision support.
  Outputs discuss historical de-identified extracts; no live treatment orders.
- Dependencies: pin new deps when added; prefer stdlib; current `requirements.txt` uses
  minimum versions (tighten over time if desired). **Dependabot is not required** for this
  repo unless the human enables it deliberately.
- Versioning: keep **semver `x.y.z`** in sync across `VERSION`, `src/__version__.py`, and
  release tags. After shipping app changes, rebuild with `./scripts/install_local.sh`.

## Validation & Tooling

- Lint: **not configured** (no ruff/flake8 project config). Report-only until added.
- Types: **not configured** (no pyright/mypy project config). Report-only until added.
- Tests (must pass for code changes):
  ```bash
  PYTHONPATH=. QT_QPA_PLATFORM=offscreen pytest -q
  ```
  CI equivalent: `PYTHONPATH=. pytest --cov=src` with `QT_QPA_PLATFORM=offscreen`.
- Thresholds smoke:  
  `PYTHONPATH=. python -c "from src.config import load_thresholds; load_thresholds('default')"`
- Packaging checks: `PYTHONPATH=. pytest tests/test_packaging.py -q`
- Quirks: desktop/Qt tests need system Qt libs on CI; skip setup wizard under
  `QT_QPA_PLATFORM=offscreen` or `MOVER_SKIP_SETUP=1`.

## Project workflow (spec overrides / alignment with agents.md)

- **Push / open PRs only** when the human explicitly asks in the current conversation
  (do not open PRs “for convenience”).
- Prefer task branches when committing; default branch should stay green.
- Update `context.md` (state + handoff) and `CHANGELOG.md` (user-visible history) per `agents.md`.
- Do not put policy rules in `context.md` or `CHANGELOG.md`.

## Acceptance Criteria

- [ ] Desktop launches and shows Ask / Summary / Timeline / Rules for configured data paths.
- [ ] Pipeline produces parquet under processed dir for a small `--n-cases` sample.
- [ ] On synthetic data every injected anomaly raises its expected rules and clean cases
      raise no warn/critical flags (`tests/test_synthetic.py`).
- [ ] Profile output contains no PIDs or timestamps (`tests/test_profile.py`).
- [ ] LLM path refuses non-loopback Ollama URLs without override (`tests/test_local_only.py`).
- [ ] `PYTHONPATH=. QT_QPA_PLATFORM=offscreen pytest -q` exits 0.
- [ ] `VERSION` matches `src/__version__.py`.
- [ ] `CHANGELOG.md` updated for user-visible changes.
- [ ] CI runs on **Python 3.14**.
