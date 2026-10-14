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
- Status: [ ]

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
- Status: [ ]

## Phase 6 — Calibrate on real MOVER data (v0.13.0)
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
- Status: [ ]

## Phase 7 — One workspace, step 2 (v0.14.0)
- Tasks:
  - Scenario analysis and comparison become question intents in G, answered from
    bounded extracts and never from the Ask session
  - A follow-up field in F scoped to the selected case and minute
  - Remove the Ask, Summary, Case timeline, and Rule reference tabs once their tests
    pass in G and F
- Complete when: no legacy tab remains, every scenario test passes, and the
  isolation test covers the new intents
- Status: [ ]

## Phase 8 — Grounded numbers and measured answer quality (v0.15.0)
- Tasks:
  - Check every number in an answer against its extracts
  - A recorded-answer eval of about 20 synthetic questions in CI, with a live-model
    mode that writes metrics for comparison
  - An aggregate-only profile of the full-EMR scan built from mergeable shard histograms
  - Sanitized scan failure messages
- Complete when: the eval reports grounding and gate rates per release, and no
  answer number lacks a source
- Status: [ ]

## Phase 9 — Article search (v0.16.0)
- Tasks:
  - A local SQLite article catalog with full-text search and title-plus-abstract
    vectors, shown in G as its own labeled scope, per `docs/MONITORING_CONCEPTS.md`
- Complete when: article results carry citations and an empty result says so
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
- Unused `scipy` and `plotly` pins in the desktop requirements, and unlocked
  transitive dependencies
- The Nobara XKB crash, if the operator reopens it
