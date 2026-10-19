"""Argument parsing for `gpu-up up|down|ls|status`."""

from __future__ import annotations

import argparse
import os
import sys

from gpu_up.client import RunPodClient
from gpu_up.config import load_config, resolve_api_key
from gpu_up.errors import ConfigError
from gpu_up.provision import bring_up, list_pods, show_status, shut_down
from gpu_up.redact import Redactor


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

    try:
        api_key = resolve_api_key(args.api_key)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "up":
            return _up(args, api_key)
        client = _client(api_key)
        log = Redactor([api_key])
        if args.command == "ls":
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
    secrets = [api_key, *cfg.secrets]
    log = Redactor(secrets)
    wait = _parse_wait(args.wait)
    client = _client(api_key)
    return bring_up(cfg, client, wait=wait, log=log)


def _client(api_key: str) -> RunPodClient:
    base = os.environ.get("GPU_UP_HEALTH_BASE") or None
    return RunPodClient(api_key, health_base=base)


def _parse_wait(raw: str) -> bool:
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    raise ConfigError("--wait must be true or false")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gpu-up",
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
    _add_api_key(up)

    down = sub.add_parser("down", help="Terminate a pod this tool created")
    down.add_argument("name", nargs="?", help="Pod name from the config")
    down.add_argument(
        "--all", action="store_true", help="Terminate every pod this tool created"
    )
    _add_api_key(down)

    ls = sub.add_parser("ls", help="List pods this tool created")
    _add_api_key(ls)

    status = sub.add_parser(
        "status", help="Show state, hourly price, and remaining TTL"
    )
    status.add_argument("name", help="Pod name from the config")
    _add_api_key(status)
    return parser


def _add_api_key(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--api-key",
        help="RunPod API key, or a ${ENV} reference such as ${RUNPOD_API_KEY}",
    )


if __name__ == "__main__":
    sys.exit(main())
