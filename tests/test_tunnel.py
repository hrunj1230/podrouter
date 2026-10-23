"""Tunnel mode selection: none, Tailscale Funnel, Cloudflare named or quick tunnel."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from podrouter.errors import ConfigError
from podrouter.tunnel import (
    Tunnel,
    cloudflare_docker_args,
    find_quick_url,
    resolve_tunnel,
    tunnel_env,
)
from tests.test_acceptance import run, up_cmd, write_config
from tests.stub_server import StubServer

CF = "cf-tunnel-token-not-in-logs"
TS = "tskey-auth-test-not-in-logs"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ("PODROUTER_TUNNEL", "TS_AUTHKEY", "CF_TUNNEL_TOKEN", "CF_TUNNEL_HOSTNAME"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_empty_mode_keeps_old_rule(clean_env: pytest.MonkeyPatch) -> None:
    assert resolve_tunnel() == Tunnel("none")
    clean_env.setenv("TS_AUTHKEY", TS)
    assert resolve_tunnel() == Tunnel("tailscale")


def test_none_mode_ignores_saved_tailscale_key(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("TS_AUTHKEY", TS)
    clean_env.setenv("PODROUTER_TUNNEL", "none")
    tunnel = resolve_tunnel()
    assert tunnel == Tunnel("none")
    assert tunnel_env(tunnel) == {}


def test_cloudflare_named_only_with_token(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("PODROUTER_TUNNEL", "Cloudflare")
    assert resolve_tunnel() == Tunnel("cloudflare", cloudflare_named=False)
    clean_env.setenv("CF_TUNNEL_TOKEN", CF)
    clean_env.setenv("TS_AUTHKEY", TS)
    tunnel = resolve_tunnel()
    assert tunnel == Tunnel("cloudflare", cloudflare_named=True)
    assert tunnel_env(tunnel) == {"CF_TUNNEL_TOKEN": CF}


def test_bad_modes_are_rejected(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("PODROUTER_TUNNEL", "ngrok")
    with pytest.raises(ConfigError):
        resolve_tunnel()
    clean_env.setenv("PODROUTER_TUNNEL", "tailscale")
    with pytest.raises(ConfigError, match="TS_AUTHKEY"):
        resolve_tunnel()


def test_find_quick_url_reads_marker_line() -> None:
    logs = (
        "INF Requesting new quick Tunnel\n"
        "podrouter-tunnel-url=https://calm-river-12ab.trycloudflare.com\n"
        "llama server listening\n"
    )
    assert find_quick_url(logs) == "https://calm-river-12ab.trycloudflare.com"
    assert find_quick_url("https://x.trycloudflare.com without marker") == ""


@pytest.mark.parametrize("named", [True, False])
def test_cloudflare_script_is_valid_bash(named: bool) -> None:
    payload = json.loads(
        cloudflare_docker_args(
            port=8080,
            user_args="-hf a/b:Q4 --host 0.0.0.0 --port 8080",
            ttl_seconds=60,
            ttl_text="1m",
            image="ghcr.io/ggml-org/llama.cpp:server-cuda",
            named=named,
        )
    )
    script = payload["cmd"][0]
    check = subprocess.run(["bash", "-n", "-c", script], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    assert "exec /app/llama-server -hf a/b:Q4" in script


def test_up_cloudflare_named_passes_token_only_in_env(tmp_path: Path) -> None:
    with StubServer() as stub:
        cfg = write_config(tmp_path)
        proc = run(
            stub,
            [*up_cmd(cfg), "--wait=false"],
            extra_env={
                "PODROUTER_TUNNEL": "cloudflare",
                "CF_TUNNEL_TOKEN": CF,
                "CF_TUNNEL_HOSTNAME": "https://llm.example.com/",
                "TS_AUTHKEY": TS,
            },
            timeout=10,
        )
        text = proc.stdout + proc.stderr
        assert proc.returncode == 0, text
        assert CF not in text
        assert "tunnel=cloudflare" in proc.stdout
        assert "tunnel_url=https://llm.example.com" in proc.stdout
        assert "funnel_hostname" not in proc.stdout
        created = stub.accepted_creates()[0]
        script = json.loads(created["docker_args"])["cmd"][0]
        assert 'TUNNEL_TOKEN="$CF_TUNNEL_TOKEN" cloudflared tunnel' in script
        assert CF not in script
        assert "tailscale" not in script
        assert created["env"]["CF_TUNNEL_TOKEN"] == CF
        assert "TS_AUTHKEY" not in created["env"]
        assert created["env"]["PODROUTER_CONFIG_HASH"].endswith("-cloudflare")


def test_up_cloudflare_quick_needs_no_key(tmp_path: Path) -> None:
    with StubServer() as stub:
        cfg = write_config(tmp_path)
        proc = run(
            stub,
            [*up_cmd(cfg), "--wait=false"],
            extra_env={"PODROUTER_TUNNEL": "cloudflare"},
            timeout=10,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "tunnel=cloudflare-quick" in proc.stdout
        created = stub.accepted_creates()[0]
        script = json.loads(created["docker_args"])["cmd"][0]
        assert "--url http://localhost:8000" in script
        assert "podrouter-tunnel-url=" in script
        assert "CF_TUNNEL_TOKEN" not in created["env"]
        assert created["env"]["PODROUTER_CONFIG_HASH"].endswith("-cloudflare-quick")


def test_up_tailscale_without_key_fails_before_create(tmp_path: Path) -> None:
    with StubServer() as stub:
        cfg = write_config(tmp_path)
        proc = run(
            stub,
            [*up_cmd(cfg), "--wait=false"],
            extra_env={"PODROUTER_TUNNEL": "tailscale"},
            timeout=10,
        )
        assert proc.returncode == 2
        assert "TS_AUTHKEY" in proc.stderr
        assert stub.all_creates() == []


def test_up_cloudflare_quick_prints_address_from_pod_log(tmp_path: Path) -> None:
    with StubServer() as stub:
        stub.logs = "podrouter-tunnel-url=https://calm-river-12ab.trycloudflare.com\n"
        cfg = write_config(tmp_path)
        proc = run(
            stub, up_cmd(cfg), extra_env={"PODROUTER_TUNNEL": "cloudflare"}, timeout=30
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "tunnel_url=https://calm-river-12ab.trycloudflare.com" in proc.stdout


def test_ls_json_reports_quick_tunnel_url_when_healthy(tmp_path: Path) -> None:
    with StubServer() as stub:
        stub.logs = "podrouter-tunnel-url=https://calm-river-12ab.trycloudflare.com\n"
        cfg = write_config(tmp_path)
        env = {"PODROUTER_TUNNEL": "cloudflare"}
        first = run(stub, [*up_cmd(cfg), "--wait=false"], extra_env=env)
        assert first.returncode == 0, first.stderr
        ls_cmd = ["ls", "--json", "--api-key", "${PODROUTER_TEST_KEY}"]

        stub.health_code = 503
        pods = json.loads(run(stub, ls_cmd).stdout)["pods"]
        assert pods[0]["tunnel_url"] == ""
        assert "_hash" not in pods[0]

        stub.health_code = 200
        pods = json.loads(run(stub, ls_cmd).stdout)["pods"]
        assert pods[0]["tunnel_url"] == "https://calm-river-12ab.trycloudflare.com"


def test_ls_json_reports_named_tunnel_hostname(tmp_path: Path) -> None:
    with StubServer() as stub:
        cfg = write_config(tmp_path)
        env = {
            "PODROUTER_TUNNEL": "cloudflare",
            "CF_TUNNEL_TOKEN": CF,
            "CF_TUNNEL_HOSTNAME": "llm.example.com",
        }
        first = run(stub, [*up_cmd(cfg), "--wait=false"], extra_env=env)
        assert first.returncode == 0, first.stderr
        ls = run(stub, ["ls", "--json", "--api-key", "${PODROUTER_TEST_KEY}"], extra_env=env)
        assert CF not in ls.stdout + ls.stderr
        pods = json.loads(ls.stdout)["pods"]
        assert pods[0]["tunnel_url"] == "https://llm.example.com"
