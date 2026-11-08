# Anesthesia Case Reconstruction & Handoff Trainer (working name)

> **Educational use only. This is NOT clinical decision support.** It replays past,
> de-identified anesthesia cases for teaching handoff communication. It makes no
> diagnosis or treatment recommendations and must never be used for patient care.

## Status in MOVER Suite

This folder is the imported M0 scaffold, kept so its history and fixture paths stay intact.
The working trainer is the suite's Handoff practice page (`src/modules/handoff_trainer/`).
It uses authored fictional cases only: no EMR folder, no real-case picker, and no MOVER
patient rows. Its event and grading rules are the "Decided practice contracts" in the
suite's `spec.md`, which wins over this folder's `spec.md` stub. The Rust crate and the
Streamlit app here are placeholders and are not built.

Replaying a real MOVER case is a future idea, not a plan. It waits until the Phase 6
calibration on real data is done (suite `roadmap.md`) and needs its own approval.

## Original scaffold plan

The M0 scaffold planned a retrospective trainer: pick a past case from the MOVER SIS
dataset, see a synchronized timeline (vitals, medications, fluids, procedure events),
write an I-PASS handoff in free text, and get scored on which detected events you
mentioned, missed, or claimed without support. The suite build replaced the real-case
part with fictional cases.

## Layout
- `core/` Rust crate (PyO3/maturin) for preprocessing and event detection (placeholder)
- `trainer/` Python package: case loading, keyword scoring, I-PASS model (stub)
- `app/` Streamlit UI (stub)
- `scripts/inspect_schema.py` read-only schema inspector (prints no patient values)
- `tests/` pytest suite; `tests/fixtures/` holds FAKE CSVs with real column names only
- `context.md` data facts from M0; `roadmap.md` milestones; `agents.md` rules for AI helpers

## Data
The dataset is under a data use agreement. It is never committed. Raw data lives outside
the repo (`/var/mnt/games/EMR`, read-only); derived files go to `/var/mnt/games/processed`.
Copy `config.example.toml` to `config.toml` to point at your paths.

## Dev
```
python3 -m pytest tests
python3 scripts/inspect_schema.py --raw-dir tests/fixtures
```
