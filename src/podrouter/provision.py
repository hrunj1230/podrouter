"""Create one pod, wait until it is healthy, and terminate it on every failure."""

from __future__ import annotations

import json
import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor

from podrouter.client import RunPodClient, is_stock_or_permanent
from podrouter.config import Config
from podrouter.errors import (
    AllocationError,
    BootTimeout,
    ConfigError,
    PodError,
    ReadinessTimeout,
)
from podrouter.pods import (
    CONFIG_HASH,
    CREATED_AT,
    GPU,
    MANAGED,
    NAME,
    PORT,
    PRICE,
    TTL_SEC,
    hourly_price,
    is_live,
    is_managed,
    parse_env,
    remaining_ttl_sec,
    same_identity,
)
from podrouter.protect import ENV_NAME as PROTECTED_ENV
from podrouter.protect import is_protected
from podrouter.reaper import render_start_command
from podrouter.redact import Redactor
from podrouter.tunnel import (
    Tunnel,
    cloudflare_hostname,
    find_quick_url,
    resolve_tunnel,
    tunnel_docker_args,
    tunnel_env,
)
from podrouter.urls import proxy_url


class _Session:
    def __init__(self) -> None:
        self.pod_id: str | None = None
        self.gpu: str = ""
        self.price: float = 0.0
        self.success = False
        self.interrupted = False


# Exit code of `up` when no GPU seat was found. The timer uses it to tell
# "no seat, try again" from other failures.
NO_SEAT_EXIT = 3


def bring_up(
    cfg: Config,
    client: RunPodClient,
    *,
    wait: bool,
    log: Redactor,
    retry: bool = True,
) -> int:
    """Run `up`. A successful return does not terminate the pod."""
    if is_protected(cfg.name):
        log.stderr(f"error: {cfg.name} is protected ({PROTECTED_ENV})")
        return 2
    try:
        tunnel = resolve_tunnel()
    except ConfigError as exc:
        log.stderr(f"error: {exc}")
        return 2
    started = time.monotonic()
    session = _Session()
    previous = signal.getsignal(signal.SIGINT)

    def on_sigint(signum: int, frame: object) -> None:
        session.interrupted = True
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, on_sigint)
    exit_code = 1
    try:
        existing = _find_match(client, cfg, tunnel)
        if existing is not None:
            session.pod_id = str(existing["id"])
            session.gpu = parse_env(existing.get("env")).get(GPU, "")
            session.price = hourly_price(existing)
            _stage(
                log,
                "reuse",
                started,
                pod_id=session.pod_id,
                gpu=session.gpu,
                hourly_price=_price_text(session.price),
            )
        else:
            pod_id, gpu, price = _allocate(
                cfg, client, log, started, tunnel, retry=retry
            )
            session.pod_id = pod_id
            session.gpu = gpu
            session.price = price

        url = proxy_url(session.pod_id, cfg.port)
        if not wait:
            _print_endpoint(log, session.pod_id, url, cfg, session.price, tunnel)
            session.success = True
            exit_code = 0
            return exit_code

        _wait_running(cfg, client, session, log, started)
        _wait_healthy(cfg, client, session, log, started)
        _print_endpoint(
            log, session.pod_id, url, cfg, session.price, tunnel, client=client
        )
        session.success = True
        exit_code = 0
        return exit_code
    except KeyboardInterrupt:
        session.interrupted = True
        log.stderr("error: interrupted")
        exit_code = 130
        return exit_code
    except AllocationError as exc:
        log.stderr(f"error: {exc}")
        exit_code = NO_SEAT_EXIT
        return exit_code
    except Exception as exc:  # noqa: BLE001 — unknown SDK failures must still tear the pod down
        log.stderr(f"error: {exc}")
        exit_code = 1
        return exit_code
    finally:
        try:
            if session.pod_id and not session.success:
                signal.signal(signal.SIGINT, signal.SIG_IGN)
                _cleanup(cfg, client, session, log, started)
        finally:
            signal.signal(signal.SIGINT, previous)


def list_pods(client: RunPodClient, log: Redactor) -> int:
    found = False
    for pod in client.get_pods():
        if not is_managed(pod) or not is_live(pod):
            continue
        found = True
        env = parse_env(pod.get("env"))
        log.stdout(
            " ".join(
                [
                    f"pod_id={pod.get('id')}",
                    f"name={env.get(NAME, pod.get('name', ''))}",
                    f"state={pod.get('desiredStatus') or 'UNKNOWN'}",
                    f"hourly_price={_price_text(hourly_price(pod))}",
                ]
            )
        )
    if not found:
        log.stderr("no pods")
    return 0


def list_pods_json(
    client: RunPodClient, log: Redactor, health_path: str = "/health"
) -> int:
    """Print managed live pods as JSON, each with a quick health check.

    `healthy` is true only when the health URL answers 200. The Mac app uses it to
    tell "starting" from "ready".
    """
    rows: list[dict] = []
    for pod in client.get_pods():
        if not is_managed(pod) or not is_live(pod):
            continue
        env = parse_env(pod.get("env"))
        try:
            port = int(env.get(PORT, "") or 0)
        except ValueError:
            port = 0
        pod_id = str(pod.get("id") or "")
        rows.append(
            {
                "name": _name_of(pod),
                "pod_id": pod_id,
                "state": pod.get("desiredStatus") or "UNKNOWN",
                "hourly_price": hourly_price(pod),
                "port": port,
                "url": proxy_url(pod_id, port) if pod_id and port else "",
                "healthy": False,
                "tunnel_url": "",
                "_hash": env.get(CONFIG_HASH, ""),
            }
        )

    def check(row: dict) -> bool:
        if row["state"] != "RUNNING" or not row["pod_id"] or not row["port"]:
            return False
        return client.health_status(row["pod_id"], row["port"], health_path, 4.0) == 200

    if rows:
        with ThreadPoolExecutor(max_workers=min(8, len(rows))) as pool:
            for row, ok in zip(rows, pool.map(check, rows)):
                row["healthy"] = ok
            for row, found in zip(
                rows, pool.map(lambda r: _row_tunnel_url(client, r), rows)
            ):
                row["tunnel_url"] = found
    for row in rows:
        row.pop("_hash", None)
    log.stdout(json.dumps({"pods": rows}, ensure_ascii=False))
    return 0


def show_status(client: RunPodClient, name: str, log: Redactor) -> int:
    matches = [
        pod
        for pod in client.get_pods()
        if is_managed(pod) and is_live(pod) and _name_of(pod) == name
    ]
    if not matches:
        log.stderr(f"error: no pod named {name}")
        return 1
    now = time.time()
    for pod in matches:
        env = parse_env(pod.get("env"))
        remaining = remaining_ttl_sec(pod, now)
        ttl = env.get(TTL_SEC, "")
        remaining_text = "" if remaining is None else str(remaining)
        log.stdout(
            " ".join(
                [
                    f"pod_id={pod.get('id')}",
                    f"name={_name_of(pod)}",
                    f"state={pod.get('desiredStatus') or 'UNKNOWN'}",
                    f"hourly_price={_price_text(hourly_price(pod))}",
                    f"ttl_sec={ttl}",
                    f"remaining_ttl_sec={remaining_text}",
                ]
            )
        )
    return 0


def shut_down(
    client: RunPodClient, *, name: str | None, all_pods: bool, log: Redactor
) -> int:
    if is_protected(name):
        log.stderr(f"error: {name} is protected ({PROTECTED_ENV})")
        return 2
    try:
        resolve_tunnel()  # rejects a bad tunnel setting before touching pods
    except ConfigError as exc:
        log.stderr(f"error: {exc}")
        return 2
    pods = [
        pod
        for pod in client.get_pods()
        if is_managed(pod) and is_live(pod) and not is_protected(_name_of(pod))
    ]
    if not all_pods:
        pods = [pod for pod in pods if _name_of(pod) == name]
    if not pods:
        log.stderr("error: no matching pod")
        return 1
    failed = False
    for pod in pods:
        pod_id = str(pod["id"])
        try:
            client.terminate(pod_id)
        except Exception as exc:  # noqa: BLE001 — report the failure and keep terminating the rest
            failed = True
            log.stderr(f"error: failed to terminate pod {pod_id}: {exc}")
            continue
        log.stdout(f"terminated pod_id={pod_id} name={_name_of(pod)}")
    return 1 if failed else 0


def _allocate(
    cfg: Config,
    client: RunPodClient,
    log: Redactor,
    started: float,
    tunnel: Tunnel,
    *,
    retry: bool = True,
) -> tuple[str, str, float]:
    """Try every GPU candidate. With `retry`, repeat the round until a seat is
    found or `timeouts.allocation_sec` runs out."""
    deadline = time.monotonic() + cfg.allocation_timeout_sec
    round_no = 1
    while True:
        found, timed_out = _allocate_round(cfg, client, log, started, tunnel, deadline)
        if found is not None:
            return found
        remaining = deadline - time.monotonic()
        if timed_out or remaining <= 0:
            if round_no > 1:
                raise AllocationError(
                    f"no GPU seat within allocation_sec={cfg.allocation_timeout_sec:g}"
                    f" ({round_no} rounds)"
                )
            raise AllocationError("allocation timed out" if timed_out else _NO_SEAT)
        if not retry:
            raise AllocationError(_NO_SEAT)
        pause = min(cfg.allocation_retry_sec, remaining)
        _stage(
            log,
            "wait",
            started,
            round=round_no,
            retry_in_sec=format(pause, ".0f"),
            remaining_sec=format(remaining, ".0f"),
            reason="no-seat",
        )
        time.sleep(pause)
        round_no += 1


_NO_SEAT = "no GPU candidate could be allocated"


def _allocate_round(
    cfg: Config,
    client: RunPodClient,
    log: Redactor,
    started: float,
    tunnel: Tunnel,
    deadline: float,
) -> tuple[tuple[str, str, float] | None, bool]:
    """One pass over the candidates. Returns (result, timed_out)."""
    for gpu in cfg.candidates:
        if time.monotonic() > deadline:
            return None, True
        try:
            price = client.hourly_price(gpu, cfg.cloud, cfg.gpu_count)
        except Exception as exc:
            if is_stock_or_permanent(exc):
                _stage(log, "skip", started, gpu=gpu, reason="unavailable")
                continue
            raise
        if time.monotonic() > deadline:
            return None, True
        if price is None or not isinstance(price, (int, float)):
            _stage(
                log,
                "skip",
                started,
                gpu=gpu,
                hourly_price="",
                reason="price",
            )
            continue
        try:
            created = client.create_pod(
                name=cfg.name,
                image=cfg.image,
                gpu_type_id=gpu,
                cloud=cfg.cloud,
                gpu_count=cfg.gpu_count,
                container_disk_gb=cfg.container_disk_gb,
                ports=f"{cfg.port}/http",
                docker_args=_docker_args(cfg, tunnel),
                env=_pod_env(cfg, gpu, price, tunnel),
                network_volume_id=cfg.network_volume_id,
            )
        except Exception as exc:
            if is_stock_or_permanent(exc):
                _stage(
                    log,
                    "skip",
                    started,
                    gpu=gpu,
                    hourly_price=_price_text(price),
                    reason="stock",
                )
                continue
            raise
        pod_id = str(created.get("id") or "")
        if not pod_id:
            _stage(log, "skip", started, gpu=gpu, reason="no-id")
            continue
        _stage(
            log,
            "created",
            started,
            pod_id=pod_id,
            gpu=gpu,
            hourly_price=_price_text(price),
        )
        return (pod_id, gpu, price), False
    return None, False


def _active_hash(cfg: Config, tunnel: Tunnel) -> str:
    """Separate pods by tunnel mode so switching modes makes a new pod.

    Keys stay out of the hash, so rotating a key still reuses the pod.
    """
    return f"{cfg.config_hash}{tunnel.hash_suffix}"


def _docker_args(cfg: Config, tunnel: Tunnel) -> str:
    args = tunnel_docker_args(
        tunnel,
        hostname=cfg.name,
        port=cfg.port,
        user_args=cfg.args,
        ttl_seconds=cfg.ttl_sec,
        ttl_text=cfg.ttl_text,
        image=cfg.image,
    )
    if args is not None:
        return args
    return render_start_command(cfg.args, cfg.ttl_sec, cfg.ttl_text)


def _pod_env(cfg: Config, gpu: str, price: float, tunnel: Tunnel) -> dict[str, str]:
    env = dict(cfg.env)
    env.update(tunnel_env(tunnel))
    # Optional: lets gated Hugging Face models download. The YAML value wins.
    hf_token = os.environ.get("HF_TOKEN", "").strip()
    if hf_token and "HF_TOKEN" not in env:
        env["HF_TOKEN"] = hf_token
    # Optional: require this key on the model API. llama.cpp reads
    # LLAMA_ARG_API_KEY and vLLM reads VLLM_API_KEY; /health stays open.
    model_key = os.environ.get("MODEL_API_KEY", "").strip()
    if model_key:
        env.setdefault("LLAMA_ARG_API_KEY", model_key)
        env.setdefault("VLLM_API_KEY", model_key)
    env.update(
        {
            MANAGED: "1",
            NAME: cfg.name,
            CONFIG_HASH: _active_hash(cfg, tunnel),
            TTL_SEC: str(cfg.ttl_sec),
            CREATED_AT: str(int(time.time())),
            PORT: str(cfg.port),
            GPU: gpu,
            PRICE: _price_text(price),
        }
    )
    return env


def _find_match(client: RunPodClient, cfg: Config, tunnel: Tunnel) -> dict | None:
    for pod in client.get_pods():
        if same_identity(pod, cfg.name, _active_hash(cfg, tunnel)):
            return pod
    return None


def _wait_running(
    cfg: Config, client: RunPodClient, session: _Session, log: Redactor, started: float
) -> None:
    assert session.pod_id is not None
    deadline = time.monotonic() + cfg.boot_timeout_sec

    def running() -> bool:
        pod = client.get_pod(session.pod_id or "")
        if pod is None:
            raise PodError(f"pod {session.pod_id} disappeared during boot")
        status = str(pod.get("desiredStatus") or "").upper()
        if status == "RUNNING":
            return True
        if status in {"EXITED", "TERMINATED", "DEAD", "FAILED"}:
            raise PodError(f"pod {session.pod_id} entered {status} during boot")
        return False

    _poll(deadline, cfg.boot_interval_sec, running, BootTimeout("boot timed out"))
    _stage(
        log,
        "running",
        started,
        pod_id=session.pod_id,
        gpu=session.gpu,
        hourly_price=_price_text(session.price),
    )


def _wait_healthy(
    cfg: Config, client: RunPodClient, session: _Session, log: Redactor, started: float
) -> None:
    assert session.pod_id is not None
    deadline = time.monotonic() + cfg.readiness_timeout_sec
    request_timeout = min(5.0, cfg.readiness_interval_sec)

    def healthy() -> bool:
        status = client.health_status(
            session.pod_id or "",
            cfg.port,
            cfg.readiness_path,
            request_timeout,
        )
        return status == 200

    _poll(
        deadline,
        cfg.readiness_interval_sec,
        healthy,
        ReadinessTimeout("readiness timed out"),
    )
    _stage(
        log,
        "ready",
        started,
        pod_id=session.pod_id,
        gpu=session.gpu,
        hourly_price=_price_text(session.price),
        url=proxy_url(session.pod_id, cfg.port),
    )


def _poll(deadline: float, interval: float, probe, timeout_error: Exception) -> None:
    while True:
        if probe():
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise timeout_error
        time.sleep(min(interval, remaining))


def _cleanup(
    cfg: Config, client: RunPodClient, session: _Session, log: Redactor, started: float
) -> None:
    pod_id = session.pod_id
    if not pod_id:
        return
    try:
        text = client.pod_logs(pod_id)
    except Exception as exc:  # noqa: BLE001 — log fetch must not skip terminate
        log.stderr(f"error: pod logs unavailable: {exc}")
        text = ""
    if text:
        lines = text.splitlines()[-50:]
        log.stderr("pod_log_begin")
        for line in lines:
            log.stderr(line)
        log.stderr("pod_log_end")
    try:
        client.terminate(pod_id)
    except Exception as exc:  # noqa: BLE001 — surface the terminate error after the pod id exists
        log.stderr(f"error: failed to terminate pod {pod_id}: {exc}")
        return
    _stage(
        log,
        "terminated",
        started,
        pod_id=pod_id,
        gpu=session.gpu,
        hourly_price=_price_text(session.price),
    )


def _print_endpoint(
    log: Redactor,
    pod_id: str,
    url: str,
    cfg: Config,
    price: float,
    tunnel: Tunnel,
    *,
    client: RunPodClient | None = None,
) -> None:
    log.stdout(f"pod_id={pod_id}")
    log.stdout(url)
    log.stdout(f"hourly_price={_price_text(price)}")
    log.stdout(f"tunnel={tunnel.label}")
    if tunnel.mode == "tailscale":
        log.stdout(f"funnel_hostname={cfg.name}")
    elif tunnel.mode == "cloudflare" and tunnel.cloudflare_named:
        host = cloudflare_hostname()
        if host:
            log.stdout(f"tunnel_url=https://{host}")
    elif tunnel.mode == "cloudflare":
        found = _quick_tunnel_url(client, pod_id) if client is not None else ""
        if found:
            log.stdout(f"tunnel_url={found}")
        else:
            log.stderr(
                "note: quick tunnel address is printed in the pod log as podrouter-tunnel-url="
            )


def _row_tunnel_url(client: RunPodClient, row: dict) -> str:
    """Public tunnel address for one listed pod, from the mode stored in its hash."""
    tag = str(row.get("_hash") or "")
    if tag.endswith("-cloudflare-quick"):
        if not row.get("healthy"):
            return ""
        try:
            return find_quick_url(client.pod_logs(row["pod_id"]))
        except Exception:  # noqa: BLE001 — the address is a convenience, not a failure
            return ""
    if tag.endswith("-cloudflare"):
        host = cloudflare_hostname()
        return f"https://{host}" if host else ""
    return ""


def _quick_tunnel_url(client: RunPodClient, pod_id: str) -> str:
    """Read the random trycloudflare.com address from the pod log. Empty if not there yet."""
    for attempt in range(4):
        try:
            found = find_quick_url(client.pod_logs(pod_id))
        except Exception:  # noqa: BLE001 — the address is a convenience, not a failure
            found = ""
        if found:
            return found
        if attempt < 3:
            time.sleep(3)
    return ""


def _stage(log: Redactor, name: str, started: float, **fields: object) -> None:
    parts = [
        f"stage={name}",
        f"elapsed_sec={time.monotonic() - started:.3f}",
    ]
    for key, value in fields.items():
        if value is None or value == "":
            continue
        parts.append(f"{key}={value}")
    log.stderr(" ".join(parts))


def _price_text(price: float) -> str:
    return format(price, "g")


def _name_of(pod: dict) -> str:
    env = parse_env(pod.get("env"))
    return str(env.get(NAME) or pod.get("name") or "")
