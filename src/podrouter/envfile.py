"""Read `.env` and mask secret values found in the process environment."""

from __future__ import annotations

import os
from pathlib import Path

from podrouter.paths import data_home

_SECRET_NAMES = (
    "RUNPOD_API_KEY",
    "HF_TOKEN",
    "VLLM_API_KEY",
    "MODEL_API_KEY",
    "TS_AUTHKEY",
    "CF_TUNNEL_TOKEN",
    "XAI_API_KEY",
)


def load_dotenv(path: Path | None = None) -> None:
    """Fill empty process env from `.env`. Never overrides a value already set."""
    file = path or (data_home() / ".env")
    if not file.is_file():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def secret_values() -> list[str]:
    """Secret values currently set in the environment, longest first."""
    found = [os.environ.get(name, "") for name in _SECRET_NAMES]
    return sorted({v for v in found if v}, key=len, reverse=True)


def redact(text: str) -> str:
    redacted = text
    for secret in secret_values():
        redacted = redacted.replace(secret, "***")
    return redacted
