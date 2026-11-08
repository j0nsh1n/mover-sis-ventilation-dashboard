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
- Status: [x] 2026-10-17 — merged in #37 with Linux and Windows CI passing.
  A local v0.14.0 Linux candidate is built and validated; publication is pending.

## MOVER Suite migration (approved 2026-10-27)

Plans and authorization state only. The sequence is the approved plan in
`/home/jonathans/Projects/medical/mover/_work/mover-suite/MOVER-SUITE-PLAN.md`.
This subsection does not mark an independently reviewed implementation
milestone complete.

### Phase 1 — Baseline and spec (authorized 2026-10-27)

- Record checkout, HEAD, candidate AppImage, original checklist hashes, and
  tooling versions
- Update `spec.md` for PySide6, four tools, retired legacy tabs, shared
  source and generation boundaries, and deferred decisions
- Write the suite native QA record, UNRUN except the carried deferred keyboard
  route, with the original candidate
  checklist left UNRUN
- Write `.handoff/mover-suite/phase-1.md`
- Status: [x] 2026-10-27 — documentation complete on `feat/v0.15-signal-grounding`
  at baseline `1bca1ce`. No Phase 1 commit exists yet. Main lightweight
  Ruff, diff, and document checks passed. Heavy Python, coverage, and Rust
  checks were resource-deferred; native suite QA awaits the suite target.
  Approved GPT-6.1 Terra review through T3 Codex returned PASS after three
  documentation fixes were applied and independently confirmed. This
  closes the documentation phase only.

### Phase 2 — Monitoring shell (authorized 2026-10-27)

- Add suite navigation with one live module: today's G and F workspace
- Preserve Monitoring behavior. Unfinished trainer, medication, and WHO
  tools stay off the shell
- Carry native suite checks for Setup, Cases, source rows, Rules, keyboard
  use, scaling, safe errors, and persistence
- Status: [x] 2026-10-27 local / 2026-10-28 UTC — Monitoring shell is
  implemented on `feat/v0.15-signal-grounding`. Committed locally on
  2026-11-01; parent `1bca1ce`. Not pushed. Independent reviews recorded
  no remaining code issues. Native
  source QA E1–E2 and sections 1–10 PASS except 3.3 DEFERRED; section 11
  UNRUN. Historical full source gate FAIL was two mypy errors, later
  fixed; ruff, mypy, and `git diff --check` then exited 0. Final
  `pytest --cov=src` exited 0: 1259 passed, 7 skipped, 853 warnings in
  964.83s; TOTAL 11909 statements, 1898 missed, 84%. All six recorded
  source/test hashes match. Source-shell scope and closeout are complete;
  frozen-artifact section 11 remains UNRUN. Phase 3 follows.

### Phase 3 — Shared data context (authorized 2026-11-01)

- One context records the selected source, processed directory, preset,
  threshold identity, and a generation for pipeline, load, and scan jobs
- Case-fetch cache keys include the EMR path, preset, and threshold hash
- Status: implemented on 2026-11-01 on `feat/v0.15-signal-grounding`.
  Terra and Opus 5.5 reviewed the first draft. Their findings are fixed:
  a scan uses the stamp of the worker that finished, fetches do not
  refill a cleared cache, a missing threshold hash does not bump the
  generation, a fetch keeps its starting EMR path, and a reload during
  the first pipeline run is queued. Full offscreen `pytest -q` exited 0
  after those fixes. Not pushed. Native AI stays paused.

### Folder rename (done 2026-11-01)

The folder rename (plan step 4) is DONE as of 2026-11-01: the repository is now `/home/jonathans/Projects/medical/mover/mover-suite`. A compatibility symlink `mover-sis-monitor -> mover-suite` remains. The launcher, `.desktop` Path, T3 worktrees (`git worktree repair`), and `.venv` (rebuilt, Python 3.14) all use the real path. Product name, settings path and packaging names are unchanged until the branding step.

Earlier history: a first attempt found the destination absent and stopped. The July preserved virtualenv was moved, not deleted, to
`/home/jonathans/Projects/medical/mover/_scratch/suite-move/venv-july-3.14.6`.
The live `.venv` stayed. Preflight stopped because this session’s
processes use the repository as their working directory. `lsof.err` is
Docker overlay and netns warnings only. Do not move the folder until a
later preflight shows it unused.

### Practice modules (2026-11-01)

- Handoff grading, medication and vital rows, and fictional WHO checklist
  practice are in the desktop. Cases and cards are fictional. Trainer history
  is imported at `6c64f11` under `imports/handoff-trainer`. The original
  trainer repo stays where it is.
- The folder rename is now done (path `mover-suite`); see above.
- A new suite candidate was not built. No portable container command was
  found to copy, and a host build is not a substitute. Native QA on a new
  artifact remains UNRUN.

Suite QA section 12 (2026-11-07, suite AppImage, hidden KWin session) passed
6 of 9 practice-tool steps. It failed on: handoff grading charging a stated
minute to the wrong episode (12.2); combo and page names not exposed to screen
readers (12.1); Tab traps in the handoff text and the medication table (12.9).
A goal audit of the same day ranks real-data calibration, a live-AI session,
and a release as the largest open items.

GPT-6.1 Terra reviewed the practice pages and did not accept the first
grader. The fix is local commit `eb09044`. Terra's re-review accepted it:
a wrong minute, a normal saturation, and a generic word no longer count
as a completed mention, and an empty timeline still reports the missing
MAP and SpO2 series. Not pushed.

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
  its tests use it; the unused `scipy` pin was removed in Phase 10)
- Signal- and unit-aware number grounding (coincidental matches with unrelated
  source numbers): implemented and independently reviewed locally on the v0.15
  branch, 2026-10-17; publication is pending.
- A local v0.15.0 Linux AppImage was built and independently reviewed on
  2026-10-17. Manual replacement and Bash packaging were selected. Two minor
  fixes passed focused tests; the final rebuild, startup, scan and packaging
  checks passed. Publication remains pending.
- On Windows, a minimum window width of about 1,060 px (the 900 px layout tests
  skip there)
- The per-size chart image cache grows on each resize until the next case opens
- The Cases table's sort arrow points up for a descending sort on Linux; Qt draws it
  the other way on Windows, so the fix is to draw our own arrow, checked on both
  systems (a flip of Qt's indicator was dropped from v0.14)
- The Nobara XKB crash, if the operator reopens it
