# Changelog

All notable user-visible changes to this project are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

Re-run the pipeline to rebuild existing processed caches: flags and scores
change, and `cases.parquet` gains a column.

### Added

- Radical interface studies F, G, and H: a signal studio, an evidence canvas,
  and a case review deck. Each uses the same fictional measurements with working
  source inspection and scripted AI interactions (2026-09-27).
- Two more interactive layout drafts: D assembles an AI research notebook with
  inline sources, and E uses an AI-directed visual cohort atlas (2026-09-27).
- Three interactive desktop-layout drafts and a setup, retrieval, and anesthesia
  research proof-of-concept proposal in `docs/` (2026-09-26).
- Synthetic SIS generator: `python -m src.pipeline.synthetic --out data/synthetic/EMR`
  writes fictional cases (`SYN*` PIDs) with the real dump's quirks and a
  `synthetic_truth.json` of injected anomalies, for development without MOVER data.
- Aggregate-only profile: `python -m src.pipeline.profile` reports coverage and
  ventilator/vitals gaps, signal percentiles, and where each rule threshold falls in
  the observed data. No PIDs or timestamps; small case counts are suppressed.
- `cases.parquet` column `anomaly_score_per_hour` (score per observed hour).
- Timeseries column `MAC_total_Et`: volatile MAC plus N₂O (`n2o_mac_age40: 104`).
- Project governance set: filled `spec.md`, `roadmap.md`, `context.md`, `CHANGELOG.md`;
  global rules in single file `agents.md` (no separate `AGENTS.md`).
- CI and release workflows target **Python 3.14**.
- CodeQL workflow under `.github/workflows/codeql.yml` (Python).

### Removed

- Legacy root `waveform_decode.py` incomplete snippet (production: `src/wave_decode.py`).
- Unused `src/desktop/plotly_view.py` re-export shim.

### Fixed

- Duration and slope rules counted rows, not minutes. Each case's timeseries now has one
  row per minute (gap minutes are empty), so windows like "PIP rising over 10 minutes"
  and "zero PEEP for 15 minutes" no longer stretch across gaps in the ventilator export.
  Rolling windows use `window_min` from `thresholds.yaml`.
- `rr_low` could never reach critical (RR ≤ 4) and warned only at exactly RR 6; it now
  requires delivered TV ≥ `min_tv` (200 mL) instead. `hypoventilation_pattern` now fires
  on low-RR hypoventilation.
- Case summary no longer puts row counts into columns missing from the timeseries;
  `n_minutes` counts observed minutes.
- High desflurane readings were discarded: a single 12 vol% cap applied to every agent,
  but 2 MAC of desflurane is ~13%, so critical `agent_high` could not fire for younger
  desflurane cases. Caps are now per agent (`agent_clean_max`: desflurane 18%,
  sevoflurane / isoflurane 12%).
- Induction was cut off: the data window started 5 minutes before incision. It now
  covers the whole OR stay (`OR_start`..`OR_end`); `t_min` is still minutes from
  incision, so induction minutes are negative.

### Changed

- Connected the preferred D research notebook and A case workbench into a
  question-preserving inspection flow, with E available for cohort discovery
  in the interactive draft (2026-09-27).
- Revised the interface drafts around an AI question flow: a guided start opens
  an evidence workbench, with the cohort board retained as an optional browse view.
  Expanded the retrieval proposal using the neighboring LitSieve project's article
  search pattern and clarified the FDA CDS boundary for signal patterns (2026-09-27).
- Anomaly score counts each flagged minute once at its worst severity, and weights
  composite patterns per episode rather than per minute. `n_composite` counts episodes.
- `agent_high` uses total MAC (volatile + N₂O).
- Co-pilot case briefing reports "N of M minutes with data" instead of a row count.
- PID sampling scans the `PID` column of the whole ventilator file, not its first 400,000
  rows; `--vent-scan-rows` defaults to the full file.
- Single agent policy file **`agents.md`** (removed dual `AGENTS.md` pointer).
- State document is **`context.md` only** (legacy `CONTEXT.md` redirect removed).
- Removed unused `Github Templates/` (Dependabot template discarded; real CI stays project-specific).

## [0.6.0] — 2026-07

### Added

- On-demand EMR case fetch (`services/case_fetch.py`) for shortlist + flags without full
  corpus precompute.
- Waveform decode path (`wave_decode.py`) for Bernoulli/GE S5 archives.
- Research answer gate improvements (directive phrasing / accuracy rewrite round).
- Local-only Ollama URL enforcement tests.

### Changed

- Desktop settings via gear icon; LLM-first Ask tab continues as primary entry.
- Packaging artifacts for Linux/Windows release line at 0.6.0.

## [0.5.x] — 2026-07

### Added

- Ollama Start / Stop / Unload / Refresh lifecycle.
- Scheduler-style tool schemas, multi-round agent, patient-description auto workflow.
- Themes (light / dark / system); setup wizard and settings dialog.
- Case analysis / compare modes on Ask tab.

### Fixed

- Start after Stop without restarting the app (port wait + retries).
- Scrollable case timeline charts; less-rounded UI chrome; group box title styling.

## [0.3.x] – [0.4.x] — 2026-07

### Added

- Settings persistence, dual EMR/wave paths, keyword search (later folded into LLM tools).
- Initial Ollama case Q&A integration and desktop packaging path.

## [0.2.0] — 2026-07

### Added

- Desktop app packaging focus; Ollama-integrated build line.

## [0.1.0] — 2026-07

### Added

- Initial research pipeline, flags, Streamlit views, and first Linux release asset.
