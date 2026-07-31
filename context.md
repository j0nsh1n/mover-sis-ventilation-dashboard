# context.md — MOVER SIS Monitor

## Current State

- **Version:** 0.6.0 (`VERSION` / `src/__version__.py`).
- **Branch:** `main` (local may have uncommitted governance work).
- **Runtime:** Python **3.14** (agents + CI target). Dev host verified 3.14.x.
- **Tests:** `PYTHONPATH=. QT_QPA_PLATFORM=offscreen pytest -q` — green (2026-07-31).
  Lint/types not configured (report-only per spec).
- **Product:** Local research desktop for MOVER SIS EMR/wave; optional Streamlit;
  local Ollama co-pilot (not clinical CDS).
- **Known gaps:**
  - Some LLM tools (`corpus_overview`, `top_anomaly_cases`, …) still reflect
    **cached sample**, not full EMR corpus — can overstate “corpus size.”
  - Frozen onedir is large (~400MB+) — scipy/matplotlib/pyarrow/Qt collected broadly.
  - `requirements.txt` uses minimum versions (`>=`), not full lockfile pins.
  - README still says dataset years 2015–2017; real `OR_start` mass is **2015–2018**.

## Repo Landmarks

| Path | Role |
|------|------|
| `src/desktop/` | PySide6 app (Ask first), theme, charts, settings |
| `src/llm/` | Ollama tools, agent loop, prompts, start/stop/unload |
| `src/pipeline/` | load → clean → merge → features → flags → run |
| `src/services/case_fetch.py` | On-demand EMR shortlist + flag shortlist |
| `src/wave_decode.py` | Waveform decode (Bernoulli/GE S5) |
| `src/guardrails/` | Config / data / IO validation |
| `src/config/thresholds.yaml` | Flag rule presets |
| `scripts/install_local.sh` | Build + install `~/.local/share/mover-sis-monitor` |
| `packaging/` | PyInstaller entry + rthooks |
| `.github/workflows/` | `ci.yml`, `release.yml` |
| `agents.md` / `spec.md` | Policy (global / project; one agents file) |
| `context.md` / `CHANGELOG.md` / `roadmap.md` | State / history / plan |

## Domain Model

- **PID** — surgery-level ID (not a lifelong patient).
- **EMR** — tabular CSVs (`patient_information`, ventilator, vitals, meds, events, …).
- **Wave** — optional archives / `Waveforms/<prefix>/<PID>/`.
- **Processed** — parquet: `cases`, `timeseries`, `flags`, `episodes` (+ meta).
- **Flags** — derived rule screens from thresholds (not stored as source-of-truth in EMR).
- **Session (LLM)** — `active_pid`, `focus_pids`, tool traces; answers grounded in tools/briefings.
- **Settings** — paths, theme, `ollama_models_dir` / preferred model under XDG config.

```
EMR CSVs ──► pipeline / case_fetch ──► processed parquet
                                      │
Wave root (optional) ──► wave_decode  │
                                      ▼
                              desktop + LLM tools ──► local Ollama (loopback)
```

## Non-Obvious Decisions

- **LLM-first UI:** pipeline/filters not on main sidebar; co-pilot + Settings/Setup.
- **On-demand fetch:** full `patient_information` search is cheap; flag only shortlist
  (compute-now vs full corpus precompute).
- **Research answer gate:** rewrite round on directive phrasing / wrong asserted age
  (live model drifts into “consider reducing TV”, wrong ages).
- **Ollama local-only by default:** remote base URL needs explicit allow env.
- **Stop/Start lifecycle:** wait for port free; Start retries so Stop → Start works
  without relaunching the app.
- **PyInstaller onedir** (not onefile) for faster cold start; Qt system deps on CI.
- **Streamlit optional**; desktop is primary.
- **`.streamlit/secrets.toml` gitignored** — fine for this project; no product SMTP.

## Session Handoff

- **Date:** 2026-07-31
- **Branch:** main (ahead of origin; not pushed unless asked)
- **Done:** Single `agents.md` (removed `AGENTS.md`); state only in `context.md`.
  Governance + CodeQL local; Dependabot absent on remote.
- **Next:** Human review of optional legacy files (see change summary);
  push when ready; Phase 3 corpus-tool scoping / package size.
