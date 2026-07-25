"""Native desktop application (PySide6)."""

__all__ = ["main"]


def main() -> int:
    from src.desktop.app import main as _main

    return _main()
