"""Load a server YAML file and resolve ${ENV} secrets without writing it back."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from gpu_up.duration import parse_duration
from gpu_up.errors import ConfigError

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_CLOUDS = {"SECURE", "COMMUNITY", "ALL"}


@dataclass(frozen=True)
class Config:
    """Resolved server config. Secret values live only on this object."""

    name: str
    image: str
    args: str
    candidates: tuple[str, ...]
    gpu_count: int
    max_price_per_hour: float
    cloud: str
    container_disk_gb: int
    network_volume_id: str | None
    env: dict[str, str]
    port: int
    readiness_path: str
    readiness_interval_sec: float
    readiness_timeout_sec: float
    ttl_text: str
    ttl_sec: int
    allocation_timeout_sec: float
    boot_timeout_sec: float
    boot_interval_sec: float
    config_hash: str
    secrets: tuple[str, ...]


def resolve_api_key(raw: str | None) -> str:
    """Resolve `--api-key`, including a `${ENV}` reference. Empty is refused."""
    if raw is None or str(raw).strip() == "":
        raise ConfigError("--api-key is required")
    text = str(raw).strip()
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", text)
    if match:
        name = match.group(1)
        value = os.environ.get(name, "")
        if value == "":
            raise ConfigError(f"secret {name} is empty")
        return value
    return text


def load_config(path: str | Path, environ: dict[str, str] | None = None) -> Config:
    """Read YAML. `${ENV}` is resolved in memory and never written back."""
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config must be a mapping")

    config_hash = hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()[:16]

    env = os.environ if environ is None else environ
    secrets: list[str] = []
    resolved = _resolve(raw, env, secrets)
    return _build(resolved, config_hash, secrets)


def _resolve(node: object, environ: dict[str, str], secrets: list[str]) -> object:
    if isinstance(node, dict):
        return {key: _resolve(value, environ, secrets) for key, value in node.items()}
    if isinstance(node, list):
        return [_resolve(item, environ, secrets) for item in node]
    if isinstance(node, str):
        return _resolve_string(node, environ, secrets)
    return node


def _resolve_string(value: str, environ: dict[str, str], secrets: list[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        found = environ.get(name, "")
        if found == "":
            raise ConfigError(f"secret {name} is empty")
        secrets.append(found)
        return found

    return _ENV_REF.sub(replace, value)


def _build(raw: dict, config_hash: str, secrets: list[str]) -> Config:
    name = _require_str(raw, "name")
    image = _require_str(raw, "image")
    args = raw.get("args", "")
    if not isinstance(args, str):
        raise ConfigError("args must be a string")

    gpu = raw.get("gpu")
    if not isinstance(gpu, dict):
        raise ConfigError("gpu must be a mapping")
    candidates_raw = gpu.get("candidates")
    if not isinstance(candidates_raw, list) or not candidates_raw:
        raise ConfigError("gpu.candidates must be a non-empty list")
    candidates: list[str] = []
    for item in candidates_raw:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError("gpu.candidates entries must be non-empty strings")
        candidates.append(item.strip())

    gpu_count = _positive_int(gpu.get("count", 1), "gpu.count")
    max_price = gpu.get("max_price_per_hour")
    if isinstance(max_price, bool) or not isinstance(max_price, (int, float)):
        raise ConfigError("gpu.max_price_per_hour is required")
    if float(max_price) < 0:
        raise ConfigError("gpu.max_price_per_hour must be >= 0")

    cloud = str(gpu.get("cloud", "")).strip().upper()
    if cloud not in _CLOUDS:
        raise ConfigError("gpu.cloud must be SECURE, COMMUNITY, or ALL")

    storage = raw.get("storage")
    if not isinstance(storage, dict):
        raise ConfigError("storage must be a mapping")
    disk = _positive_int(storage.get("container_disk_gb"), "storage.container_disk_gb")
    volume = storage.get("network_volume_id")
    if volume is not None and not isinstance(volume, str):
        raise ConfigError("storage.network_volume_id must be a string or null")
    if isinstance(volume, str) and not volume.strip():
        volume = None

    env_raw = raw.get("env") or {}
    if not isinstance(env_raw, dict):
        raise ConfigError("env must be a mapping")
    env = {str(key): str(value) for key, value in env_raw.items()}

    port = _positive_int(raw.get("port"), "port")
    if port > 65535:
        raise ConfigError("port must be <= 65535")

    readiness = raw.get("readiness") or {}
    if not isinstance(readiness, dict):
        raise ConfigError("readiness must be a mapping")
    path = readiness.get("path", "/health")
    if not isinstance(path, str) or not path.startswith("/"):
        raise ConfigError("readiness.path must start with /")
    interval = _positive_number(
        readiness.get("interval_sec", 10), "readiness.interval_sec"
    )
    ready_timeout = _positive_number(
        readiness.get("timeout_sec", 900), "readiness.timeout_sec"
    )

    lifecycle = raw.get("lifecycle")
    if not isinstance(lifecycle, dict) or "ttl" not in lifecycle:
        raise ConfigError("lifecycle.ttl is required")
    ttl_text = lifecycle["ttl"]
    if not isinstance(ttl_text, str):
        raise ConfigError("lifecycle.ttl must be a duration like 3h or 30m")
    ttl_sec = parse_duration(ttl_text, field="lifecycle.ttl")
    # idle_timeout is accepted and ignored (F8 is out of scope).
    if "idle_timeout" in lifecycle and lifecycle["idle_timeout"] is not None:
        idle = lifecycle["idle_timeout"]
        if not isinstance(idle, str):
            raise ConfigError("lifecycle.idle_timeout must be a duration like 30m")
        parse_duration(idle, field="lifecycle.idle_timeout")

    timeouts = raw.get("timeouts") or {}
    if not isinstance(timeouts, dict):
        raise ConfigError("timeouts must be a mapping")
    allocation = _positive_number(
        timeouts.get("allocation_sec", 300), "timeouts.allocation_sec"
    )
    boot = _positive_number(timeouts.get("boot_sec", 300), "timeouts.boot_sec")
    boot_interval = _positive_number(
        timeouts.get("boot_interval_sec", 2), "timeouts.boot_interval_sec"
    )

    return Config(
        name=name,
        image=image,
        args=args,
        candidates=tuple(candidates),
        gpu_count=gpu_count,
        max_price_per_hour=float(max_price),
        cloud=cloud,
        container_disk_gb=disk,
        network_volume_id=volume if isinstance(volume, str) else None,
        env=env,
        port=port,
        readiness_path=path,
        readiness_interval_sec=interval,
        readiness_timeout_sec=ready_timeout,
        ttl_text=ttl_text.strip(),
        ttl_sec=ttl_sec,
        allocation_timeout_sec=allocation,
        boot_timeout_sec=boot,
        boot_interval_sec=boot_interval,
        config_hash=config_hash,
        secrets=tuple(secrets),
    )


def _require_str(raw: dict, field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} is required")
    return value.strip()


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{field} must be a positive integer")
    if value <= 0:
        raise ConfigError(f"{field} must be a positive integer")
    return value


def _positive_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{field} must be a positive number")
    if float(value) <= 0:
        raise ConfigError(f"{field} must be a positive number")
    return float(value)
