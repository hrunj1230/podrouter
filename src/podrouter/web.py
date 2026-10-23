"""Local loopback site for GPU stock polling and Pod launch."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import yaml

from podrouter.client import RunPodClient
from podrouter.duration import parse_duration
from podrouter.paths import data_home
from podrouter.errors import ConfigError
from podrouter.pods import (
    GPU,
    NAME,
    TTL_SEC,
    hourly_price,
    is_live,
    is_managed,
    parse_env,
    remaining_ttl_sec,
)

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = Path(__file__).resolve().parent / "static" / "index.html"
_LIFECYCLE = Path(__file__).resolve().parents[2] / "docs" / "pod-lifecycle.html"
_WORK = _ROOT / ".podrouter-web"
_GPU_QUERY = """
query {
  gpuTypes {
    id
    displayName
    memoryInGb
    secureCloud
    communityCloud
    securePrice
    communityPrice
    secureStock: lowestPrice(input: {gpuCount: 1, secureCloud: true}) {
      uninterruptablePrice
      stockStatus
    }
    communityStock: lowestPrice(input: {gpuCount: 1, secureCloud: false}) {
      uninterruptablePrice
      stockStatus
    }
  }
}
"""
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,40}$")
_GPU_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+-]{1,80}$")
_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:@-]{0,160}$")
_CLOUDS = {"SECURE", "COMMUNITY", "ALL"}
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_LAUNCH_LOCK = threading.Lock()
_CANCEL: set[str] = set()
_MIN_RETRY_SEC = 15
_MAX_ATTEMPTS = 240


def load_dotenv(path: Path | None = None) -> None:
    """Fill empty process env from `.env`. Never overrides a value already set."""
    file = path or (data_home() / ".env")
    if not file.is_file():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _secrets() -> list[str]:
    found = []
    for name in ("RUNPOD_API_KEY", "HF_TOKEN", "VLLM_API_KEY", "MODEL_API_KEY", "TS_AUTHKEY", "CF_TUNNEL_TOKEN", "XAI_API_KEY"):
        value = os.environ.get(name, "")
        if value:
            found.append(value)
    return sorted(set(found), key=len, reverse=True)


def _redact(text: str) -> str:
    redacted = text
    for secret in _secrets():
        redacted = redacted.replace(secret, "***")
    return redacted


def _tier(gpu: dict, prefix: str) -> dict:
    stock = gpu.get(f"{prefix}Stock") or {}
    price = gpu.get(f"{prefix}Price")
    offer = stock.get("uninterruptablePrice")
    return {
        "offered": bool(gpu.get(f"{prefix}Cloud")),
        "price": price if isinstance(price, (int, float)) else None,
        "stock": stock.get("stockStatus"),
        "offer": offer if isinstance(offer, (int, float)) else None,
    }


def gpu_catalog(api_key: str) -> list[dict]:
    """Return one row per GPU type. Prices and stock only, no account secrets."""
    from runpod.api.graphql import run_graphql_query

    payload = run_graphql_query(_GPU_QUERY, api_key)
    rows = []
    for gpu in payload["data"]["gpuTypes"]:
        rows.append(
            {
                "id": gpu["id"],
                "name": gpu["displayName"],
                "memory_gb": gpu["memoryInGb"],
                "secure": _tier(gpu, "secure"),
                "community": _tier(gpu, "community"),
            }
        )
    rows.sort(key=lambda item: (item["name"] or "", item["id"]))
    return rows


def managed_pods(api_key: str) -> list[dict]:
    client = RunPodClient(api_key)
    now = time.time()
    listed = []
    for pod in client.get_pods():
        if not is_managed(pod) or not is_live(pod):
            continue
        env = parse_env(pod.get("env"))
        remaining = remaining_ttl_sec(pod, now)
        listed.append(
            {
                "pod_id": pod.get("id"),
                "name": env.get(NAME) or pod.get("name") or "",
                "state": pod.get("desiredStatus") or "UNKNOWN",
                "gpu": env.get(GPU, ""),
                "hourly_price": hourly_price(pod),
                "ttl_sec": env.get(TTL_SEC, ""),
                "remaining_ttl_sec": remaining,
            }
        )
    return listed


def _require(body: dict, key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} is required")
    return value.strip()


def _config_from_body(body: dict) -> dict:
    name = _require(body, "name")
    if _NAME.fullmatch(name) is None:
        raise ConfigError("name must be letters, numbers, and dashes")
    gpu_id = _require(body, "gpu_id")
    if _GPU_ID.fullmatch(gpu_id) is None:
        raise ConfigError("gpu_id is not a RunPod GPU id")
    cloud = _require(body, "cloud").upper()
    if cloud not in _CLOUDS:
        raise ConfigError("cloud must be SECURE, COMMUNITY, or ALL")
    image = _require(body, "image")
    if _IMAGE.fullmatch(image) is None:
        raise ConfigError("image must be a single image reference")
    args = body.get("args", "")
    if not isinstance(args, str) or "\n" in args or "\r" in args:
        raise ConfigError("args must be one line")
    try:
        port = int(body.get("port"))
        disk = int(body.get("disk_gb"))
    except (TypeError, ValueError) as exc:
        raise ConfigError("port and disk_gb must be numbers") from exc
    if not 1 <= port <= 65535:
        raise ConfigError("port must be 1-65535")
    if not 1 <= disk <= 200:
        raise ConfigError("disk_gb must be 1-200")
    max_price = body.get("max_price_per_hour")
    if max_price is not None:
        try:
            p_val = float(max_price)
            if not 0 < p_val <= 20:
                raise ConfigError("max_price_per_hour must be between 0 and 20")
        except (TypeError, ValueError) as exc:
            raise ConfigError("max_price_per_hour must be a number") from exc
    ttl = _require(body, "ttl")
    parse_duration(ttl, field="lifecycle.ttl")
    wait = body.get("wait", False)
    if not isinstance(wait, bool):
        raise ConfigError("wait must be true or false")
    document = {
        "name": name,
        "image": image,
        "args": args,
        "gpu": {
            "candidates": [gpu_id],
            "count": 1,
            "cloud": cloud,
        },
        "storage": {"container_disk_gb": disk, "network_volume_id": None},
        "port": port,
        "readiness": {"path": "/health", "interval_sec": 10, "timeout_sec": 900},
        "lifecycle": {"ttl": ttl},
        "timeouts": {
            "allocation_sec": 180,
            "boot_sec": 600,
            "boot_interval_sec": 5,
        },
    }
    retry = body.get("retry_until_seat", False)
    if not isinstance(retry, bool):
        raise ConfigError("retry_until_seat must be true or false")
    attempts = 1
    interval = 0
    if retry:
        if wait:
            raise ConfigError("자리 재시도는 헬스 대기를 끈 상태에서만 할 수 있습니다")
        try:
            attempts = int(body.get("max_attempts", _MAX_ATTEMPTS))
            interval = int(body.get("interval_sec", _MIN_RETRY_SEC))
        except (TypeError, ValueError) as exc:
            raise ConfigError("max_attempts and interval_sec must be integers") from exc
        if not 1 <= attempts <= _MAX_ATTEMPTS:
            raise ConfigError(f"max_attempts must be 1-{_MAX_ATTEMPTS}")
        if not _MIN_RETRY_SEC <= interval <= 300:
            raise ConfigError(f"interval_sec must be {_MIN_RETRY_SEC}-300")
    return {
        "document": document,
        "wait": wait,
        "name": name,
        "attempts": attempts,
        "interval_sec": interval,
    }


def plan_from_body(body: dict, *, post: object = None, catalog: list[dict] | None = None) -> dict:
    xai_api_key = os.environ.get("XAI_API_KEY", "")
    if not xai_api_key.strip():
        raise ConfigError("XAI_API_KEY가 없습니다. 서버의 .env에만 넣고 이 프로세스를 다시 띄우세요.")
    if not isinstance(body, dict):
        raise ConfigError("request must be a JSON object")
    name = body.get("name")
    wish = body.get("wish")
    from podrouter.planner import plan_pod, validate_plan_input

    wish_folded = validate_plan_input(name, wish)
    if catalog is None:
        if not os.environ.get("RUNPOD_API_KEY"):
            raise ConfigError("secret RUNPOD_API_KEY is empty")
        catalog = gpu_catalog(os.environ["RUNPOD_API_KEY"])

    return plan_pod(name, wish_folded, catalog, api_key=xai_api_key, post=post)  # type: ignore[arg-type]


def start_launch(body: dict) -> dict:
    if not os.environ.get("RUNPOD_API_KEY"):
        raise ConfigError("secret RUNPOD_API_KEY is empty")
    spec = _config_from_body(body)
    if not _LAUNCH_LOCK.acquire(blocking=False):
        raise ConfigError("a launch is already running")
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "status": "running",
        "log": "",
        "url": None,
        "pod_id": None,
        "hourly_price": None,
        "exit_code": None,
        "name": spec["name"],
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = job
    thread = threading.Thread(
        target=_run_launch, args=(job_id, spec), name=f"podrouter-{job_id}", daemon=True
    )
    thread.start()
    return {"job_id": job_id, "status": "running"}


def _cancelled(job_id: str) -> bool:
    with _JOBS_LOCK:
        return job_id in _CANCEL


def _sleep_or_cancel(job_id: str, seconds: int) -> bool:
    for _ in range(seconds):
        if _cancelled(job_id):
            return True
        time.sleep(1)
    return False


def _execute_once(spec: dict) -> dict:
    _WORK.mkdir(mode=0o700, exist_ok=True)
    path = _WORK / f"{uuid.uuid4().hex[:12]}.yaml"
    try:
        path.write_text(
            yaml.safe_dump(spec["document"], sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        os.chmod(path, 0o600)
        wait = "true" if spec["wait"] else "false"
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "from podrouter.cli import main; raise SystemExit(main())",
                "up",
                str(path),
                f"--wait={wait}",
                "--api-key",
                "${RUNPOD_API_KEY}",
            ],
            cwd=_ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=1200 if spec["wait"] else 180,
            check=False,
        )
        log = _redact(proc.stdout + proc.stderr).strip()
        url_match = re.search(r"https://[A-Za-z0-9]+-\d+\.proxy\.runpod\.net", log)
        pod_match = re.search(r"pod_id=([A-Za-z0-9-]+)", log)
        price_match = re.search(
            r"(?:^|\s)hourly_price=([0-9]+(?:\.[0-9]+)?)(?:\s|$)", log
        )
        price = float(price_match.group(1)) if price_match else None
        return {
            "ok": proc.returncode == 0 and url_match is not None,
            "log": log,
            "url": url_match.group(0) if url_match else None,
            "pod_id": pod_match.group(1) if pod_match else None,
            "hourly_price": price,
            "exit_code": proc.returncode,
        }
    except subprocess.TimeoutExpired as exc:
        log = _redact((exc.stdout or "") + (exc.stderr or "")).strip()
        return {
            "ok": False,
            "log": f"{log}\nerror: timed out".strip(),
            "url": None,
            "pod_id": None,
            "hourly_price": None,
            "exit_code": 1,
        }
    finally:
        path.unlink(missing_ok=True)


def _run_launch(job_id: str, spec: dict) -> None:
    chunks: list[str] = []
    try:
        for attempt in range(1, spec["attempts"] + 1):
            if _cancelled(job_id):
                chunks.append("취소했습니다")
                _update_job(job_id, status="error", log="\n".join(chunks), exit_code=1)
                return
            header = f"attempt {attempt}/{spec['attempts']}"
            chunks.append(header)
            _update_job(job_id, log="\n".join(chunks)[-8000:])
            try:
                result = _execute_once(spec)
            except OSError as exc:
                result = {
                    "ok": False,
                    "log": f"error: {exc}",
                    "url": None,
                    "pod_id": None,
                    "hourly_price": None,
                    "exit_code": 1,
                }
            chunks.append(result["log"])
            if result["ok"]:
                _update_job(
                    job_id,
                    status="ok",
                    log="\n".join(chunks)[-8000:],
                    url=result["url"],
                    pod_id=result["pod_id"],
                    hourly_price=result.get("hourly_price"),
                    exit_code=0,
                )
                return
            if attempt == spec["attempts"]:
                chunks.append("stop: no seat within the attempt limit")
                _update_job(
                    job_id,
                    status="error",
                    log="\n".join(chunks)[-8000:],
                    exit_code=result["exit_code"],
                )
                return
            chunks.append(f"wait {spec['interval_sec']}s")
            _update_job(job_id, log="\n".join(chunks)[-8000:])
            if _sleep_or_cancel(job_id, spec["interval_sec"]):
                chunks.append("취소했습니다")
                _update_job(
                    job_id, status="error", log="\n".join(chunks)[-8000:], exit_code=1
                )
                return
    finally:
        with _JOBS_LOCK:
            _CANCEL.discard(job_id)
        _LAUNCH_LOCK.release()


def cancel_launch(job_id: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{12}", job_id):
        raise ConfigError("unknown job")
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise ConfigError("unknown job")
        if job["status"] == "running":
            _CANCEL.add(job_id)
    return {"ok": True}


def _update_job(job_id: str, **fields: object) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job.update(fields)


def stop_pod(name: str) -> dict:
    if _NAME.fullmatch(name) is None:
        raise ConfigError("name must be letters, numbers, and dashes")
    if not os.environ.get("RUNPOD_API_KEY"):
        raise ConfigError("secret RUNPOD_API_KEY is empty")
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from podrouter.cli import main; raise SystemExit(main())",
            "down",
            name,
            "--api-key",
            "${RUNPOD_API_KEY}",
        ],
        cwd=_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    log = _redact(proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        raise ConfigError(log or "down failed")
    return {"ok": True, "log": log}


class Handler(BaseHTTPRequestHandler):
    server_version = "podrouter-web"

    def log_message(self, fmt: str, *args: object) -> None:
        message = _redact(fmt % args)
        sys.stderr.write(f"{self.address_string()} {message}\n")

    def _send(self, status: int, payload: object, content_type: str) -> None:
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload).encode()
        elif isinstance(payload, bytes):
            body = payload
        else:
            body = str(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: object) -> None:
        self._send(status, payload, "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        port = self.server.server_address[1]
        host = self.headers.get("Host", "")
        allowed_hosts = {"127.0.0.1", f"127.0.0.1:{port}", "localhost", f"localhost:{port}"}
        if host not in allowed_hosts:
            raise ConfigError(f"invalid host: {host}")

        origin = self.headers.get("Origin")
        if origin is not None:
            allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
            if origin not in allowed_origins:
                raise ConfigError(f"invalid origin: {origin}")

        sec_fetch_site = self.headers.get("Sec-Fetch-Site")
        if sec_fetch_site is not None and sec_fetch_site.lower() not in {"same-origin", "none"}:
            raise ConfigError(f"invalid Sec-Fetch-Site: {sec_fetch_site}")

        content_type = self.headers.get("Content-Type", "")
        media_type = content_type.split(";")[0].strip().lower()
        if media_type != "application/json":
            raise ConfigError("Content-Type must be application/json")

        length = int(self.headers.get("Content-Length", "0"))
        if length > 32_768:
            raise ConfigError("request is too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigError("request must be JSON") from exc
        if not isinstance(body, dict):
            raise ConfigError("request must be a JSON object")
        return body

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/":
                self._send(200, _STATIC.read_bytes(), "text/html; charset=utf-8")
                return
            if path == "/lifecycle":
                self._send(200, _LIFECYCLE.read_bytes(), "text/html; charset=utf-8")
                return
            if path == "/api/gpus":
                self._json(200, {"gpus": gpu_catalog(os.environ["RUNPOD_API_KEY"])})
                return
            if path == "/api/pods":
                self._json(200, {"pods": managed_pods(os.environ["RUNPOD_API_KEY"])})
                return
            if path.startswith("/api/jobs/"):
                job_id = path.removeprefix("/api/jobs/")
                with _JOBS_LOCK:
                    job = _JOBS.get(job_id)
                if job is None:
                    self._json(404, {"error": "unknown job"})
                    return
                self._json(200, job)
                return
            self._json(404, {"error": "not found"})
        except KeyError:
            self._json(400, {"error": "secret RUNPOD_API_KEY is empty"})
        except Exception as exc:  # noqa: BLE001 — the page shows the safe message
            self._json(502, {"error": _redact(str(exc))})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self._read_json()
            if path == "/api/plan":
                self._json(200, plan_from_body(body))
                return
            if path == "/api/launch":
                self._json(202, start_launch(body))
                return
            if path == "/api/cancel":
                self._json(200, cancel_launch(_require(body, "job_id")))
                return
            if path == "/api/down":
                self._json(200, stop_pod(_require(body, "name")))
                return
            self._json(404, {"error": "not found"})
        except ConfigError as exc:
            self._json(400, {"error": _redact(str(exc))})
        except Exception as exc:  # noqa: BLE001 — the page shows the safe message
            self._json(502, {"error": _redact(str(exc))})


def serve(host: str = "127.0.0.1", port: int = 8765) -> int:
    """Serve the site on loopback. Returns when the process is interrupted."""
    if host not in {"127.0.0.1", "localhost"}:
        print("error: web binds to loopback only", file=sys.stderr)
        return 2
    load_dotenv()
    if not _STATIC.is_file():
        print("error: missing web page", file=sys.stderr)
        return 2
    httpd = None
    last_error: OSError | None = None
    for candidate in range(port, port + 10):
        try:
            httpd = ThreadingHTTPServer((host, candidate), Handler)
            break
        except OSError as exc:
            last_error = exc
    if httpd is None:
        print(f"error: {last_error}", file=sys.stderr)
        return 1
    bound = httpd.server_address[1]
    print(f"listening http://127.0.0.1:{bound}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
    finally:
        httpd.server_close()
    return 0
