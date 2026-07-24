# Data directory

SIS EMR files are **not** stored in this git repository (size + MOVER data use agreement).

## Setup

1. Obtain MOVER SIS EMR after signing the [DUA](https://mover.ics.uci.edu/).
2. Extract into this project:

```bash
mkdir -p data/raw
tar -xzf /path/to/sis_emr.tar.gz -C data/raw
# expects data/raw/EMR/patient_*.csv
```

3. Run the pipeline:

```bash
PYTHONPATH=. python -m src.pipeline.run --n-cases 50
```

Outputs land in `data/processed/` (also gitignored).
