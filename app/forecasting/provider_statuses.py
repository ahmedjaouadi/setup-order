from __future__ import annotations

OK = "OK"
AVAILABLE = OK
EXTERNAL_WORKER_CONFIGURED = "EXTERNAL_WORKER_CONFIGURED"
EXTERNAL_WORKER_OK = "EXTERNAL_WORKER_OK"
WORKER_NOT_CONFIGURED = "WORKER_NOT_CONFIGURED"
WORKER_UNREACHABLE = "WORKER_UNREACHABLE"
WORKER_ERROR = "WORKER_ERROR"
MISSING_DEPENDENCY = "MISSING_DEPENDENCY"
DISABLED_BY_CONFIG = "DISABLED_BY_CONFIG"
LOAD_ERROR = "LOAD_ERROR"

READY_STATUSES = {
    OK,
    "AVAILABLE",
    EXTERNAL_WORKER_CONFIGURED,
    EXTERNAL_WORKER_OK,
}

CONFIGURED_STATUSES = {
    *READY_STATUSES,
    WORKER_UNREACHABLE,
    WORKER_ERROR,
    MISSING_DEPENDENCY,
    LOAD_ERROR,
}


def is_available_status(status: str) -> bool:
    return str(status or "") in READY_STATUSES


def status_from_reason(reason: str) -> str:
    normalized = str(reason or "").lower()
    if any(
        token in normalized
        for token in (
            "worker is not configured",
            "worker not configured",
            "worker_script is not configured",
            "external python executable is not configured",
        )
    ):
        return WORKER_NOT_CONFIGURED
    if any(
        token in normalized
        for token in (
            "worker timed out",
            "dependency probe timed out",
            "python executable not found",
            "worker script not found",
            "worker unreachable",
        )
    ):
        return WORKER_UNREACHABLE
    if any(
        token in normalized
        for token in (
            "worker exited",
            "worker failed",
            "worker did not return json",
            "worker returned no",
        )
    ):
        return WORKER_ERROR
    if any(
        token in normalized
        for token in (
            "missing optional package",
            "not installed",
            "no module named",
            "module is not available",
            "module not found",
            "dependency is not available",
        )
    ):
        return MISSING_DEPENDENCY
    if any(
        token in normalized
        for token in (
            "disabled by config",
            "disabled by forecast_stack configuration",
            "not configured",
            "restricted to offline model lab jobs",
        )
    ):
        return DISABLED_BY_CONFIG
    return LOAD_ERROR
