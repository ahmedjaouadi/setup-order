from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from typing import Any

from app.conversion import canonicalize_setup_config
from app.models import utc_now_iso
from app.storage.repositories import TradingRepository
from app.utils.id_generator import new_id

SnapshotProvider = Callable[[str], Any | None]


class SetupCreationSnapshotService:
    """Captures immutable market context when a setup is first created."""

    def __init__(self, repository: TradingRepository, snapshot_provider: SnapshotProvider) -> None:
        self.repository = repository
        self.snapshot_provider = snapshot_provider

    def capture(self, setup_id: str) -> dict[str, Any]:
        existing = self.repository.get_setup_creation_snapshot(setup_id)
        if existing is not None:
            self.repository.attach_setup_creation_snapshot(setup_id, existing)
            return existing
        setup = self.repository.get_setup(setup_id)
        if setup is None:
            raise KeyError(setup_id)
        symbol = str(setup.get("symbol") or "").upper()
        raw = _payload(self.snapshot_provider(symbol))
        config = _canonical_config(setup)
        entry = config.get("entry") if isinstance(config.get("entry"), dict) else {}
        last = _first_number(raw.get("price"), raw.get("last_price"))
        bid, ask = _number(raw.get("bid")), _number(raw.get("ask"))
        mid = (bid + ask) / 2 if bid is not None and ask is not None else last
        spread_pct = (
            ((ask - bid) / mid * 100) if bid is not None and ask is not None and mid else None
        )
        trigger = _first_number(entry.get("trigger_price"), setup.get("entry_trigger"))
        limit_price = _first_number(entry.get("limit_price"), setup.get("maximum_limit_price"))
        trailing = (
            config.get("trailing_stop_loss", {})
            if isinstance(config.get("trailing_stop_loss"), dict)
            else {}
        )
        stop = _first_number(
            trailing.get("initial_stop"),
        )
        issues = [] if last is not None else ["market_data_unavailable_at_creation"]
        if bid is not None and ask is not None and bid > ask:
            issues.append("bid_greater_than_ask")
        raw_quality = str(raw.get("data_quality_status") or "").upper()
        if raw_quality in {"WARNING", "STALE", "INVALID", "UNKNOWN"}:
            issues.append(f"market_data_quality_{raw_quality.lower()}")
        snapshot = {
            "snapshot_id": new_id("scs"),
            "setup_id": setup_id,
            "scenario_id": config.get("scenario_id"),
            "opportunity_id": config.get("opportunity_id"),
            "symbol": symbol,
            "captured_at": utc_now_iso(),
            "last_price": last,
            "bid": bid,
            "ask": ask,
            "mid_price": mid,
            "spread_pct": _round(spread_pct),
            "volume": _number(raw.get("volume")),
            "volume_ratio": _first_number(raw.get("volume_ratio"), raw.get("volume_ratio_15m")),
            "atr_15m": _number(raw.get("atr_15m")),
            "atr_1h": _number(raw.get("atr_1h")),
            "vwap": _number(raw.get("vwap")),
            "entry_trigger_price": trigger,
            "entry_limit_price": limit_price,
            "trailing_stop_loss": {"initial_stop": stop},
            "distance_to_trigger_pct": _distance(last, trigger),
            "distance_to_limit_pct": _distance(last, limit_price),
            "distance_to_stop_pct": _distance(last, stop),
            "data_quality_status": "OK" if not issues else "WARNING",
            "data_quality_issues": issues,
            "source": str(
                raw.get("market_data_source")
                or raw.get("live_quote_source")
                or raw.get("source")
                or "NO_MARKET_DATA"
            ),
        }
        self.repository.add_setup_creation_snapshot(snapshot)
        persisted = self.repository.get_setup_creation_snapshot(setup_id) or snapshot
        self.repository.attach_setup_creation_snapshot(setup_id, persisted)
        return persisted

    def price_drift(self, setup_id: str) -> dict[str, Any]:
        snapshot = self.repository.get_setup_creation_snapshot(setup_id)
        if snapshot is None:
            raise KeyError(setup_id)
        raw = _payload(self.snapshot_provider(str(snapshot["symbol"])))
        current = _first_number(raw.get("price"), raw.get("last_price"))
        created = _number(snapshot.get("last_price"))
        return {
            "setup_id": setup_id,
            "symbol": snapshot["symbol"],
            "creation_price": created,
            "current_price": current,
            "move_since_creation_pct": _distance(created, current),
            "distance_current_to_trigger_pct": _distance(
                current, _number(snapshot.get("entry_trigger_price"))
            ),
            "captured_at": snapshot["captured_at"],
        }


def _payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if is_dataclass(value):
        return asdict(value)
    return dict(value) if isinstance(value, dict) else {}


def _canonical_config(setup: dict[str, Any]) -> dict[str, Any]:
    config = setup.get("config") if isinstance(setup.get("config"), dict) else {}
    try:
        return canonicalize_setup_config(config).config
    except Exception:
        return dict(config)


def _distance(origin: float | None, target: float | None) -> float | None:
    if origin is None or target is None or origin == 0:
        return None
    return _round((target - origin) / origin * 100)


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _round(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None
