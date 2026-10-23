"""Parse the hour/minute durations the sample config accepts."""

from __future__ import annotations

import re

from podrouter.errors import ConfigError

# "3h", "30m", and combinations of those same forms ("1h30m").
_DURATION = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?$")


def parse_duration(value: object, *, field: str) -> int:
    """Return seconds for `Nh`, `Nm`, or `NhNm`.

    Bare numbers, days, and seconds are rejected. Zero is rejected.
    """
    if not isinstance(value, str):
        raise ConfigError(f"{field} must be a duration like 3h or 30m")
    match = _DURATION.fullmatch(value.strip())
    if match is None or (match.group(1) is None and match.group(2) is None):
        raise ConfigError(f"{field} must be a duration like 3h or 30m")
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = hours * 3600 + minutes * 60
    if seconds <= 0:
        raise ConfigError(f"{field} must be greater than zero")
    return seconds
