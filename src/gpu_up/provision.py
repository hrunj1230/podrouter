"""Create one pod, wait until it is healthy, and terminate it on every failure."""

from __future__ import annotations

import signal
import time

from gpu_up.client import RunPodClient, is_stock_or_permanent
from gpu_up.config import Config
from gpu_up.errors import AllocationError, BootTimeout, PodError, ReadinessTimeout
from gpu_up.pods import (
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
from gpu_up.reaper import render_start_command
from gpu_up.redact import Redactor
from gpu_up.urls import proxy_url


class _Session:
    def __init__(self) -> None:
        self.pod_id: str | None = None
        self.gpu: str = ""
        self.price: float = 0.0
        self.success = False
        self.interrupted = False


def bring_up(cfg: Config, client: RunPodClient, *, wait: bool, log: Redactor) -> int:
    """Run `up`. A successful return does not terminate the pod."""
    started = time.monotonic()
    session = _Session()
    previous = signal.getsignal(signal.SIGINT)

    def on_sigint(signum: int, frame: object) -> None:
        session.interrupted = True
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, on_sigint)
    exit_code = 1
    try:
        existing = _find_match(client, cfg)
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
            pod_id, gpu, price = _allocate(cfg, client, log, started)
            session.pod_id = pod_id
            session.gpu = gpu
            session.price = price

        url = proxy_url(session.pod_id, cfg.port)
        if not wait:
            _print_endpoint(log, session.pod_id, url)
            session.success = True
            exit_code = 0
            return exit_code

        _wait_running(cfg, client, session, log, started)
        _wait_healthy(cfg, client, session, log, started)
        _print_endpoint(log, session.pod_id, url)
        session.success = True
        exit_code = 0
        return exit_code
    except KeyboardInterrupt:
        session.interrupted = True
        log.stderr("error: interrupted")
        exit_code = 130
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
    pods = [pod for pod in client.get_pods() if is_managed(pod) and is_live(pod)]
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
    cfg: Config, client: RunPodClient, log: Redactor, started: float
) -> tuple[str, str, float]:
    deadline = time.monotonic() + cfg.allocation_timeout_sec
    last_error = "no GPU candidate could be allocated"
    for gpu in cfg.candidates:
        if time.monotonic() > deadline:
            last_error = "allocation timed out"
            break
        try:
            price = client.hourly_price(gpu, cfg.cloud, cfg.gpu_count)
        except Exception as exc:
            if is_stock_or_permanent(exc):
                _stage(log, "skip", started, gpu=gpu, reason="unavailable")
                continue
            raise
        if time.monotonic() > deadline:
            last_error = "allocation timed out"
            break
        if price is None or price > cfg.max_price_per_hour:
            _stage(
                log,
                "skip",
                started,
                gpu=gpu,
                hourly_price="" if price is None else _price_text(price),
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
                docker_args=render_start_command(cfg.args, cfg.ttl_sec, cfg.ttl_text),
                env=_pod_env(cfg, gpu, price),
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
        return pod_id, gpu, price
    raise AllocationError(last_error)


def _pod_env(cfg: Config, gpu: str, price: float) -> dict[str, str]:
    env = dict(cfg.env)
    env.update(
        {
            MANAGED: "1",
            NAME: cfg.name,
            CONFIG_HASH: cfg.config_hash,
            TTL_SEC: str(cfg.ttl_sec),
            CREATED_AT: str(int(time.time())),
            PORT: str(cfg.port),
            GPU: gpu,
            PRICE: _price_text(price),
        }
    )
    return env


def _find_match(client: RunPodClient, cfg: Config) -> dict | None:
    for pod in client.get_pods():
        if same_identity(pod, cfg.name, cfg.config_hash):
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


def _print_endpoint(log: Redactor, pod_id: str, url: str) -> None:
    log.stdout(f"pod_id={pod_id}")
    log.stdout(url)


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
