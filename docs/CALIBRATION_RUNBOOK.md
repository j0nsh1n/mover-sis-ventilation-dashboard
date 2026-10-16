# Calibration runbook (roadmap Phase 6)

These steps run on the real MOVER data on this machine. Only the operator runs them.
Agents never read the real data or its outputs. A model may see an aggregate report
only after the operator has read it and decided it can be shared.

## What stays local
- `calibration/<name>/processed/` holds per-case parquet (PIDs, minute rows, flags).
  Never share it.
- `calibration/bench-*/w*/` holds full-EMR scan output with per-case scores. Never
  share it.
- `scan-errors.log` in a scan's output folder can hold paths and values. Never share it.
- Shareable after you read them: `calibration/<name>/profile.md` and `profile.json`, and
  a scan's `corpus_profile.md` and `corpus_profile.json`. These are aggregate only, with
  counts under 11 shown as `<11`.

## 1. Baseline profile
```bash
CALIBRATION_N_CASES=200 ./scripts/calibrate.sh /path/to/real/EMR before
```
This writes `calibration/before/profile.md`. Read the header: preset, thresholds
SHA-256, case count and seed. The run's `processed/run_meta.json` records the same
thresholds hash, which F shows as "Thresholds when flagged".

## 2. Phase event names
In `profile.md`, read the "Procedure event names" table and the headline line
`phase_source events: X% of cases`. Edit the `phases:` patterns in
`src/config/thresholds.yaml` so the intubation and extubation names you see are
matched. The current patterns are guesses, marked UNVERIFIED. Then remove that
UNVERIFIED comment. Rerun step 1 under a new name (`after-phases`) until `events`
is the largest `phase_source` share.

## 3. Thresholds from percentiles
For each rule, compare its threshold with the signal percentiles in `profile.md`
(p5 to p95). For the whole EMR, use a full scan's `corpus_profile.md` (step 5).
Change one preset at a time in `thresholds.yaml`. After each edit, F shows "The
thresholds changed since these flags were computed" for data computed earlier, so it
is clear which flags are stale.

## 4. Before-and-after flag table
```bash
CALIBRATION_N_CASES=200 ./scripts/calibrate.sh /path/to/real/EMR after
PYTHONPATH=. python -m src.pipeline.profile --diff calibration/before/profile.json calibration/after/profile.json > calibration/diff.md
```
`diff.md` is the table the roadmap asks for. It shows per-rule changes and both
thresholds hashes.

## 5. Full-EMR scan timing and profile
```bash
./scripts/bench_scan.sh /path/to/real/EMR 1 4 6 8
```
This writes `calibration/bench-<time>/bench.md`, with seconds, peak memory of the
parent and of the largest worker, and scanned cases for each worker count. Each run's
`corpus_meta.json` has `cases_by_phase_source`. Its `corpus_profile.md` has
whole-EMR percentiles merged from shards. On this 16-core host, the app's automatic
choice tops out at 6 workers.

## 6. Synthetic check
```bash
PYTHONPATH=. QT_QPA_PLATFORM=offscreen pytest -q tests/test_synthetic.py
```
Every injected synthetic anomaly must still raise its expected rules after the edits.

## Done when (roadmap Phase 6)
- Synthetic anomalies still flag (step 6).
- A before-and-after flag table is recorded (step 4).
- `phase_source = events` dominates on real data (step 2 headline).
- `bench.md` is recorded (step 5).
Then commit `thresholds.yaml` with the before and after hashes in the message, and mark
Phase 6 in `roadmap.md`.
