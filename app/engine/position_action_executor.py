from __future__ import annotations

import logging
from typing import Any

from app.engine.position_manager import PositionManager
from app.engine.state_machine import StateMachine
from app.models import EventLevel, SetupStatus, SignalAction
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository


logger = logging.getLogger(__name__)


class PositionActionExecutor:
    def __init__(
        self,
        repository: TradingRepository,
        event_store: EventStore,
        position_manager: PositionManager,
        state_machine: StateMachine,
    ) -> None:
        self.repository = repository
        self.event_store = event_store
        self.position_manager = position_manager
        self.state_machine = state_machine

    def execute_raise_stop_signal(
        self,
        setup: dict[str, Any],
        current_status: SetupStatus,
        signal: Any,
    ) -> bool:
        if signal.action != SignalAction.RAISE_STOP or signal.new_stop is None:
            return False

        moved = self.move_stop(setup["symbol"], signal.new_stop)
        if moved and signal.target_status:
            self.transition_setup(setup, current_status, signal.target_status, signal.reason)
        return True

    def move_stop(
        self,
        symbol: str,
        new_stop: float,
        allow_lower: bool = False,
    ) -> bool:
        return self.position_manager.raise_stop(symbol, new_stop, allow_lower=allow_lower)

    def transition_setup(
        self,
        setup: dict[str, Any],
        current_status: SetupStatus,
        target_status: SetupStatus,
        reason: str,
    ) -> None:
        try:
            new_status = self.state_machine.transition(current_status, target_status)
        except Exception as exc:
            logger.warning("Rejected position transition for %s: %s", setup["setup_id"], exc)
            self.event_store.record(
                EventLevel.ERROR,
                "setup_transition_rejected",
                str(exc),
                setup_id=setup["setup_id"],
                symbol=setup["symbol"],
            )
            return
        self.repository.update_setup_status(setup["setup_id"], new_status.value, reason)
        self.event_store.record(
            EventLevel.INFO,
            "setup_status_changed",
            reason,
            setup_id=setup["setup_id"],
            symbol=setup["symbol"],
            data={"from": current_status.value, "to": new_status.value},
        )
