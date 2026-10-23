"""Local stand-in for the RunPod GraphQL host and the pod health URL."""

from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


class StubServer:
    """Records create, list, and terminate. Serves prices, stock misses, 5xx, and /health."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.gpus = {
            "NVIDIA GeForce RTX 4090": {
                "securePrice": 1.5,
                "communityPrice": 1.25,
                "stock": True,
            },
            "NVIDIA RTX A6000": {
                "securePrice": 0.5,
                "communityPrice": 0.25,
                "stock": True,
            },
            "NVIDIA A40": {
                "securePrice": 0.75,
                "communityPrice": 0.5,
                "stock": True,
            },
        }
        self.pods: dict[str, dict] = {}
        self.unrelated_id = "other-pod-xyz"
        self.pods[self.unrelated_id] = {
            "id": self.unrelated_id,
            "name": "not-ours",
            "desiredStatus": "RUNNING",
            "costPerHr": 9.99,
            "env": [{"key": "OTHER", "value": "1"}],
            "imageName": "nginx:latest",
        }
        self.creates: list[dict] = []
        self.terminates: list[str] = []
        self.requests: list[dict] = []
        self.health_hits: list[str] = []
        self.health_code = 200
        self.health_queue: list[int] = []
        self.block_health = False
        self.health_entered = threading.Event()
        self.health_gate = threading.Event()
        self.transient_remaining = 0
        self.null_get_pod = False
        self.hold_status: str | None = None
        self.gpu_delay = 0.0
        self.logs = "warmup still loading tokenizer\n"
        self._next_id = 1
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    def __enter__(self) -> StubServer:  # noqa: PYI034 — class object is not available for Self on 3.10
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                status, payload, content_type = stub.handle_graphql(body)
                data = (
                    payload
                    if isinstance(payload, bytes)
                    else json.dumps(payload).encode()
                )
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                path = urlparse(self.path).path
                status = stub.take_health(path)
                data = b'{"ok":true}'
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, fmt: str, *args: object) -> None:
                return

        class Server(ThreadingHTTPServer):
            allow_reuse_address = True

            def handle_error(self, request: object, client_address: object) -> None:
                return

        self._httpd = Server(("127.0.0.1", 0), Handler)
        self.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def take_health(self, path: str) -> int:
        with self.lock:
            self.health_hits.append(path)
            if self.health_queue:
                code = self.health_queue.pop(0)
            else:
                code = self.health_code
            block = self.block_health
        if block:
            self.health_entered.set()
            self.health_gate.wait(timeout=15)
        return code

    def handle_graphql(self, body: bytes) -> tuple[int, object, str]:
        arrived = time.monotonic()
        try:
            query = json.loads(body.decode()).get("query", "")
        except (UnicodeError, json.JSONDecodeError):
            query = ""
        with self.lock:
            if self.transient_remaining > 0:
                self.transient_remaining -= 1
                self.requests.append({"op": "graphql", "status": 500, "at": arrived})
                return 500, b"unavailable", "text/plain"
        op, status, payload = self._route(query)
        with self.lock:
            self.requests.append({"op": op, "status": status, "at": arrived})
        return status, payload, "application/json"

    def _route(self, query: str) -> tuple[str, int, dict]:
        if "containerLogs" in query or "gpuUpPodLogs" in query:
            return "logs", 200, self._logs(query)
        if "podTerminate" in query:
            return "terminate", 200, self._terminate(query)
        if "podFindAndDeployOnDemand" in query:
            return "create", 200, self._create(query)
        if "gpuTypes" in query:
            if self.gpu_delay:
                time.sleep(self.gpu_delay)
            return "gpu", 200, self._gpu(query)
        if "networkVolumes" in query:
            return "user", 200, self._user()
        if "pod(input:" in query:
            return "get_pod", 200, self._get_pod(query)
        if "pods" in query:
            return "list", 200, self._list()
        return "unknown", 200, {"data": {}}

    def _gpu(self, query: str) -> dict:
        match = re.search(r'gpuTypes\(input:\s*\{id:\s*"([^"]+)"', query)
        if match is None:
            types = [
                self._gpu_object(gpu_id, spec) for gpu_id, spec in self.gpus.items()
            ]
            return {"data": {"gpuTypes": types}}
        gpu_id = match.group(1)
        spec = self.gpus.get(gpu_id)
        if spec is None:
            return {"data": {"gpuTypes": []}}
        return {"data": {"gpuTypes": [self._gpu_object(gpu_id, spec)]}}

    def _gpu_object(self, gpu_id: str, spec: dict) -> dict:
        return {
            "maxGpuCount": 8,
            "id": gpu_id,
            "displayName": gpu_id,
            "manufacturer": "NVIDIA",
            "memoryInGb": 24,
            "cudaCores": 1,
            "secureCloud": True,
            "communityCloud": True,
            "securePrice": spec["securePrice"],
            "communityPrice": spec["communityPrice"],
            "oneMonthPrice": spec["securePrice"],
            "threeMonthPrice": spec["securePrice"],
            "oneWeekPrice": spec["securePrice"],
            "communitySpotPrice": spec["communityPrice"],
            "secureSpotPrice": spec["securePrice"],
            "lowestPrice": {
                "minimumBidPrice": spec["communityPrice"],
                "uninterruptablePrice": spec["securePrice"],
            },
        }

    def _create(self, query: str) -> dict:
        gpu_id = _field(query, "gpuTypeId") or ""
        spec = self.gpus.get(gpu_id, {})
        record = {
            "gpu_type_id": gpu_id,
            "name": _field(query, "name"),
            "image_name": _field(query, "imageName"),
            "cloud_type": _word(query, "cloudType"),
            "gpu_count": _int_field(query, "gpuCount"),
            "disk": _int_field(query, "containerDiskInGb"),
            "ports": _field(query, "ports"),
            "docker_args": _field(query, "dockerArgs") or "",
            "network_volume_id": _field(query, "networkVolumeId"),
            "env": dict(re.findall(r'key: "([^"]*)", value: "([^"]*)"', query)),
            "accepted": False,
            "pod_id": "",
        }
        with self.lock:
            self.creates.append(record)
            if not spec or not spec.get("stock", False):
                return {
                    "errors": [
                        {"message": "There are no longer any instances available."}
                    ]
                }
            pod_id = f"pod{self._next_id}"
            self._next_id += 1
            price = spec["securePrice"]
            if record["cloud_type"] == "COMMUNITY":
                price = spec["communityPrice"]
            status = self.hold_status or "RUNNING"
            pod = {
                "id": pod_id,
                "name": record["name"],
                "desiredStatus": status,
                "costPerHr": price,
                "env": [
                    {"key": key, "value": value} for key, value in record["env"].items()
                ],
                "imageName": record["image_name"],
                "gpuCount": record["gpu_count"],
            }
            self.pods[pod_id] = pod
            record["accepted"] = True
            record["pod_id"] = pod_id
        env_list = [f"{key}={value}" for key, value in record["env"].items()]
        return {
            "data": {
                "podFindAndDeployOnDemand": {
                    "id": pod_id,
                    "imageName": record["image_name"],
                    "env": env_list,
                    "machineId": "machine-1",
                    "machine": {"podHostId": "host-1"},
                }
            }
        }

    def _list(self) -> dict:
        with self.lock:
            pods = [self._public_pod(pod) for pod in self.pods.values()]
        return {"data": {"myself": {"pods": pods}}}

    def _get_pod(self, query: str) -> dict:
        if self.null_get_pod:
            return {"data": {"pod": None}}
        match = re.search(r'podId:\s*"([^"]+)"', query)
        pod_id = match.group(1) if match else ""
        with self.lock:
            pod = self.pods.get(pod_id)
            if pod is None:
                return {"data": {"pod": None}}
            return {"data": {"pod": self._public_pod(pod)}}

    def _terminate(self, query: str) -> dict:
        match = re.search(r'podId:\s*"([^"]+)"', query)
        pod_id = match.group(1) if match else ""
        with self.lock:
            self.terminates.append(pod_id)
            pod = self.pods.get(pod_id)
            if pod is not None:
                pod["desiredStatus"] = "TERMINATED"
        return {"data": {"podTerminate": None}}

    def _logs(self, query: str) -> dict:
        match = re.search(r'podId:\s*"([^"]+)"', query)
        pod_id = match.group(1) if match else ""
        return {"data": {"pod": {"id": pod_id, "containerLogs": self.logs}}}

    def _user(self) -> dict:
        return {
            "data": {
                "myself": {
                    "id": "user-1",
                    "pubKey": None,
                    "networkVolumes": [
                        {
                            "id": "vol-1",
                            "name": "models",
                            "size": 100,
                            "dataCenterId": "US-KS-2",
                        }
                    ],
                }
            }
        }

    def _public_pod(self, pod: dict) -> dict:
        return {
            "id": pod["id"],
            "name": pod.get("name"),
            "desiredStatus": pod.get("desiredStatus"),
            "costPerHr": pod.get("costPerHr"),
            "env": pod.get("env"),
            "imageName": pod.get("imageName"),
            "gpuCount": pod.get("gpuCount"),
            "uptimeSeconds": 1,
            "machine": {"gpuDisplayName": "gpu"},
        }

    def accepted_creates(self) -> list[dict]:
        with self.lock:
            return [dict(item) for item in self.creates if item["accepted"]]

    def all_creates(self) -> list[dict]:
        with self.lock:
            return [dict(item) for item in self.creates]

    def live_managed_ids(self) -> list[str]:
        with self.lock:
            found = []
            for pod in self.pods.values():
                if pod.get("desiredStatus") in {
                    "TERMINATED",
                    "EXITED",
                    "DEAD",
                    "FAILED",
                }:
                    continue
                env = {
                    item["key"]: item["value"]
                    for item in pod.get("env") or []
                    if isinstance(item, dict)
                }
                if env.get("PODROUTER_MANAGED") == "1":
                    found.append(pod["id"])
            return found

    def request_ops(self) -> list[str]:
        with self.lock:
            return [item["op"] for item in self.requests]


def _field(query: str, name: str) -> str | None:
    marker = f'{name}: "'
    start = query.find(marker)
    if start < 0:
        return None
    return _graphql_string(query, start + len(marker) - 1)


def _graphql_string(query: str, start: int) -> str | None:
    """Decode one GraphQL double-quoted string. `start` is the opening quote."""
    if start >= len(query) or query[start] != '"':
        return None
    i = start + 1
    out: list[str] = []
    while i < len(query):
        char = query[i]
        if char == "\\":
            if i + 1 >= len(query):
                return None
            nxt = query[i + 1]
            out.append({"n": "\n", "r": "\r", "t": "\t"}.get(nxt, nxt))
            i += 2
            continue
        if char == '"':
            return "".join(out)
        out.append(char)
        i += 1
    return None


def _word(query: str, name: str) -> str | None:
    match = re.search(rf"{name}: (\w+)", query)
    return match.group(1) if match else None


def _int_field(query: str, name: str) -> int | None:
    match = re.search(rf"{name}: (\d+)", query)
    return int(match.group(1)) if match else None
