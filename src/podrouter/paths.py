"""Where podrouter keeps user data (`.env`, saved Pod YAML files).

The Mac app sets `PODROUTER_HOME` (`~/podrouter` for the installed app, the
repo folder for a git clone). Without it, the repo that holds this package is
used, which is right for `pip install -e .`.
"""

from __future__ import annotations

import os
from pathlib import Path


def data_home() -> Path:
    raw = os.environ.get("PODROUTER_HOME", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path(__file__).resolve().parents[2]
