"""Identity for pods this tool created: name plus a hash of the config."""

from __future__ import annotations

MANAGED = "GPU_UP_MANAGED"
NAME = "GPU_UP_NAME"
CONFIG_HASH = "GPU_UP_CONFIG_HASH"
TTL_SEC = "GPU_UP_TTL_SEC"
CREATED_AT = "GPU_UP_CREATED_AT"
PORT = "GPU_UP_PORT"
GPU = "GPU_UP_GPU"
PRICE = "GPU_UP_PRICE"

_TERMINAL = {"TERMINATED", "EXITED", "DEAD", "FAILED"}


def parse_env(raw: object) -> dict[str, str]:
    """Accept the env shapes the pod query may return."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {
            str(key): "" if value is None else str(value) for key, value in raw.items()
        }
    if isinstance(raw, list):
        parsed: dict[str, str] = {}
        for item in raw:
            if isinstance(item, str) and "=" in item:
                key, value = item.split("=", 1)
                parsed[key] = value
            elif isinstance(item, dict) and "key" in item:
                parsed[str(item["key"])] = (
                    "" if item.get("value") is None else str(item["value"])
                )
        return parsed
    return {}


def is_managed(pod: dict) -> bool:
    return parse_env(pod.get("env")).get(MANAGED) == "1"


def is_live(pod: dict) -> bool:
    return str(pod.get("desiredStatus") or "").upper() not in _TERMINAL


def same_identity(pod: dict, name: str, config_hash: str) -> bool:
    env = parse_env(pod.get("env"))
    return (
        is_managed(pod)
        and is_live(pod)
        and env.get(NAME) == name
        and env.get(CONFIG_HASH) == config_hash
    )


def hourly_price(pod: dict) -> float:
    """Prefer the pod's billed price, then the price recorded at create time."""
    raw = pod.get("costPerHr")
    try:
        price = float(raw)
    except (TypeError, ValueError):
        price = 0.0
    if price > 0:
        return price
    env = parse_env(pod.get("env"))
    try:
        return float(env.get(PRICE, "0"))
    except ValueError:
        return 0.0


def remaining_ttl_sec(pod: dict, now: float) -> int | None:
    """Seconds left. Never greater than the TTL stored on the pod."""
    env = parse_env(pod.get("env"))
    try:
        ttl = int(env[TTL_SEC])
        created = int(float(env[CREATED_AT]))
    except (KeyError, TypeError, ValueError):
        return None
    if ttl < 0:
        return None
    elapsed = max(int(now) - created, 0)
    remaining = min(ttl - elapsed, ttl)
    return max(remaining, 0)
