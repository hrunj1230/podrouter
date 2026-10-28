"""Render a podrouter YAML from a subscription agent's choice.

Does not call api.x.ai and does not create a Pod.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from podrouter.catalog import gpu_catalog
from podrouter.envfile import load_dotenv
from podrouter.envfile import redact as _redact
from podrouter.errors import ConfigError
from podrouter.planner import build_plan, compact_catalog_rows, read_plan_fields

_REPO = Path(__file__).resolve().parents[4]
_PROTECTED = {
    _REPO / ".env",
    _REPO / "examples" / "a40.yaml",
}


def _api_key() -> str:
    load_dotenv()
    key = os.environ.get("RUNPOD_API_KEY", "").strip()
    if not key:
        print(
            "RUNPOD_API_KEY가 없습니다. .env에 넣은 뒤 다시 실행하세요.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return key


def _catalog() -> list[dict]:
    try:
        return gpu_catalog(_api_key())
    except SystemExit:
        raise
    except Exception as exc:
        print(_redact(str(exc)), file=sys.stderr)
        raise SystemExit(1) from exc


def _cmd_catalog() -> int:
    rows = compact_catalog_rows(_catalog())
    json.dump(rows, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    if args.out:
        target = Path(args.out).expanduser().resolve()
        if target in _PROTECTED or target.name == ".env":
            print(f"이 경로는 덮어쓰지 않습니다: {target}", file=sys.stderr)
            return 1
    else:
        target = None
    try:
        result = build_plan(
            args.name,
            args.wish,
            _catalog(),
            gpu_id=args.gpu_id,
            cloud=args.cloud,
            model=args.model,
            context_tokens=args.context,
            reason=args.reason,
            ttl=args.ttl,
        )
    except ConfigError as exc:
        print(_redact(str(exc)), file=sys.stderr)
        return 1
    yaml_text = result["yaml"]
    if not yaml_text.endswith("\n"):
        yaml_text += "\n"
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml_text, encoding="utf-8")
        print(f"wrote {target}", file=sys.stderr)
    else:
        sys.stdout.write(yaml_text)
    print(
        " ".join(
            [
                f"gpu_id={result['gpu_id']}",
                f"cloud={result['cloud']}",
                f"model={result['model']}",
                f"context_tokens={result['context_tokens']}",
                f"hourly_price={result['hourly_price']}",
                f"ttl={result['document']['lifecycle']['ttl']}",
                f"reason={result['reason']}",
            ]
        ),
        file=sys.stderr,
    )
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    """Print the app form fields of a saved YAML as JSON. No API key needed."""
    try:
        text = Path(args.file).expanduser().read_text(encoding="utf-8")
        fields = read_plan_fields(text)
    except OSError as exc:
        print(f"파일을 읽을 수 없습니다: {exc}", file=sys.stderr)
        return 1
    except ConfigError as exc:
        print(_redact(str(exc)), file=sys.stderr)
        return 1
    json.dump(fields, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="podrouter YAML from a subscription agent"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("catalog", help="print the GPU catalog as JSON")

    render = sub.add_parser("render", help="validate a choice and print YAML")
    render.add_argument("--name", required=True)
    render.add_argument("--wish", required=True)
    render.add_argument("--gpu-id", required=True)
    render.add_argument("--cloud", required=True)
    render.add_argument("--model", required=True)
    render.add_argument("--context", required=True, type=int)
    render.add_argument("--reason", required=True)
    render.add_argument(
        "--ttl", default="3h", help="Pod 수명. 지나면 스스로 삭제 (3h, 30m, 1h30m)"
    )
    render.add_argument("--out")

    inspect = sub.add_parser("inspect", help="print app form fields of a YAML as JSON")
    inspect.add_argument("--file", required=True)

    sub.add_parser("guide", help="print the rules the YAML planner follows")

    args = parser.parse_args(argv)
    if args.command == "catalog":
        return _cmd_catalog()
    if args.command == "inspect":
        return _cmd_inspect(args)
    if args.command == "guide":
        from podrouter.planner import _SYSTEM_PROMPT

        sys.stdout.write(_SYSTEM_PROMPT.rstrip() + "\n")
        return 0
    return _cmd_render(args)


if __name__ == "__main__":
    raise SystemExit(main())
