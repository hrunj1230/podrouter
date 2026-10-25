"""Argument parsing for `podrouter up|down|ls|status`."""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys

from podrouter.client import RunPodClient
from podrouter.config import load_config, resolve_api_key
from podrouter.duration import parse_duration
from podrouter.errors import ConfigError
from podrouter.provision import bring_up, list_pods, list_pods_json, show_status, shut_down
from podrouter.redact import Redactor


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return 0 if code is None else int(code)

    if args.command is None:
        parser.print_help(sys.stderr)
        return 2

    if args.command == "web":
        from podrouter.web import serve

        return serve(host=args.host, port=args.port)

    if args.command == "schedule":
        from podrouter.local_schedule import run_schedule_cli

        try:
            return run_schedule_cli(args)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    try:
        api_key = resolve_api_key(args.api_key)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "up":
            return _up(args, api_key)
        client = _client(api_key)
        log = Redactor(_secret_values(api_key))
        if args.command == "ls":
            if args.json:
                return list_pods_json(client, log, args.health_path)
            return list_pods(client, log)
        if args.command == "status":
            return show_status(client, args.name, log)
        if args.command == "down":
            if args.all == bool(args.name):
                print("error: pass a pod name or --all", file=sys.stderr)
                return 2
            return shut_down(client, name=args.name, all_pods=bool(args.all), log=log)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


def _up(args: argparse.Namespace, api_key: str) -> int:
    cfg = load_config(args.config)
    if args.ttl:
        # Overrides lifecycle.ttl for this launch only. The config hash is
        # unchanged, so a running pod from the same YAML is still reused.
        cfg = dataclasses.replace(
            cfg,
            ttl_sec=parse_duration(args.ttl, field="--ttl"),
            ttl_text=args.ttl.strip(),
        )
    log = Redactor(_secret_values(api_key, cfg.secrets))
    wait = _parse_wait(args.wait)
    retry = _parse_bool(args.retry, "--retry")
    client = _client(api_key)
    return bring_up(cfg, client, wait=wait, log=log, retry=retry)


def _secret_values(
    api_key: str, extra: list[str] | tuple[str, ...] | None = None
) -> list[str]:
    secrets = [api_key, *(extra or [])]
    for name in ("TS_AUTHKEY", "CF_TUNNEL_TOKEN", "HF_TOKEN", "MODEL_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            secrets.append(value)
    return secrets


def _client(api_key: str) -> RunPodClient:
    base = os.environ.get("PODROUTER_HEALTH_BASE") or None
    return RunPodClient(api_key, health_base=base)


def _parse_wait(raw: str) -> bool:
    return _parse_bool(raw, "--wait")


def _parse_bool(raw: str, flag: str) -> bool:
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    raise ConfigError(f"{flag} must be true or false")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="podrouter",
        description="Create a RunPod Pod from one YAML config and print its proxy URL.",
    )
    sub = parser.add_subparsers(dest="command")

    up = sub.add_parser("up", help="Create a pod and print its proxy URL")
    up.add_argument("config", help="Path to the server YAML file")
    up.add_argument(
        "--wait",
        default="true",
        help="Wait until the readiness path returns HTTP 200 (true or false)",
    )
    up.add_argument(
        "--retry",
        default="true",
        help=(
            "When no GPU has a seat, try again every timeouts.allocation_retry_sec"
            " until timeouts.allocation_sec runs out (true or false)"
        ),
    )
    up.add_argument("--ttl", help="Override lifecycle.ttl for this launch, e.g. 9h15m")
    _add_api_key(up)

    down = sub.add_parser("down", help="Terminate a pod this tool created")
    down.add_argument("name", nargs="?", help="Pod name from the config")
    down.add_argument(
        "--all", action="store_true", help="Terminate every pod this tool created"
    )
    _add_api_key(down)

    ls = sub.add_parser("ls", help="List pods this tool created")
    ls.add_argument("--json", action="store_true", help="Print JSON with a health check per pod")
    ls.add_argument("--health-path", default="/health", help="Health path for --json")
    _add_api_key(ls)

    status = sub.add_parser(
        "status", help="Show state, hourly price, and remaining TTL"
    )
    status.add_argument("name", help="Pod name from the config")
    _add_api_key(status)

    web = sub.add_parser("web", help="Open the local GPU stock and launch page")
    web.add_argument("--host", default="127.0.0.1", help="Loopback host")
    web.add_argument("--port", type=int, default=8765, help="Loopback port")

    schedule = sub.add_parser("schedule", help="Local LaunchAgent scheduler commands")
    sched_sub = schedule.add_subparsers(dest="schedule_command")

    tick = sched_sub.add_parser("tick", help="Run one schedule tick")
    tick.add_argument("--schedule", help="Path to schedule.json")
    tick.add_argument("--state", help="Path to state.json")
    tick.add_argument("--now", help="ISO-8601 current time override")
    tick.add_argument("--env-file", help="Read missing keys from this .env file")
    _add_api_key(tick)

    install = sched_sub.add_parser(
        "install", help="Install LaunchAgent plist and wrapper"
    )
    install.add_argument("--repo", help="Path to repo directory")
    install.add_argument(
        "--dry-run", action="store_true", help="Print plist without writing"
    )

    show = sched_sub.add_parser("show", help="Show current schedule")
    show.add_argument("--schedule", help="Path to schedule.json")
    return parser


def _add_api_key(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--api-key",
        help="RunPod API key, or a ${ENV} reference such as ${RUNPOD_API_KEY}",
    )


if __name__ == "__main__":
    sys.exit(main())
