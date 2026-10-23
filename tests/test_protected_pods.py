"""Protected pod names (PODROUTER_PROTECTED_PODS) across bring_up, shut_down, and the timer."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from podrouter import cli
from podrouter.client import RunPodClient
from podrouter.config import Config
from podrouter.local_schedule import tick
from podrouter.pods import CONFIG_HASH, MANAGED, NAME
from podrouter.protect import is_protected
from podrouter.provision import bring_up, shut_down
from podrouter.redact import Redactor


@pytest.fixture(autouse=True)
def _protect_keep_me(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PODROUTER_PROTECTED_PODS", " keep-me , team-pod ")


def test_bring_up_refuses_protected(capsys: pytest.CaptureFixture[str]) -> None:
    cfg = MagicMock(spec=Config)
    cfg.name = "keep-me"
    client = MagicMock(spec=RunPodClient)
    log = Redactor([])

    ret = bring_up(cfg, client, wait=False, log=log)

    assert ret == 2
    assert not client.mock_calls
    captured = capsys.readouterr()
    assert "error" in captured.err.lower()
    assert "keep-me" in captured.err


def test_shut_down_refuses_protected(capsys: pytest.CaptureFixture[str]) -> None:
    client = MagicMock(spec=RunPodClient)
    log = Redactor([])

    ret = shut_down(client, name="keep-me", all_pods=False, log=log)

    assert ret == 2
    assert not client.mock_calls
    captured = capsys.readouterr()
    assert "error" in captured.err.lower()
    assert "keep-me" in captured.err


def test_shut_down_all_pods_never_terminates_protected() -> None:
    client = MagicMock(spec=RunPodClient)
    log = Redactor([])

    client.get_pods.return_value = [
        {
            "id": "pod-safe",
            "name": "other-pod",
            "desiredStatus": "RUNNING",
            "env": {MANAGED: "1", NAME: "other-pod", CONFIG_HASH: "hash1"},
        },
        {
            "id": "pod-keep",
            "name": "keep-me",
            "desiredStatus": "RUNNING",
            "env": {MANAGED: "1", NAME: "keep-me", CONFIG_HASH: "hash2"},
        },
    ]

    ret = shut_down(client, name=None, all_pods=True, log=log)

    assert ret == 0
    client.terminate.assert_called_once_with("pod-safe")
    # Verify pod-keep was never passed to terminate
    for call in client.terminate.call_args_list:
        assert call.args[0] != "pod-keep"


def test_schedule_tick_protected_exits_2_without_runpod_call(tmp_path: Path) -> None:
    sched_file = tmp_path / "schedule.json"
    sched_file.write_text(
        json.dumps(
            {
                "pod_name": "keep-me",
                "config_path": str(tmp_path / "pod.yaml"),
                "windows": [
                    {"weekdays": [0, 1, 2, 3, 4], "on": "09:00", "off": "18:00"}
                ],
            }
        ),
        encoding="utf-8",
    )
    state_file = tmp_path / "state.json"

    called_runpod = False

    def mock_provider(_key: str) -> tuple[set[str], bool]:
        nonlocal called_runpod
        called_runpod = True
        return set(), True

    with pytest.raises(Exception) as exc_info:
        tick(
            schedule_path=sched_file,
            state_path=state_file,
            now="2026-10-23T10:00:00",
            live_pods_provider=mock_provider,
        )
    assert not called_runpod
    assert "keep-me" in str(exc_info.value)

    # Also test via cli.main
    code = cli.main(
        ["schedule", "tick", "--schedule", str(sched_file), "--state", str(state_file)]
    )
    assert code == 2
    assert not called_runpod


def test_protected_list_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert is_protected("keep-me")
    assert is_protected("team-pod")
    assert not is_protected("other-pod")
    assert not is_protected("")
    monkeypatch.delenv("PODROUTER_PROTECTED_PODS")
    # Nothing is protected by name when the setting is empty.
    assert not is_protected("keep-me")
