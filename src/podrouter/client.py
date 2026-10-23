"""RunPod calls go through the pinned SDK. Health checks are plain HTTP GETs."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from typing import TypeVar

import requests
import runpod
from runpod import error as runpod_error
from runpod.api.graphql import run_graphql_query

from podrouter.urls import health_url

T = TypeVar("T")

_STOCK_MARKERS = (
    "no longer any instances",
    "no instances",
    "could not allocate",
    "unable to deploy",
    "insufficient capacity",
    "no gpu found",
    "unauthorized",
    "invalid api key",
)


def is_stock_or_permanent(exc: BaseException) -> bool:
    """True when another try of the same call will not succeed."""
    if isinstance(exc, json.JSONDecodeError):
        return False
    if isinstance(exc, ValueError):
        return True
    if isinstance(exc, runpod_error.QueryError):
        text = str(exc).lower()
        return any(marker in text for marker in _STOCK_MARKERS)
    return False


def is_transient(exc: BaseException) -> bool:
    """HTTP 5xx, transport errors, and GraphQL faults that are not stock misses."""
    if isinstance(
        exc, (KeyboardInterrupt, SystemExit, runpod_error.AuthenticationError)
    ):
        return False
    if isinstance(exc, requests.RequestException):
        return True
    if isinstance(exc, json.JSONDecodeError):
        return True
    if isinstance(exc, runpod_error.QueryError):
        return not is_stock_or_permanent(exc)
    return False


class RunPodClient:
    """SDK pod create/get/terminate plus one readiness GET."""

    def __init__(self, api_key: str, health_base: str | None = None) -> None:
        self.api_key = api_key
        self.health_base = health_base

    def hourly_price(self, gpu_id: str, cloud: str, gpu_count: int) -> float | None:
        gpu = self._retry(lambda: runpod.get_gpu(gpu_id, gpu_count))
        return _price_for_cloud(gpu, cloud)

    def create_pod(
        self,
        *,
        name: str,
        image: str,
        gpu_type_id: str,
        cloud: str,
        gpu_count: int,
        container_disk_gb: int,
        ports: str,
        docker_args: str,
        env: dict[str, str],
        network_volume_id: str | None,
    ) -> dict:
        def call() -> dict:
            return runpod.create_pod(
                name=name,
                image_name=image,
                gpu_type_id=gpu_type_id,
                cloud_type=cloud,
                gpu_count=gpu_count,
                container_disk_in_gb=container_disk_gb,
                volume_in_gb=0,
                ports=ports,
                docker_args=graphql_string(docker_args),
                env={key: graphql_string(value) for key, value in env.items()},
                network_volume_id=network_volume_id,
            )

        created = self._retry(call)
        if not isinstance(created, dict):
            raise runpod_error.QueryError("pod create returned no pod")
        return created

    def get_pod(self, pod_id: str) -> dict | None:
        pod = self._retry(lambda: runpod.get_pod(pod_id))
        return pod if isinstance(pod, dict) else None

    def get_pods(self) -> list[dict]:
        pods = self._retry(lambda: runpod.get_pods())
        if not isinstance(pods, list):
            return []
        return [pod for pod in pods if isinstance(pod, dict)]

    def terminate(self, pod_id: str) -> None:
        self._retry(lambda: runpod.terminate_pod(pod_id))

    def pod_logs(self, pod_id: str) -> str:
        query = (
            "query gpuUpPodLogs { pod(input: {podId: "
            f'"{pod_id}"'
            "}) { id containerLogs } }"
        )

        def call() -> str:
            payload = run_graphql_query(query)
            pod = (payload.get("data") or {}).get("pod") or {}
            logs = pod.get("containerLogs") or ""
            return logs if isinstance(logs, str) else str(logs)

        return self._retry(call)

    def health_status(
        self, pod_id: str, port: int, path: str, timeout: float
    ) -> int | None:
        url = health_url(pod_id, port, path, self.health_base)
        try:
            response = requests.get(url, timeout=timeout)
        except requests.RequestException:
            return None
        return response.status_code

    def _retry(self, fn: Callable[[], T]) -> T:
        runpod.api_key = self.api_key
        attempts = _attempts()
        delay = _base_delay()
        last: BaseException | None = None
        for attempt in range(attempts):
            try:
                return fn()
            except Exception as exc:
                if not is_transient(exc):
                    raise
                last = exc
                if attempt == attempts - 1:
                    break
                time.sleep(delay)
                delay *= 2
        assert last is not None
        raise last


def graphql_string(value: str) -> str:
    """Escape a value for the SDK's unescaped GraphQL double-quoted scalar."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _price_for_cloud(gpu: dict, cloud: str) -> float | None:
    cloud = cloud.upper()
    if cloud == "SECURE":
        if gpu.get("secureCloud") is False:
            return None
        return _as_price(gpu.get("securePrice"))
    if cloud == "COMMUNITY":
        if gpu.get("communityCloud") is False:
            return None
        return _as_price(gpu.get("communityPrice"))
    options: list[float] = []
    if gpu.get("secureCloud") is not False:
        price = _as_price(gpu.get("securePrice"))
        if price is not None:
            options.append(price)
    if gpu.get("communityCloud") is not False:
        price = _as_price(gpu.get("communityPrice"))
        if price is not None:
            options.append(price)
    return min(options) if options else None


def _as_price(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _base_delay() -> float:
    raw = os.environ.get("PODROUTER_RETRY_BASE_SEC", "0.5")
    try:
        value = float(raw)
    except ValueError:
        return 0.5
    return value if value > 0 else 0.5


def _attempts() -> int:
    raw = os.environ.get("PODROUTER_RETRY_ATTEMPTS", "5")
    try:
        value = int(raw)
    except ValueError:
        return 5
    return value if value >= 1 else 5
