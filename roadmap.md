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
- Complete when: tool outputs explicitly scope sample vs full EMR; package size reduced or justified
- Status: [x] 2026-09-30 — scoped tools and full-EMR scan (#20), exact pins and
  1.2 GB → 399 MB Linux onedir (#22), ruff + mypy in CI (#23); released in v0.10.0

## Backlog (unscheduled)
- Prototype the consolidation of Ask, Summary, Case timeline, and Rule reference
  into G/F, then review capability parity before changing the desktop UI
  (prototype built: `docs/prototypes/consolidated-workspace.html`, #21; awaiting review)
- Optional Streamlit UX parity with desktop co-pilot
- Full-corpus anomaly tools without loading all timeseries into RAM
  (done 2026-09-30: `src/pipeline/corpus_scan.py`, co-pilot scope, #20)
- Dependabot: enable only if human wants weekly bot PRs (pip-only; no npm)
- Windows/Linux release polish (signing, smaller Qt subset)
