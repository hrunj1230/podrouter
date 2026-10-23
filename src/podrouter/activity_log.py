"""Append-only activity log shared by the timer and the podrouter Mac app.

One JSON object per line in ``logs/activity.jsonl`` (mode 0600). When the file
passes ``MAX_BYTES`` it is moved to ``activity.1.jsonl`` and a new file starts,
so at most two files are kept. Callers must pass already-redacted text.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

LOG_NAME = "activity.jsonl"
ROTATED_NAME = "activity.1.jsonl"
MAX_BYTES = 1_000_000
_DETAIL_LIMIT = 2000


def log_path_for(base_dir: Path) -> Path:
    """Log file inside ``<base_dir>/logs``; base_dir is the podrouter data folder."""
    return base_dir / "logs" / LOG_NAME


def append_event(
    path: Path,
    *,
    source: str,
    action: str,
    pod: str,
    ok: bool,
    message: str = "",
    detail: str = "",
    now: datetime | None = None,
    max_bytes: int = MAX_BYTES,
) -> None:
    """Write one event. Logging problems never break the caller."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        if path.exists() and path.stat().st_size > max_bytes:
            os.replace(path, path.with_name(ROTATED_NAME))
        record = {
            "time": (now or datetime.now()).isoformat(timespec="seconds"),
            "source": source,
            "action": action,
            "pod": pod,
            "ok": ok,
            "message": message.strip()[:300],
            "detail": detail.strip()[-_DETAIL_LIMIT:],
        }
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
