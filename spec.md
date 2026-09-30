# spec.md — MOVER SIS Monitor

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
  the data tabs disabled until setup is completed.
- The first desktop tab is the **Research** evidence workspace (G). The existing Ask,
  Summary, Case timeline, and Rule reference tabs remain available.
- A research question searches one short metadata record per indexed surgery. A local
  embedding model ranks records when available; SQLite caches vectors by source-text
  hash and embedding-model ID. Search falls back to keywords when embeddings are
  unavailable. The cache stores vectors derived from surgery metadata, not
  article text. Search remains available without a processed parquet cache.
- The selected local chat model receives source extracts for at most three retrieved
  cases. Embedding vectors and ranking scores are not passed to the model. Without a
  chat model, users can still search and inspect cases.
- The Research workspace opens a retrieved case in the focused signal view (F).
  F shows PIP, ETCO₂, and heart rate at a selected minute, plus source rows.
  Returning to G preserves the question and comparison. Missing samples remain
  marked as missing.
- Ask tab case analysis: user describes a patient or scenario; the app pre-fetches
  tools, then a local model answers from **grounded** extracts only.
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
  or installed `mover-sis-monitor` after `./scripts/install_local.sh`.
- Example: complete Setup → ask about pressure in laparoscopic cases in Research
  → inspect a retrieved case in F → return to the same question and comparison.
  With a selected local chat model, the answer uses bounded source extracts.
- The Ask tab remains available for case analysis. For example, type
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
  theme). Setup opens before the app loads data on first run or for an invalid EMR path.
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
- Frameworks (minimum versions in `requirements.txt`; not fully upper-bound locked):
  - PySide6 ≥ 6.6 (desktop)
  - pandas / numpy / pyarrow / scipy / matplotlib / PyYAML
  - streamlit ≥ 1.28 (optional UI)
  - pytest / pyinstaller (dev & packaging)
- Storage: local filesystem — EMR CSVs, optional waves, parquet cache; prefs in
  `~/.config/mover-sis-monitor/settings.json` (override with `MOVER_CONFIG_DIR`).
  The embedding cache is `case_embeddings.sqlite3` beside the settings file.
- Major components:
  - `src/pipeline/` — load → clean → merge → features → flags → run
  - `src/pipeline/synthetic.py` — fictional SIS EMR generator; `profile.py` — aggregate report
  - `src/pipeline/corpus_scan.py` — full-EMR scan in PID shards (bounded memory)
  - `src/services/` — `ensure_data` / `load_processed`; `case_fetch.py` on-demand
    case data; `retrieval.py` metadata search and embedding cache
  - `src/desktop/` — PySide6 app, G research workspace, F signal view, charts,
    theme, settings UI
  - `src/llm/` — Ollama client, retrieved-source handoff, tools, agent loop,
    prompts, service lifecycle
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
- Dependencies: pin new deps when added; prefer stdlib; current `requirements.txt` uses
  minimum versions (tighten over time if desired). **Dependabot is not required** for this
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

## Acceptance Criteria

- [ ] Desktop opens Setup before data loading when the EMR folder is unconfigured or
      invalid, then opens Research (G) with Ask, Summary, Case timeline, and Rule
      reference available.
- [ ] A research question searches indexed surgery metadata with cached embeddings
      when available and keyword fallback otherwise, including when processed
      parquet is absent. It loads at most three case extracts for a selected local
      chat model; vectors and ranking scores do not enter the answer context.
- [ ] A retrieved case opens in F with source signal rows and missing values visible;
      returning to G preserves the question and comparison.
- [ ] Pipeline produces parquet under processed dir for a small `--n-cases` sample.
- [ ] On synthetic data every injected anomaly raises its expected rules and clean cases
      raise no warn/critical flags (`tests/test_synthetic.py`).
- [ ] Profile output contains no PIDs or timestamps (`tests/test_profile.py`).
- [ ] LLM path refuses non-loopback Ollama URLs without override (`tests/test_local_only.py`).
- [ ] `PYTHONPATH=. QT_QPA_PLATFORM=offscreen pytest -q` exits 0.
- [ ] `ruff check .` and `mypy` exit 0.
- [ ] The Rust updater contract checks pass on Linux with Python 3.14 and Qt
      available. An empty manifest URL disables requests in a frozen app.
- [ ] The Rust signer checks pass and the Windows apply test passes in CI.
- [ ] The public Pages manifest has a valid signature and names both
      published packages with matching size and SHA256.
- [ ] `VERSION` matches `src/__version__.py`.
- [ ] `CHANGELOG.md` updated for user-visible changes.
- [ ] CI runs on **Python 3.14**.
