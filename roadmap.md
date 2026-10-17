# roadmap.md — MOVER SIS Monitor

Note: "Complete when" conditions are verified locally (tests pass, feature works)
and via review. One phase may span several small commits/PRs when the human asks
to publish.

## Phase 1 — Governance & hygiene
- Tasks:
  - Fill `spec.md` / `roadmap.md`; adopt `agents.md` policy supremacy
  - Slim state into `context.md`; seed `CHANGELOG.md`
  - Merge legacy `AGENTS.md` into pointer + move product rules into `spec.md`
  - CI/release on Python 3.14
- Complete when: governance files present; CI uses 3.14; tests green locally
- Status: [x] 2026-07-30

## Phase 2 — Core research desktop product (shipped baseline)
- Tasks:
  - Pipeline + flags + parquet cache
  - PySide6 LLM-first UI, themes, settings/setup
  - Ollama co-pilot tools, Start/Stop/Unload, research answer gate
  - On-demand case fetch; wave decode support
  - Local install + release packaging (Linux/Windows workflows)
- Complete when: v0.6.0 behavior matches README/desktop for research workflows
- Status: [x] 2026-07 (baseline on main)

## Phase 3 — Accuracy, corpus scope, and package size
- Tasks:
  - Scope corpus-wide LLM tools so they do not imply sample-cache = full SIS corpus
  - Tighten dependency pins / PyInstaller includes (large onedir ~400MB+)
  - Optional: add lint/type toolchain only if human requests (document in spec)
- Complete when: tool outputs explicitly scope sample vs full EMR; package size
  reduced or justified
- Status: [x] 2026-09-30 — scoped tools and full-EMR scan (#20), exact pins and
  1.2 GB → 399 MB Linux onedir (#22), ruff + mypy in CI (#23); released in v0.10.0

## Phase 4 — Trustworthy answers and a responsive app (v0.11.0)
- Tasks:
  - Offscreen desktop driver (V0)
  - Full answer gate on the Research path (V1)
  - Directive-check corpus with zero false results (V2)
  - Ollama start off the GUI thread (V3)
  - Cancel controls and visible failure reasons (V4)
  - Honest partial-index retrieval (V5)
  - Frozen-app scan self-test in CI (V6)
  - Repeatable, safer calibration tooling (V7)
- Complete when: V0 to V7 are verified and landed, and v0.11.0 is released
- Status: [x] 2026-10-15 — V0 to V7 landed (#27, #28). Ships in v0.12.0; the owner
  chose not to publish v0.11.0 separately. Released in v0.12.1 (2026-10-16).

## Phase 5 — One workspace, step 1 (v0.12.0)
- Tasks:
  - Searchable rules drawer in G from one shared rules model
  - F keeps the full fetch result and shows episodes, a flag lane, and rule detail
    with values and missingness
  - Cases overview in G with the analyzed scope labeled, opened by one click
  - A conversation panel in a side panel beside the G evidence board (chosen
    2026-10-14), with an explicit no-model state
- Complete when: all five prototype scenarios pass as driver scenarios on the
  desktop app, and the old tabs still work
- Status: [x] 2026-10-16 — rules drawer, F episodes and rule detail (#29); Cases
  overview, flag-aware search, side-panel conversation and the five prototype
  scenarios (#30); Settings preset selector and Markdown answers (#31). Released in
  v0.12.1 (2026-10-16); the v0.12.0 build failed on Windows and was not published.

## Phase 6 — Calibrate on real MOVER data (v0.13.0, engineering)
- Tasks:
  - Run `scripts/calibrate.sh` on the real EMR on this machine
  - The operator reads the aggregate report before any model sees it
  - Replace the guessed `phases:` event patterns with observed names
  - Recalibrate thresholds from percentiles
  - Stamp a thresholds version on flags and episodes so F can show it
  - Time the full-EMR scan at 1, 4, 6, and 8 workers on the 16-core host and record
    peak memory
- Complete when: synthetic anomalies still flag, a before-and-after flag table is
  recorded, and `phase_source = events` dominates on real data
- Status: [~] 2026-10-16 — engineering released in v0.13.0 (#34): thresholds version
  recorded with each run and shown in F, scan memory and phase-source counts,
  `scripts/bench_scan.sh`, the merged full-EMR profile, and
  `docs/CALIBRATION_RUNBOOK.md`. The real-data steps (run, read, patterns,
  percentiles, before-and-after table, timing) are open for the operator.

## Phase 7 — One workspace, step 2 (v0.13.0)
- Tasks:
  - Scenario analysis and comparison become question intents in G, answered from
    bounded extracts and never from the Ask session
  - A follow-up field in F scoped to the selected case and minute
  - Remove the Ask, Summary, Case timeline, and Rule reference tabs once their tests
    pass in G and F
- Complete when: no legacy tab remains, every scenario test passes, and the
  isolation test covers the new intents
- Status: [x] 2026-10-16 — legacy tabs retired into G/F, scenario and comparison intents,
  ask about a minute in F (#35). Released in v0.13.0.

## Phase 8 — Grounded numbers and measured answer quality (v0.13.0)
- Tasks:
  - Check every number in an answer against its extracts
  - A recorded-answer eval of about 20 synthetic questions in CI, with a live-model
    mode that writes metrics for comparison
  - An aggregate-only profile of the full-EMR scan built from mergeable shard histograms
  - Sanitized scan failure messages
- Complete when: the eval reports grounding and gate rates per release, and no
  answer number lacks a source
- Status: [x] 2026-10-16 — number grounding with one rewrite, recorded-answer eval in CI,
  merged full-EMR profile, sanitized scan failures (#34). Released in v0.13.0.
  Known limit: coincidental matches with unrelated source numbers pass.

## Phase 9 — Article search (v0.13.0)
- Tasks:
  - A local SQLite article catalog with full-text search and title-plus-abstract
    vectors, shown in G as its own labeled scope, per `docs/MONITORING_CONCEPTS.md`
- Complete when: article results carry citations and an empty result says so
- Status: [x] 2026-10-16 — local article catalog (CSV/JSONL import, FTS5, optional
  vectors) as its own scope in G, at most five cited abstracts (#35). Released in v0.13.0.

## Phase 10 — Desktop follow-ups and dependency trim (v0.14.0)
- Tasks:
  - Reloading a different EMR clears the previous full-EMR scan, so scan scope and
    the indexed count describe the new data only
  - The scenario agent filter also matches indexed cases that are not yet analyzed,
    or the answer says plainly that only analyzed cases were searched
  - Clear the progress line ("Rewriting the draft...", "Model round 1/8...") when an
    answer finishes, fails, or is cancelled
  - Enter activates the Cases button in G, as Space already does
  - Remove the unused `scipy` pin from `requirements.txt` (nothing imports it); keep
    the packaging exclude
- Complete when: each item has a test that fails before the fix, the five prototype
  scenarios still pass, and CI passes on Linux and Windows
- Status: [ ]

## Backlog (unscheduled)
- Prototype the consolidation of Ask, Summary, Case timeline, and Rule reference
  into G/F (prototype built: `docs/prototypes/consolidated-workspace.html`, #21;
  reviewed 2026-10-14, side panel chosen; scheduled as Phases 5 and 7)
- Optional Streamlit UX parity with desktop co-pilot
- Full-corpus anomaly tools without loading all timeseries into RAM
  (done 2026-09-30: `src/pipeline/corpus_scan.py`, co-pilot scope, #20)
- Dependabot: enable only if human wants weekly bot PRs (pip-only; no npm)
- Windows/Linux release polish (signing, smaller Qt subset)
- The GitHub Pages manifest cache of 600 seconds against a 60-second poll
- Unlocked transitive dependencies (`plotly` stays: the optional Streamlit UI and
  its tests use it; the `scipy` pin is scheduled in Phase 10)
- Signal- and unit-aware number grounding (coincidental matches with unrelated
  source numbers pass today)
- On Windows, a minimum window width of about 1,060 px (the 900 px layout tests
  skip there)
- The per-size chart image cache grows on each resize until the next case opens
- The Cases table's sort arrow points up for a descending sort on Linux; Qt draws it
  the other way on Windows, so the fix is to draw our own arrow, checked on both
  systems (a flip of Qt's indicator was dropped from v0.14)
- The Nobara XKB crash, if the operator reopens it
