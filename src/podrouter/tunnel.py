"""Pick how a Pod gets a public address: Runpod proxy, Tailscale Funnel, or Cloudflare Tunnel.

The choice comes from `PODROUTER_TUNNEL` in the environment (the Mac app saves it
in the repo `.env`):

- `none`: Runpod proxy address only.
- `tailscale`: Tailscale Funnel. Needs `TS_AUTHKEY`.
- `cloudflare`: Cloudflare Tunnel. With `CF_TUNNEL_TOKEN` it runs a named
  tunnel whose public hostname is set in the Cloudflare dashboard. Without a
  token it runs a quick tunnel on a random `trycloudflare.com` address.

When `PODROUTER_TUNNEL` is empty, the old rule applies: Tailscale if `TS_AUTHKEY`
is set, otherwise none.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from podrouter.errors import ConfigError
from podrouter.funnel import funnel_docker_args, model_exec

MODES = ("none", "tailscale", "cloudflare")

CLOUDFLARED_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/"
    "cloudflared-linux-amd64"
)
QUICK_URL_MARK = "podrouter-tunnel-url="
_QUICK_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


@dataclass(frozen=True)
class Tunnel:
    mode: str  # none | tailscale | cloudflare
    cloudflare_named: bool = False

    @property
    def hash_suffix(self) -> str:
        if self.mode == "tailscale":
            return "-funnel"
        if self.mode == "cloudflare":
            return "-cloudflare" if self.cloudflare_named else "-cloudflare-quick"
        return ""

    @property
    def label(self) -> str:
        if self.mode == "cloudflare":
            return "cloudflare" if self.cloudflare_named else "cloudflare-quick"
        return self.mode


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def resolve_tunnel() -> Tunnel:
    """Read the selected mode. Raise ConfigError when it cannot work."""
    raw = _env("PODROUTER_TUNNEL").lower()
    if not raw:
        return Tunnel("tailscale" if _env("TS_AUTHKEY") else "none")
    if raw not in MODES:
        raise ConfigError(
            f"PODROUTER_TUNNEL must be one of {', '.join(MODES)} (got {raw!r})"
        )
    if raw == "tailscale" and not _env("TS_AUTHKEY"):
        raise ConfigError("PODROUTER_TUNNEL=tailscale needs TS_AUTHKEY")
    if raw == "cloudflare":
        return Tunnel("cloudflare", cloudflare_named=bool(_env("CF_TUNNEL_TOKEN")))
    return Tunnel(raw)


def tunnel_env(tunnel: Tunnel) -> dict[str, str]:
    """Secrets the Pod needs for the selected mode. Other tunnel keys stay on the Mac."""
    if tunnel.mode == "tailscale":
        return {"TS_AUTHKEY": _env("TS_AUTHKEY")}
    if tunnel.mode == "cloudflare" and tunnel.cloudflare_named:
        return {"CF_TUNNEL_TOKEN": _env("CF_TUNNEL_TOKEN")}
    return {}


def cloudflare_hostname() -> str:
    """Public hostname of the named tunnel, as typed in the app. Not a secret."""
    host = _env("CF_TUNNEL_HOSTNAME")
    host = re.sub(r"^https?://", "", host).rstrip("/")
    return host


def find_quick_url(logs: str) -> str:
    for line in reversed(logs.splitlines()):
        if QUICK_URL_MARK in line:
            match = _QUICK_URL.search(line)
            if match:
                return match.group(0)
    return ""


def cloudflare_docker_args(
    *,
    port: int,
    user_args: str,
    ttl_seconds: int,
    ttl_text: str,
    image: str,
    named: bool,
) -> str:
    """Return Runpod docker args that run cloudflared beside the model server.

    A named tunnel reads its token from `$CF_TUNNEL_TOKEN` inside the container,
    so the token is never written into the command. Its route
    (`hostname -> http://localhost:<port>`) lives in the Cloudflare dashboard.
    A quick tunnel prints its random address. A background loop copies that
    address to the container log as `podrouter-tunnel-url=...` so podrouter can show it.
    Tunnel failures are logged and do not stop the model server.
    """
    if not 1 <= port <= 65535:
        raise ConfigError("port must be 1-65535")
    args = user_args.strip()
    if not args:
        args = "--host 0.0.0.0"
    if '"' in args or "'" in args or "\n" in args or "\\" in args:
        raise ConfigError("tunnel start command cannot contain quotes")
    install = (
        "{ command -v cloudflared >/dev/null 2>&1"
        f" || {{ curl -fsSL -o /usr/local/bin/cloudflared {CLOUDFLARED_URL}"
        " && chmod +x /usr/local/bin/cloudflared; }"
        " || echo podrouter-tunnel-failed; }"
    )
    log = "/tmp/cloudflared.log"
    if named:
        # TUNNEL_TOKEN is read by cloudflared, so the token is not in argv.
        run = (
            '{ (TUNNEL_TOKEN="$CF_TUNNEL_TOKEN" cloudflared tunnel'
            f" --no-autoupdate run >{log} 2>&1 &) }}"
        )
        steps = [install, run]
    else:
        run = (
            "{ (cloudflared tunnel --no-autoupdate"
            f" --url http://localhost:{int(port)} >{log} 2>&1 &) }}"
        )
        announce = (
            "{ (i=0; while [ $i -lt 90 ]; do"
            " u=$(grep -o 'https://[a-z0-9-]*[.]trycloudflare[.]com'"
            f" {log} 2>/dev/null | head -n 1);"
            f' if [ -n "$u" ]; then echo "{QUICK_URL_MARK}$u"; break; fi;'
            " i=$((i+1)); sleep 2; done) & }"
        )
        steps = [install, run, announce]
    script = " && ".join(
        [
            *steps,
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


def tunnel_docker_args(
    tunnel: Tunnel,
    *,
    hostname: str,
    port: int,
    user_args: str,
    ttl_seconds: int,
    ttl_text: str,
    image: str,
) -> str | None:
    """Docker args for the selected mode, or None for the plain Runpod start."""
    if tunnel.mode == "tailscale":
        return funnel_docker_args(
            hostname=hostname,
            port=port,
            user_args=user_args,
            ttl_seconds=ttl_seconds,
            ttl_text=ttl_text,
            image=image,
        )
    if tunnel.mode == "cloudflare":
        return cloudflare_docker_args(
            port=port,
            user_args=user_args,
            ttl_seconds=ttl_seconds,
            ttl_text=ttl_text,
            image=image,
            named=tunnel.cloudflare_named,
        )
    return None
