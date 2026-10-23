"""Pod names this tool must never create, stop, or schedule.

Set `PODROUTER_PROTECTED_PODS` in `.env` as a comma-separated list, for example
`PODROUTER_PROTECTED_PODS=team-pod,old-notebook`. It is empty by default.

Pods this tool did not create are already left alone (only pods tagged
`PODROUTER_MANAGED` are listed or stopped). This list adds a second guard for
names you never want touched, even by mistake.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

ENV_NAME = "PODROUTER_PROTECTED_PODS"


def protected_names(environ: Mapping[str, str] | None = None) -> frozenset[str]:
    env = os.environ if environ is None else environ
    raw = env.get(ENV_NAME, "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def is_protected(name: str | None, environ: Mapping[str, str] | None = None) -> bool:
    return bool(name) and name in protected_names(environ)
