"""Unit tests for local schedule engine and CLI."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from podrouter import cli
from podrouter.config import ConfigError
from podrouter.local_schedule import (
    decide,
    generate_wrapper,
    install_schedule,
    is_inside_any_window,
    load_schedule,
    read_state,
    tick,
    validate_schedule,
)


class FakeRunner:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, argv: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append({"argv": list(argv), "kwargs": kwargs})
        return subprocess.CompletedProcess(
            argv, self.returncode, self.stdout, self.stderr
        )


def _write_schedule(
    path: Path,
    pod_name: str = "qwen38-27b",
    windows: list[dict[str, Any]] | None = None,
) -> Path:
    if windows is None:
        windows = [{"weekdays": [0, 1, 2, 3, 4], "on": "09:00", "off": "18:00"}]
    data = {
        "pod_name": pod_name,
        "config_path": str(path.parent / f"{pod_name}.yaml"),
        "windows": windows,
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_start_inside_window_when_pod_absent(tmp_path: Path) -> None:
    sched_file = _write_schedule(tmp_path / "schedule.json")
    state_file = tmp_path / "state.json"
    fake_runner = FakeRunner()

    def fake_live_pods(_key: str) -> tuple[set[str], bool]:
        return set(), True

    # Monday (2026-10-26) 10:00 is inside [0,1,2,3,4] 09:00-18:00
    ret = tick(
        schedule_path=sched_file,
        state_path=state_file,
        now="2026-10-26T10:00:00",
        api_key_raw="test-key-1234",
        runner=fake_runner,
        live_pods_provider=fake_live_pods,
    )

    assert ret == 0
    assert len(fake_runner.calls) == 1
    call = fake_runner.calls[0]
    assert "-m" in call["argv"]
    assert "up" in call["argv"]
    assert "${RUNPOD_API_KEY}" in call["argv"]
    assert call["kwargs"]["env"]["RUNPOD_API_KEY"] == "test-key-1234"

    state = read_state(state_file)
    assert state.get("last_action") == "start"
    assert state.get("last_error") is None
    assert state.get("last_tick") == "2026-10-26T10:00:00"


def test_already_running_inside_window_does_nothing(tmp_path: Path) -> None:
    sched_file = _write_schedule(tmp_path / "schedule.json")
    state_file = tmp_path / "state.json"
    fake_runner = FakeRunner()

    def fake_live_pods(_key: str) -> tuple[set[str], bool]:
        return {"qwen38-27b"}, True

    # Monday 10:00, pod already present
    ret = tick(
        schedule_path=sched_file,
        state_path=state_file,
        now="2026-10-26T10:00:00",
        api_key_raw="test-key-1234",
        runner=fake_runner,
        live_pods_provider=fake_live_pods,
    )

    assert ret == 0
    assert len(fake_runner.calls) == 0

    state = read_state(state_file)
    assert state.get("last_action") == "none"
    assert state.get("last_error") is None


def test_stop_outside_window_when_pod_present(tmp_path: Path) -> None:
    sched_file = _write_schedule(tmp_path / "schedule.json")
    state_file = tmp_path / "state.json"
    fake_runner = FakeRunner()

    def fake_live_pods(_key: str) -> tuple[set[str], bool]:
        return {"qwen38-27b"}, True

    # Monday 19:00 is outside 09:00-18:00
    ret = tick(
        schedule_path=sched_file,
        state_path=state_file,
        now="2026-10-26T19:00:00",
        api_key_raw="test-key-1234",
        runner=fake_runner,
        live_pods_provider=fake_live_pods,
    )

    assert ret == 0
    assert len(fake_runner.calls) == 1
    call = fake_runner.calls[0]
    assert "-m" in call["argv"]
    assert "down" in call["argv"]
    assert "qwen38-27b" in call["argv"]
    assert "${RUNPOD_API_KEY}" in call["argv"]
    assert call["kwargs"]["env"]["RUNPOD_API_KEY"] == "test-key-1234"

    state = read_state(state_file)
    assert state.get("last_action") == "stop"
    assert state.get("last_error") is None


def test_asleep_past_window_does_not_start(tmp_path: Path) -> None:
    sched_file = _write_schedule(tmp_path / "schedule.json")
    state_file = tmp_path / "state.json"
    fake_runner = FakeRunner()

    def fake_live_pods(_key: str) -> tuple[set[str], bool]:
        return set(), True

    # Mac was asleep during window and wakes up at 20:00 (outside window, pod absent)
    ret = tick(
        schedule_path=sched_file,
        state_path=state_file,
        now="2026-10-26T20:00:00",
        api_key_raw="test-key-1234",
        runner=fake_runner,
        live_pods_provider=fake_live_pods,
    )

    assert ret == 0
    assert len(fake_runner.calls) == 0

    state = read_state(state_file)
    assert state.get("last_action") == "none"
    assert state.get("last_error") is None


def test_list_failure_does_neither(tmp_path: Path) -> None:
    sched_file = _write_schedule(tmp_path / "schedule.json")
    state_file = tmp_path / "state.json"
    fake_runner = FakeRunner()

    def fake_live_pods_failure(_key: str) -> tuple[set[str], bool]:
        return set(), False

    # Inside window with list_ok = False
    ret = tick(
        schedule_path=sched_file,
        state_path=state_file,
        now="2026-10-26T10:00:00",
        api_key_raw="test-key-1234",
        runner=fake_runner,
        live_pods_provider=fake_live_pods_failure,
    )

    assert ret == 0
    assert len(fake_runner.calls) == 0
    state = read_state(state_file)
    assert state.get("last_error") is not None
    assert "failed" in state["last_error"]

    # Outside window with list_ok = False
    ret2 = tick(
        schedule_path=sched_file,
        state_path=state_file,
        now="2026-10-26T20:00:00",
        api_key_raw="test-key-1234",
        runner=fake_runner,
        live_pods_provider=fake_live_pods_failure,
    )

    assert ret2 == 0
    assert len(fake_runner.calls) == 0


def test_overnight_window(tmp_path: Path) -> None:
    # Monday (0) 22:00 to Tuesday 06:00
    sched = {
        "pod_name": "qwen38-27b",
        "config_path": "pod.yaml",
        "windows": [{"weekdays": [0], "on": "22:00", "off": "06:00"}],
    }
    validated = validate_schedule(sched)
    windows = validated["windows"]

    # Monday 21:59 -> outside
    assert not is_inside_any_window(datetime(2026, 10, 26, 21, 59, 0), windows)
    assert (
        decide(datetime(2026, 10, 26, 21, 59, 0), sched, set(), list_ok=True) == "none"
    )

    # Monday 22:00 -> inside
    assert is_inside_any_window(datetime(2026, 10, 26, 22, 0, 0), windows)
    assert (
        decide(datetime(2026, 10, 26, 22, 0, 0), sched, set(), list_ok=True) == "start"
    )

    # Monday 23:59 -> inside
    assert is_inside_any_window(datetime(2026, 10, 26, 23, 59, 0), windows)

    # Tuesday (1) 00:00 -> inside
    assert is_inside_any_window(datetime(2026, 10, 27, 0, 0, 0), windows)

    # Tuesday 05:59 -> inside
    assert is_inside_any_window(datetime(2026, 10, 27, 5, 59, 59), windows)

    # Tuesday 06:00 -> outside
    assert not is_inside_any_window(datetime(2026, 10, 27, 6, 0, 0), windows)
    assert (
        decide(datetime(2026, 10, 27, 6, 0, 0), sched, {"qwen38-27b"}, list_ok=True)
        == "stop"
    )

    # Tuesday 07:00 -> outside
    assert not is_inside_any_window(datetime(2026, 10, 27, 7, 0, 0), windows)


def test_equal_times_means_24_hours() -> None:
    # Monday 09:00 to 09:00 -> exactly 24 hours (Monday 09:00 to Tuesday 09:00 exclusive)
    sched = {
        "pod_name": "qwen38-27b",
        "config_path": "pod.yaml",
        "windows": [{"weekdays": [0], "on": "09:00", "off": "09:00"}],
    }
    validated = validate_schedule(sched)
    windows = validated["windows"]

    # Monday 08:59 -> outside
    assert not is_inside_any_window(datetime(2026, 10, 26, 8, 59, 59), windows)
    # Monday 09:00 -> inside
    assert is_inside_any_window(datetime(2026, 10, 26, 9, 0, 0), windows)
    # Tuesday 08:59 -> inside
    assert is_inside_any_window(datetime(2026, 10, 27, 8, 59, 59), windows)
    # Tuesday 09:00 -> outside
    assert not is_inside_any_window(datetime(2026, 10, 27, 9, 0, 0), windows)


def test_protected_pod_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PODROUTER_PROTECTED_PODS", "keep-me")
    sched_file = _write_schedule(tmp_path / "schedule.json", pod_name="keep-me")
    state_file = tmp_path / "state.json"

    with pytest.raises(ConfigError) as exc_info:
        load_schedule(sched_file)
    assert "keep-me" in str(exc_info.value)

    ret = cli.main(
        ["schedule", "tick", "--schedule", str(sched_file), "--state", str(state_file)]
    )
    assert ret == 2


def test_flock_skips_when_lock_held(tmp_path: Path) -> None:
    sched_file = _write_schedule(tmp_path / "schedule.json")
    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    state_file = state_dir / "state.json"

    fake_runner = FakeRunner()

    # Hold flock on state_dir
    lock_fd = os.open(state_dir, os.O_RDONLY)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        ret = tick(
            schedule_path=sched_file,
            state_path=state_file,
            now="2026-10-26T10:00:00",
            api_key_raw="test-key",
            runner=fake_runner,
        )

        assert ret == 0
        assert len(fake_runner.calls) == 0
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def test_dry_run_plist_has_startinterval_15_and_no_sample_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sample_key = "SAMPLE_KEY_SECRET_NOT_ALLOWED"
    ret = cli.main(["schedule", "install", "--dry-run"])
    assert ret == 0

    captured = capsys.readouterr()
    stdout = captured.out

    assert "<key>StartInterval</key>" in stdout
    assert "<integer>15</integer>" in stdout
    assert "<key>RunAtLoad</key>" in stdout
    assert "<false/>" in stdout
    assert sample_key not in stdout


def test_child_argv_contains_literal_runpod_api_key(tmp_path: Path) -> None:
    sched_file = _write_schedule(tmp_path / "schedule.json")
    state_file = tmp_path / "state.json"
    fake_runner = FakeRunner()

    def fake_live_pods(_key: str) -> tuple[set[str], bool]:
        return set(), True

    real_api_key = "rpa-real-secret-key-9999"

    ret = tick(
        schedule_path=sched_file,
        state_path=state_file,
        now="2026-10-26T10:00:00",
        api_key_raw=real_api_key,
        runner=fake_runner,
        live_pods_provider=fake_live_pods,
    )

    assert ret == 0
    assert len(fake_runner.calls) == 1
    argv = fake_runner.calls[0]["argv"]
    env = fake_runner.calls[0]["kwargs"]["env"]

    # The argv text must contain literal ${RUNPOD_API_KEY}
    assert "${RUNPOD_API_KEY}" in argv
    # Real key must NOT be in argv
    assert real_api_key not in argv
    # Real key is in child env
    assert env["RUNPOD_API_KEY"] == real_api_key


def test_missing_api_key_records_error_and_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    sched_file = _write_schedule(tmp_path / "schedule.json")
    state_file = tmp_path / "state.json"
    fake_runner = FakeRunner()

    called_runpod = False

    def fake_provider(_key: str) -> tuple[set[str], bool]:
        nonlocal called_runpod
        called_runpod = True
        return set(), True

    # Inside window -> action would be start
    ret = tick(
        schedule_path=sched_file,
        state_path=state_file,
        now="2026-10-26T10:00:00",
        api_key_raw=None,  # missing
        runner=fake_runner,
        live_pods_provider=fake_provider,
    )

    assert ret == 0
    assert not called_runpod
    assert len(fake_runner.calls) == 0

    state = read_state(state_file)
    assert state.get("last_error") == "missing RUNPOD_API_KEY"


def test_validation_rejects_duplicate_weekdays() -> None:
    # Within single window
    sched = {
        "pod_name": "qwen38-27b",
        "config_path": "pod.yaml",
        "windows": [{"weekdays": [0, 1, 0], "on": "09:00", "off": "18:00"}],
    }
    with pytest.raises(ConfigError, match="duplicate weekday"):
        validate_schedule(sched)

    # Across multiple windows
    sched_multi = {
        "pod_name": "qwen38-27b",
        "config_path": "pod.yaml",
        "windows": [
            {"weekdays": [0, 1], "on": "09:00", "off": "18:00"},
            {"weekdays": [1, 2], "on": "10:00", "off": "16:00"},
        ],
    }
    with pytest.raises(ConfigError, match="duplicate weekday"):
        validate_schedule(sched_multi)


def test_validation_rejects_bad_times() -> None:
    for bad in ["24:00", "9:00", "12:60", "bad", "12:00:00", ""]:
        sched = {
            "pod_name": "qwen38-27b",
            "config_path": "pod.yaml",
            "windows": [{"weekdays": [0], "on": bad, "off": "18:00"}],
        }
        with pytest.raises(ConfigError, match="bad time"):
            validate_schedule(sched)


def test_validation_rejects_bad_pod_names() -> None:
    for bad_name in ["-start", "end-", "has_underscore", "has.dot", "toolong" * 10, ""]:
        sched = {
            "pod_name": bad_name,
            "config_path": "pod.yaml",
            "windows": [{"weekdays": [0], "on": "09:00", "off": "18:00"}],
        }
        with pytest.raises(ConfigError, match="invalid pod_name"):
            validate_schedule(sched)


def test_schedule_show(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sched_file = _write_schedule(tmp_path / "schedule.json")
    ret = cli.main(["schedule", "show", "--schedule", str(sched_file)])
    assert ret == 0

    captured = capsys.readouterr()
    loaded = json.loads(captured.out)
    entry = loaded["entries"][0]
    assert entry["pod_name"] == "qwen38-27b"
    assert entry["windows"][0]["on"] == "09:00"
    assert entry["enabled"] is True


def test_wrapper_reads_repo_env_without_keychain(tmp_path: Path) -> None:
    script = generate_wrapper(tmp_path)
    assert "security" not in script
    env_file = tmp_path.resolve() / ".env"
    assert f'--env-file "{env_file}"' in script
    assert "export" not in script


def test_tick_env_file_fills_missing_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("RUNPOD_API_KEY=from-env-file\nHF_TOKEN=hf-from-file\n")
    for name in ("RUNPOD_API_KEY", "HF_TOKEN"):
        # setenv first so monkeypatch restores the original state afterwards.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    seen: dict[str, str] = {}

    def fake_tick(**kwargs: object) -> int:
        seen["key"] = os.environ.get("RUNPOD_API_KEY", "")
        seen["hf"] = os.environ.get("HF_TOKEN", "")
        return 0

    monkeypatch.setattr("podrouter.local_schedule.tick", fake_tick)
    rc = cli.main(["schedule", "tick", "--env-file", str(env_file)])
    assert rc == 0
    assert seen == {"key": "from-env-file", "hf": "hf-from-file"}


def test_install_loads_agent_into_launchd(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        rc = 5 if argv[1] == "bootout" else 0  # bootout fails when not loaded yet
        return subprocess.CompletedProcess(argv, rc, "", "")

    plist = tmp_path / "agent.plist"
    rc = install_schedule(
        repo_path=tmp_path,
        plist_path=plist,
        wrapper_path=tmp_path / "tick.zsh",
        runner=fake_run,
    )
    assert rc == 0
    domain = f"gui/{os.getuid()}"
    assert calls == [
        ["launchctl", "bootout", f"{domain}/io.github.hrunj1230.podrouter"],
        ["launchctl", "bootstrap", domain, str(plist)],
    ]
    assert plist.is_file()


def test_install_reports_bootstrap_failure(tmp_path: Path) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 5, "", "Bootstrap failed: 5")

    rc = install_schedule(
        repo_path=tmp_path,
        plist_path=tmp_path / "agent.plist",
        wrapper_path=tmp_path / "tick.zsh",
        runner=fake_run,
    )
    assert rc == 1


def _read_log(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "logs" / "activity.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_tick_logs_actions_and_redacts_key(tmp_path: Path) -> None:
    sched_file = _write_schedule(tmp_path / "schedule.json")
    state_file = tmp_path / "state.json"
    runner = FakeRunner(returncode=1, stderr="boom key=test-key-1234")

    tick(
        schedule_path=sched_file,
        state_path=state_file,
        now="2026-10-26T10:00:00",
        api_key_raw="test-key-1234",
        runner=runner,
        live_pods_provider=lambda _k: (set(), True),
    )
    events = _read_log(tmp_path)
    assert len(events) == 1
    event = events[0]
    assert event["source"] == "timer"
    assert event["action"] == "start"
    assert event["pod"] == "qwen38-27b"
    assert event["ok"] is False
    assert event["time"] == "2026-10-26T10:00:00"
    assert "test-key-1234" not in json.dumps(event)
    log_file = tmp_path / "logs" / "activity.jsonl"
    assert (log_file.stat().st_mode & 0o777) == 0o600


def test_tick_does_not_log_idle_or_repeat_errors(tmp_path: Path) -> None:
    sched_file = _write_schedule(tmp_path / "schedule.json")
    state_file = tmp_path / "state.json"
    for minute in ("20:00", "20:01"):
        tick(
            schedule_path=sched_file,
            state_path=state_file,
            now=f"2026-10-26T{minute}:00",
            api_key_raw="test-key-1234",
            runner=FakeRunner(),
            live_pods_provider=lambda _k: (set(), True),
        )
    assert _read_log(tmp_path) == []

    for minute in ("10:00", "10:01"):
        tick(
            schedule_path=sched_file,
            state_path=state_file,
            now=f"2026-10-26T{minute}:00",
            api_key_raw="test-key-1234",
            runner=FakeRunner(),
            live_pods_provider=lambda _k: (set(), False),
        )
    events = _read_log(tmp_path)
    assert [e["action"] for e in events] == ["check"]
    assert events[0]["ok"] is False


def test_activity_log_rotates(tmp_path: Path) -> None:
    from podrouter.activity_log import append_event

    path = tmp_path / "logs" / "activity.jsonl"
    for i in range(5):
        append_event(path, source="app", action="start", pod=f"p{i}", ok=True, max_bytes=150)
    assert path.exists()
    assert (tmp_path / "logs" / "activity.1.jsonl").exists()
    assert len(path.read_text().splitlines()) < 5


def test_ttl_until_window_end() -> None:
    from podrouter.local_schedule import ttl_until_window_end

    day = validate_schedule(
        {"pod_name": "p", "config_path": "x.yaml",
         "windows": [{"weekdays": [0, 1, 2, 3, 4], "on": "09:00", "off": "18:00"}]}
    )["windows"]
    assert ttl_until_window_end(datetime(2026, 10, 26, 10, 0), day) == "8h15m"
    assert ttl_until_window_end(datetime(2026, 10, 26, 17, 30), day) == "45m"

    night = validate_schedule(
        {"pod_name": "p", "config_path": "x.yaml",
         "windows": [{"weekdays": [0], "on": "22:00", "off": "02:00"}]}
    )["windows"]
    assert ttl_until_window_end(datetime(2026, 10, 26, 23, 30), night) == "2h45m"


def test_tick_start_passes_ttl_to_window_end(tmp_path: Path) -> None:
    sched_file = _write_schedule(tmp_path / "schedule.json")
    runner = FakeRunner()
    tick(
        schedule_path=sched_file,
        state_path=tmp_path / "state.json",
        now="2026-10-26T09:00:00",
        api_key_raw="test-key-1234",
        runner=runner,
        live_pods_provider=lambda _k: (set(), True),
    )
    argv = runner.calls[0]["argv"]
    assert argv[argv.index("--ttl") + 1] == "9h15m"


def _write_multi(path: Path, entries: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return path


def _entry(name: str, on: str, off: str, enabled: bool = True) -> dict[str, Any]:
    return {
        "pod_name": name,
        "config_path": f"/tmp/{name}.yaml",
        "enabled": enabled,
        "windows": [{"weekdays": [0, 1, 2, 3, 4], "on": on, "off": off}],
    }


def test_multi_pod_each_entry_decides_on_its_own(tmp_path: Path) -> None:
    sched = _write_multi(
        tmp_path / "schedule.json",
        [
            _entry("day-pod", "09:00", "18:00"),
            _entry("night-pod", "20:00", "23:00"),
            _entry("paused-pod", "09:00", "18:00", enabled=False),
        ],
    )
    runner = FakeRunner()
    # Monday 10:00: day-pod should start, night-pod running should stop,
    # paused-pod is ignored even though it is inside its window and absent.
    rc = tick(
        schedule_path=sched,
        state_path=tmp_path / "state.json",
        now="2026-10-26T10:00:00",
        api_key_raw="test-key-1234",
        runner=runner,
        live_pods_provider=lambda _k: ({"night-pod"}, True),
    )
    assert rc == 0
    calls = [(c["argv"][3], c["argv"][4]) for c in runner.calls]
    assert ("up", "/tmp/day-pod.yaml") in calls
    assert ("down", "night-pod") in calls
    assert all("paused-pod" not in " ".join(c["argv"]) for c in runner.calls)

    state = read_state(tmp_path / "state.json")
    assert state["pods"]["day-pod"]["last_action"] == "start"
    assert state["pods"]["night-pod"]["last_action"] == "stop"
    assert "paused-pod" not in state["pods"]
    events = _read_log(tmp_path)
    assert sorted(e["pod"] for e in events) == ["day-pod", "night-pod"]


def test_multi_pod_failure_does_not_block_others(tmp_path: Path) -> None:
    sched = _write_multi(
        tmp_path / "schedule.json",
        [_entry("a-pod", "09:00", "18:00"), _entry("b-pod", "09:00", "18:00")],
    )

    class FailFirst(FakeRunner):
        def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            self.calls.append({"argv": list(argv), "kwargs": kwargs})
            rc = 1 if "/tmp/a-pod.yaml" in argv else 0
            return subprocess.CompletedProcess(argv, rc, "", "boom" if rc else "")

    runner = FailFirst()
    rc = tick(
        schedule_path=sched,
        state_path=tmp_path / "state.json",
        now="2026-10-26T10:00:00",
        api_key_raw="test-key-1234",
        runner=runner,
        live_pods_provider=lambda _k: (set(), True),
    )
    assert rc == 1
    assert len(runner.calls) == 2
    state = read_state(tmp_path / "state.json")
    assert state["pods"]["a-pod"]["last_error"] == "boom"
    assert state["pods"]["b-pod"]["last_error"] is None
    assert state["last_error"] == "a-pod: boom"


def test_multi_pod_rejects_duplicate_names_and_protected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PODROUTER_PROTECTED_PODS", "keep-me")
    from podrouter.local_schedule import validate_schedules

    with pytest.raises(ConfigError, match="duplicate"):
        validate_schedules({"entries": [_entry("x-pod", "09:00", "18:00")] * 2})
    with pytest.raises(ConfigError, match="keep-me"):
        validate_schedules({"entries": [_entry("keep-me", "09:00", "18:00")]})
    assert validate_schedules({"entries": []}) == []


def test_legacy_single_state_carries_over(tmp_path: Path) -> None:
    sched_file = _write_schedule(tmp_path / "schedule.json")
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"last_action": "start", "last_error": None}))
    # No API key, outside the window, last action start -> would stop -> error recorded.
    tick(
        schedule_path=sched_file,
        state_path=state_file,
        now="2026-10-26T20:00:00",
        api_key_raw="",
        runner=FakeRunner(),
        live_pods_provider=lambda _k: (set(), True),
    )
    state = read_state(state_file)
    assert state["last_error"] == "missing RUNPOD_API_KEY"
    assert state["pods"]["qwen38-27b"]["last_action"] == "start"


def test_no_seat_retries_quietly_and_notifies_first_miss_and_success(
    tmp_path: Path,
) -> None:
    sched_file = _write_schedule(tmp_path / "schedule.json")
    state_file = tmp_path / "state.json"
    notes: list[tuple[str, str]] = []
    runners: list[FakeRunner] = []

    def run_tick(second: str, returncode: int) -> None:
        runner = FakeRunner(returncode=returncode, stderr="stage=skip reason=stock")
        runners.append(runner)
        tick(
            schedule_path=sched_file,
            state_path=state_file,
            now=f"2026-10-26T10:00:{second}",
            api_key_raw="test-key-1234",
            runner=runner,
            live_pods_provider=lambda _k: (set(), True),
            notifier=lambda title, message: notes.append((title, message)),
        )

    run_tick("00", 3)
    run_tick("15", 3)
    run_tick("30", 3)
    assert all("--retry" in r.calls[0]["argv"] for r in runners)
    assert len(notes) == 1
    assert "자리 없음" in notes[0][0]
    assert len(_read_log(tmp_path)) == 1
    pod_state = read_state(state_file)["pods"]["qwen38-27b"]
    assert pod_state["seat_waiting"] is True
    assert pod_state["seat_attempts"] == 3

    run_tick("45", 0)
    assert len(notes) == 2
    assert "켜짐" in notes[1][0]
    assert "4번째 시도" in notes[1][1]
    events = _read_log(tmp_path)
    assert len(events) == 2
    assert events[-1]["ok"] is True
    assert "seat_waiting" not in read_state(state_file)["pods"]["qwen38-27b"]


def test_window_end_while_waiting_for_seat_notifies(tmp_path: Path) -> None:
    sched_file = _write_schedule(tmp_path / "schedule.json")
    state_file = tmp_path / "state.json"
    notes: list[tuple[str, str]] = []
    for now, code in (("2026-10-26T17:59:50", 3), ("2026-10-26T18:00:05", 3)):
        tick(
            schedule_path=sched_file,
            state_path=state_file,
            now=now,
            api_key_raw="test-key-1234",
            runner=FakeRunner(returncode=code),
            live_pods_provider=lambda _k: (set(), True),
            notifier=lambda title, message: notes.append((title, message)),
        )
    assert len(notes) == 2
    assert "켜기 실패" in notes[1][0]
    assert "seat_waiting" not in read_state(state_file)["pods"]["qwen38-27b"]
