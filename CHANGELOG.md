# Changelog

All notable user-visible changes to this project are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- View can open Handoff practice, Medication and vitals, or a fictional WHO
  checklist. Monitoring still opens first. Handoff practice grades fictional
  timelines as mentioned, missed, or unsupported. The medication page shows
  units, time, bolus versus infusion, and source, and it does not recommend
  treatment. The checklist page walks Sign in, Time out, and Sign out on
  authored cards and can save that practice on this computer.

- A Monitoring label sits on the left of the menu bar. The research
  workspace, menus, and Settings gear stay in place.

### Changed

- New look, case page: the page that shows one case now uses the same paper
  and night ledger colors as the research page, in light and dark. The header
  is the procedure with one line of facts (surgery length, age, sampling, rule
  preset and its 12-character hash, number of rule episodes) and the selected
  minute at the right. The chart has no boxes: each track shows its name, unit
  and the value at the cursor (or "missing"), every gap inside a recorded span is
  hatched and says "no samples" where there is room, incision and closure are
  dotted lines, and the rule episodes sit under the tracks as hatched bands
  that you can click. The source row, rule episodes and selected rule are in a
  panel at the right (below the chart when the window is narrow). The selected
  rule has a threshold ruler that shows its limits and where the episode's
  extreme fell. Back says where it returns, "More signals" is "+ Track", and the
  keys line lists only keys that work.

- New look, first step: warm paper (light) and "night ledger" (dark) colors with
  one indigo accent, Inter and Charter fonts bundled with the app, quieter
  controls and tables, and a 2 px accent focus ring on every control that can
  take focus. Rule flags are one ink in every theme: a circle, triangle or
  diamond with the words info, warn or critical beside it, never red, amber or
  blue. Missing values in the medication and vitals table and in the source
  samples window are hatched and keep their text. Layouts are unchanged.

- The app is now called MOVER Suite. The window title reads "MOVER Suite" with
  the version; About, the setup wizard, update messages, the menu entry, and
  release files use the new name. Monitoring is still the SIS Monitor.
- Settings move to `~/.config/mover-suite/`. On first start the old
  `~/.config/mover-sis-monitor/` folder is copied there once and left as it was,
  so an older version still finds its settings. If the copy fails, the app keeps
  using the old folder and says so in the status bar.
- Installed v0.13.0 apps update to MOVER Suite on their own. The folder inside
  the download keeps its old name for that reason. An AppImage from before the
  rename keeps its data folder.
- `install_local.sh` adds a `mover-suite` command next to `mover-sis-monitor`.

### Fixed

- Opening the Cases/Articles dropdown by keyboard shows both choices. A scroll
  arrow no longer covers half of "Articles". The same fix applies to the other
  dropdowns that ignore the mouse wheel until focused.
- Handoff practice accepts a minute or a saturation value only when that
  detail is on the fictional episode. A wrong minute, a normal saturation,
  or a generic word such as "sustained" does not count as mentioning the
  event. "SpO2 missing" names a missing series. An empty timeline still
  reports the missing MAP and SpO2 series.
- Handoff practice counts a minute or value only for the finding it is
  written with. "Phenylephrine bolus given at minute 30" no longer marks a
  correct hypotension sentence unsupported. It marks phenylephrine
  unsupported when no dose was given at minute 30. With two episodes of the
  same kind, each keeps its own minutes.
- In Handoff practice, Tab moves on from the handoff text instead of typing a
  tab, and in Medication and vitals, Tab leaves the table instead of walking
  every cell.
- Screen readers hear the practice pages properly: the scenario and card
  dropdowns announce their purpose, notices and the practice count read out
  their text, and the window title names the open page.
- Before the first search, the research card shows how many analyzed cases
  are loaded instead of a dash, and it shows them again after switching back
  from Articles.
- When Ollama is not running, the status line names the reason (for example
  "connection refused") instead of raw error text.
- Scrolling the research board or Settings with the mouse wheel no longer
  changes a dropdown that passes under the pointer, such as the question
  scope. A dropdown follows the wheel once it has focus.
- On a small or high-scale screen the research header moves its buttons
  below the title, wrapping them if needed, instead of cutting their
  labels short.
- Checking a long answer's numbers against its sources is faster again. It
  had been rescanning the whole answer for each number.
- A case fetch is reused only for the same EMR folder, rule preset, and
  threshold hash. The same surgery id in another folder is computed again.
  A pipeline load or full-EMR scan that finishes after the data folders
  change is not applied, including when the folders are switched back.
  The saved chat model and embedding model are part of that shared record.
  A new embedding model applies to the next search and leaves the analyzed
  sample in place. Reloading without a recorded threshold hash does not
  count as a new threshold identity. A fetch keeps the EMR path it
  started with, and a reload requested while a pipeline is still writing
  the first cache runs again when that job finishes.
- Dismissing the startup load warning puts keyboard focus on the research question.
- A failed pipeline keeps folder paths off the status bar for the whole error
  dialog, including when a reload changes the status while that dialog is open.
  Closing it preserves a newer direct message or restores the latest path line.
- At the default window size, the question card and its indexed-surgeries line
  stay fully visible after a failed local-model check. A longer status still
  wraps instead of being cut off inside the card.
- The question-scope and Local AI dropdowns use their purpose as the accessible
  name and the current choice as the accessible value. A new choice, including
  a model-list refresh, updates that value. The selected model is unchanged.

- Pipeline and full-EMR scan outputs are refused inside the source EMR tree,
  including when external output paths are enabled. Exports with other folder
  names default to a separate processed folder.
- Desktop reloads clear the previous analyzed sample and check the recorded EMR
  source before loading cached data. Results from jobs for another folder are
  discarded.
- A number attributed to an absent case signal cannot borrow an age or another
  signal's value. Heart-rate readings and thresholds accept both bpm and /min.
- Load, pipeline, case and research failures show short codes. Full diagnostic
  details go to a local app-errors.log in the settings folder, readable only by
  the user, including when the log already existed.
- Research controls have field names and button names that match their labels.
  Status changes and completed answers emit accessibility announcements. Empty
  scroll containers no longer take keyboard focus, and dropdowns show a focus
  border and visible chevrons.
- Signal charts have an accessible description and distinguish flag severities
  by marker shape. The ready-state question card fits at the default size.
  Initial windows fit the available screen, and Setup and Settings contents
  scroll when space is limited.

## [0.15.0] - 2026-10-17

This version combines the v0.14 desktop fixes and v0.15 number-grounding work.
The local v0.14 Linux candidate was not published separately.

- A Linux AppImage packages the desktop as one executable file. It keeps saved
  data paths, defaults new outputs to the user's data directory, and uses manual
  AppImage replacement for updates.

- The number check ties each number in an answer to the signal it describes: "PIP reached 23" must match a PIP figure in the sources (or a threshold in PIP's unit), not the patient's age 23 or a heart-rate value. A number is tied to the signals named just before it in its sentence, or to its column's header in a table of rows; with no signal named, a number with a measurement unit (cmH2O, mmHg, mL, mL/kg, bpm, /min, vol%) must match a source figure in that unit. Counts, times, dates, ages, doses and figure numbers, and every number in article abstracts, are compared as before. On the eval's three seed-8 extracts, made-up PIP, PEEP, tidal volume, ETCO2, heart-rate, SpO2 and FiO2 values that passed by coincidence fell from 52% to none, and every figure the extracts print still passes; the eval reports both as `coincidental_pass_rate` and `true_claim_pass_rate`. The "Ask about this minute" extract names the signal on its "Mean flagged value" line when the rule watches one signal.

- Article number checks recognize catalog headers with parenthesized publication years and periods in titles. Abstract figures retain their existing value-only check, while unsupported figures are still rewritten or removed.

- A saved full-EMR scan records which EMR folder it scored (as a hash, not a path). Pointing the app at a different EMR or processed folder drops the old scan's cases, scan scope and indexed count at once, even while another scan runs; a scan that finishes after either folder changed is not loaded, and a scan whose `patient_information.csv` changed while it ran writes nothing. Scan again for the new EMR. Scans saved by earlier versions still load.
- Scenario questions that name an anesthetic agent now match every surgery a full-EMR scan scored, not only the analyzed sample (the sample's agent wins where both have the case). Without a scan, the scope line of an agent-filtered scenario says the agent is known for analyzed cases only; when the agent filter leaves nothing, the answer says where agents come from and that a scan would widen the search.
- The Research status line no longer keeps a running step such as "Model round 1/8…" or "Rewriting the draft…" after the search ends: it reads "Search finished.", keeps a failure message, and returns to the earlier status when the search is cancelled. Reopening an earlier question, cancelled ones included, shows "Search finished." or "Search cancelled." instead of the running step.
- Enter and Return now press the focused button in the main window, as Space already did: Cases, the Rules drawer, the Cases overview, the conversation panel and the status-bar buttons included.
- `requirements.txt` no longer installs scipy: nothing in the app, the optional browser UI or the tests imports it. The packaged app already left it out.

## [0.13.0] — 2026-10-16

- Signal detail has an "Ask about this minute" field under the slider: the local model answers from that case's rows within 10 minutes of the cursor, the flagged episode covering it and its thresholds, with the same checks as research answers. The answer is labeled with the minute and case, stays out of the conversation panel and later questions, and clears when the minute or case changes or signal detail is left.
- Research answer checks preserve signed range endpoints and supported calculations, catch disguised numbers and model-written verification markers, and reject rewrites that introduce advice even when no extracts were loaded.
- Research answers are checked number by number against the source extracts and the question: a number found in neither is sent back for one rewrite, and any still unsupported is replaced with "[number not in sources]" and a note, so a fabricated measurement no longer reads as a record value.
- Full-EMR scans write `corpus_profile.json` and `corpus_profile.md`: percentiles per signal, rule and phase counts and the phase-source share, merged from small per-shard histograms (precision is one bin width; counts under 11 are shown as `<11`; no PIDs or timestamps). Shards that fail are left out and counted.
- Full-EMR scans record peak memory, requested and used workers, and phase-source counts; profiles show the events percentage, and `scripts/bench_scan.sh` compares worker counts in separate calibration outputs.
- Runs and full-EMR scans record their thresholds version; signal detail and the Rules drawer show the recorded version and say when thresholds have changed or were not recorded.
- A failed full-EMR scan shows only a short code, such as `load:KeyError`, in the scan details, status bar and dialog; the full exception text and traceback go to `scan-errors.log` in the processed folder, so file paths and patient values no longer appear on screen or in `corpus_meta.json`.
- Research is the sole desktop workspace; Cases has optional charts and summary metrics, F has a More signals chooser and grounded context, and Rules includes scoring and Age-40 MAC. Local AI controls are in the question card.
- Research can import a local CSV/JSONL article catalog and search Articles separately from Cases, with FTS ranking, optional cached embeddings, and answers citing at most five abstracts. A file with a damaged line or an oversized field is reported with its line and imports nothing; case-ID-like tokens are removed from article answers.
- In F, the More signals you choose now stay for the session across episode selection, clearing and other cases, with a note when a chosen signal has no samples in the case; the buttons under the sample line stack so F no longer scrolls sideways beside the Rules drawer at 900 px. Cases charts are drawn to fit their areas, and the chart areas have names and a tab order that follows the page.
- Research recognises scenario questions (age or age range, sex, procedure, anesthetic agent) and comparisons (two or more PIDs, or "compare"/"vs"): scenarios search only matching cases and say which filter left none, comparisons use the named PIDs, and the board's scope line shows the scenario or the cases compared. Answers still come from at most three source extracts and the model is no longer told to call tools it does not have. Article answers get the same number check as case answers.

## [0.12.1] — 2026-10-16

### Fixed

- The pipeline no longer stops when its progress output goes to a Windows pipe or file, whose legacy code page (cp1252, cp437) cannot encode `…` or `→`. Progress lines are now plain ASCII. 0.12.0 was not published: its Windows build failed on this.

## [0.12.0] — 2026-10-16 (not published)

This release also carries the 0.11 work (#27, #28); 0.11.0 was not published separately.

### Added

- Research and Ask answers render Markdown formatting with inert links and image alt text; model HTML is displayed as text.

- Settings and Setup let you choose Default, Lenient or Strict rules for the next pipeline run or full-EMR scan; loaded data keeps its recorded preset.

- Research search finds cases for questions about flags and signals, such as "Which cases have ETCO2 flags?" or "Which cases have missing ETCO2?", even without an embedding model: analyzed cases carry their signals, raised rules and signal gaps in the search text, and keyword matching ignores question words and punctuation and matches whole words.

- Signal detail shows flag episodes, a minute-aligned flag lane, current rule thresholds, and episode measurements with missing-minute counts. Traces retain fractional minutes and stay readable when the window is resized; empty lane clicks leave the selection unchanged.

- Research has a Rules button (Ctrl+R) that opens a searchable list of the loaded
  preset's rules beside the evidence board; Escape closes it. Outside the
  workspace Ctrl+R still reloads processed data.
- Research answers stay in a conversation beside the evidence board, with cited case links, saved boards, and controls to revisit or repeat each question. New question clears the board and keeps the conversation; Reset asks before clearing both. After processed data is reloaded, earlier answers stay but their saved cases must be reopened for current data.

- The Research view has a Cases button that opens a sortable list of the analyzed cases (PID, procedure, age, sex, duration, anomaly score, warn and critical counts, top rules by name). It states its scope, switches to the full-EMR scan when one exists, and opens a case in the signal view with a click or Enter; Back or Escape returns to the list.
- The Rule reference, the co-pilot's rule lookup, the dashboard rule table and the
  calibration profile read one rule catalog. The reference lists phase skips and
  follows the active preset instead of always showing `default`.
- `scripts/calibrate.sh <emr-dir> <name>` writes a profile into the ignored
  `calibration/` folder. The profile header names the preset, thresholds hash,
  case count and seed; `--diff` compares two profiles per rule and phase;
  threshold shares respect `skip_phases`; minutes and gaps from fewer than
  `--min-cell` cases are suppressed.
- Research search stays complete on a partly built index: records without an
  embedding rank by keywords and keep a share of the shortlist, and G's scope
  label states how many records were embedded. PIDs are no longer embedded, and
  cached vectors are keyed by the embedding model's digest.
- Research and full-EMR scans have Cancel controls. Research cancellation restores
  the previous board, and Reset remains usable. Scan cancellation keeps the previous scan.
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
- The local chat model now reads each answer sentence and labels it as a
  description or as clinical advice, in one extra call per draft (a rewritten
  draft is labeled again; about 0.8 s for six sentences on a 9B model). A
  sentence labeled as advice sends the draft back for the same single rewrite as
  the word-pattern check, and one still present afterwards is named in the
  verification note. On 40 held-out sentences the
  model caught 20 of 20 advice sentences and flagged none of 20 descriptions,
  where the word patterns alone caught 1 of 20. When the model does not reply
  with a label per sentence, the answer ships with the pattern check alone and
  says so.

### Changed

- Expected transition behavior is no longer flagged by default: `agent_drift`,
  `agent_fi_et_gap`, `agent_low_maint`, `fio2_high_long`, `etco2_low`,
  `etco2_zero_vent`, `peep_zero_long`, `fio2_room_air_vent`, `tv_low_mlkg`,
  `tv_high_mlkg`, `tv_abs_extreme`, `rr_low` and `rr_high` skip the phases where
  the value is usually routine (for example wash-in and wash-out, pre-oxygenation,
  mask ventilation, an extubated circuit). `spo2_low`, `hr_high`, `hr_low`,
  `map_low`, `pip_high`, `pip_rising`, `peep_high`, `etco2_high`, `agent_high` and
  the composite patterns stay on in every phase. `etco2_zero_vent` stays on in
  induction and emergence (esophageal tube, disconnect) and skips only mask
  ventilation and the extubated circuit. On a 60-case synthetic set
  `agent_drift` dropped from 525 to 49 minute flags.
- The agent drift window restarts at each phase change, so induction wash-in does
  not show up as drift in the first maintenance minutes.
- Re-running the pipeline or the full-EMR scan changes flags, episodes, scores and
  the top-rules text compared with earlier runs (fewer flags in induction and
  emergence, and `rr_low` fires on RR 1-3).
- The full-EMR scan (File → Scan full EMR, or `python -m src.pipeline.corpus_scan`)
  now scores several batches at the same time on separate CPU cores. By default it
  uses your core count minus one, up to 6; `--workers N` on the command line sets it
  (`--workers 1` runs CLI scans in one process). Desktop scans use a spawn pool
  even for one shard so cancellation can stop that worker. On 600 synthetic
  surgeries with 4 cores it took 26 s instead of 75 s; the results are identical
  whatever the worker count. The progress text shows how many cores are in use, and
  each worker needs memory for one batch, so lower `--workers` on a small machine.
- Desktop scan status counts surgeries that could not be scored. Its tooltip
  shows the first failed shard's error, capped at 300 characters.
- The Research workspace reflows at 900 px, keeping its controls reachable.
- Surgeries with the same anomaly score are now listed in PID order in the scan
  results, so repeated scans give the same order.

### Fixed

- The answer check now catches hedged advice ("would likely be better", "worth
  trying a higher PEEP", "I'd suggest", "Recommended:", "ought to have extubated")
  and no longer flags record descriptions such as "Increase in PIP began at minute
  40" or "Monitor alarms were not recorded".
- Local Linux packages include the PySide6 wheel's Qt libraries when the build
  path contains parentheses, preventing a crash from loading host Qt libraries.
- The Research answer check now verifies ages, doses and case IDs of the cited
  cases. A misstated age or dose, or a PID outside the cited set, sends the draft
  back for one rewrite. A sentence naming several cases is checked against each of
  them, and an age or dose that names no case must match one of the cited cases.
  When the medication record cannot be read, the answer says doses were not checked.
- Only 16-character hex IDs and synthetic `SYN` IDs count as PIDs in answers, so
  synthetic PIDs are checked and long numbers such as sample counts are no longer
  reported as unknown PIDs. A PID typed in a question must now be complete to open
  that case.
- Processed-data load errors and corrupt saved scans show dialogs naming the
  affected paths. Missing processed folders are reported after setup completes.
- Starting and probing Ollama run in the background so the desktop remains responsive.
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
