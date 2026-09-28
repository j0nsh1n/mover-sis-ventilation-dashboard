# context.md — MOVER SIS Monitor

## Current State

- **Version:** 0.7.0 (`VERSION` / `src/__version__.py`); local Linux build
  installed, with the 0.6.0 bundle retained for rollback.
- **Branch:** local `feat/public-auto-updates` builds on the published v0.7.0
  release from merged PRs #13 and #14; update work is not published.
- **Runtime:** Python **3.14** (agents + CI target). Dev host verified 3.14.x.
- **Tests:** `PYTHONPATH=. QT_QPA_PLATFORM=offscreen pytest -q`, packaging,
  and the Rust updater contract suite — green (2026-09-28). Frozen-binary
  smoke passed before the test port.
  Real wave tree unavailable.
  Lint/types not configured (report-only per spec).
- **Product:** Local research desktop for MOVER SIS EMR/wave; optional Streamlit;
  local Ollama co-pilot (not clinical CDS).
- **Desktop implementation:** G is the first tab after setup; local embedding
  retrieval indexes one short metadata record per surgery and gives up to three
  case extracts to the local AI. Keyword search remains available when the
  embedding model is absent. The desktop G uses the evidence map layout;
  F opens all three case signal tracks and their source rows in a dark studio.
- **Design reference:** `docs/MONITORING_CONCEPTS.md` holds the selected G/F
  design and a future article-search proposal based on `HealthDatabaseAccess`.
- **Interactive studies:** `docs/prototypes/monitoring-radical-concepts.html`
  starts in G, opens F from an observation or comparison at its cited minute,
  and returns with the question and comparison intact. H remains an alternate
  study. The older A–E drafts remain in `monitoring-concepts.html`. All use
  fictional records and scripted AI.
- **Known gaps:**
  - Python coverage reports do not include updater behavior exercised by the
    separate Rust contract suite.
  - No public update host or production manifest URL is selected; the updater
    remains inactive by default. Windows install swap has not run on Windows.
  - Public manifest integrity depends on the chosen HTTPS host; signing should
    be resolved before enabling automatic package installation for users.
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
| `src/desktop/` | PySide6 app (G research first), F signals, theme, settings |
| `src/llm/` | Ollama tools, agent loop, prompts, start/stop/unload |
| `src/pipeline/` | load → clean → merge → features → flags → run |
| `src/pipeline/synthetic.py` | Fictional SIS EMR generator + `synthetic_truth.json` |
| `src/pipeline/profile.py` | Aggregate-only profile / threshold calibration report |
| `src/services/case_fetch.py` | On-demand EMR shortlist + flag shortlist |
| `src/services/retrieval.py` | Surgery metadata search + SQLite embedding cache |
| `src/wave_decode.py` | Waveform decode (Bernoulli/GE S5) |
| `src/guardrails/` | Config / data / IO validation |
| `src/config/thresholds.yaml` | Flag rule presets |
| `scripts/install_local.sh` | Build + install `~/.local/share/mover-sis-monitor` |
| `packaging/` | PyInstaller entry + rthooks |
| `rust-updater-tests/` | Rust contract tests with a Python probe for the updater and Qt controller |
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
- **Settings** — paths, theme, chat and embedding model choices under XDG config.
- **Retrieval cache** — SQLite vectors keyed by source-text hash and embedding model;
  vectors rank candidates, then bounded source extracts ground the AI answer.

```
EMR CSVs ──► metadata index ──► embedding cache ──► retrieved cases
        └──► pipeline / case_fetch ──► processed parquet / source extracts
Wave root (optional) ──► wave_decode                   │
                                                       ▼
                                          desktop G / F + local Ollama
```

## Non-Obvious Decisions

- **Question-first UI:** G opens after setup; legacy Ask, Summary, Timeline,
  and Rule reference remain available as secondary tabs.
- **On-demand fetch:** full `patient_information` search is cheap; flag only shortlist
  (compute-now vs full corpus precompute).
- **Research answer gate:** rewrite round on directive phrasing / wrong asserted age
  (live model drifts into “consider reducing TV”, wrong ages).
- **Research isolation:** G disables legacy agent tools and uses a session limited
  to retrieved PIDs, so prior Ask-tab selections cannot enter its prompt.
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

- **Date:** 2026-09-28
- **Branch:** local `feat/public-auto-updates` (no updater PR or release).
- **Done:** v0.7.0 is published with Linux and Windows packages. The local
  updater branch checks a configured public manifest every minute, stages a
  verified newer package, and offers a restart with a previous-version backup.
  FlexWeek's separated release decision, checksum, and swap tests informed it.
  `spec.md` now records the updater behavior and Rust test gate.
- **Verified:** Full Python suite, packaging, threshold smoke, Rust updater
  contracts, Rust formatting and Clippy passed on 2026-09-28. Linux helper
  swap and startup rollback and staging from the actual v0.7.0 Linux archive
  passed before the Rust test port. Windows helper behavior has only been
  inspected and tested as generated script on Linux. Python lint/types are
  not configured.
- **Next:** Select a public host, settle manifest signing, test the Windows
  swap on Windows, and then enable the default update URL in a release.
