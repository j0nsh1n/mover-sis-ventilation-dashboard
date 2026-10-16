# context.md — MOVER SIS Monitor

## Current State

- **Version:** 0.13.0 released 2026-10-16 (roadmap Phases 7, 8 and 9, and Phase 6
  engineering). 0.12.1 before it carried the v0.11 and v0.12 work. Installed apps
  update through the signed Pages manifest.
- **Branch:** `main` includes everything through v0.13.0 (#34, #35 and the release
  PR). No working branches are open.
- **Distribution:** The repository and release packages are public. GitHub
  Pages hosts the signed manifest at
  `https://j0nsh1n.github.io/mover-sis-ventilation-dashboard/updates/latest.json`.
  Frozen v0.9.0 builds check it by default. The v0.9.0 release and Pages
  manifest are live.
- **Runtime:** Python **3.14** (agents + CI target). Dev host verified 3.14.x.
- **Tests:** `PYTHONPATH=. QT_QPA_PLATFORM=offscreen pytest -q` passes on
  `feat/v0.12-g` (809 passed, 2 skipped, 5 deselected, 2026-10-16) with
  `ruff check .` and `mypy` clean. The five real-wave decode tests run only
  with `MOVER_REAL_DATA_TESTS=1`. Rust updater and signer suites last passed
  2026-09-28. The v0.11 local build ran 10 s on both wayland and xcb without a
  crash (2026-10-14).
  Real EMR and wave data are on this machine; no test or lane reads them.
  `tools/desktop_drive.py` drives the app offscreen on synthetic data and
  saves a screenshot per step; every desktop check uses it.
- **Product:** Local research desktop for MOVER SIS EMR/wave; optional Streamlit;
  local Ollama co-pilot (not clinical CDS).
- **Desktop implementation:** G is the first tab after setup; local embedding
  retrieval indexes one short record per surgery, plus short facts (signals,
  flagged rules, signal gaps) for analyzed cases, and gives up to three case
  extracts to the local AI. Keyword search matches whole words and works
  without the embedding model. G has a Rules drawer (Ctrl+R), a Cases
  overview and a conversation panel beside the evidence board. F shows the
  three signal tracks, source rows, flag episodes, a flag lane and rule detail
  with current thresholds.
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
  - The full-EMR scan has only run on synthetic data here. It now scores shards
    in a spawn-started process pool (default cores - 1, at most 6): 600 synthetic
    surgeries took 75 s on 1 worker, 38 s on 2, 26 s on 4 (4-core container; splitting
    the CSVs is about 1 s of that). That is about 0.13 s per surgery per core, so
    ~19k real surgeries should take roughly 40 min on one core and 10 to 15 min on
    four to six, if real rows cost like synthetic ones; real per-worker memory is
    unmeasured (250 MB per 300-surgery synthetic shard; 1 to 2 GB assumed). The
    frozen-app and Windows paths of the pool are unverified. It is started
    manually and not refreshed automatically.
  - Frozen onedir is 399 MB unpacked / 155 MB tarball (was 1.2 GB / 464 MB).
    The rest is mostly pyarrow (113 MB), Qt core/GUI/widgets + ICU (88 MB),
    `libpython` (32 MB), and numpy's OpenBLAS (27 MB); none is removable safely.
  - `requirements.txt` pins direct dependencies exactly; transitive packages
    are not locked. `scipy` and `plotly` stay listed although the desktop
    bundle omits both (only the optional Streamlit UI and its tests use plotly;
    nothing in `src/` imports scipy).
  - The Windows spec changes (hook-based Qt/pyarrow collection, path filters) are
    verified on Linux only; the release workflow's Windows smoke test is the check.
  - Procedure-event name patterns (`phases:` in `thresholds.yaml`) are unverified on
    real data: the synthetic EMR uses "Intubation"/"Extubation", the real MOVER names
    are unknown. Until they are checked (profile report, "Procedure event names"),
    real cases may fall back to `phase_source = fallback`.
  - Thresholds not yet calibrated against real distributions (`src.pipeline.profile`
    exists for that; needs a run on real data). `scripts/calibrate.sh` samples
    50 cases by default (`CALIBRATION_N_CASES` to change).
  - No embedding model is installed in the local Ollama, so retrieval has only
    been measured with keywords and a fake embedder. The `search_query:` /
    `search_document:` prefix trial waits for a real model.
  - On this host, PyInstaller 6.22.3's Linux `ldd` parser truncates paths at the
    parentheses in the project folder name, so local builds bundled no Qt
    libraries and loaded the host's `libQt6Core` (`Qt_6_PRIVATE_API` crash).
    `feat/v0.11-app` adds a Qt library fallback and a build check; CI builds on
    Ubuntu were never affected.
  - The model-labeled advice check uses the configured chat model. The configured
    `qwen3:14b` is not installed on this host; the check was measured with
    `holo9b-16k`. `qwen3-vl:2b` returns empty answers because its thinking uses
    the whole output budget.
  - Advice-check word patterns alone caught 1 of 20 held-out advice sentences;
    the model label is what carries recall (20 of 20). Without a reachable model
    the answer ships with the pattern check and says so.
  - The Windows self-test scan step and the frozen Windows app after the Ollama
    worker change have not run on Windows yet (the release workflow is the check).

## Repo Landmarks

| Path | Role |
|------|------|
| `src/desktop/` | PySide6 app (G research first), F signals, theme, settings |
| `src/llm/` | Ollama tools, agent loop, prompts, start/stop/unload |
| `src/pipeline/` | load → clean → merge → features → flags → run |
| `src/pipeline/phases.py` | Per-minute anesthesia phase from procedure events (fallback: case times) |
| `src/pipeline/synthetic.py` | Fictional SIS EMR generator + `synthetic_truth.json` |
| `src/pipeline/profile.py` | Aggregate-only profile / threshold calibration report |
| `src/pipeline/corpus_scan.py` | Full-EMR scan in PID shards; `process_frames` shared with `run.py` |
| `src/services/case_fetch.py` | On-demand EMR shortlist + flag shortlist |
| `src/services/retrieval.py` | Surgery metadata search + SQLite embedding cache |
| `src/wave_decode.py` | Waveform decode (Bernoulli/GE S5) |
| `src/guardrails/` | Config / data / IO validation |
| `src/config/thresholds.yaml` | Flag rule presets |
| `scripts/install_local.sh` | Build + install `~/.local/share/mover-sis-monitor` |
| `packaging/` | PyInstaller spec (targeted collection + path filters), entry, rthooks |
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

- **Narrow lint rules, no pandas-stubs:** ruff selects only `E4/E7/E9/F/B` and
  mypy checks `src/` without `pandas-stubs`, so adoption finds real bugs without
  reformatting the codebase or drowning in stub false positives. Widening either
  is a possible later step.
- **Question-first UI:** G opens after setup; legacy Ask, Summary, Timeline,
  and Rule reference remain available as secondary tabs.
- **On-demand fetch:** full `patient_information` search is cheap; flag only shortlist
  (compute-now vs full corpus precompute).
- **Research answer gate:** one rewrite round on directive phrasing, a wrong age
  or dose for a cited case, or a PID outside the cited set (live models drift into
  "consider reducing TV" and restate the user's age as the record). The Research
  session carries the cited cases' age, sex, procedure and medication rows; text
  naming no case is checked against all cited cases, and group ages ("mean age
  56", "aged 80 or older") do not count as a claimed age. A second check asks the
  chat model to label each sentence as description or advice in one call, because
  word patterns miss hedged advice; a sentence labeled advice is treated like a
  pattern hit.
- **PID shape:** the answer gate recognizes only 16 lowercase hex characters or
  `SYN` plus 10 hex. Measured 2026-10-14 as an aggregate count: 19,098 of 19,114
  real PIDs are 16 hex (the rest are 8 or 9 characters and are never checked as
  PIDs). An 8-digit number is therefore not mistaken for an unknown PID.
- **Dialogs never block startup:** load-failure and corrupt-scan boxes use
  `QMessageBox.open()`, since a modal box during `_startup` held the window back
  before it was shown and hung the offscreen suite.
- **Desktop scans always use the spawn pool** when a `should_stop` is given, even
  for one shard, so Cancel can terminate the worker; CLI scans with one worker
  stay in-process.
- **Research isolation:** G disables legacy agent tools and uses a session limited
  to retrieved PIDs, so prior Ask-tab selections cannot enter its prompt.
- **Ollama local-only by default:** remote base URL needs explicit allow env.
- **Stop/Start lifecycle:** wait for port free; Start retries so Stop → Start works
  without relaunching the app.
- **PyInstaller onedir** (not onefile) for faster cold start; Qt system deps on CI.
- **Targeted PyInstaller collection:** the spec does not use `collect_all()`
  for Qt or the scientific stack; only QtCore/QtGui/QtWidgets are imported.
  Path filters drop embedded Qt platforms, the on-screen keyboard, the PDF image
  plugin, translations, tests and headers. The GTK3 platform theme (about 19 MB
  of host GTK libraries) and the SVG icon plugins stay for native dialogs and
  theme icons. WebEngine is unused, so its env vars are gone.
- **Update host:** GitHub Pages serves only the signed manifest. GitHub
  Releases serves the packages. The app uses no GitHub API for polling.
- **Streamlit optional**; desktop is primary.
- **Wave decode:** production code is `src/wave_decode.py` (provider gain overrides
  + NaN sentinels); algorithm cross-checked in tests, not a root script snippet.
- **docs/DESIGN.md** is historical (Streamlit-era plan); keep for quirks/rules rationale.
- **Minute grid:** `fill_minute_grid` (merge + start of `add_features`) inserts empty
  rows for missing minutes so row-based rolling windows and run lengths are minutes.
  Cases spanning > 28 h are left ungridded. `n_minutes` counts observed minutes only.
- **Phases:** the earliest matching intubation event and the latest extubation event
  after it set the boundaries (a re-intubation does not restart induction); events
  outside the case's data are ignored. Induction is the intubation event plus
  `induction_after_min`, emergence starts `emergence_before_min` before extubation.
  `skip_phases` is applied to each minute before run lengths are counted, and the
  agent-drift rolling window restarts at each phase change. Safety rules (`spo2_low`,
  `hr_*`, `map_low`, `pip_*`, `etco2_high`, `agent_high`, composites) have no default skip.
  `RR` 0 stays blanked (no rate recorded); 1-3 are kept for `rr_low` critical.
- **Score:** each flagged minute scores once at its worst severity; composites weight
  per episode. `n_warn`/`n_critical` still count minutes with any flag of that severity.
- **Agent caps per agent:** `clean_ranges.Agent_Et/Fi` is the widest bound (18%);
  `agent_clean_max` caps each agent (desflurane 18, others 12).
- **Data window vs anchor:** rows are kept for the OR stay (`window_start/end`, falling
  back to the surgery window when OR times do not enclose it); `t_min` is anchored at
  incision (`case_start`), so induction minutes are negative.
- **`.streamlit/secrets.toml` gitignored** — fine for this project; no product SMTP.
- **Real-data tests are opt-in:** the five real-wave tests in `tests/test_wave_decode.py`
  run only with `MOVER_REAL_DATA_TESTS=1`, so agent and CI runs never read real rows.

## Session Handoff

- **Date:** 2026-10-16
- **Released:** v0.13.0. The plan and decision trail are under
  `~/.claude/orchestrate/mover-overhaul/` (`docs/v013/plan.md`, `decisions.tsv`).
- **Done:** one Research workspace with the legacy tabs retired; scenario and
  comparison intents; ask about a minute in F; local article search; number
  grounding with an eval in CI; thresholds version shown in F; scan memory,
  bench script, merged full-EMR profile and sanitized scan failures; the
  calibration runbook. PR CI now runs the test suite on Windows too.
- **Verified:** every item had an owner gate, an independent audit (Codex or
  Sonnet) and fixes in a second round; the stacked gate passed 1164 tests.
- **Next:** the operator runs Phase 6 on the real data per
  `docs/CALIBRATION_RUNBOOK.md` and approves the spec.md wording proposed in #34
  and #35. Follow-ups: signal- and unit-aware number grounding (coincidental
  matches), the stale progress line after an answer, the Windows minimum window
  width, Enter on the Cases button, the descending sort arrow, a scan surviving a
  reload of a different EMR, the per-size chart image cache, and the agent filter
  matching analyzed cases only.
