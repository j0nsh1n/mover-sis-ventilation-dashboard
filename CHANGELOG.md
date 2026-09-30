# Changelog

All notable user-visible changes to this project are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Anesthesia phase for every minute: `pre_induction`, `induction`, `maintenance`,
  `emergence`, `post_emergence`. The boundaries come from each surgery's
  intubation and extubation procedure events, matched by the new `phases:` patterns
  in `thresholds.yaml`. Without usable events the phases fall back to the first
  ventilator minute, incision and surgery end. The timeseries gets `phase` and
  `phase_source` (`events`, `mixed` or `fallback`); the cases table gets
  `phase_source`. The event-name patterns are guesses until they are checked against
  the real MOVER event names.
- Flags and episodes have a `phase` column (episodes also `phase_end`, which
  differs when a run crosses a phase change). The co-pilot says "during induction"
  when it lists a case's episodes and flag counts, and the desktop episode table
  shows the column.
- A rule can list `skip_phases` in `thresholds.yaml` to stay quiet in those phases.
  The config check rejects unknown phase names, patterns that are not lists of
  strings, and invalid regular expressions.
- The profile report lists procedure event names with counts and the phase role each
  one matches (names used in fewer than 11 cases are pooled; no PIDs or timestamps),
  plus observed minutes per phase and how many cases used events or the fallback.
- Synthetic data gets a brief desaturation right after intubation in every sixth
  ventilated case (marked `"phase": "induction"` in `synthetic_truth.json`).

### Changed

- Expected transition behavior is no longer flagged by default: `agent_drift`,
  `agent_fi_et_gap`, `agent_low_maint`, `fio2_high_long`, `etco2_low`,
  `etco2_zero_vent`, `peep_zero_long`, `fio2_room_air_vent`, `tv_low_mlkg`,
  `tv_high_mlkg`, `tv_abs_extreme`, `rr_low` and `rr_high` skip the phases where
  the value is usually routine (for example wash-in and wash-out, pre-oxygenation,
  mask ventilation, an extubated circuit). `spo2_low`, `hr_high`, `hr_low`,
  `map_low`, `pip_high`, `pip_rising`, `peep_high`, `etco2_high`, `agent_high` and
  the composite patterns stay on in every phase. On a 60-case synthetic set
  `agent_drift` dropped from 525 to 49 minute flags.
- The agent drift window restarts at each phase change, so induction wash-in does
  not show up as drift in the first maintenance minutes.
- Re-running the pipeline or the full-EMR scan changes flags, episodes, scores and
  the top-rules text compared with earlier runs (fewer flags in induction and
  emergence, and `rr_low` fires on RR 1-3).

### Fixed

- `rr_low` critical (RR 4 or below) fired only at exactly RR 4 because the RR keep
  range blanked every rate under 4. The range is now 1 to 40, so critical fires across
  RR 1-4 while tidal volume is delivered. RR 0 is still treated as no rate recorded.

## [0.10.0] — 2026-09-30

### Changed

- Direct dependencies in `requirements.txt` are exact `==` pins of the versions
  resolved on Python 3.14 (PySide6 6.11.2, pandas 3.0.6, numpy 2.5.3, pyarrow
  25.0.1, scipy 1.18.1, matplotlib 3.11.2, plotly 7.1.0, PyYAML 6.0.3, streamlit
  1.64.0, pytest 9.1.1, pytest-cov 7.1.0, PyInstaller 6.22.3). PyInstaller uses
  the same pin in `build_executable.sh`, `ci.yml`, and `release.yml`.
- The Linux package is smaller: 1.2 GB unpacked / 464 MB tarball down to 399 MB
  unpacked / 155 MB tarball. The build bundles only the Qt libraries and plugins
  that QtCore/QtGui/QtWidgets need instead of all of Qt (QtWebEngine, Qt3D,
  QtQuick/QML, designer, translations), leaves out scipy (the app never imports
  it), test suites, headers, sample data and pyarrow Flight. `launch.sh` no longer
  sets unused QtWebEngine variables.

### Added

- Full-EMR scan: **File → Scan full EMR** or
  `python -m src.pipeline.corpus_scan` scores every surgery with ventilator data
  in batches, keeping only per-case scores and flag episodes
  (`corpus_cases.parquet`, `corpus_episodes.parquet`, `corpus_meta.json`), so
  memory stays bounded on the full export. Scores match the normal pipeline.
- Co-pilot tool `rule_case_counts`: how many surgeries each rule fired in, with
  episodes and flagged minutes, for the full scan or the loaded sample.
- Planned a clickable G/F consolidation study for the existing Ask, Summary,
  Case timeline, and Rule reference functions.
- Built the clickable G/F consolidation prototype at
  `docs/prototypes/consolidated-workspace.html`. It uses fictional `SYN` data
  and scripted AI responses, compares a side panel with an inline thread for the
  AI conversation, and does not change the desktop app.
- Lint and type checks: `ruff check .` (bug-class rules only) and `mypy` (on
  `src/`) are configured and run in CI. Findings were fixed, including unused
  imports and variables, explicit `zip(strict=...)`, and missing `None` guards.

### Changed

- Co-pilot tools now say what they cover. Every search and ranking result starts
  with a SCOPE line: the full-EMR scan (when loaded) or "loaded sample — N
  analyzed cases, NOT the whole EMR" with the EMR's indexed and ventilated
  counts. `corpus_overview` no longer reports the sample as "Corpus size".
  `search_cases`, `find_similar_cases`, `top_anomaly_cases` (optionally per
  observed hour) and `filter_by_agent` take `scope` = auto / full / sample.
- Selecting a surgery that is in the full scan but not the loaded sample
  analyzes it on demand instead of reporting an unknown PID.
- The sidebar shows the loaded sample size and whether a full-EMR scan exists.

### Fixed

- File → Open Wave folder no longer raises an error after saving the chosen
  folder; the status bar now shows the new wave folder.
- Release asset metadata is verified from the authenticated draft listing before
  publication; public download URLs are checked before Pages is updated.
- Research (G) search no longer opens a dead end first. Equal matches now rank
  surgeries with ventilator data ahead of those without, the first ventilated
  result opens automatically, and the AI extracts use ventilated cases first.
  Surgeries without ventilator rows are labelled "no ventilator data" and
  explained when opened, instead of "no signal samples were found" next to
  "No case selected".
- Linux build ships `libxcb-cursor`, which Qt loads at runtime, so the app starts
  on stock Ubuntu instead of failing to load the Qt "xcb" platform plugin.
- `launch.sh` names any missing system library and the install command for
  Ubuntu/Debian and Fedora/Nobara (also as a dialog when kdialog or zenity is
  available) instead of Qt's generic plugin error.

## [0.9.0] — 2026-09-28

### Added

- Frozen Linux and Windows builds check the signed GitHub Pages manifest by
  default. An empty `MOVER_UPDATE_MANIFEST_URL` disables checks; another HTTPS
  URL can override the default.
- Releases publish signed manifests to GitHub Pages after Linux and Windows
  packages are public. The release job checks asset metadata and public URLs
  before publishing the stable manifest.
- The source repository and release packages are public.

## [0.8.0] — 2026-09-28

### Added

- Desktop updater: with a configured public manifest URL, the installed app
  checks about once a minute, verifies an Ed25519-signed manifest, downloads
  and verifies a newer Linux or Windows package, and offers a restart to apply
  it with a previous-version backup. Update checks stay off by default in
  v0.8.0 (2026-09-28).
- Rust release manifest signer and Windows install and rollback validation
  (2026-09-28).

## [0.7.0] — 2026-09-28

Re-run the pipeline to rebuild existing processed caches: flags and scores
change, and `cases.parquet` gains a column.

### Added

- Desktop research workspace G opens first after setup, searches indexed surgery
  metadata with cached local embeddings or keyword fallback, and gives a local
  model bounded source extracts for a research answer (2026-09-27).
- Focused signal view F opens a retrieved case at its observed minute, shows
  PIP, ETCO₂, and heart rate with source rows, and returns to the same question
  and comparison (2026-09-27).
- First-run setup now opens before data loading; the setup form checks the EMR
  folder and shows embedding model readiness (2026-09-27).
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

- Release packaging uploads Linux and Windows downloads directly to a draft
  GitHub Release, then publishes it after both builds complete. Published
  versions reject replacement runs (2026-09-28).
- Research answers can no longer run legacy case tools or inherit a prior Ask-tab
  active case; the local model receives only the retrieved source extracts for
  the current question, capped at three (2026-09-28).
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

- The installed desktop now presents G as the warm evidence map and F as a
  dedicated dark signal studio. The research tab keeps the AI question,
  observation, source, comparison, and limits visible as distinct regions;
  F shows all three signal tracks together (2026-09-28).
- Selected G as the starting evidence workspace in the interactive prototype.
  Its observation and comparison cards now open F's signal detail at the cited
  sample, with a return path that preserves the question and comparison
  (2026-09-27).

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
