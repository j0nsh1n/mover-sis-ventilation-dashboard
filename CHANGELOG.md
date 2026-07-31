# Changelog

All notable user-visible changes to this project are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Project governance set: filled `spec.md`, `roadmap.md`, `context.md`, `CHANGELOG.md`;
  global rules in `agents.md`; `AGENTS.md` is a pointer only.
- CI and release workflows target **Python 3.14**.
- CodeQL workflow under `.github/workflows/codeql.yml` (Python).

### Changed

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
