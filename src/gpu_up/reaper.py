"""In-pod TTL command. It does not call back to the local process."""

from __future__ import annotations


def render_start_command(user_args: str, ttl_seconds: int, ttl_text: str) -> str:
    """Arm `runpodctl` to remove the current pod, then exec the configured args.

    The SDK places this string inside a GraphQL double-quoted scalar, so the
    command itself contains no double quotes. `$RUNPOD_POD_ID` is left for the
    container shell to expand; the local process is not part of the timer.
    """
    command = user_args.strip()
    if not command:
        command = "sleep infinity"
    command = command.replace("'", "'\\''")
    return (
        "bash -lc '(sleep "
        f"{int(ttl_seconds)}; runpodctl remove pod '$RUNPOD_POD_ID') "
        f"& exec {command}' # ttl={ttl_text}"
    )
