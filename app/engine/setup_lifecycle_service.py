from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.engine.broker_reality import (
    REPORT_STATE_KEY,
    broker_tracker_config,
    execution_safety_config,
    freshen_broker_reality_report,
)
from app.engine.state_machine import StateMachine
from app.models import EventLevel, MarketSnapshot, SetupStatus
from app.setups.setup_roles import (
    setup_allows_entry,
    setup_is_management_only,
    setup_role_from_config,
)
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository

logger = logging.getLogger(__name__)

DEFAULT_MAX_AGE_DAYS = 30.0
DEFAULT_PRICE_TOO_FAR_PERCENT = 1.5
DEFAULT_REVALIDATE_INTERVAL_SECONDS = 30.0

# Statuses the lifecycle service owns: it may move a setup out of them and,
# for recoverable ones, back into them. Everything else is left untouched.
LIFECYCLE_MANAGED_STATUSES = frozenset(
    {
        SetupStatus.WAITING_ACTIVATION.value,
        SetupStatus.BLOCKED.value,
        SetupStatus.STALE_SETUP.value,
        SetupStatus.MISSED_BREAKOUT_WAIT_RETEST.value,
        SetupStatus.RECONCILING_EXISTING_POSITION.value,
    }
)

# Statuses considered incompatible with arming.
NON_ARMABLE_STATUSES = frozenset(
    {
        SetupStatus.INVALIDATED.value,
        SetupStatus.EXPIRED.value,
        SetupStatus.STALE_SETUP.value,
        SetupStatus.MISSED_BREAKOUT_WAIT_RETEST.value,
    }
)

# Pre-fill statuses that can still be fully revalidated (thesis + environment).
# Setups in a status outside this set (orders placed, positions, terminal)
# are passed through untouched.
EVALUABLE_STATUSES = LIFECYCLE_MANAGED_STATUSES | frozenset(
    {
        SetupStatus.DISABLED.value,
        SetupStatus.VALIDATED.value,
        SetupStatus.WAITING_BREAKOUT.value,
        SetupStatus.MISSED_BREAKOUT.value,
        SetupStatus.WAITING_RETEST.value,
        SetupStatus.WAITING_REBOUND.value,
        SetupStatus.WAITING_CONFIRMATION.value,
        SetupStatus.REARMED_ON_NEW_BASE.value,
        SetupStatus.WAITING_ENTRY_SIGNAL.value,
        SetupStatus.ENTRY_READY.value,
    }
)

# Statuses from which an order can still be transmitted once triggered.
SENDABLE_STATUSES = frozenset(
    {
        SetupStatus.WAITING_ACTIVATION.value,
        SetupStatus.WAITING_BREAKOUT.value,
        SetupStatus.MISSED_BREAKOUT.value,
        SetupStatus.WAITING_RETEST.value,
        SetupStatus.WAITING_REBOUND.value,
        SetupStatus.WAITING_CONFIRMATION.value,
        SetupStatus.REARMED_ON_NEW_BASE.value,
        SetupStatus.WAITING_ENTRY_SIGNAL.value,
        SetupStatus.ENTRY_READY.value,
    }
)


def revalidate_setup(
    setup: dict[str, Any],
    market_snapshot: MarketSnapshot | dict[str, Any] | None,
    broker_reality: dict[str, Any] | None,
    now: datetime | str | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recompute the lifecycle status of a pre-entry setup.

    Returns a status_result dict:
    {
        "status": ..., "status_reason": ..., "blocking_reasons": [...],
        "warnings": [...], "can_be_armed": bool, "can_send_order": bool,
        "last_revalidated_at": iso timestamp
    }
    """
    now_dt = _as_datetime(now) or datetime.now(UTC)
    now_iso = now_dt.isoformat()
    config = setup.get("config") if isinstance(setup.get("config"), dict) else {}
    current_status = str(setup.get("status") or "")
    role = setup_role_from_config(config, infer_position_management=True)
    blocking: list[str] = []
    warnings: list[str] = []

    def result(status: str, reason: str, *, can_be_armed: bool | None = None) -> dict[str, Any]:
        armed = can_be_armed
        if armed is None:
            armed = status not in NON_ARMABLE_STATUSES
        can_send = (
            status in SENDABLE_STATUSES
            and not blocking
            and setup_allows_entry(role)
            and _entry_enabled(config)
            and _auto_execution_enabled(setup, config)
        )
        return {
            "status": status,
            "status_reason": reason,
            "blocking_reasons": list(dict.fromkeys(blocking)),
            "warnings": list(dict.fromkeys(warnings)),
            "can_be_armed": bool(armed),
            "can_send_order": bool(can_send),
            "last_revalidated_at": now_iso,
            "previous_status": current_status,
        }

    if current_status not in EVALUABLE_STATUSES:
        # Position/order/terminal statuses are owned by other engines.
        return result(current_status, "NOT_REVALIDATED", can_be_armed=False)

    # When every check passes, lifecycle-managed statuses converge to
    # WAITING_ACTIVATION; other evaluable statuses keep their current value.
    healthy_status = (
        SetupStatus.WAITING_ACTIVATION.value
        if current_status
        in (LIFECYCLE_MANAGED_STATUSES | {SetupStatus.DISABLED.value, SetupStatus.VALIDATED.value})
        else current_status
    )

    if setup_is_management_only(role):
        return _revalidate_management_only(
            setup,
            config,
            current_status,
            broker_reality,
            blocking,
            warnings,
            result,
        )

    direction = str(config.get("direction") or "long").strip().lower()
    is_long = direction != "short"
    entry_ref = _entry_reference_price(config)
    max_limit = _maximum_limit_price(config, entry_ref)
    initial_stop = _trailing_initial_stop(config)

    # 1. Structural coherence (no market data required).
    if initial_stop is None:
        blocking.append("TRAILING_STOP_MISSING")
        return result(SetupStatus.BLOCKED.value, "TRAILING_STOP_NOT_READY")
    if entry_ref is not None:
        if is_long and initial_stop >= entry_ref:
            return result(SetupStatus.INVALIDATED.value, "STOP_ABOVE_ENTRY_FOR_LONG")
        if not is_long and initial_stop <= entry_ref:
            return result(SetupStatus.INVALIDATED.value, "STOP_BELOW_ENTRY_FOR_SHORT")

    # 2. Explicit expiration.
    expires_at = _expiration_datetime(config)
    if expires_at is not None and now_dt >= expires_at:
        return result(SetupStatus.EXPIRED.value, "TIME_EXPIRED")

    # 3. Age -> stale.
    max_age_days = _max_age_days(config, settings)
    created_at = _as_datetime(setup.get("created_at"))
    if created_at is not None and max_age_days > 0:
        age_days = (now_dt - created_at).total_seconds() / 86400.0
        if age_days > max_age_days:
            return result(SetupStatus.STALE_SETUP.value, "SETUP_TOO_OLD")

    # 4. Market data must be present before any price-based judgement.
    prices = _snapshot_prices(market_snapshot, symbol=str(setup.get("symbol") or ""))
    if prices is None:
        blocking.append("MISSING_MARKET_DATA")
        return result(SetupStatus.BLOCKED.value, "MISSING_MARKET_DATA")

    # 5. Price-based invalidation (technical thesis broken).
    close = prices["close"]
    invalidation_reason = _invalidation_reason(config, close, initial_stop, is_long)
    if invalidation_reason:
        return result(SetupStatus.INVALIDATED.value, invalidation_reason)

    # 6. Price too far from entry: missed breakout / stale.
    executable = prices["ask"] if is_long else prices["bid"]
    if executable is None:
        executable = prices["price"]
    if max_limit is not None and executable is not None:
        too_far_reason = "PRICE_TOO_FAR_ABOVE_ENTRY" if is_long else "PRICE_TOO_FAR_BELOW_ENTRY"
        beyond_limit = executable > max_limit if is_long else executable < max_limit
        if beyond_limit:
            retest_zone = _retest_zone(config)
            threshold = _anti_chase_threshold(config, entry_ref or max_limit, is_long, settings)
            beyond_threshold = executable > threshold if is_long else executable < threshold
            if retest_zone is not None:
                warnings.append(
                    "RETEST_ZONE_AVAILABLE:" f"{retest_zone[0]:.4f}-{retest_zone[1]:.4f}"
                )
                return result(
                    SetupStatus.MISSED_BREAKOUT_WAIT_RETEST.value,
                    too_far_reason,
                )
            if beyond_threshold:
                return result(SetupStatus.STALE_SETUP.value, too_far_reason)
            blocking.append("PRICE_ABOVE_MAXIMUM_LIMIT" if is_long else "PRICE_BELOW_MINIMUM_LIMIT")
            warnings.append("PRICE_BEYOND_LIMIT_WITHIN_ANTI_CHASE_BUFFER")

    # 7. Broker execution environment.
    broker_reason = _broker_blocking_reason(broker_reality, settings)
    if broker_reason:
        blocking.append(broker_reason)
        return result(SetupStatus.BLOCKED.value, broker_reason)

    # 8. Spread quality.
    spread_reason = _spread_blocking_reason(config, prices)
    if spread_reason:
        blocking.append(spread_reason)
        return result(SetupStatus.BLOCKED.value, spread_reason)

    # 9. Non-status blockers: order transmission remains impossible but the
    # setup itself is still a valid waiting setup.
    if not _trailing_stop_order_ready(config):
        blocking.append("TRAILING_STOP_NOT_READY")
    if not _entry_enabled(config):
        blocking.append("ENTRY_DISABLED")
    if not _auto_execution_enabled(setup, config):
        warnings.append("WATCH_ONLY_OR_DISABLED_RUNTIME")

    return result(healthy_status, "SETUP_VALID")


def _revalidate_management_only(
    setup: dict[str, Any],
    config: dict[str, Any],
    current_status: str,
    broker_reality: dict[str, Any] | None,
    blocking: list[str],
    warnings: list[str],
    result: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    broker_reason = _broker_blocking_reason(broker_reality, None)
    if broker_reason:
        blocking.append(broker_reason)
        return result(SetupStatus.BLOCKED.value, broker_reason, can_be_armed=False)
    position_open = _position_open(broker_reality)
    if position_open is False:
        blocking.append("MANAGEMENT_ONLY_POSITION_MISSING")
        return result(
            SetupStatus.BLOCKED.value,
            "MANAGEMENT_ONLY_POSITION_MISSING",
            can_be_armed=False,
        )
    if position_open is None:
        warnings.append("MANAGEMENT_ONLY_POSITION_UNKNOWN")
    if current_status == SetupStatus.BLOCKED.value:
        # Position exists again: hand the setup back to reconciliation.
        return result(
            SetupStatus.RECONCILING_EXISTING_POSITION.value,
            "POSITION_FOUND",
        )
    return result(current_status or SetupStatus.RECONCILING_EXISTING_POSITION.value, "SETUP_VALID")


class SetupLifecycleService:
    """Revalidates pre-entry setups and applies allowed status transitions."""

    def __init__(
        self,
        repository: TradingRepository,
        event_store: EventStore,
        state_machine: StateMachine | None = None,
        settings: dict[str, Any] | None = None,
        market_snapshot_provider: Callable[[str], Any] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.event_store = event_store
        self.state_machine = state_machine or StateMachine()
        self.settings = settings if isinstance(settings, dict) else {}
        self.market_snapshot_provider = market_snapshot_provider
        self.now_provider = now_provider or (lambda: datetime.now(UTC))
        self._last_full_revalidation: datetime | None = None

    def revalidate(
        self,
        setup: dict[str, Any],
        market_snapshot: MarketSnapshot | dict[str, Any] | None = None,
        broker_reality: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if market_snapshot is None and self.market_snapshot_provider is not None:
            try:
                market_snapshot = self.market_snapshot_provider(
                    str(setup.get("symbol") or "").upper()
                )
            except Exception:
                market_snapshot = None
        if broker_reality is None:
            broker_reality = self._broker_reality_context(setup)
        return revalidate_setup(
            setup,
            market_snapshot,
            broker_reality,
            now or self.now_provider(),
            settings=self.settings,
        )

    def revalidate_and_apply(
        self,
        setup: dict[str, Any],
        market_snapshot: MarketSnapshot | dict[str, Any] | None = None,
        broker_reality: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current_status = str(setup.get("status") or "")
        if market_snapshot is None and self.market_snapshot_provider is not None:
            try:
                market_snapshot = self.market_snapshot_provider(
                    str(setup.get("symbol") or "").upper()
                )
            except Exception:
                market_snapshot = None
        if broker_reality is None:
            broker_reality = self._broker_reality_context(setup)
        if market_snapshot is None and not _has_broker_evidence(broker_reality):
            # Totally blind (no quote ever received and no broker reality
            # report): do not reclassify on zero evidence.
            now_iso = (now or self.now_provider()).isoformat()
            return {
                "status": current_status,
                "status_reason": "REVALIDATION_SKIPPED_NO_DATA",
                "blocking_reasons": [],
                "warnings": ["NO_MARKET_DATA_AND_NO_BROKER_REALITY"],
                "can_be_armed": current_status not in NON_ARMABLE_STATUSES,
                "can_send_order": False,
                "last_revalidated_at": now_iso,
                "previous_status": current_status,
            }
        result = self.revalidate(setup, market_snapshot, broker_reality, now)
        setup_id = str(setup.get("setup_id") or "")
        if not setup_id or current_status not in LIFECYCLE_MANAGED_STATUSES:
            return result
        target = str(result.get("status") or "")
        reason = str(result.get("status_reason") or "")
        revalidated_at = str(result.get("last_revalidated_at") or "")
        if target == current_status:
            self.repository.update_setup_revalidation(setup_id, reason, revalidated_at)
            return result
        try:
            current_enum = SetupStatus(current_status)
            target_enum = SetupStatus(target)
        except ValueError:
            self.repository.update_setup_revalidation(setup_id, reason, revalidated_at)
            return result
        role = setup_role_from_config(
            setup.get("config") if isinstance(setup.get("config"), dict) else {},
            infer_position_management=True,
        )
        decision = self.state_machine.explain_transition(current_enum, target_enum, role)
        if not decision.allowed:
            logger.warning("Lifecycle transition rejected for %s: %s", setup_id, decision.reason)
            self.repository.update_setup_revalidation(setup_id, reason, revalidated_at)
            return result
        self.repository.update_setup_status(
            setup_id,
            target,
            f"Revalidation: {reason}",
            status_reason=reason,
            last_revalidated_at=revalidated_at,
        )
        recovered = target == SetupStatus.WAITING_ACTIVATION.value
        self.event_store.record(
            EventLevel.INFO if recovered else EventLevel.WARNING,
            "setup_lifecycle_status_changed",
            f"Setup revalidation: {current_status} -> {target} ({reason})",
            setup_id=setup_id,
            symbol=str(setup.get("symbol") or "").upper() or None,
            data={
                "from": current_status,
                "to": target,
                "status_reason": reason,
                "blocking_reasons": result.get("blocking_reasons", []),
                "warnings": result.get("warnings", []),
            },
        )
        return result

    def revalidate_all(self, force: bool = False) -> list[dict[str, Any]]:
        now = self.now_provider()
        if not force and self._last_full_revalidation is not None:
            elapsed = (now - self._last_full_revalidation).total_seconds()
            if elapsed < self._revalidate_interval_seconds():
                return []
        self._last_full_revalidation = now
        results: list[dict[str, Any]] = []
        for setup in self.repository.list_setups():
            if str(setup.get("status") or "") not in LIFECYCLE_MANAGED_STATUSES:
                continue
            try:
                results.append(self.revalidate_and_apply(setup, now=now))
            except Exception:
                logger.exception("Setup revalidation failed for %s", setup.get("setup_id"))
        return results

    def _revalidate_interval_seconds(self) -> float:
        lifecycle = self.settings.get("lifecycle", {})
        if not isinstance(lifecycle, dict):
            lifecycle = {}
        value = _number_or_none(lifecycle.get("revalidate_interval_seconds"))
        if value is None or value <= 0:
            return DEFAULT_REVALIDATE_INTERVAL_SECONDS
        return float(value)

    def _broker_reality_context(self, setup: dict[str, Any]) -> dict[str, Any]:
        report = self.repository.get_bot_state(REPORT_STATE_KEY, {})
        context: dict[str, Any] = {}
        if isinstance(report, dict) and report.get("broker_last_sync_at"):
            try:
                context = dict(freshen_broker_reality_report(report, settings=self.settings))
            except Exception:
                context = dict(report)
        setup_id = str(setup.get("setup_id") or "")
        if setup_id:
            try:
                protection = self.repository.protection_snapshot_for_setup(setup_id)
            except Exception:
                protection = {}
            if isinstance(protection, dict) and "position_open" in protection:
                context["position_open"] = bool(protection.get("position_open"))
        return context


def _entry_enabled(config: dict[str, Any]) -> bool:
    entry = config.get("entry")
    if not isinstance(entry, dict):
        return False
    return entry.get("enabled", True) is not False


def _auto_execution_enabled(setup: dict[str, Any], config: dict[str, Any]) -> bool:
    config_enabled = config.get("enabled", True) is not False
    return bool(setup.get("enabled", True)) and config_enabled


def _entry_reference_price(config: dict[str, Any]) -> float | None:
    entry = config.get("entry")
    if not isinstance(entry, dict):
        return None
    for key in ("trigger_price", "entry_price"):
        value = _number_or_none(entry.get(key))
        if value is not None:
            return value
    return None


def _maximum_limit_price(config: dict[str, Any], entry_ref: float | None) -> float | None:
    entry = config.get("entry")
    if not isinstance(entry, dict):
        return None
    for key in ("maximum_limit_price", "limit_price"):
        value = _number_or_none(entry.get(key))
        if value is not None:
            return value
    if entry_ref is None:
        return None
    offset = _number_or_none(entry.get("limit_offset"))
    if offset is None:
        return entry_ref
    direction = str(config.get("direction") or "long").strip().lower()
    return entry_ref - offset if direction == "short" else entry_ref + offset


def _trailing_initial_stop(config: dict[str, Any]) -> float | None:
    trailing = config.get("trailing_stop_loss")
    if not isinstance(trailing, dict) or trailing.get("enabled") is False:
        return None
    return _number_or_none(trailing.get("initial_stop"))


def _trailing_stop_order_ready(config: dict[str, Any]) -> bool:
    trailing = config.get("trailing_stop_loss")
    if not isinstance(trailing, dict):
        return False
    broker_order = trailing.get("broker_order")
    if not isinstance(broker_order, dict):
        return False
    ready = trailing.get("trailing_stop_order_ready")
    if ready is None:
        ready = broker_order.get("trailing_stop_order_ready")
    return ready is True


def _expiration_datetime(config: dict[str, Any]) -> datetime | None:
    expiration = config.get("expiration")
    candidates: list[Any] = []
    if isinstance(expiration, dict):
        candidates.extend(
            expiration.get(key) for key in ("expires_at", "valid_until", "expiration_date")
        )
    candidates.extend(config.get(key) for key in ("expires_at", "valid_until"))
    for candidate in candidates:
        parsed = _as_datetime(candidate)
        if parsed is not None:
            return parsed
    return None


def _max_age_days(config: dict[str, Any], settings: dict[str, Any] | None) -> float:
    lifecycle = config.get("lifecycle")
    if isinstance(lifecycle, dict):
        value = _number_or_none(lifecycle.get("max_age_days"))
        if value is not None:
            return float(value)
    expiration = config.get("expiration")
    if isinstance(expiration, dict):
        value = _number_or_none(expiration.get("max_age_days"))
        if value is not None:
            return float(value)
    if isinstance(settings, dict):
        settings_lifecycle = settings.get("lifecycle", {})
        if isinstance(settings_lifecycle, dict):
            value = _number_or_none(settings_lifecycle.get("max_age_days"))
            if value is not None:
                return float(value)
    return DEFAULT_MAX_AGE_DAYS


def _snapshot_prices(
    snapshot: MarketSnapshot | dict[str, Any] | None,
    symbol: str = "",
) -> dict[str, float | None] | None:
    if snapshot is None:
        return None
    if isinstance(snapshot, dict):
        getter = snapshot.get
    else:
        getter = lambda key, default=None: getattr(snapshot, key, default)  # noqa: E731
    snapshot_symbol = str(getter("symbol") or "").upper()
    if symbol and snapshot_symbol and snapshot_symbol != symbol.upper():
        return None
    price = _positive_or_none(getter("price"))
    bid = _positive_or_none(getter("bid"))
    ask = _positive_or_none(getter("ask"))
    close = _positive_or_none(getter("close"))
    last = price or close or bid or ask
    if last is None:
        return None
    return {
        "price": price or last,
        "bid": bid,
        "ask": ask,
        "close": close or price or last,
        "spread_bps": _number_or_none(getter("spread_bps")),
    }


def _invalidation_reason(
    config: dict[str, Any],
    close: float | None,
    initial_stop: float | None,
    is_long: bool,
) -> str:
    if close is None:
        return ""
    if is_long:
        zone = config.get("support_zone")
        zone = zone if isinstance(zone, dict) else {}
        invalidation_below = _number_or_none(zone.get("invalidation_below"))
        if invalidation_below is None:
            invalidation = config.get("invalidation")
            if isinstance(invalidation, dict):
                invalidation_below = _number_or_none(invalidation.get("price_below"))
        support_min = _number_or_none(zone.get("min"))
        if invalidation_below is not None and close < invalidation_below:
            return "INVALIDATION_LEVEL_BROKEN"
        if support_min is not None and close < support_min:
            return "SUPPORT_BROKEN"
        if initial_stop is not None and close < initial_stop:
            return "TECHNICAL_THESIS_BROKEN"
        return ""
    zone = config.get("resistance_zone")
    zone = zone if isinstance(zone, dict) else {}
    invalidation_above = _number_or_none(zone.get("invalidation_above"))
    if invalidation_above is None:
        invalidation = config.get("invalidation")
        if isinstance(invalidation, dict):
            invalidation_above = _number_or_none(invalidation.get("price_above"))
    resistance_max = _number_or_none(zone.get("max"))
    if invalidation_above is not None and close > invalidation_above:
        return "INVALIDATION_LEVEL_BROKEN"
    if resistance_max is not None and close > resistance_max:
        return "SUPPORT_BROKEN"
    if initial_stop is not None and close > initial_stop:
        return "TECHNICAL_THESIS_BROKEN"
    return ""


def _retest_zone(config: dict[str, Any]) -> tuple[float, float] | None:
    missed = config.get("missed_breakout")
    if not isinstance(missed, dict):
        return None
    zone_min = _number_or_none(missed.get("retest_zone_min"))
    zone_max = _number_or_none(missed.get("retest_zone_max"))
    if zone_min is None or zone_max is None:
        return None
    return (min(zone_min, zone_max), max(zone_min, zone_max))


def _anti_chase_threshold(
    config: dict[str, Any],
    entry_ref: float,
    is_long: bool,
    settings: dict[str, Any] | None,
) -> float:
    percent: float | None = None
    anti_chase = config.get("anti_chase")
    if isinstance(anti_chase, dict) and anti_chase.get("enabled") is not False:
        percent = _number_or_none(anti_chase.get("max_price_above_entry_percent"))
    if percent is None and isinstance(settings, dict):
        lifecycle = settings.get("lifecycle", {})
        if isinstance(lifecycle, dict):
            percent = _number_or_none(lifecycle.get("price_too_far_percent"))
    if percent is None:
        percent = DEFAULT_PRICE_TOO_FAR_PERCENT
    factor = percent / 100.0
    return entry_ref * (1.0 + factor) if is_long else entry_ref * (1.0 - factor)


def _broker_blocking_reason(
    broker_reality: dict[str, Any] | None,
    settings: dict[str, Any] | None,
) -> str:
    if not isinstance(broker_reality, dict) or not broker_reality:
        return ""
    if broker_reality.get("broker_connected") is False:
        return "BROKER_DISCONNECTED"
    tracker_status = str(
        broker_reality.get("broker_tracker_status")
        or broker_reality.get("broker_sync_status")
        or ""
    ).upper()
    if tracker_status in {"STALE", "NOT_RUNNING"} and _tracker_blocks(settings):
        return "BROKER_TRACKER_STALE"
    risk_status = str(broker_reality.get("remaining_risk_status") or "").upper()
    if risk_status == "UNKNOWN_CRITICAL":
        return "RISK_UNKNOWN"
    return ""


def _tracker_blocks(settings: dict[str, Any] | None) -> bool:
    if not isinstance(settings, dict) or not settings:
        return True
    tracker = broker_tracker_config(settings)
    safety = execution_safety_config(settings)
    return bool(
        tracker.get("enabled")
        and tracker.get("block_auto_execution_if_stale")
        and safety.get("block_new_entries_if_broker_tracker_stale")
    )


def _spread_blocking_reason(
    config: dict[str, Any],
    prices: dict[str, float | None],
) -> str:
    spread_bps = prices.get("spread_bps")
    if spread_bps is None:
        return ""
    max_spread: float | None = None
    for section_key in ("execution_quality", "breakout", "entry"):
        section = config.get(section_key)
        if isinstance(section, dict):
            max_spread = _number_or_none(section.get("max_spread_bps"))
            if max_spread is not None:
                break
    if max_spread is None or max_spread <= 0:
        return ""
    if spread_bps > max_spread:
        return "SPREAD_TOO_WIDE"
    return ""


def _has_broker_evidence(broker_reality: dict[str, Any] | None) -> bool:
    if not isinstance(broker_reality, dict) or not broker_reality:
        return False
    return bool(
        broker_reality.get("broker_last_sync_at")
        or "broker_connected" in broker_reality
        or broker_reality.get("broker_tracker_status")
    )


def _position_open(broker_reality: dict[str, Any] | None) -> bool | None:
    if not isinstance(broker_reality, dict):
        return None
    if "position_open" in broker_reality:
        return bool(broker_reality.get("position_open"))
    protection = broker_reality.get("protection")
    if isinstance(protection, dict) and "position_open" in protection:
        return bool(protection.get("position_open"))
    return None


def _as_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _number_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positive_or_none(value: Any) -> float | None:
    number = _number_or_none(value)
    if number is None or number <= 0:
        return None
    return number
