# MOVER SIS Monitor — Living Project Context

**Purpose:** Keep agents and humans aligned on structure, behavior, and process.  
**Version file:** `VERSION` (semver `x.y.z`). Current release line: see that file.

---

## Maintenance rules (mandatory)

1. **Version numbers**
   - Always use **semver `x.y.z`** (e.g. `0.2.0`).
   - **Every user-facing change set that ships** must bump `VERSION` and keep in sync:
     - `VERSION`
     - `src/__version__.py` (`__version__`)
     - UI strings that hard-code a version (prefer `get_version()`)
     - Release notes / GitHub release tag `v{x.y.z}` when releasing
   - **Patch** `0.2.1` — fixes only. **Minor** `0.3.0` — features. **Major** `1.0.0` — breaking.

2. **Build the executable when shipping**
   - After meaningful app changes, run:
     ```bash
     ./scripts/install_local.sh
     ```
     (rebuilds PyInstaller onedir + installs to `~/.local/share/mover-sis-monitor`).
   - For GitHub: merge to `main` triggers `.github/workflows/release.yml` (Release asset).
   - Do **not** leave the local install on an older binary after a feature lands.

3. **Context document hygiene**
   - This file is the **durable** context: architecture, data layout, how to run, release rules.
   - After updates, **delete or rewrite outdated narrative** (old bugs, temporary workarounds, stale “next steps”).
   - **Keep** structural facts: tables, pipeline stages, UI tabs, path resolution, LLM grounding, safety rules.
   - Prefer short bullets over long history. Chat logs are not a substitute for this file.

4. **PRs**
   - Prefer feature branches + PRs for non-trivial changes (user preference).
   - CI must stay green (Qt system libs on Ubuntu; `QT_QPA_PLATFORM=offscreen` for GUI tests).

---

## What the product is

Research **desktop app** for **MOVER SIS** perioperative data (UCI, 2015–2017).  
De-identified; **not for clinical care**.

- **EMR** = tabular SIS CSVs (`patient_*.csv`) — separate from  
- **Wave** = waveform archives (`sis_wave*.tar.gz` or `Waveforms/<prefix>/<PID>/`)

`PID` = **surgery** ID in SIS (not longitudinal patient ID).

---

## Architecture (high level)

```
src/
  pipeline/     load → clean → merge → features → flags → run
  services/     ensure_data / load_processed
  desktop/      PySide6 UI (primary)
  dashboard/    optional Streamlit
  llm/          Ollama client, case context, prompts, service lifecycle
  guardrails/   config/IO/data validation
  runtime_paths.py   EMR / wave / processed path resolution
  search.py     keyword case filter
  user_settings.py   persisted prefs (~/.config/mover-sis-monitor/)
```

**Processed cache:** `cases`, `timeseries`, `flags`, `episodes` parquet under configured `processed_dir`.

**UI tabs:** Summary · Case timeline · Rule reference · **Ask about case** (local LLM).

---

## Local LLM (Ollama)

- App talks to **Ollama HTTP API** (`http://127.0.0.1:11434` by default).
- App should **ensure Ollama is running** (start `ollama serve` if binary found and API down).
- Models remain on the host (not embedded in the .exe — too large). User needs Ollama installed + at least one model (`ollama pull …`).
- Case answers are **grounded** via `src/llm/case_context.py` (procedure, vent/vitals stats, flags, meds, events, wave presence).
- System prompt: research-only, no inventing missing data.

---

## Paths

Resolution order (typical): **env** → **session/UI settings** → defaults under app/data.

| Path | Role |
|------|------|
| EMR | `patient_information.csv`, ventilator, vitals, meds, events |
| Wave | optional waveforms root |
| Processed | pipeline parquet cache (may sit next to EMR on external drives) |

External mounts (e.g. `/var/mnt/games/…`) are allowed for EMR/processed next to selected data; see `resolve_output_dir`.

---

## How to run

```bash
# Dev UI
PYTHONPATH=. python -m src.desktop

# Local frozen install
./scripts/install_local.sh
mover-sis-monitor

# Pipeline only
PYTHONPATH=. python -m src.pipeline.run --n-cases 50

# Tests
PYTHONPATH=. QT_QPA_PLATFORM=offscreen pytest -q
```

Ollama:
```bash
ollama serve   # app may start this if installed
ollama list
```

---

## Release checklist

- [ ] Bump `VERSION` + `src/__version__.py` (`x.y.z`)
- [ ] Update this `CONTEXT.md` if structure/behavior changed (drop stale notes)
- [ ] Tests green
- [ ] `./scripts/install_local.sh` (local exe)
- [ ] Merge PR → confirm GitHub Release asset for `v{x.y.z}`
