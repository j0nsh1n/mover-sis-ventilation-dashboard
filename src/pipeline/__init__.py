"""Data loading, cleaning, merge, and anomaly pipeline."""

__all__ = ["run_pipeline"]


def __getattr__(name: str):
    if name == "run_pipeline":
        from .run import run_pipeline
        return run_pipeline
    raise AttributeError(name)
