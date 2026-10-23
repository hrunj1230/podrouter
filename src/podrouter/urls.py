"""Proxy URL formatting and the health-check URL derived from it."""

from __future__ import annotations


def proxy_url(pod_id: str, port: int) -> str:
    return f"https://{pod_id}-{port}.proxy.runpod.net"


def health_url(pod_id: str, port: int, path: str, health_base: str | None) -> str:
    if not path.startswith("/"):
        path = "/" + path
    if health_base:
        return f"{health_base.rstrip('/')}/{pod_id}-{port}{path}"
    return proxy_url(pod_id, port) + path
