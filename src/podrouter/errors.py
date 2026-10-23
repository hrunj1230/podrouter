"""Errors raised before or during provisioning."""


class GpuUpError(Exception):
    """Base error for the CLI."""


class ConfigError(GpuUpError):
    """The config or CLI secrets cannot be used. No pod is created."""


class AllocationError(GpuUpError):
    """Every GPU candidate was skipped or rejected. No pod is left running."""


class BootTimeout(GpuUpError):
    """The pod did not reach RUNNING within the boot timeout."""


class ReadinessTimeout(GpuUpError):
    """The readiness path did not return HTTP 200 within the warmup timeout."""


class PodError(GpuUpError):
    """The pod disappeared or entered a terminal state before it was ready."""
