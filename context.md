# context.md — MOVER SIS Monitor

## Current State

- **Version:** 0.6.0 (`VERSION` / `src/__version__.py`); Unreleased changes in CHANGELOG.
- **Branch:** `docs/monitoring-concepts` (local draft branch).
- **Runtime:** Python **3.14** (agents + CI target). Dev host verified 3.14.x.
- **Tests:** `PYTHONPATH=. QT_QPA_PLATFORM=offscreen pytest -q` — green (2026-09-26),
  desktop tests included. Skips: frozen binary, real wave tree.
  Lint/types not configured (report-only per spec).
- **Product:** Local research desktop for MOVER SIS EMR/wave; optional Streamlit;
  local Ollama co-pilot (not clinical CDS).
- **Design draft:** `docs/MONITORING_CONCEPTS.md` and its interactive HTML prototype
  compare three layouts, a pre-workspace setup modal, and bounded retrieval.
- **Known gaps:**
  - Some LLM tools (`corpus_overview`, `top_anomaly_cases`, …) still reflect
    **cached sample**, not full EMR corpus — can overstate “corpus size.”
  - Frozen onedir is large (~400MB+) — scipy/matplotlib/pyarrow/Qt collected broadly.
  - `requirements.txt` uses minimum versions (`>=`), not full lockfile pins.
  - Induction is now in the timeseries; rules have no induction/emergence context
    yet, so expect extra flags around intubation (procedure-event context planned).
  - `clean_ranges.RR` [4, 40] blanks RR < 4, so `rr_low` critical (≤ 4) fires only at 4.
  - `agent_drift` (info) fires in most cases around induction/emergence ramps;
    procedure-event context would separate those.
  - Thresholds not yet calibrated against real distributions (`src.pipeline.profile`
    exists for that; needs a run on real data).

## Repo Landmarks

| Path | Role |
|------|------|
| `src/desktop/` | PySide6 app (Ask first), theme, charts, settings |
| `src/llm/` | Ollama tools, agent loop, prompts, start/stop/unload |
| `src/pipeline/` | load → clean → merge → features → flags → run |
| `src/pipeline/synthetic.py` | Fictional SIS EMR generator + `synthetic_truth.json` |
| `src/pipeline/profile.py` | Aggregate-only profile / threshold calibration report |
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
- **Wave decode:** production code is `src/wave_decode.py` (provider gain overrides
  + NaN sentinels); algorithm cross-checked in tests, not a root script snippet.
- **docs/DESIGN.md** is historical (Streamlit-era plan); keep for quirks/rules rationale.
- **Minute grid:** `fill_minute_grid` (merge + start of `add_features`) inserts empty
  rows for missing minutes so row-based rolling windows and run lengths are minutes.
  Cases spanning > 28 h are left ungridded. `n_minutes` counts observed minutes only.
- **Score:** each flagged minute scores once at its worst severity; composites weight
  per episode. `n_warn`/`n_critical` still count minutes with any flag of that severity.
- **Agent caps per agent:** `clean_ranges.Agent_Et/Fi` is the widest bound (18%);
  `agent_clean_max` caps each agent (desflurane 18, others 12).
- **Data window vs anchor:** rows are kept for the OR stay (`window_start/end`, falling
  back to the surgery window when OR times do not enclose it); `t_min` is anchored at
  incision (`case_start`), so induction minutes are negative.
- **`.streamlit/secrets.toml` gitignored** — fine for this project; no product SMTP.

## Session Handoff

- **Date:** 2026-09-26
- **Branch:** `docs/monitoring-concepts` (local, not pushed)
- **Done:** Three interactive layout drafts, a pre-workspace setup concept, and
  a case and optional article retrieval proposal. No product behavior changed.
- **Verified:** Drafts opened in the T3 preview at desktop and 375 px widths;
  modal, layout switcher, filters, case selection, and timeline navigation were
  exercised. Full pytest suite, threshold smoke, and packaging checks passed.
  Local data read timings are recorded in the proposal.
- **Next:** review a layout direction and article source; update `spec.md` with
  approval before implementing startup, navigation, or retrieval changes.
