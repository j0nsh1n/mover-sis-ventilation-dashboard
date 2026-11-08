# spec.md — MOVER Suite (formerly MOVER SIS Monitor)

## Problem

Researchers need a **local, offline desktop tool** to explore de-identified MOVER SIS
perioperative records (UC Irvine): align ventilator/vitals per surgery (`PID`), surface
transparent rule-based anomaly flags, and discuss **documented management patterns** via
a local LLM—without sending case text to the cloud or implying clinical decision support.

## Intended Users

- Solo / lab researchers and students on a local Linux (or Windows package) workstation
- Users with SIS EMR/wave data on disk and optional Ollama for private Q&A
- **Not** clinicians making real-time care decisions

## Required Behavior

- Load SIS EMR tables from a user-chosen folder; optional wave root; write/read processed
  parquet (`cases`, `timeseries`, `flags`, `episodes`, and optional `events`).
- Desktop UI is primary. On first run or with an invalid EMR folder, a modal Setup
  wizard validates the folder before data loading. Canceling first-run setup leaves
  the Research workspace in the setup-required state until setup is completed.
- The desktop central widget is the **Research** evidence workspace (G) in
  `ResearchWorkspace`, set from `src/desktop/app.py` `_build_ui`. The Ask, Summary,
  Case timeline, and Rule reference tabs are retired. Equivalent functions remain
  required in G, the focused signal view (F), the Cases list, and the Rules drawer.
- The Rules drawer, the co-pilot rule lookup, the Streamlit rule table and the
  calibration profile read one rule catalog (`src/rules.py`) built from
  `thresholds.yaml`. They show the preset the loaded flags were computed with, list
  each rule's skipped phases, and say when the data did not record its preset (it is
  then treated as `default`).
- A research question searches one short record per indexed surgery: procedure, age,
  sex, ventilator rows and record scope. Records for analyzed cases also carry short
  facts from the loaded processed data: the signals present, the rules flagged, and
  the signals with gaps inside their recorded span. A local embedding model ranks
  records when available; SQLite caches vectors by source-text hash and
  embedding-model ID. Search falls back to keywords when embeddings are unavailable.
  Keyword matching uses whole words, ignores case, punctuation and question words,
  treats "CO₂" as "CO2" and ignores a trailing plural "s". A record scores by the
  share of question terms it contains, counting terms in its best single fact twice,
  so three terms in one fact can outrank four terms spread across facts. The cache
  stores vectors derived from surgery metadata and, for analyzed cases, the short
  analysis facts, not article text. Search remains available without a processed
  parquet cache.
- The selected local chat model receives source extracts for at most three retrieved
  cases. An extract begins with the case's search record, including its analysis
  facts. Embedding vectors and ranking scores are not passed to the model. Without a
  chat model, users can still search and inspect cases.
- Research answers stay in a conversation panel beside the evidence board. Each turn
  keeps the question, the model badge, the answer, links to the cases it names, and a
  saved board that "Show on board" restores. "Ask again" repeats a question. Each
  question is answered from its own source extracts; earlier questions and answers are
  never sent to the model. New question clears the board and keeps the conversation.
  Reset asks before clearing both. Answers render Markdown formatting; links and
  images stay inert text and raw HTML is not interpreted.
- The Research workspace opens a retrieved case, or a case chosen in the Cases list,
  in the focused signal view (F). F shows PIP, ETCO₂, and heart rate at a selected
  minute, plus source rows, and the case's flag episodes. Episodes are listed in time
  order and marked on a flag lane under the traces. Selecting an episode, or a mark on
  the lane, shows its rule, the loaded preset's current thresholds for that rule, and
  each of the rule's signals over the episode (min, max, mean, missing minutes), adds
  the rule's other signals as extra tracks, and can open the rule in the Rules drawer.
  Traces break at missing minutes. A case with no flags says so. Returning to G
  preserves the question and comparison. Missing samples remain marked as missing.
- The Research workspace has a Rules button (Ctrl+R) that opens a searchable drawer of
  the loaded preset's rules, on G and on F. It shows each rule's signals, thresholds
  with units and skipped phases, and names the preset and the first 12 characters of
  the thresholds hash. Escape closes it.
- The Research workspace has a Cases button. It opens a sortable list of the analyzed
  cases with PID, procedure, age, sex, duration, anomaly score, warn and critical
  counts, and top rules by name. The list states its scope: the loaded sample, with
  the indexed-surgery count when a full-EMR scan knows it. When a full-EMR scan
  exists, a switch lists the scan's scored cases instead. A click or Enter opens the
  case in F; Back or Escape returns to the list. Opening a case from the Cases list
  does not change the question's evidence board.
- Reloading processed data, or loading data with a different preset, closes any open
  case, cancels pending searches and case fetches, and discards a late research answer
  with a note. The question and the conversation stay; saved cases must be reopened for
  current data.
- Research workspace (G) case and scenario questions: the user describes a patient or
  scenario; a local model answers from at most three retrieved, **grounded** source
  extracts. The answer path skips heuristic prefetch and disables corpus tools.
- Ollama lifecycle: Start / Stop / Unload / Refresh; models dir via settings → `OLLAMA_MODELS`.
- Ollama HTTP must default to **loopback only** (case text stays local unless an explicit
  env override is set for remote).
- Answer gate rejects prescriptive clinical directives and unstated case facts where
  implemented in `src/llm/agent.py` / `prompts.py`.
- Pipeline and guardrails validate config/IO; empty or missing processed data fails clearly.
- Each case's data window is the whole OR stay (`OR_start`..`OR_end` ± pad), so induction
  and emergence are included; `t_min` is minutes from incision (negative before it).
- The timeseries has one row per minute per case; minutes without data are empty rows.
  Duration, slope and rolling-window rules are measured in real minutes, never rows.
- Anomaly score counts each flagged minute once at its worst severity and composites per
  episode; cases also carry a score per observed hour.
- Synthetic data generator writes fictional cases (`SYN*` PIDs) in the SIS layout, with
  a truth file of injected anomalies; it never reads real data.
- Profile report is **aggregate only**: no PIDs, timestamps or row-level values, and case
  counts below the small-cell threshold (default 11) are suppressed.
- Co-pilot corpus tools state their scope on every search or ranking result: the
  full-EMR scan when one is loaded, otherwise the loaded sample with the EMR's
  indexed and ventilated counts. Answers must not present the sample as the whole EMR.
- The full-EMR scan scores every surgery with ventilator rows in bounded batches and
  stores per-case scores and episodes only; its scores equal the pipeline's.
- Optional Streamlit dashboard remains available for browser exploration of the same pipeline.
- Frozen Linux and Windows desktop builds check a signed manifest on GitHub
  Pages by default. Checks run in the background at launch, when the app
  becomes active, and about once per minute. Set
  `MOVER_UPDATE_MANIFEST_URL` to an empty string to disable checks, or to
  another signed HTTPS manifest URL. Source checkouts do not poll.
- The app accepts only a schema 2 envelope signed with the pinned Ed25519
  public key. The signed payload names both platform packages and their sizes
  and SHA256 digests. A newer version may download and stage automatically
  after its size and SHA256 match the signed payload. The app offers a restart
  to apply it, keeps a backup of
  the previous install, and restores that backup if the new app exits during
  its startup check. The updater refuses an install with research files inside
  it or a root `data` symlink until those paths are migrated.

## User Experience

- **Desktop (primary):** `PYTHONPATH=. python -m src.desktop` or `./scripts/run_desktop.sh`
  or installed `mover-suite` (or `mover-sis-monitor`) after `./scripts/install_local.sh`.
- Example: complete Setup → ask about pressure in laparoscopic cases in Research
  → inspect a retrieved case in F → return to the same question and comparison.
  With a selected local chat model, the answer uses bounded source extracts.
- In Research (G), type
  `55y woman, hysterectomy, sevoflurane, elevated PIP` to request a grounded
  research answer about similar cases and documented management patterns.
- **Pipeline CLI:** `PYTHONPATH=. python -m src.pipeline.run --n-cases 50 --preset default`
- **Full-EMR scan:** File → Scan full EMR, or
  `PYTHONPATH=. python -m src.pipeline.corpus_scan --preset default`
- **Profile (threshold calibration):**
  `PYTHONPATH=. python -m src.pipeline.profile --processed-dir data/processed --out profile.md`
- **Synthetic data (no MOVER access needed):**
  `PYTHONPATH=. python -m src.pipeline.synthetic --out data/synthetic/EMR --n-cases 30`,
  then run the pipeline with `--emr-dir data/synthetic/EMR --output-dir data/synthetic/processed`
- **Streamlit (optional):** `PYTHONPATH=. streamlit run src/dashboard/app.py`
- Settings: gear icon / `Ctrl+,` (paths, Ollama models dir, chat and embedding model,
  rule preset for new runs, theme). Setup opens before the app loads data on first run
  or for an invalid EMR path.
- File > Run pipeline and File > Scan full EMR use the saved rule preset (`default`
  when unset). The Rules drawer, Research questions, and case fetches use the preset
  recorded with the loaded data, or the saved preset when no data is loaded; run the
  pipeline again to apply a new preset to loaded data.
- Ctrl+R reloads processed data, except inside the Research workspace, where it opens
  the Rules drawer.
- Themes: light / dark / system. Charts are matplotlib (theme-aware); tall timelines scroll.
- A verified update appears as a **Restart to update** control. Builds older
  than v0.9.0 need one manual update to enable the default manifest URL.

## Architecture

- Language/runtime: **Python 3.14** — PINNED for agents and CI/release. (Local verified 3.14.x.)
- Updater contract tests use **Rust 1.98.1** with no third-party Rust crates.
  A Python probe drives the Python updater and Qt controller; Rust holds the
  scenarios and assertions.
- The release manifest signer is a separate Rust CLI with pinned crates.
  The Python updater verifies signatures with pinned `cryptography`.
- Frameworks (exact `==` pins in `requirements.txt`, resolved on Python 3.14; transitive
  dependencies are not locked):
  - PySide6 6.11.2 (desktop)
  - pandas / numpy / pyarrow / scipy / matplotlib / PyYAML
  - streamlit 1.64.0 (optional UI)
  - pytest / pytest-cov / pyinstaller 6.22.3 (dev & packaging)
- Storage: local filesystem — EMR CSVs, optional waves, parquet cache; prefs in
  `~/.config/mover-suite/settings.json` (override with `MOVER_CONFIG_DIR`), copied once
  from `~/.config/mover-sis-monitor/` when only that folder exists.
  The embedding cache is `case_embeddings.sqlite3` beside the settings file.
- Major components:
  - `src/pipeline/` — load → clean → merge → features → flags → run
  - `src/pipeline/synthetic.py` — fictional SIS EMR generator; `profile.py` — aggregate report
  - `src/pipeline/corpus_scan.py` — full-EMR scan in PID shards (bounded memory)
  - `src/services/` — `ensure_data` / `load_processed`; `case_fetch.py` on-demand
    case data; `retrieval.py` metadata and analysis-fact search and embedding cache
  - `src/desktop/` — PySide6 app, G research workspace, F signal view, charts,
    theme, settings UI
  - `src/llm/` — Ollama client, retrieved-source handoff, tools, agent loop,
    prompts, service lifecycle
  - `src/rules.py` — one rule catalog (labels, signals, units, phase skips,
    thresholds hash) per preset
  - `src/guardrails/` — limits, config/data/IO validation
  - `src/wave_decode.py` — Bernoulli/GE S5 cpcArchive decode (research waveforms)
  - `src/runtime_paths.py`, `search.py`, `user_settings.py`
  - `src/update.py`, `src/desktop/updates.py` — manifest checks, package staging,
    restart apply, and desktop update state
  - `rust-updater-tests/` — updater contract tests
  - `tools/update-manifest/` — signed release manifest CLI
  - `packaging/`, `scripts/build_executable.sh`, `scripts/install_local.sh`
- External services:
  - **Ollama** (optional, local HTTP, default `http://127.0.0.1:11434`)
  - **GitHub Pages** (small signed manifest) and **public GitHub Releases**
    (versioned Linux and Windows packages) for installed app updates
  - No cloud LLM/email required for core product

## Security & Privacy

- No secrets in source. Prefer environment variables / local settings JSON for paths.
- `.streamlit/secrets.toml` is gitignored (Streamlit only if used).
- Case text must not leave the machine via remote Ollama unless explicit override env is set
  (see `src/llm/ollama_client.py` and `tests/test_local_only.py`).
- Update requests carry no case data, research data, telemetry, or machine ID.
  The updater verifies an Ed25519-signed manifest using a pinned public key,
  then checks package size and SHA256. Windows install and rollback run in CI.
  The private signing key stays in the GitHub Actions secret
  `MOVER_UPDATE_SIGNING_KEY_HEX`.
- **Research / education only.** Not a medical device; not clinical decision support.
  Outputs discuss historical de-identified extracts; no live treatment orders.
- Dependencies: pin new deps when added; prefer stdlib; `requirements.txt` pins every
  direct dependency to an exact version (`==`); bump pins deliberately and rerun the tests
  and a frozen build. The PyInstaller pin is repeated in `scripts/build_executable.sh` and
  the CI/release workflows. **Dependabot is not required** for this
  repo unless the human enables it deliberately.
- Versioning: keep **semver `x.y.z`** in sync across `VERSION`, `src/__version__.py`, and
  release tags. After shipping app changes, rebuild with `./scripts/install_local.sh`.

## Validation & Tooling

- Python lint: `ruff check .` (config: `ruff.toml`, ruff 0.16.9). The rule set is
  bug-class only: `E4`, `E7`, `E9`, `F`, `B`. Style, formatting, and import-order
  rules are deliberately excluded, and `ruff format` is not used. `E402` is ignored
  only in `src/dashboard/app.py` and `src/desktop/app.py`, which put the repo root
  on `sys.path` before importing `src.*`. CI enforces it.
- Python types: `mypy` (config: `mypy.ini`, mypy 2.3.1, checks `src/` only, missing
  third-party imports ignored, unused `type: ignore` and redundant casts reported).
  `pandas-stubs` is intentionally not installed: its strict DataFrame typing would
  produce mostly false positives on this pandas-heavy code. `types-PyYAML` is
  installed. CI enforces it.
- Install the checkers with
  `pip install ruff==0.16.9 mypy==2.3.1 types-PyYAML` (they are not in
  `requirements.txt`).
- Tests (must pass for code changes):
  ```bash
  PYTHONPATH=. QT_QPA_PLATFORM=offscreen pytest -q
  ```
  CI equivalent: `PYTHONPATH=. pytest --cov=src` with `QT_QPA_PLATFORM=offscreen`.
  Five tests in `tests/test_wave_decode.py` decode the real MOVER wave tree. They are
  skipped unless `MOVER_REAL_DATA_TESTS=1`, so default and CI runs never read real
  patient data.
- Thresholds smoke:  
  `PYTHONPATH=. python -c "from src.config import load_thresholds; load_thresholds('default')"`
- Packaging checks: `PYTHONPATH=. pytest tests/test_packaging.py -q`
- Updater contract checks on Linux, from `rust-updater-tests/`:
  ```bash
  cargo fmt --all -- --check
  cargo clippy --workspace --all-targets --locked -- -D warnings
  PYTHONPATH=.. QT_QPA_PLATFORM=offscreen cargo test --workspace --locked
  ```
- Manifest signer checks, from `tools/update-manifest/`:
  ```bash
  cargo fmt --all -- --check
  cargo clippy --workspace --all-targets --locked -- -D warnings
  cargo test --workspace --locked
  ```
- Desktop contract checks, from `rust-desktop-tests/` (the harness uses `.venv/bin/python`):
  ```bash
  cargo fmt --all -- --check
  cargo clippy --workspace --all-targets --locked -- -D warnings
  QT_QPA_PLATFORM=offscreen cargo test --workspace --locked -- --test-threads=1
  ```
  The frozen Linux GLIBC and icons check is an ignored test. Run it after a
  frozen onedir exists, with `MOVER_FROZEN_BUNDLE` pointing at that onedir, not
  at an AppImage:
  `MOVER_FROZEN_BUNDLE=/path/to/onedir cargo test --locked --test frozen_bundle frozen_bundle_runs_on_glibc_2_39_and_contains_icons -- --ignored`
- Windows CI runs the Rust `windows_apply` integration test against the actual
  PowerShell install helper and rollback path, then builds and launches the
  frozen Windows app.
- Quirks: desktop/Qt tests need system Qt libs on CI; skip setup wizard under
  `QT_QPA_PLATFORM=offscreen` or `MOVER_SKIP_SETUP=1`.
- Python coverage reports do not include updater behavior exercised by the
  separate Rust contract suite.

## Project workflow (spec overrides / alignment with agents.md)

- **Push / open PRs only** when the human explicitly asks in the current conversation
  (do not open PRs “for convenience”).
- Prefer task branches when committing; default branch should stay green.
- Update `context.md` (state + handoff) and `CHANGELOG.md` (user-visible history) per `agents.md`.
- Do not put policy rules in `context.md` or `CHANGELOG.md`.

## MOVER Suite migration (approved 2026-10-27)

This section records the approved product target. Phase 1 of the approved plan
authorizes this spec update and the baseline, suite QA, and phase-handoff
documents. Application implementation, a Monitoring shell, trainer import,
commits, pushes, folder moves, and publication remain later permissions.

### Authorization and branch

- Suite documentation work uses the existing working branch
  `feat/v0.15-signal-grounding`. That branch is an explicit user override of opening
  a new task branch. The other working branch `feat/v0.14-desktop-fixes` stays.
  A third working branch is not opened for this work.
- Phase 1 scope is record the baseline, update this spec, write the suite QA record,
  and write the phase handoff. Joo approved the Phase 2 Monitoring shell
  (confirmed 2026-11-01; roadmap records it as authorized 2026-10-27).
- Each commit, push, folder move, trainer import, publication, and remote rename
  needs its own later permission.

### Current implemented behavior

The desktop is a PySide6 Python 3.14 app in this existing repository.
`src/desktop/app.py` `_build_ui` sets `ResearchWorkspace` as the central widget.
Required G, F, Cases, Rules, pipeline, one rule catalog, guardrail, updater, and
optional Streamlit behavior in this spec remains in force. Language, runtime, and
Validation & Tooling commands stay as written above. This section adds no validation
commands and changes no dependency pins.

### Approved suite target

Grow this repository into a local MOVER Suite desktop. The working product name is
MOVER Suite. The repository folder was renamed to `mover-suite` on 2026-11-01. That rename, on
its own, leaves GitHub, settings, the shipped executable, and updater identity
unchanged.

Exactly four tools:

1. SIS Monitor (Monitoring): wrap today's G and F Research workspace, Cases, and
   Rules.
2. Handoff trainer: fictional practice cases, handoff text, and grading.
3. Medication and vitals explorer: aligned drug events and measured vital signs.
4. Fictional WHO checklist simulator: authored fictional scenarios only.

Desktop UI stays PySide6. Python 3.14 remains the language for the UI and existing
processing. No new Rust component and no port of tests into Rust is granted.
Existing Rust updater, signer, and desktop contract suites stay as they are.

Monitoring opens first. View can switch to Handoff practice, Medication and
vitals, or the WHO checklist. Those pages are working practice tools, not empty
tabs. The corner label names the page that is open.

### Shared source, preset, and generation

Real-data modules share one data context, `src/services/data_context.py`. It
records the selected source, processed directory, preset and threshold identity,
and a generation number for background work. A source change, a preset
change, or a new recorded threshold hash invalidates the shared fetch caches.
A missing hash does not. The desktop clears incompatible analyzed
results through its existing reload path. Pipeline, load, and full-EMR scan
results carry the generation from when they started. A newer generation rejects
them, including when the folders are switched away and then back. Case-fetch
cache keys include the EMR path, the preset, and the threshold hash, so the same
surgery id in two sources cannot reuse the other source's frames. A fetch
resolves that EMR path once and uses it for the whole call. The context
also records the saved chat model and embedding model. Changing the embedding
model drops the prepared search engine and does not discard analyzed cases or
case fetches. Indexed surgeries stay distinct from analyzed cases. The context
is not a second loader.

The WHO simulator uses authored fictional fixtures only. It uses no EMR path,
real-case picker, retrieval engine, or patient-derived prompts.

### Identity, packaging, and QA

The product ships as MOVER Suite from 0.15.0 (approved 2026-11-08,
`_work/mover-suite/RENAME-MIGRATION-PLAN.md`). "SIS Monitor" stays the name of the
Monitoring tool.

- Visible name: window title "MOVER Suite v<version>", About, setup wizard, update
  messages, desktop entry name, Qt application name, release title, README.
- Release files: `MOVER-Suite-v<version>-linux-x86_64.tar.gz`,
  `MOVER-Suite-v<version>-windows-x86_64.zip`, `MOVER-Suite-v<version>-x86_64.AppImage`.
- Settings: `~/.config/mover-suite/`. On start, when it has no `settings.json` and
  `~/.config/mover-sis-monitor/` has one, the whole old folder is copied once and
  `migrated-from.txt` names the source and date. The old folder is never changed. If the
  copy fails, that session uses the old folder and the status bar says why.
  `MOVER_CONFIG_DIR` wins over both.
- Logs go to `~/.local/state/mover-suite/`. AppImage data uses
  `~/.local/share/mover-sis-monitor/data` when it exists, else
  `~/.local/share/mover-suite/data`.
- Kept, because installed v0.13.0 updaters depend on them: the archive folder
  `MOVER-SIS-Monitor/` and its programs `MOVER-SIS-Monitor`, `launch.sh`, and
  `MOVER-SIS-Monitor.exe`; the manifest URL and signing key; the repository name
  `mover-sis-ventilation-dashboard` (GitHub does not redirect Pages after a rename).
  Also kept: the desktop entry id `mover-sis-monitor`, the window class, the
  `install_local.sh` folder, and the `mover-sis-monitor` command (`mover-suite` is added).
- Renaming the program and archive folder would need a bridge release whose updater
  accepts both names. It is not planned.

Optional Streamlit in `src/dashboard/` stays during migration.

Carry native QA into the suite record. The original candidate AppImage and
`/home/jonathans/Projects/medical/mover/_work/appimage-fix-first/CANDIDATE-QA.md`
stay unchanged and UNRUN. Suite results do not mark that candidate passed.

### Decided practice contracts (2026-11-01)

The tracked trainer tree at `a9a6e5d` is an M0 scaffold. Its fixtures use the
fictional id `FAKE0001` only. No separate review document was found. History is at `imports/handoff-trainer` from `a9a6e5d`. `git subtree` is not
installed, so the import is an unsquashed merge that reads the trainer files
into that prefix. It does not take the untracked `core/Cargo.lock`. The Rust
placeholder is not built. The import prefix stays intact so its own fixture
paths keep working. Cases used for grading are fictional.

Handoff grading:

- Hypotension: MAP under 65 mmHg on consecutive usable readings whose span is
  more than 5 minutes. Arterial MAP (`MAP_ART`, 20–200 mmHg, gap at most 1
  minute) is used when any usable arterial reading exists. Otherwise
  noninvasive MAP is used (gap at most 6 minutes, at least two readings).
- Desaturation: SpO2 under 90% on at least two usable readings (`SP02`,
  50–100%) at most 1 minute apart. The first and last of those readings
  are on different minutes, so two readings in the same minute do not count.
- Vasopressors: Phenylephrine, Ephedrine, Vasopressin, Epinephrine,
  Norepinephrine. Inotropes: Dopamine, Dobutamine, Milrinone, Isoproterenol.
  Phenylephrine nasal is excluded. A missing end time is a bolus. A later end
  time is an infusion.
- A mentioned item names the finding and one concrete detail the timeline
  supports, such as the drug, a minute inside the episode, or 65 / 90. A
  keyword, or a generic word such as sustained, arterial, or minute, is
  missed. A minute or value the episode does not show is unsupported, and
  that episode stays missed. A missing MAP or SpO2 series, including an
  empty timeline, is its own item: name the gap, for example "SpO2 missing",
  or the grade is missed. Stating a value for a missing series is unsupported.
- A minute or value counts only for the finding it is written with: the
  finding named just before it in the same sentence, or the sentence's first
  finding when it comes earlier. A minute inside another episode of the same
  kind belongs to that episode. A drug given a minute that none of its doses
  show is unsupported, and that drug stays missed.
- The page does not recommend treatment. Cases are fictional only. A
  real-case practice path is not included. Replaying a real MOVER case is a
  future idea that waits for the Phase 6 calibration on real data and needs
  its own approval. The imported scaffold's README and spec stub say the same.

Medication and vitals display:

- Rows show the drug or measurement, the value, the unit, bolus versus
  infusion, the time, and the source (`patient_medication.csv` or the case
  timeseries). A missing dose or measurement stays missing.
- Drug clocks and vital minutes share an axis only when the medication rows
  already use minutes or a case start is known. Otherwise both times are shown
  and the page says they are not shifted.
- The page says it does not explain why a drug was given and does not say what
  to do next. With no open Monitoring case, it shows a fictional demo. An open
  case reuses `load_case_medications` and the timeseries already on screen.

WHO checklist practice:

- Two authored cards use the stage order Sign in, Time out, Sign out. Item
  wording is practice text for those cards, not a copy of a published checklist
  and not a patient record.
- A later stage stays closed until the open stage is complete. An item that is
  not on a card must be marked that way. Saving writes local practice state.
  This module does not import the EMR, retrieval, or data-context stack.

### Deferred decisions

These remain open:

- Whether to later add `.venv.old-premove/` to the committed `.gitignore`.
- Any new Rust function or test-language port.
- A new suite candidate. The portable-build record does not contain a container
  command to copy, so no host build is substituted.
- Push, publication, and remote rename.

Live AI in native QA stays paused. Plan approval does not resume paused model calls.

Every module keeps the research-and-education framing. The suite is not a medical
device and is not clinical decision support.

## Acceptance Criteria

- [x] Desktop opens Setup before data loading when the EMR folder is unconfigured or
      invalid, then opens Research (G) as the central workspace. Equivalent G, F,
      Cases, and Rules functions remain available. The Ask, Summary, Case timeline,
      and Rule reference tabs stay retired. (suite QA 11.4 E2 and 1.1–1.5, 2026-11-07; tests)
- [x] A research question searches indexed surgery metadata, plus analysis facts for
      analyzed cases, with cached embeddings when available and whole-word keyword
      fallback otherwise, including when processed parquet is absent (then without
      facts). Questions such as "Which cases have ETCO2 flags?" find analyzed cases
      without an embedding model. It loads at most three case extracts for a selected
      local chat model; vectors and ranking scores do not enter the answer context. (retrieval and drive tests; embeddings not yet measured with a real model)
- [x] A retrieved case opens in F with source signal rows and missing values visible,
      and its flag episodes are listed; selecting one shows its thresholds and
      per-signal measurements with missing-minute counts. Returning to G preserves the
      question and comparison. (suite QA 11.4 3.2–3.5; episode detail and return covered by tests)
- [x] The Rules drawer (Ctrl+R) lists the loaded preset's rules on G and F, and Escape
      closes it. (tests; suite QA 11.4 3.7 opened it by button)
- [x] The Cases list opens a case in F and Back or Escape returns to it. (suite QA 11.4 3.2 and 3.6)
- [x] Each research question adds a turn to the conversation panel; "Show on board"
      restores that turn's board, and earlier turns never reach the model. (tests with a scripted model; live AI paused)
- [x] The five prototype questions pass as driver scenarios with a scripted model and
      without one (`tests/test_desktop_drive.py`). (full offscreen suite, 2026-11-07)
- [x] Pipeline produces parquet under processed dir for a small `--n-cases` sample. (suite QA 11.4 2.1–2.2; tests)
- [x] On synthetic data every injected anomaly raises its expected rules and clean cases
      raise no warn/critical flags (`tests/test_synthetic.py`). (full offscreen suite, 2026-11-07)
- [x] Profile output contains no PIDs or timestamps (`tests/test_profile.py`). (full offscreen suite, 2026-11-07)
- [x] LLM path refuses non-loopback Ollama URLs without override (`tests/test_local_only.py`). (full offscreen suite, 2026-11-07)
- [x] `PYTHONPATH=. QT_QPA_PLATFORM=offscreen pytest -q` exits 0. (2026-11-07)
- [x] `ruff check .` and `mypy` exit 0. (2026-11-07)
- [x] The Rust updater contract checks pass on Linux with Python 3.14 and Qt
      available. An empty manifest URL disables requests in a frozen app. (CI on PR #38)
- [x] The Rust signer checks pass and the Windows apply test passes in CI. (CI on PR #38)
- [x] The public Pages manifest has a valid signature and names both
      published packages with matching size and SHA256. (v0.13.0 manifest verified with the pinned key, 2026-11-07; sizes match the release)
- [x] `VERSION` matches `src/__version__.py`. (0.15.0 in both)
- [x] `CHANGELOG.md` updated for user-visible changes. (Unreleased section current)
- [x] CI runs on **Python 3.14**. (`ci.yml`, `release.yml`)

Suite criteria (added 2026-11-07, from "Approved suite target", "Shared source,
preset, and generation", and "Decided practice contracts"):

- [x] Monitoring opens first; View switches to Handoff practice, Medication and
      vitals, and the WHO checklist and back; the corner label names the open
      page (tests; suite QA 12.8).
- [x] A source change, a preset change, or a new recorded threshold hash
      invalidates shared fetch caches; older-generation results are rejected;
      case-fetch keys include the EMR path, preset, and threshold hash (tests).
- [x] Handoff grading marks an item mentioned only for a supported detail of that
      finding, and a minute or value the episode does not show leaves only that
      episode missed (tests; suite QA 12.2 passes on a source-build recheck,
      2026-11-08).
- [x] Medication and vitals rows show value, unit, bolus versus infusion, time,
      and source; missing values stay missing; the page gives no reason or
      advice (tests; suite QA 12.4–12.5).
- [x] The WHO checklist runs Sign in, Time out, Sign out on authored cards; a
      later stage stays closed until the open one is complete; saving writes
      local practice state only (tests; suite QA 12.6–12.7).
- [x] Practice pages are keyboard and screen-reader usable: Tab leaves the text
      fields and does not walk table cells, combos announce their purpose, and
      the window title names the open page (tests; suite QA 12.1 and 12.9 pass
      on recheck, 2026-11-07).
