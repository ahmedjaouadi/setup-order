from __future__ import annotations

from app.models import EventLevel, OrderStatus, PositionRecord, SetupStatus
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository


class PostFillProgression:
    """Setup progression that follows a fill, shared by the simulated fill
    path (fill_executor) and the real broker reconciliation path.

    Placing the protective stop order is NOT part of this component: the
    caller places it (or already has one from a bracket order) and reports
    the outcome back via ``protection_verified``.
    """

    def __init__(self, repository: TradingRepository, event_store: EventStore) -> None:
        self.repository = repository
        self.event_store = event_store

    def record_fill(
        self,
        order_id: str,
        setup_id: str,
        quantity: int,
        fill_price: float,
        symbol: str,
    ) -> PositionRecord | None:
        self.repository.update_order_status(order_id, OrderStatus.FILLED.value)
        setup = self.repository.get_setup(setup_id)
        if not setup:
            return None

        config = setup.get("config", {}) if isinstance(setup.get("config"), dict) else {}
        trailing = config.get("trailing_stop_loss", {}) if isinstance(config, dict) else {}
        stop_raw = trailing.get("initial_stop") if isinstance(trailing, dict) else None
        if stop_raw is None:
            self.event_store.record(
                EventLevel.CRITICAL,
                "entry_fill_missing_trailing_stop",
                "Filled entry has no trailing_stop_loss.initial_stop",
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
            )
            self.repository.update_setup_status(
                setup["setup_id"],
                SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
                "Filled entry missing trailing stop",
            )
            return None
        stop_loss = float(stop_raw)
        position = PositionRecord(
            symbol=symbol,
            setup_id=setup["setup_id"],
            quantity=quantity,
            average_price=fill_price,
            current_price=fill_price,
            unrealized_pnl=0.0,
            current_stop=stop_loss,
            risk_remaining=round(max(fill_price - stop_loss, 0) * quantity, 2),
            status="OPEN",
        )
        self.repository.upsert_position(position)
        self.repository.update_setup_status(
            setup["setup_id"],
            SetupStatus.ENTRY_FILLED.value,
            "Entry order filled",
        )
        self.event_store.record(
            EventLevel.TRADE,
            "entry_filled",
            "Entry filled by the internal test broker",
            setup_id=setup["setup_id"],
            symbol=setup["symbol"],
            data={"fill_price": fill_price, "quantity": quantity},
        )
        return position

    def has_active_protection(self, setup_id: str) -> bool:
        protection = self.repository.protection_snapshot_for_setup(setup_id)
        return bool(protection.get("has_active_stop_order"))

    def mark_in_position(self, setup_id: str, *, protection_verified: bool) -> None:
        """Write IN_POSITION. Refuses to write unless the caller has proven
        the position is protected (audit 19, obstacle 3: an unconditional
        write here would let a caller mark a naked position as protected).
        """
        if not protection_verified:
            return
        self.repository.update_setup_status(
            setup_id,
            SetupStatus.IN_POSITION.value,
            "Position protected and open",
        )
