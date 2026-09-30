# context.md — MOVER SIS Monitor

## Current State

- **Version:** 0.9.0 in source and in the local installed build. The prior
  v0.7.0 install remains as a backup.
- **Branch:** `main` includes the signed updater and release validation from
  merged PRs #15–#17. A local docs branch holds the G/F consolidation plan.
- **Distribution:** The repository and release packages are public. GitHub
  Pages hosts the signed manifest at
  `https://j0nsh1n.github.io/mover-sis-ventilation-dashboard/updates/latest.json`.
  Frozen v0.9.0 builds check it by default. The v0.9.0 release and Pages
  manifest are live.
- **Runtime:** Python **3.14** (agents + CI target). Dev host verified 3.14.x.
- **Tests:** `PYTHONPATH=. QT_QPA_PLATFORM=offscreen pytest -q`, packaging,
  and Rust updater and signer suites passed locally (2026-09-28). v0.9.0
  Linux and Windows release jobs passed. Both archive hashes match the signed
  Pages manifest. The local frozen Linux app later crashed in Qt XCB keyboard
  handling on this Nobara host.
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
  - Existing v0.8.0 and older installs need one manual update to v0.9.0.
  - GitHub Pages currently serves the manifest with a 600-second cache time,
    which can delay when a one-minute poll sees a new release.
  - The v0.9.0 Linux bundle contains `libxkbcommon.so.0` from its Ubuntu build
    and loads `libxkbcommon-x11.so.0` from this Nobara host. The installed app
    reproduced a SIGSEGV twice in `xkb_state_key_get_layout`. An isolated copy
    without the bundled library loaded both host XKB libraries and stayed open
    past 40 seconds. The project owner treats this as a one-off bug; the
    bundle is unchanged and no fix is planned.
  - v0.9.0 on stock Ubuntu fails to load the Qt xcb plugin without the system
    `libxcb-cursor0`; the same branch bundles it and `launch.sh` names missing
    libraries.
  - The full-EMR scan has only run on synthetic data here (54 cases in ~13 s,
    about 0.25 s per surgery); expect over an hour for ~19k real surgeries,
    single-threaded. It is started manually and not refreshed automatically.
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
| `src/pipeline/corpus_scan.py` | Full-EMR scan in PID shards; `process_frames` shared with `run.py` |
| `src/services/case_fetch.py` | On-demand EMR shortlist + flag shortlist |
| `src/services/retrieval.py` | Surgery metadata search + SQLite embedding cache |
| `src/wave_decode.py` | Waveform decode (Bernoulli/GE S5) |
| `src/guardrails/` | Config / data / IO validation |
| `src/config/thresholds.yaml` | Flag rule presets |
| `scripts/install_local.sh` | Build + install `~/.local/share/mover-sis-monitor` |
| `packaging/` | PyInstaller entry + rthooks |
| `rust-updater-tests/` | Rust contract tests with a Python probe for the updater and Qt controller |
| `src/update.py`, `src/desktop/updates.py` | Signed checks, staging, apply, rollback |
| `tools/update-manifest/` | Rust release manifest signer |
| `.github/workflows/` | CI and release jobs, including Pages deployment |
| `agents.md` / `spec.md` | Policy (global / project; one agents file) |
| `context.md` / `CHANGELOG.md` / `roadmap.md` | State / history / plan |

## Domain Model

- **PID** — surgery-level ID (not a lifelong patient).
- **EMR** — tabular CSVs (`patient_information`, ventilator, vitals, meds, events, …).
- **Wave** — optional archives / `Waveforms/<prefix>/<PID>/`.
- **Processed** — parquet: `cases`, `timeseries`, `flags`, `episodes` (+ meta) for the
  analyzed sample; optional `corpus_cases` / `corpus_episodes` / `corpus_meta.json`
  from the full-EMR scan (no timeseries).
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
- **Update host:** GitHub Pages serves only the signed manifest. GitHub
  Releases serves the packages. The app uses no GitHub API for polling.
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

- **Date:** 2026-09-29
- **Branch:** `fix/research-vent-and-linux-launch` (from `main` after #18).
- **Done:** Ran the published v0.9.0 Linux package on synthetic data (cloud,
  Xvfb). Fixed two findings: G search ranked a surgery without ventilator rows
  first and opened a dead end; the package needed the system `libxcb-cursor0`
  on stock Ubuntu. `launch.sh` now names missing system libraries.
- **Verified:** full `pytest` (frozen-binary smoke included) passed; a local
  build started on Xvfb with the host `libxcb-cursor0` removed, and the same G
  question opened a ventilated case; `launch.sh` printed install commands when
  the library was missing.
- **Next:** Phase 3 (corpus scoping, pins, package size, lint/types), full-data
  anomaly tools, and the G/F consolidation prototype, then a release.
