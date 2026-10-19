"""Drive the installed gpu-up console script against a local RunPod/health stub."""

from __future__ import annotations

import hashlib
import os
import re
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import yaml

from tests.stub_server import StubServer

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(sys.prefix) / "bin" / "gpu-up"
HF = "hf-secret-ZZZ-not-in-logs"
VLLM = "vllm-secret-QQQ-not-in-logs"
API = "rp-secret-YYY-not-in-logs"
SECRETS = (HF, VLLM, API)
URL_RE = re.compile(r"https://([A-Za-z0-9]+)-(\d+)\.proxy\.runpod\.net")


def test_help_lists_commands() -> None:
    proc = subprocess.run(
        [str(BIN), "--help"], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0
    for name in ("up", "down", "ls", "status"):
        assert name in proc.stdout


def test_up_prints_proxy_url_only_after_health(tmp_path: Path) -> None:
    with StubServer() as stub:
        stub.block_health = True
        cfg = write_config(
            tmp_path,
            lambda data: data["readiness"].update(
                {"interval_sec": 5, "timeout_sec": 20}
            ),
        )
        before = cfg.read_bytes()
        proc = subprocess.Popen(
            [str(BIN), *up_cmd(cfg)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=make_env(stub),
            start_new_session=True,
        )
        assert proc.stdout is not None
        assert stub.health_entered.wait(15), "health check was not requested"
        os.set_blocking(proc.stdout.fileno(), False)
        early = _read_for(proc.stdout, 0.4)
        assert "proxy.runpod.net" not in early
        assert proc.poll() is None
        os.set_blocking(proc.stdout.fileno(), True)
        stub.health_gate.set()
        out, err = _finish(proc, timeout=20)
        out = early + out
        text = out + err
        assert proc.returncode == 0, text
        url, pod_id, port = _one_url(out)
        assert port == "8000"
        assert url == f"https://{pod_id}-8000.proxy.runpod.net"
        assert f"pod_id={pod_id}" in out
        assert stub.health_hits
        assert stub.health_hits[0].endswith(f"/{pod_id}-8000/health")
        accepted = stub.accepted_creates()
        assert len(accepted) == 1
        assert accepted[0]["pod_id"] == pod_id
        assert stub.terminates == []
        assert cfg.read_bytes() == before
        _assert_stage_log(err, pod_id)
        _assert_no_secrets(text)
        _assert_payload(accepted[0])


def test_wait_false_returns_before_health(tmp_path: Path) -> None:
    with StubServer() as stub:
        stub.hold_status = "CREATED"
        stub.health_code = 503
        cfg = write_config(
            tmp_path,
            lambda data: data["timeouts"].update({"boot_sec": 30}),
        )
        proc = run(stub, [*up_cmd(cfg), "--wait=false"], timeout=10)
        assert proc.returncode == 0, proc.stderr
        _url, pod_id, port = _one_url(proc.stdout)
        assert port == "8000"
        assert f"pod_id={pod_id}" in proc.stdout
        assert stub.health_hits == []
        assert "get_pod" not in stub.request_ops()
        assert stub.terminates == []
        assert len(stub.accepted_creates()) == 1
        _assert_no_secrets(proc.stdout + proc.stderr)


def test_over_cap_gpu_is_not_requested(tmp_path: Path) -> None:
    with StubServer() as stub:
        cfg = write_config(tmp_path)
        proc = run(stub, up_cmd(cfg))
        assert proc.returncode == 0, proc.stderr
        attempted = [item["gpu_type_id"] for item in stub.all_creates()]
        assert attempted == ["NVIDIA RTX A6000"]
        assert "NVIDIA GeForce RTX 4090" not in attempted


def test_price_uses_configured_cloud_and_allows_equal_cap(tmp_path: Path) -> None:
    with StubServer() as stub:
        cfg = write_config(
            tmp_path,
            lambda data: data["gpu"].update(
                {"cloud": "COMMUNITY", "max_price_per_hour": 0.3}
            ),
        )
        proc = run(stub, [*up_cmd(cfg), "--wait=false"])
        assert proc.returncode == 0, proc.stderr
        created = stub.accepted_creates()
        assert len(created) == 1
        assert created[0]["gpu_type_id"] == "NVIDIA RTX A6000"
        assert created[0]["cloud_type"] == "COMMUNITY"

    with StubServer() as stub:
        cfg = write_config(
            tmp_path,
            lambda data: data["gpu"].update({"max_price_per_hour": 0.5}),
        )
        proc = run(stub, [*up_cmd(cfg, name="equal.yaml"), "--wait=false"])
        assert proc.returncode == 0, proc.stderr
        assert stub.accepted_creates()[0]["gpu_type_id"] == "NVIDIA RTX A6000"


def test_stock_miss_tries_next_candidate(tmp_path: Path) -> None:
    with StubServer() as stub:
        stub.gpus["NVIDIA GeForce RTX 4090"]["securePrice"] = 0.5
        stub.gpus["NVIDIA GeForce RTX 4090"]["stock"] = False
        cfg = write_config(tmp_path)
        proc = run(stub, up_cmd(cfg))
        assert proc.returncode == 0, proc.stderr
        attempted = stub.all_creates()
        assert [item["gpu_type_id"] for item in attempted] == [
            "NVIDIA GeForce RTX 4090",
            "NVIDIA RTX A6000",
        ]
        assert attempted[0]["accepted"] is False
        assert attempted[1]["accepted"] is True
        assert stub.live_managed_ids() == [attempted[1]["pod_id"]]


def test_all_candidates_fail_and_leave_no_pod(tmp_path: Path) -> None:
    with StubServer() as stub:
        stub.gpus["NVIDIA GeForce RTX 4090"]["stock"] = False
        stub.gpus["NVIDIA RTX A6000"]["stock"] = False
        cfg = write_config(tmp_path)
        proc = run(stub, up_cmd(cfg))
        assert proc.returncode != 0
        assert stub.live_managed_ids() == []
        assert "proxy.runpod.net" not in proc.stdout
        assert stub.unrelated_id not in stub.terminates

    with StubServer() as stub:
        cfg = write_config(
            tmp_path,
            lambda data: data["gpu"].update({"max_price_per_hour": 0.1}),
        )
        proc = run(stub, [*up_cmd(cfg, name="cap.yaml"), "--wait=false"])
        assert proc.returncode != 0
        assert stub.all_creates() == []
        assert stub.live_managed_ids() == []


def test_missing_or_bad_ttl_creates_nothing(tmp_path: Path) -> None:
    cases = [
        lambda data: data["lifecycle"].pop("ttl"),
        lambda data: data["lifecycle"].update({"ttl": "3d"}),
        lambda data: data["lifecycle"].update({"ttl": 3}),
        lambda data: data["lifecycle"].update({"ttl": "3"}),
    ]
    for index, mutate in enumerate(cases):
        with StubServer() as stub:
            cfg = write_config(tmp_path, mutate, name=f"ttl-{index}.yaml")
            proc = run(stub, up_cmd(cfg))
            assert proc.returncode != 0
            assert stub.requests == []
            assert stub.all_creates() == []


def test_thirty_minutes_and_combined_ttl_are_accepted(tmp_path: Path) -> None:
    with StubServer() as stub:
        cfg = write_config(
            tmp_path, lambda data: data["lifecycle"].update({"ttl": "30m"})
        )
        proc = run(stub, [*up_cmd(cfg), "--wait=false"])
        assert proc.returncode == 0, proc.stderr
        args = stub.accepted_creates()[0]["docker_args"]
        assert "sleep 1800" in args
        assert "ttl=30m" in args

    with StubServer() as stub:
        cfg = write_config(
            tmp_path,
            lambda data: data["lifecycle"].update({"ttl": "1h30m"}),
            name="combined.yaml",
        )
        proc = run(stub, [*up_cmd(cfg), "--wait=false"])
        assert proc.returncode == 0, proc.stderr
        args = stub.accepted_creates()[0]["docker_args"]
        assert "sleep 5400" in args
        assert "ttl=1h30m" in args


def test_boot_timeout_terminates_without_waiting_for_health(tmp_path: Path) -> None:
    with StubServer() as stub:
        stub.hold_status = "CREATED"
        stub.logs = f"warmup still loading tokenizer\ntoken={HF}\n"
        cfg = write_config(
            tmp_path,
            lambda data: data["timeouts"].update(
                {"boot_sec": 1, "boot_interval_sec": 0.1}
            ),
        )
        cfg_text = cfg.read_text(encoding="utf-8")
        proc = run(stub, up_cmd(cfg), timeout=15)
        assert proc.returncode != 0
        assert "proxy.runpod.net" not in proc.stdout
        assert stub.health_hits == []
        assert stub.terminates
        assert stub.live_managed_ids() == []
        assert "warmup still loading tokenizer" in proc.stderr
        assert "readiness.timeout_sec: 5" in cfg_text or "timeout_sec: 5" in cfg_text
        _assert_no_secrets(proc.stdout + proc.stderr)


def test_health_timeout_terminates(tmp_path: Path) -> None:
    with StubServer() as stub:
        stub.health_code = 503
        stub.logs = f"warmup still loading tokenizer\ntoken={HF}\n"
        cfg = write_config(
            tmp_path,
            lambda data: data["readiness"].update(
                {"interval_sec": 0.1, "timeout_sec": 1}
            ),
        )
        proc = run(stub, up_cmd(cfg), timeout=15)
        assert proc.returncode != 0
        assert "proxy.runpod.net" not in proc.stdout
        assert stub.health_hits
        assert stub.terminates
        assert stub.live_managed_ids() == []
        assert "warmup still loading tokenizer" in proc.stderr
        _assert_no_secrets(proc.stdout + proc.stderr)


def test_client_exception_terminates_and_prints_logs(tmp_path: Path) -> None:
    with StubServer() as stub:
        stub.null_get_pod = True
        stub.logs = f"warmup still loading tokenizer\ntoken={VLLM}\n"
        cfg = write_config(tmp_path)
        proc = run(stub, up_cmd(cfg))
        assert proc.returncode != 0
        assert stub.accepted_creates()
        assert stub.terminates
        assert stub.live_managed_ids() == []
        assert "warmup still loading tokenizer" in proc.stderr
        _assert_no_secrets(proc.stdout + proc.stderr)


def test_sigint_terminates_the_pod(tmp_path: Path) -> None:
    with StubServer() as stub:
        stub.hold_status = "CREATED"
        cfg = write_config(
            tmp_path,
            lambda data: data["timeouts"].update(
                {"boot_sec": 30, "boot_interval_sec": 0.2}
            ),
        )
        proc = subprocess.Popen(
            [str(BIN), *up_cmd(cfg)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=make_env(stub),
            start_new_session=True,
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            if "get_pod" in stub.request_ops():
                break
            if proc.poll() is not None:
                out, err = proc.communicate()
                raise AssertionError(f"exited early {proc.returncode}\n{out}\n{err}")
            time.sleep(0.05)
        else:
            proc.kill()
            raise AssertionError("pod status was never polled")
        os.kill(proc.pid, signal.SIGINT)
        out, err = _finish(proc, timeout=15)
        assert proc.returncode != 0, out + err
        assert stub.terminates
        assert stub.live_managed_ids() == []
        _assert_no_secrets(out + err)


def test_start_command_arms_in_pod_runpodctl(tmp_path: Path) -> None:
    with StubServer() as stub:
        cfg = write_config(tmp_path)
        proc = run(stub, [*up_cmd(cfg), "--wait=false"])
        assert proc.returncode == 0, proc.stderr
        command = stub.accepted_creates()[0]["docker_args"]
        assert "runpodctl" in command
        assert "sleep 10800" in command
        assert "ttl=3h" in command
        assert "$RUNPOD_POD_ID" in command
        assert "--model Qwen/Qwen2.5-7B-Instruct" in command
        assert "127.0.0.1" not in command
        assert VLLM in command
        assert VLLM not in proc.stdout + proc.stderr


def test_same_config_reuses_ready_pod_and_different_config_does_not(
    tmp_path: Path,
) -> None:
    with StubServer() as stub:
        cfg = write_config(tmp_path)
        first = run(stub, up_cmd(cfg))
        second = run(stub, up_cmd(cfg))
        assert first.returncode == 0, first.stderr
        assert second.returncode == 0, second.stderr
        url1, pod1, _ = _one_url(first.stdout)
        url2, pod2, _ = _one_url(second.stdout)
        assert url1 == url2
        assert pod1 == pod2
        assert len(stub.accepted_creates()) == 1

        other = write_config(
            tmp_path,
            lambda data: data.update({"image": "vllm/vllm-openai:other"}),
            name="other.yaml",
        )
        third = run(stub, up_cmd(other))
        assert third.returncode == 0, third.stderr
        url3, pod3, _ = _one_url(third.stdout)
        assert pod3 != pod1
        assert url3 != url1
        assert len(stub.accepted_creates()) == 2


def test_ls_status_down_and_down_all(tmp_path: Path) -> None:
    with StubServer() as stub:
        cfg = write_config(tmp_path)
        first = run(stub, up_cmd(cfg))
        assert first.returncode == 0, first.stderr
        _, pod_id, _ = _one_url(first.stdout)

        status = run(
            stub, ["status", "qwen-7b-serve", "--api-key", "${GPU_UP_TEST_KEY}"]
        )
        assert status.returncode == 0, status.stderr
        assert "state=RUNNING" in status.stdout
        assert "hourly_price=0.5" in status.stdout
        remaining = int(_field(status.stdout, "remaining_ttl_sec"))
        ttl = int(_field(status.stdout, "ttl_sec"))
        assert ttl == 10800
        assert 0 <= remaining <= ttl
        assert pod_id in status.stdout

        listed = run(stub, ["ls", "--api-key", "${GPU_UP_TEST_KEY}"])
        assert listed.returncode == 0, listed.stderr
        assert pod_id in listed.stdout
        assert stub.unrelated_id not in listed.stdout

        down = run(stub, ["down", "qwen-7b-serve", "--api-key", "${GPU_UP_TEST_KEY}"])
        assert down.returncode == 0, down.stderr
        assert pod_id in stub.terminates
        assert stub.live_managed_ids() == []
        assert stub.unrelated_id not in stub.terminates

        again = run(stub, up_cmd(cfg))
        reused = run(stub, up_cmd(cfg))
        assert again.returncode == 0, again.stderr
        assert reused.returncode == 0, reused.stderr
        _, new_id, _ = _one_url(again.stdout)
        _, reused_id, _ = _one_url(reused.stdout)
        assert new_id != pod_id
        assert reused_id == new_id
        assert len(stub.accepted_creates()) == 2

        down_all = run(stub, ["down", "--all", "--api-key", "${GPU_UP_TEST_KEY}"])
        assert down_all.returncode == 0, down_all.stderr
        assert new_id in stub.terminates
        assert stub.live_managed_ids() == []
        assert stub.pods[stub.unrelated_id]["desiredStatus"] == "RUNNING"
        _assert_no_secrets(
            status.stdout + listed.stdout + down.stdout + down_all.stdout
        )


def test_default_health_path_and_network_volume(tmp_path: Path) -> None:
    with StubServer() as stub:
        cfg = write_config(tmp_path, lambda data: data.pop("readiness"))
        proc = run(stub, up_cmd(cfg))
        assert proc.returncode == 0, proc.stderr
        _, pod_id, _ = _one_url(proc.stdout)
        assert stub.health_hits[0].endswith(f"/{pod_id}-8000/health")

    with StubServer() as stub:
        cfg = write_config(
            tmp_path,
            lambda data: data["storage"].update({"network_volume_id": "vol-1"}),
            name="volume.yaml",
        )
        proc = run(stub, [*up_cmd(cfg), "--wait=false"])
        assert proc.returncode == 0, proc.stderr
        assert stub.accepted_creates()[0]["network_volume_id"] == "vol-1"
        assert "user" in stub.request_ops()


def test_missing_api_key_or_empty_secret_creates_nothing(tmp_path: Path) -> None:
    with StubServer() as stub:
        cfg = write_config(tmp_path)
        proc = run(stub, ["up", str(cfg)])
        assert proc.returncode != 0
        assert stub.requests == []

    with StubServer() as stub:
        cfg = write_config(tmp_path, name="empty-key.yaml")
        proc = run(
            stub,
            ["up", str(cfg), "--api-key", "${UNSET_GPU_UP_KEY}"],
            extra_env={"UNSET_GPU_UP_KEY": ""},
        )
        assert proc.returncode != 0
        assert stub.requests == []

    with StubServer() as stub:
        cfg = write_config(tmp_path, name="empty-hf.yaml")
        proc = run(stub, up_cmd(cfg), extra_env={"HF_TOKEN": ""})
        assert proc.returncode != 0
        assert stub.requests == []
        assert HF not in proc.stdout + proc.stderr


def test_transient_5xx_is_retried_with_increasing_delay(tmp_path: Path) -> None:
    with StubServer() as stub:
        stub.transient_remaining = 2
        cfg = write_config(tmp_path)
        proc = run(
            stub,
            [*up_cmd(cfg), "--wait=false"],
            extra_env={"GPU_UP_RETRY_BASE_SEC": "0.4"},
            timeout=20,
        )
        assert proc.returncode == 0, proc.stderr
        graphql = [item for item in stub.requests if item["status"] in {200, 500}]
        assert [item["status"] for item in graphql[:3]] == [500, 500, 200]
        first = graphql[1]["at"] - graphql[0]["at"]
        second = graphql[2]["at"] - graphql[1]["at"]
        assert first >= 0.25
        assert second >= 0.6
        assert second > first
        assert len(stub.accepted_creates()) == 1


def test_allocation_timeout_is_independent_of_boot_and_warmup(tmp_path: Path) -> None:
    with StubServer() as stub:
        stub.gpu_delay = 0.8
        cfg = write_config(
            tmp_path,
            lambda data: (
                data["gpu"].update({"candidates": ["NVIDIA RTX A6000"]}),
                data["timeouts"].update({"allocation_sec": 0.3, "boot_sec": 30}),
                data["readiness"].update({"timeout_sec": 30}),
            )[-1],
        )
        proc = run(stub, [*up_cmd(cfg), "--wait=false"], timeout=15)
        assert proc.returncode != 0
        assert stub.all_creates() == []
        assert "allocation timed out" in proc.stderr


def write_config(directory: Path, mutate=None, name: str = "serve.yaml") -> Path:
    data = {
        "name": "qwen-7b-serve",
        "image": "vllm/vllm-openai:latest",
        "args": "--model Qwen/Qwen2.5-7B-Instruct --port 8000 --api-key ${VLLM_API_KEY}",
        "gpu": {
            "candidates": ["NVIDIA GeForce RTX 4090", "NVIDIA RTX A6000"],
            "count": 1,
            "max_price_per_hour": 0.8,
            "cloud": "SECURE",
        },
        "storage": {"container_disk_gb": 50, "network_volume_id": None},
        "env": {"HF_TOKEN": "${HF_TOKEN}"},
        "port": 8000,
        "readiness": {"path": "/health", "interval_sec": 0.2, "timeout_sec": 5},
        "lifecycle": {"ttl": "3h", "idle_timeout": "30m"},
        "timeouts": {"allocation_sec": 30, "boot_sec": 5, "boot_interval_sec": 0.2},
    }
    if mutate is not None:
        mutate(data)
    path = directory / name
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def up_cmd(cfg: Path, name: str | None = None) -> list[str]:
    del name
    return ["up", str(cfg), "--api-key", "${GPU_UP_TEST_KEY}"]


def make_env(stub: StubServer, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("RUNPOD_API_KEY", None)
    env.update(
        {
            "RUNPOD_API_BASE_URL": stub.base,
            "GPU_UP_HEALTH_BASE": stub.base + "/proxy",
            "GPU_UP_RETRY_BASE_SEC": "0.05",
            "HF_TOKEN": HF,
            "VLLM_API_KEY": VLLM,
            "GPU_UP_TEST_KEY": API,
        }
    )
    if extra:
        env.update(extra)
    return env


def run(
    stub: StubServer,
    args: list[str],
    extra_env: dict[str, str] | None = None,
    timeout: float = 20,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BIN), *args],
        capture_output=True,
        text=True,
        env=make_env(stub, extra_env),
        timeout=timeout,
        check=False,
    )


def _finish(proc: subprocess.Popen, timeout: float) -> tuple[str, str]:
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise AssertionError(f"timed out\n{out}\n{err}")
    if isinstance(out, bytes):
        out = out.decode()
    if isinstance(err, bytes):
        err = err.decode()
    return out, err


def _read_for(stream, seconds: float) -> str:
    chunks: list[str] = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        ready, _, _ = select.select([stream], [], [], 0.1)
        if not ready:
            continue
        try:
            data = os.read(stream.fileno(), 65536)
        except BlockingIOError:
            continue
        if not data:
            break
        chunks.append(data.decode())
    return "".join(chunks)


def _one_url(text: str) -> tuple[str, str, str]:
    found = URL_RE.findall(text)
    assert found, text
    pod_id, port = found[0]
    return f"https://{pod_id}-{port}.proxy.runpod.net", pod_id, port


def _field(text: str, name: str) -> str:
    match = re.search(rf"{name}=(\S+)", text)
    assert match is not None, text
    return match.group(1)


def _assert_no_secrets(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text


def _assert_stage_log(err: str, pod_id: str) -> None:
    assert pod_id in err
    assert "NVIDIA RTX A6000" in err
    assert "elapsed_sec=" in err
    assert "hourly_price=0.5" in err


def _assert_payload(created: dict) -> None:
    assert created["image_name"] == "vllm/vllm-openai:latest"
    assert created["gpu_count"] == 1
    assert created["disk"] == 50
    assert created["cloud_type"] == "SECURE"
    assert created["ports"] == "8000/http"
    assert created["env"]["HF_TOKEN"] == HF
    assert created["env"]["GPU_UP_MANAGED"] == "1"
    assert created["env"]["GPU_UP_NAME"] == "qwen-7b-serve"
    assert created["env"]["GPU_UP_TTL_SEC"] == "10800"
    command = created["docker_args"]
    assert "runpodctl" in command
    assert "sleep 10800" in command
    assert "ttl=3h" in command
    assert "$RUNPOD_POD_ID" in command
    assert "--model Qwen/Qwen2.5-7B-Instruct" in command
    assert "${VLLM_API_KEY}" not in command
    assert "${HF_TOKEN}" not in command


def test_bin_exists() -> None:
    assert BIN.is_file(), f"installed console script missing at {BIN}"
    digest = hashlib.sha256(BIN.read_bytes()).hexdigest()
    assert digest
