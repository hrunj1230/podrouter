"""Stable public address for a Pod via Tailscale Funnel."""

from __future__ import annotations

import json
import re

from podrouter.errors import ConfigError

_HOSTNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def model_exec(image: str, args: str) -> str:
    """Pick the process Funnel should exec after the image entrypoint is replaced."""
    lowered = image.lower()
    if "vllm" in lowered:
        return f"exec vllm serve {args}"
    if "llama" in lowered:
        return f"exec /app/llama-server {args}"
    return f"exec {args}"


def funnel_docker_args(
    *,
    hostname: str,
    port: int,
    user_args: str,
    ttl_seconds: int,
    ttl_text: str,
    image: str,
) -> str:
    """Return Runpod docker args that replace the image entrypoint.

    Funnel has to run beside the model server, so the payload is the JSON
    shape Runpod accepts for an entrypoint override. A vLLM image is started
    with `vllm serve`. A llama.cpp image is started with `/app/llama-server`.
    Any other image execs the configured args as-is.
    `$TS_AUTHKEY` is expanded inside the container, not written here.
    """
    if _HOSTNAME.fullmatch(hostname) is None:
        raise ConfigError("pod name cannot be a Tailscale hostname")
    if not 1 <= port <= 65535:
        raise ConfigError("port must be 1-65535")
    args = user_args.strip()
    if not args:
        args = "--host 0.0.0.0"
    if '"' in args or "'" in args or "\n" in args or "\\" in args:
        raise ConfigError("funnel start command cannot contain quotes")
    socket = "--socket=/run/tailscale/tailscaled.sock"
    # `$TS_AUTHKEY` is expanded by the container shell. The key stays in env.
    # userspace networking is required: Runpod containers have no /dev/net/tun.
    # Ephemeral is a property of that auth key. `tailscale up` 1.104 rejects
    # `--ephemeral`, and a rejected flag aborts the chain before the server.
    # `&` stays inside `{ }` so it does not background the rest of the chain.
    script = " && ".join(
        [
            "curl -fsSL https://tailscale.com/install.sh | sh",
            "mkdir -p /var/lib/tailscale /run/tailscale",
            (
                "(tailscaled --tun=userspace-networking"
                " --state=/var/lib/tailscale/tailscaled.state"
                f" {socket} >/tmp/tailscaled.log 2>&1 &)"
            ),
            (
                "{ i=0; while [ $i -lt 30 ]; do"
                f" tailscale {socket} status >/dev/null 2>&1 && break;"
                " i=$((i+1)); sleep 1; done; }"
            ),
            (
                f'tailscale {socket} up --auth-key="$TS_AUTHKEY"'
                f" --hostname={hostname} --reset --ssh=false"
                " --accept-dns=false --timeout=30s"
            ),
            (
                "{ "
                f"tailscale {socket} funnel --bg --yes {int(port)}"
                " || echo podrouter-funnel-failed; }"
            ),
            (
                "{ (sleep "
                f"{int(ttl_seconds)}; runpodctl remove pod $RUNPOD_POD_ID) & }}"
            ),
            f"{model_exec(image, args)} # ttl={ttl_text}",
        ]
    )
    return json.dumps(
        {"entrypoint": ["/bin/bash", "-lc"], "cmd": [script]},
        separators=(",", ":"),
    )
