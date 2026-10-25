"""Local LaunchAgent scheduler for podrouter."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import plistlib
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal

from podrouter.activity_log import append_event, log_path_for
from podrouter.client import RunPodClient
from podrouter.config import ConfigError, resolve_api_key
from podrouter.paths import data_home
from podrouter.planner import _NAME_REGEX
from podrouter.pods import NAME, is_live, is_managed, parse_env
from podrouter.protect import ENV_NAME as PROTECTED_ENV
from podrouter.protect import is_protected
from podrouter.provision import NO_SEAT_EXIT
from podrouter.redact import Redactor

DEFAULT_SCHEDULE_PATH = (
    Path.home() / "Library" / "Application Support" / "podrouter" / "schedule.json"
)
DEFAULT_STATE_PATH = (
    Path.home() / "Library" / "Application Support" / "podrouter" / "state.json"
)
DEFAULT_WRAPPER_PATH = (
    Path.home() / "Library" / "Application Support" / "podrouter" / "tick.zsh"
)
LAUNCH_LABEL = "io.github.hrunj1230.podrouter"
# launchd runs one tick this often. A pod with no GPU seat is retried every tick.
TICK_INTERVAL_SEC = 15
DEFAULT_PLIST_PATH = (
    Path.home() / "Library" / "LaunchAgents" / "io.github.hrunj1230.podrouter.plist"
)

_TIME_REGEX = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _secret_values(
    api_key: str, extra: list[str] | tuple[str, ...] | None = None
) -> list[str]:
    secrets = [api_key, *(extra or [])]
    for name in ("TS_AUTHKEY", "CF_TUNNEL_TOKEN", "HF_TOKEN", "MODEL_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            secrets.append(value)
    return [s for s in secrets if s]


def parse_hh_mm(raw: Any) -> time:
    """Parse local naive HH:MM time string."""
    if not isinstance(raw, str) or not _TIME_REGEX.fullmatch(raw):
        raise ConfigError(f"bad time: {raw!r}")
    parts = raw.split(":")
    return time(int(parts[0]), int(parts[1]))


def validate_schedule(data: dict[str, Any]) -> dict[str, Any]:
    """Validate schedule schema, rejecting bad times, bad names, and duplicate weekdays."""
    if not isinstance(data, dict):
        raise ConfigError("schedule must be a JSON object")

    pod_name = data.get("pod_name")
    if not isinstance(pod_name, str) or not _NAME_REGEX.fullmatch(pod_name):
        raise ConfigError(f"invalid pod_name: {pod_name!r}")
    if is_protected(pod_name):
        raise ConfigError(f"pod_name '{pod_name}' is protected ({PROTECTED_ENV})")

    config_path = data.get("config_path")
    if not isinstance(config_path, str) or not config_path.strip():
        raise ConfigError(f"invalid config_path: {config_path!r}")

    windows = data.get("windows")
    if not isinstance(windows, list) or len(windows) == 0:
        raise ConfigError("schedule must contain a non-empty 'windows' list")

    seen_weekdays: set[int] = set()
    validated_windows: list[dict[str, Any]] = []

    for idx, window in enumerate(windows):
        if not isinstance(window, dict):
            raise ConfigError(f"window[{idx}] must be an object")

        weekdays = window.get("weekdays")
        if not isinstance(weekdays, list) or len(weekdays) == 0:
            raise ConfigError(f"window[{idx}] must contain a non-empty 'weekdays' list")

        w_seen: set[int] = set()
        for day in weekdays:
            if not isinstance(day, int) or isinstance(day, bool) or not (0 <= day <= 6):
                raise ConfigError(
                    f"window[{idx}] weekday must be an integer from 0 to 6: {day!r}"
                )
            if day in w_seen or day in seen_weekdays:
                raise ConfigError(f"duplicate weekday: {day}")
            w_seen.add(day)
            seen_weekdays.add(day)

        on_raw = window.get("on")
        off_raw = window.get("off")
        on_time = parse_hh_mm(on_raw)
        off_time = parse_hh_mm(off_raw)

        validated_windows.append(
            {
                "weekdays": weekdays,
                "on": on_raw,
                "off": off_raw,
                "_on_time": on_time,
                "_off_time": off_time,
            }
        )

    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"enabled must be true or false: {enabled!r}")

    return {
        "pod_name": pod_name,
        "config_path": config_path,
        "windows": validated_windows,
        "enabled": enabled,
    }


def validate_schedules(data: Any) -> list[dict[str, Any]]:
    """Validate a schedule file: `{"entries": [...]}` or the older single-pod form.

    Each entry is one pod with its own windows. Pod names must be unique.
    """
    if isinstance(data, dict) and "entries" in data:
        raw = data["entries"]
        if not isinstance(raw, list):
            raise ConfigError("entries must be a list")
        entries = []
        for idx, item in enumerate(raw):
            try:
                entries.append(validate_schedule(item))
            except ConfigError as exc:
                raise ConfigError(f"entries[{idx}]: {exc}") from exc
    else:
        entries = [validate_schedule(data)]
    names = [e["pod_name"] for e in entries]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ConfigError(f"duplicate pod_name in schedule: {', '.join(dupes)}")
    return entries


def load_schedule(path: str | Path) -> list[dict[str, Any]]:
    """Read and validate schedule JSON into a list of per-pod entries."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise ConfigError(f"schedule file not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"malformed schedule JSON in {p}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read schedule file {p}: {exc}") from exc
    return validate_schedules(data)


def is_inside_window(now: datetime, window: dict[str, Any]) -> bool:
    """Check if `now` falls inside a single schedule window."""
    now_weekday = now.weekday()
    now_time = now.time()

    on_time = window.get("_on_time") or parse_hh_mm(window["on"])
    off_time = window.get("_off_time") or parse_hh_mm(window["off"])
    weekdays = window["weekdays"]

    if off_time > on_time:
        # off > on: active that weekday from on inclusive to off exclusive
        return (now_weekday in weekdays) and (on_time <= now_time < off_time)

    # off <= on: crosses midnight: active from on that weekday until off the next day.
    # Equal times mean 24 hours.
    if (now_weekday in weekdays) and (now_time >= on_time):
        return True
    prev_weekday = (now_weekday - 1) % 7
    return (prev_weekday in weekdays) and (now_time < off_time)


def is_inside_any_window(now: datetime, windows: list[dict[str, Any]]) -> bool:
    """Check if `now` falls inside any window in the schedule."""
    return any(is_inside_window(now, w) for w in windows)


TTL_MARGIN_MIN = 15


def window_end(now: datetime, windows: list[dict[str, Any]]) -> datetime:
    """First minute at or after `now` that is outside every window (max 8 days)."""
    t = now.replace(second=0, microsecond=0)
    for _ in range(8 * 24 * 60):
        if not is_inside_any_window(t, windows):
            return t
        t += timedelta(minutes=1)
    return t


def ttl_until_window_end(
    now: datetime, windows: list[dict[str, Any]], margin_min: int = TTL_MARGIN_MIN
) -> str:
    """TTL text like `9h15m` that ends `margin_min` after the window closes.

    The pod then deletes itself even if the Mac sleeps through the off time.
    """
    seconds = (window_end(now, windows) - now).total_seconds()
    minutes = max(1, int(-(-seconds // 60))) + margin_min
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}h{mins}m"
    return f"{hours}h" if hours else f"{mins}m"


def decide(
    now: datetime,
    schedule: dict[str, Any],
    live_names: set[str] | list[str] | tuple[str, ...],
    list_ok: bool,
    busy: bool = False,
) -> Literal["start", "stop", "none"]:
    """Pure decision function for starting, stopping, or taking no action."""
    if not list_ok:
        return "none"
    if busy:
        return "none"

    pod_name = schedule["pod_name"]
    pod_present = pod_name in live_names
    inside = is_inside_any_window(now, schedule["windows"])

    if inside and not pod_present:
        return "start"
    if not inside and pod_present:
        return "stop"
    return "none"


def read_state(path: Path) -> dict[str, Any]:
    """Read mode 0600 state file if present."""
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_state(path: Path, data: dict[str, Any]) -> None:
    """Write mode 0600 state file without any secret values."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_action": data.get("last_action"),
        "last_error": data.get("last_error"),
        "last_tick": data.get("last_tick"),
        "pods": data.get("pods") or {},
    }
    content = json.dumps(payload, indent=2) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def generate_plist(wrapper_path: Path) -> str:
    """Generate launchd LaunchAgent XML plist."""
    data = {
        "Label": LAUNCH_LABEL,
        "ProgramArguments": ["/bin/zsh", str(wrapper_path.expanduser().resolve())],
        "RunAtLoad": False,
        "StartInterval": TICK_INTERVAL_SEC,
    }
    return plistlib.dumps(data, fmt=plistlib.FMT_XML).decode("utf-8")


def generate_wrapper(
    repo_path: Path | None = None,
    *,
    python: Path | None = None,
    env_file: Path | None = None,
) -> str:
    """Generate mode 0700 tick.zsh wrapper script.

    With `repo_path` (a git clone), the clone's `.venv` and `.env` are used.
    Otherwise the running Python and `<PODROUTER_HOME>/.env` are used, which is
    what the installed Mac app needs.
    """
    if repo_path is not None:
        root = repo_path.expanduser().resolve()
        py = python or root / ".venv" / "bin" / "python"
        env = env_file or root / ".env"
    else:
        py = python or Path(sys.executable)
        env = env_file or data_home().expanduser().resolve() / ".env"
    return f"""#!/bin/zsh
set -e

# Keys come from the .env file (mode 0600). Nothing is echoed.
exec "{py}" -m podrouter schedule tick --env-file "{env}" "$@"
"""


def _save_tick_state(
    path: Path,
    now: datetime,
    entries: list[dict[str, Any]],
    pods_state: dict[str, dict[str, Any]],
    acted: list[str],
) -> None:
    """Write per-pod state plus a top-level summary for older readers.

    With one entry the summary is that pod's state. With several, last_action
    is the last action taken this tick and last_error joins every pod error.
    """
    if len(entries) == 1:
        only = pods_state.get(entries[0]["pod_name"], {})
        last_action = only.get("last_action") or "none"
        last_error = only.get("last_error")
    else:
        last_action = acted[-1] if acted else "none"
        errors = [
            f"{name}: {value['last_error']}"
            for name, value in sorted(pods_state.items())
            if value.get("last_error")
        ]
        last_error = "; ".join(errors) or None
    save_state(
        path,
        {
            "last_action": last_action,
            "last_error": last_error,
            "last_tick": now.isoformat(),
            "pods": pods_state,
        },
    )


def _name_of_pod(pod: dict[str, Any]) -> str:
    env = parse_env(pod.get("env"))
    return str(env.get(NAME) or pod.get("name") or "")


def tick(
    *,
    schedule_path: str | Path | None = None,
    state_path: str | Path | None = None,
    now: datetime | str | None = None,
    api_key_raw: str | None = None,
    repo_path: str | Path | None = None,
    runner: Callable[..., Any] | None = None,
    live_pods_provider: Callable[[str], tuple[set[str], bool]] | None = None,
    notifier: Callable[[str, str], None] | None = None,
) -> int:
    """Run one schedule tick protected by an exclusive flock on the state directory.

    `notifier(title, message)` shows a desktop notification. It is called once
    when a start first finds no GPU seat, once when the start succeeds, and once
    when the window ends while still waiting for a seat.
    """
    notify = notifier or (lambda title, message: None)
    sched_p = Path(schedule_path or DEFAULT_SCHEDULE_PATH).expanduser()
    state_p = Path(state_path or DEFAULT_STATE_PATH).expanduser()
    # Logs sit next to the state file, so tests with tmp state never touch ~/Library.
    log_p = log_path_for(state_p.parent)
    del repo_path  # kept for callers; the running Python is used now

    # Determine `now`
    if now is None:
        current_time = datetime.now()
    elif isinstance(now, str):
        current_time = datetime.fromisoformat(now)
    else:
        current_time = now

    # Take flock on state directory
    state_dir = state_p.parent
    state_dir.mkdir(parents=True, exist_ok=True)
    dir_fd = os.open(state_dir, os.O_RDONLY)
    try:
        try:
            fcntl.flock(dir_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            return 0  # Lock held, exit 0

        # Load and validate schedule. A protected pod name raises ConfigError.
        entries = load_schedule(sched_p)
        names = {e["pod_name"] for e in entries}
        active = [e for e in entries if e["enabled"]]

        # Resolve API key
        raw_key = api_key_raw if api_key_raw is not None else "${RUNPOD_API_KEY}"
        resolved_key: str | None = None
        try:
            resolved_key = resolve_api_key(raw_key)
        except ConfigError:
            resolved_key = None

        existing_state = read_state(state_p)
        prev_pods = existing_state.get("pods") or {}
        if not prev_pods and len(entries) == 1:
            # State written by the single-pod timer.
            prev_pods = {
                entries[0]["pod_name"]: {
                    "last_action": existing_state.get("last_action"),
                    "last_error": existing_state.get("last_error"),
                }
            }
        pods_state: dict[str, dict[str, Any]] = {
            name: dict(value)
            for name, value in prev_pods.items()
            if name in names and isinstance(value, dict)
        }
        acted: list[str] = []

        def finish(rc: int) -> int:
            _save_tick_state(state_p, current_time, entries, pods_state, acted)
            return rc

        missing = "missing RUNPOD_API_KEY"
        if not resolved_key:
            for entry in active:
                name = entry["pod_name"]
                prev = pods_state.get(name, {})
                inside = is_inside_any_window(current_time, entry["windows"])
                if inside:
                    would = "start"
                elif prev.get("last_action") == "start":
                    would = "stop"
                else:
                    would = "none"
                if would == "none":
                    pods_state[name] = {
                        "last_action": prev.get("last_action") or "none",
                        "last_error": None,
                    }
                    continue
                if prev.get("last_error") != missing:
                    append_event(
                        log_p,
                        source="timer",
                        action=would,
                        pod=name,
                        ok=False,
                        message="RUNPOD_API_KEY가 없어 실행하지 못했습니다.",
                        now=current_time,
                    )
                pods_state[name] = {
                    "last_action": prev.get("last_action") or "none",
                    "last_error": missing,
                }
            return finish(0)

        # Query live pods once for every entry.
        if live_pods_provider is not None:
            live_names, list_ok = live_pods_provider(resolved_key)
            list_err = None if list_ok else "list pods provider failed"
        else:
            try:
                base = os.environ.get("PODROUTER_HEALTH_BASE") or None
                client = RunPodClient(resolved_key, health_base=base)
                pods = client.get_pods()
                live_names = {
                    _name_of_pod(pod)
                    for pod in pods
                    if is_managed(pod) and is_live(pod)
                }
                list_ok = True
                list_err = None
            except Exception as exc:  # noqa: BLE001
                live_names = set()
                list_ok = False
                list_err = str(exc)

        # The wrapper already runs the right Python (clone .venv or app venv).
        python_bin = Path(sys.executable)
        run_cmd = runner or subprocess.run
        child_env = os.environ.copy()
        child_env["RUNPOD_API_KEY"] = resolved_key
        redactor = Redactor(_secret_values(resolved_key))
        rc = 0

        for entry in active:
            name = entry["pod_name"]
            prev = pods_state.get(name, {})
            action = decide(
                current_time, entry, live_names, list_ok=list_ok, busy=False
            )

            if action == "none":
                last_err = f"list failed: {list_err}" if not list_ok else None
                if last_err and prev.get("last_error") != last_err:
                    append_event(
                        log_p,
                        source="timer",
                        action="check",
                        pod=name,
                        ok=False,
                        message="Pod 목록 조회에 실패해 이번 회차는 건너뜁니다.",
                        detail=redactor.text(last_err),
                        now=current_time,
                    )
                if prev.get("seat_waiting") and list_ok and name not in live_names:
                    tries = int(prev.get("seat_attempts") or 0)
                    message = f"시간대가 끝날 때까지 GPU 자리를 찾지 못했습니다 ({tries}번 시도)."
                    append_event(
                        log_p,
                        source="timer",
                        action="start",
                        pod=name,
                        ok=False,
                        message=message,
                        now=current_time,
                    )
                    notify(f"podrouter: '{name}' 켜기 실패", message)
                # If list failed, keep the previous action; if it worked, nothing to do.
                pods_state[name] = {
                    "last_action": (prev.get("last_action") or "none")
                    if not list_ok
                    else "none",
                    "last_error": last_err,
                }
                continue

            if action == "start":
                argv = [
                    str(python_bin),
                    "-m",
                    "podrouter",
                    "up",
                    str(entry["config_path"]),
                    "--wait",
                    "false",
                    "--ttl",
                    ttl_until_window_end(current_time, entry["windows"]),
                    # The timer retries on its own every tick, so `up` tries once.
                    "--retry",
                    "false",
                    "--api-key",
                    "${RUNPOD_API_KEY}",
                ]
            else:
                argv = [
                    str(python_bin),
                    "-m",
                    "podrouter",
                    "down",
                    name,
                    "--api-key",
                    "${RUNPOD_API_KEY}",
                ]

            res = run_cmd(argv, env=child_env, capture_output=True, text=True)
            redacted_out = redactor.text(res.stdout or "")
            redacted_err = redactor.text(res.stderr or "")
            if redacted_out.strip():
                redactor.stdout(redacted_out.strip())
            if redacted_err.strip():
                redactor.stderr(redacted_err.strip())

            verb = "켜기" if action == "start" else "끄기"
            detail = "\n".join(
                t for t in (redacted_out.strip(), redacted_err.strip()) if t
            )
            no_seat = action == "start" and res.returncode == NO_SEAT_EXIT
            was_waiting = bool(prev.get("seat_waiting"))
            tries = int(prev.get("seat_attempts") or 0) + 1 if was_waiting else 1
            if no_seat:
                # Log and notify only the first miss; later ticks retry quietly.
                if not was_waiting:
                    message = f"GPU 자리가 없습니다. {TICK_INTERVAL_SEC}초마다 다시 시도합니다."
                    append_event(
                        log_p,
                        source="timer",
                        action=action,
                        pod=name,
                        ok=False,
                        message=message,
                        detail=detail,
                        now=current_time,
                    )
                    notify(f"podrouter: '{name}' 자리 없음", message)
            else:
                if res.returncode == 0:
                    message = f"일정에 따라 {verb} 완료"
                    if action == "start" and was_waiting:
                        message += f" ({tries}번째 시도)"
                else:
                    message = f"일정에 따른 {verb} 실패 (종료 코드 {res.returncode})"
                append_event(
                    log_p,
                    source="timer",
                    action=action,
                    pod=name,
                    ok=res.returncode == 0,
                    message=message,
                    detail=detail,
                    now=current_time,
                )
                if action == "start" and res.returncode == 0:
                    notify(f"podrouter: '{name}' 켜짐", message)
            acted.append(action)
            pods_state[name] = {
                "last_action": action,
                "last_error": None
                if res.returncode == 0
                else (redacted_err.strip() or f"exit code {res.returncode}"),
            }
            if no_seat:
                pods_state[name]["seat_waiting"] = True
                pods_state[name]["seat_attempts"] = tries
            if res.returncode != 0 and rc == 0:
                rc = res.returncode

        return finish(rc)
    finally:
        try:
            fcntl.flock(dir_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(dir_fd)


def notify_macos(title: str, message: str) -> None:
    """Show a macOS notification. Failures are ignored."""
    if sys.platform != "darwin" or os.environ.get("PODROUTER_NO_NOTIFY"):
        return
    script = [
        "-e",
        "on run argv",
        "-e",
        "display notification (item 2 of argv) with title (item 1 of argv)",
        "-e",
        "end run",
    ]
    # A missed notification must not break the tick.
    with contextlib.suppress(Exception):
        subprocess.run(
            ["/usr/bin/osascript", *script, title, message],
            capture_output=True,
            timeout=10,
            check=False,
        )


def install_schedule(
    repo_path: str | Path | None = None,
    dry_run: bool = False,
    plist_path: Path | None = None,
    wrapper_path: Path | None = None,
    runner: Callable[..., Any] | None = None,
) -> int:
    """Install LaunchAgent plist and mode 0700 tick wrapper, then load it into launchd.

    Writing the plist alone does nothing until the next login, so the agent is
    re-registered with `launchctl bootout` + `bootstrap` right away.
    """
    repo_p = Path(repo_path).expanduser() if repo_path else None
    plist_p = plist_path or DEFAULT_PLIST_PATH
    wrapper_p = wrapper_path or DEFAULT_WRAPPER_PATH

    plist_content = generate_plist(wrapper_p)
    if dry_run:
        sys.stdout.write(plist_content)
        sys.stdout.flush()
        return 0

    wrapper_content = generate_wrapper(repo_p)

    plist_p.parent.mkdir(parents=True, exist_ok=True)
    plist_p.write_text(plist_content, encoding="utf-8")

    wrapper_p.parent.mkdir(parents=True, exist_ok=True)
    wrapper_p.write_text(wrapper_content, encoding="utf-8")
    os.chmod(wrapper_p, 0o700)

    run_cmd = runner or subprocess.run
    domain = f"gui/{os.getuid()}"
    # Unload an older copy first; failure here just means it was not loaded.
    run_cmd(
        ["launchctl", "bootout", f"{domain}/{LAUNCH_LABEL}"],
        capture_output=True,
        text=True,
    )
    res = run_cmd(
        ["launchctl", "bootstrap", domain, str(plist_p)],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()
        print(f"error: launchctl bootstrap failed: {detail}", file=sys.stderr)
        return 1
    print(f"loaded {LAUNCH_LABEL} into {domain}", file=sys.stderr)
    return 0


def show_schedule(schedule_path: str | Path | None = None) -> int:
    """Print the validated schedule as `{"entries": [...]}`."""
    sched_p = Path(schedule_path or DEFAULT_SCHEDULE_PATH).expanduser()
    entries = load_schedule(sched_p)
    output = {
        "entries": [
            {
                "pod_name": e["pod_name"],
                "config_path": e["config_path"],
                "enabled": e["enabled"],
                "windows": [
                    {"weekdays": w["weekdays"], "on": w["on"], "off": w["off"]}
                    for w in e["windows"]
                ],
            }
            for e in entries
        ]
    }
    sys.stdout.write(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


def run_schedule_cli(args: argparse.Namespace) -> int:
    """CLI dispatcher for `podrouter schedule tick|install|show`."""
    subcmd = getattr(args, "schedule_command", None)
    if subcmd == "tick":
        env_file = getattr(args, "env_file", None)
        if env_file:
            from podrouter.envfile import load_dotenv

            # Fills only empty variables; values already set win.
            load_dotenv(Path(env_file).expanduser())
        return tick(
            schedule_path=args.schedule,
            state_path=args.state,
            now=args.now,
            api_key_raw=args.api_key,
            notifier=notify_macos,
        )
    if subcmd == "install":
        return install_schedule(
            repo_path=args.repo,
            dry_run=bool(args.dry_run),
        )
    if subcmd == "show":
        return show_schedule(schedule_path=args.schedule)

    print("error: pass tick, install, or show", file=sys.stderr)
    return 2
