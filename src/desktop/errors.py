"""Keep diagnostic details in a local log and return safe desktop messages."""

from __future__ import annotations

import os
import traceback
from datetime import UTC, datetime

from src.user_settings import settings_path


def error_message(operation: str, exc: BaseException) -> str:
    code = f"{operation}:{type(exc).__name__}"
    try:
        path = settings_path().parent / "app-errors.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            # The creation mode does not apply to a log that already exists
            os.fchmod(handle.fileno(), 0o600)
            handle.write(f"[{datetime.now(UTC).isoformat(timespec='seconds')}] {code}\n")
            handle.write("".join(traceback.format_exception(exc)) + "\n")
    except OSError:
        return f"The operation could not finish ({code}). The diagnostic log could not be written."
    return f"The operation could not finish ({code}). Details are in app-errors.log in the settings folder."
