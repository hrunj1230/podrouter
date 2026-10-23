"""Keep resolved secret values out of stdout and stderr."""

from __future__ import annotations

import sys


class Redactor:
    """Replace known secret values with a fixed mask."""

    def __init__(self, secrets: list[str]) -> None:
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def text(self, value: str) -> str:
        redacted = value
        for secret in self._secrets:
            redacted = redacted.replace(secret, "***")
        return redacted

    def stdout(self, message: str) -> None:
        print(self.text(message), file=sys.stdout, flush=True)

    def stderr(self, message: str) -> None:
        print(self.text(message), file=sys.stderr, flush=True)
