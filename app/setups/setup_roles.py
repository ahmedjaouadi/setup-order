from __future__ import annotations

from typing import Any

from app.models import SetupRole

DEFAULT_SETUP_ROLE = SetupRole.ENTRY_AND_MANAGEMENT
ENTRY_SETUP_ROLES = frozenset(
    {
        SetupRole.ENTRY_AND_MANAGEMENT,
        SetupRole.ENTRY_ONLY,
    }
)
VALID_SETUP_ROLE_VALUES = frozenset(role.value for role in SetupRole)


def setup_role_from_config(
    config: dict[str, Any],
    *,
    default: SetupRole = DEFAULT_SETUP_ROLE,
    infer_position_management: bool = False,
) -> SetupRole:
    raw_role = config.get("setup_role")
    if (
        infer_position_management
        and raw_role in (None, "")
        and config.get("setup_type") == "position_management"
    ):
        return SetupRole.MANAGEMENT_ONLY
    return normalize_setup_role(raw_role, default=default)


def normalize_setup_role(
    raw_role: Any,
    *,
    default: SetupRole = DEFAULT_SETUP_ROLE,
) -> SetupRole:
    try:
        return SetupRole(str(raw_role or default.value).strip())
    except ValueError:
        return default


def is_valid_setup_role(raw_role: Any) -> bool:
    return str(raw_role or "").strip() in VALID_SETUP_ROLE_VALUES


def setup_allows_entry(role: SetupRole | str | Any) -> bool:
    return normalize_setup_role(role) in ENTRY_SETUP_ROLES


def setup_is_management_only(role: SetupRole | str | Any) -> bool:
    return normalize_setup_role(role) == SetupRole.MANAGEMENT_ONLY


def entry_policy_errors(role: SetupRole | str | Any, entry_enabled: bool) -> list[str]:
    normalized = normalize_setup_role(role)
    errors: list[str] = []
    if setup_is_management_only(normalized) and entry_enabled:
        errors.append("MANAGEMENT_ONLY setup cannot enable entry orders")
    if setup_allows_entry(normalized) and not entry_enabled:
        errors.append("entry.enabled must be true when setup_role allows entries")
    return errors
