from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.engine.position_action_executor import PositionActionExecutor
from app.engine.position_manager import PositionManager
from app.engine.state_machine import StateMachine
from app.engine.stop_modification_service import StopModificationService
from app.models import SetupSignal, SetupStatus, SignalAction
from app.setups.breakout_retest import BreakoutRetestSetup
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_breakout_config


class _SpyStopModificationService:
    """Records calls and returns a scripted result -- proves execute_raise_stop_signal
    routes through the service instead of touching PositionManager directly.
    """

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[tuple[str, float]] = []

    async def modify_stop(self, symbol: str, new_stop: float) -> dict[str, Any]:
        self.calls.append((symbol, new_stop))
        return self.result


class PositionActionExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.position_manager = PositionManager(self.repository, self.event_store)
        self.stop_modification_service = StopModificationService(
            self.repository,
            self.event_store,
            broker=None,
            position_manager=self.position_manager,
        )
        self.executor = PositionActionExecutor(
            repository=self.repository,
            event_store=self.event_store,
            position_manager=self.position_manager,
            state_machine=StateMachine(),
            stop_modification_service=self.stop_modification_service,
        )

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _open_setup_position(self) -> tuple[str, str]:
        setup = BreakoutRetestSetup(valid_breakout_config()).to_record(SetupStatus.IN_POSITION)
        self.repository.upsert_setup(setup)
        self.position_manager.open_or_update_position(
            setup_id=setup.setup_id,
            symbol=setup.symbol,
            quantity=10,
            average_price=14.50,
            current_price=15.00,
            stop_loss=13.85,
        )
        return setup.setup_id, setup.symbol

    async def test_raise_stop_signal_routes_to_broker_service_not_local(self) -> None:
        """Central proof: modify_stop of the service is called with the
        (symbol, new_stop) extracted from the signal, and never through
        PositionManager.raise_stop directly (move_stop is bypassed).
        """
        setup_id, symbol = self._open_setup_position()
        setup_row = self.repository.get_setup(setup_id)
        spy = _SpyStopModificationService({"ok": True})
        executor = PositionActionExecutor(
            repository=self.repository,
            event_store=self.event_store,
            position_manager=self.position_manager,
            state_machine=StateMachine(),
            stop_modification_service=spy,
        )
        signal = SetupSignal(
            action=SignalAction.RAISE_STOP,
            reason="Trailing stop rule reached",
            target_status=SetupStatus.MANAGING_POSITION,
            new_stop=14.25,
        )

        handled = await executor.execute_raise_stop_signal(
            setup_row,
            SetupStatus.IN_POSITION,
            signal,
        )

        self.assertTrue(handled)
        self.assertEqual(spy.calls, [(symbol, 14.25)])
        # The service is a spy that never touches the position: the local
        # stop is unchanged, proving the raise did not go through move_stop.
        updated_position = self.repository.get_position(symbol)
        self.assertEqual(updated_position["current_stop"], 13.85)

    async def test_result_ok_true_transitions_setup(self) -> None:
        setup_id, symbol = self._open_setup_position()
        setup_row = self.repository.get_setup(setup_id)
        spy = _SpyStopModificationService({"ok": True})
        executor = PositionActionExecutor(
            repository=self.repository,
            event_store=self.event_store,
            position_manager=self.position_manager,
            state_machine=StateMachine(),
            stop_modification_service=spy,
        )
        signal = SetupSignal(
            action=SignalAction.RAISE_STOP,
            reason="Trailing stop rule reached",
            target_status=SetupStatus.MANAGING_POSITION,
            new_stop=14.25,
        )

        handled = await executor.execute_raise_stop_signal(
            setup_row,
            SetupStatus.IN_POSITION,
            signal,
        )

        updated_setup = self.repository.get_setup(setup_id)
        self.assertTrue(handled)
        self.assertEqual(updated_setup["status"], SetupStatus.MANAGING_POSITION.value)

    async def test_result_ok_false_no_transition_but_signal_consumed(self) -> None:
        setup_id, symbol = self._open_setup_position()
        setup_row = self.repository.get_setup(setup_id)
        spy = _SpyStopModificationService(
            {"ok": False, "reason_code": "STOP_LOWERING_FORBIDDEN", "reason": "never_lower_stop"}
        )
        executor = PositionActionExecutor(
            repository=self.repository,
            event_store=self.event_store,
            position_manager=self.position_manager,
            state_machine=StateMachine(),
            stop_modification_service=spy,
        )
        signal = SetupSignal(
            action=SignalAction.RAISE_STOP,
            reason="Bad stop rule",
            target_status=SetupStatus.MANAGING_POSITION,
            new_stop=13.50,
        )

        handled = await executor.execute_raise_stop_signal(
            setup_row,
            SetupStatus.IN_POSITION,
            signal,
        )

        updated_setup = self.repository.get_setup(setup_id)
        events = self.repository.list_events(setup_id=setup_id, limit=5)
        self.assertTrue(handled)
        self.assertEqual(updated_setup["status"], SetupStatus.IN_POSITION.value)
        self.assertTrue(any(event["event_type"] == "raise_stop_rejected" for event in events))

    async def test_b2_guard_inherited_via_real_service(self) -> None:
        """A RAISE_STOP with new_stop below the current stop is rejected by
        the real StopModificationService's never_lower_stop guard (B-2),
        with no code added in this lot to reproduce that guard.
        """
        setup_id, symbol = self._open_setup_position()
        setup_row = self.repository.get_setup(setup_id)
        signal = SetupSignal(
            action=SignalAction.RAISE_STOP,
            reason="Bad stop rule",
            target_status=SetupStatus.MANAGING_POSITION,
            new_stop=13.50,
        )

        handled = await self.executor.execute_raise_stop_signal(
            setup_row,
            SetupStatus.IN_POSITION,
            signal,
        )

        updated_setup = self.repository.get_setup(setup_id)
        updated_position = self.repository.get_position(symbol)
        self.assertTrue(handled)
        self.assertEqual(updated_setup["status"], SetupStatus.IN_POSITION.value)
        self.assertEqual(updated_position["current_stop"], 13.85)

    async def test_non_raise_stop_signal_is_not_handled(self) -> None:
        setup_id, symbol = self._open_setup_position()
        setup_row = self.repository.get_setup(setup_id)
        signal = SetupSignal(action=SignalAction.HOLD, reason="No action")

        handled = await self.executor.execute_raise_stop_signal(
            setup_row,
            SetupStatus.IN_POSITION,
            signal,
        )

        self.assertFalse(handled)

    def test_manual_move_stop_uses_same_position_manager_rules(self) -> None:
        """move_stop is now dead code in production (RAISE_STOP no longer
        calls it) but is kept in place undeleted per the B-1b-1 order; this
        test documents it still works on its own.
        """
        _, symbol = self._open_setup_position()

        moved = self.executor.move_stop(symbol, 14.10)

        updated_position = self.repository.get_position(symbol)
        self.assertTrue(moved)
        self.assertEqual(updated_position["current_stop"], 14.10)


if __name__ == "__main__":
    unittest.main()
