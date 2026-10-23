"""The installed Mac app keeps data in PODROUTER_HOME, not in the repo."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from podrouter.local_schedule import generate_wrapper
from podrouter.paths import data_home
from podrouter.web import load_dotenv


def test_data_home_follows_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PODROUTER_HOME", str(tmp_path))
    assert data_home() == tmp_path
    monkeypatch.delenv("PODROUTER_HOME")
    assert (data_home() / "pyproject.toml").is_file()


def test_load_dotenv_reads_data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("PODROUTER_PATHS_TEST=from-home\n")
    monkeypatch.setenv("PODROUTER_HOME", str(tmp_path))
    monkeypatch.delenv("PODROUTER_PATHS_TEST", raising=False)
    load_dotenv()
    assert os.environ.pop("PODROUTER_PATHS_TEST") == "from-home"


def test_wrapper_without_repo_uses_running_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PODROUTER_HOME", str(tmp_path))
    script = generate_wrapper()
    assert f'exec "{Path(sys.executable)}" -m podrouter' in script
    assert f'--env-file "{tmp_path.resolve() / ".env"}"' in script
