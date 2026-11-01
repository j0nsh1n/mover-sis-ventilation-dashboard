# Changelog

## [0.0.1] - 2026-10-17
### Added
- Repo scaffold (README with educational disclaimer, agents.md, spec stub, roadmap).
- `scripts/inspect_schema.py`: read-only schema inspector that prints no patient values.
- FAKE fixture CSVs and a pytest that runs the inspector on them.
- `context.md`: M0 data summary.
- Fixtures mirror the real formats (M/D/YY H:MM vs ISO, `\N` for missing); all values are fake.
- `pytest.ini` with `addopts = -p no:cacheprovider` so pytest never writes a cache dir (one was accidentally created in the raw data folder during M0 and removed).
