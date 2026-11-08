# Rules for AI agents working in this repo

1. Educational trainer only. Never add treatment advice, dosing guidance, or wording that
   implies clinical decision support. Rule definitions stay visible to the user.
2. Raw data (`/var/mnt/games/EMR`, `/var/mnt/games/sis_wave_v2`) is strictly read-only.
   Never write, move, rename or chmod anything there.
3. Never commit data: no CSV, Parquet, or derived case files. `.gitignore` enforces
   `data/`, `processed/`, `*.csv`, `*.parquet`, `.env`. The only CSVs allowed are the
   FAKE fixtures in `tests/fixtures/` (force-added, made-up values).
4. Never print, log, or paste patient values (PIDs, ages, weights, procedure text,
   individual timestamps) into code, commits, issues, chat, or test output.
   Aggregate counts and schema facts are fine.
5. Don't delete or loosen tests to make a change pass.
6. Read `context.md` before touching data code; update `CHANGELOG.md` and `roadmap.md`
   with each milestone.
7. No pushing or remotes until Joo says so.
8. Run pytest only from the repo root (pytest.ini disables the cache). Never use the raw data folder as the working directory for anything that writes.
