from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.broker.ib_models import BrokerOrderRequest, BrokerOrderResult, BrokerPosition
from app.broker.tws_connector import SimulatedBrokerConnector
from app.engine.entry_order_executor import EntryOrderExecutor
from app.engine.order_manager import OrderManager
from app.engine.post_fill_progression import PostFillProgression
from app.engine.reconciliation import ReconciliationEngine
from app.engine.setup_engine import SetupEngine
from app.models import OrderRecord, OrderStatus, OrderType, RiskDecision, SetupRecord, SetupStatus
from app.setups.breakout_retest import BreakoutRetestSetup
from app.storage.database import Database
from app.storage.event_store import EventStore
from app.storage.repositories import TradingRepository
from tests.test_setups import valid_breakout_config

"""S5b-2 (audit 40): behavioural companion to
tests/test_active_status_write_sites.py. That file is a static ratchet --
it can only tell you WHERE an ACTIF status is written, never whether the
write is safe from an existing alarm. This file answers that second
question for every site in ALLOWED_ACTIVE_WRITE_SITES that is reachable
through an automatic (non-disarm_setup) code path, by actually posing the
setup in MANUAL_REVIEW_REQUIRED / ERROR_REQUIRES_MANUAL_REVIEW, driving
the real code path, and checking what survives.

Two outcomes are documented here, not just one:
- STICKY tests: the alarm survives. These pin down protection that already
  exists and must keep existing.
- GAP tests (marked "S5b-3 debt" in their docstring): the alarm does NOT
  survive today. Per audit/ORDRE_S5b2.md section 6, these are NOT fixed by
  this lot -- the assertions describe the actual current (unsafe) behaviour
  so that a real fix later has to consciously touch this file, and so a
  future regression in the opposite direction (an even worse overwrite) is
  also caught.
"""


def _adoption_config(setup_id: str, symbol: str, *, initial_stop: float = 90.0) -> dict:
    return {
        "setup_id": setup_id,
        "symbol": symbol,
        "enabled": True,
        "mode": "paper",
        "setup_type": "position_management",
        "setup_role": "MANAGEMENT_ONLY",
        "direction": "long",
        "entry": {"enabled": False},
        "trailing_stop_loss": {"initial_stop": initial_stop, "current_stop": initial_stop},
        "position_source": {
            "mode": "adopt_existing_ibkr_position",
            "block_if_position_not_found": True,
        },
        "safety": {"pause_if_stop_is_missing": False},
    }


def _upsert_management_setup(
    repository: TradingRepository,
    setup_id: str,
    symbol: str,
    status: str,
) -> None:
    config = _adoption_config(setup_id, symbol)
    repository.upsert_setup(
        SetupRecord(
            setup_id=setup_id,
            symbol=symbol,
            setup_type="position_management",
            enabled=True,
            mode="paper",
            status=status,
            entry_zone="",
            stop_loss=config["trailing_stop_loss"]["initial_stop"],
            risk_amount=None,
            order_status="",
            position_status="",
            last_event="test setup",
            config=config,
        )
    )


class _AdoptionBroker(SimulatedBrokerConnector):
    """Reports exactly one broker position, no open orders -- enough for
    the reconciliation.run() existing-position adoption loop to reach its
    unconditional IN_POSITION write (reconciliation.py:263)."""

    def __init__(self, position: BrokerPosition) -> None:
        super().__init__()
        self._adoption_position = position

    async def positions(self) -> list[BrokerPosition]:
        return [self._adoption_position]

    async def open_orders(self) -> list[BrokerOrderRequest]:
        return []


class PositionAdoptionReviewStickyTests(unittest.IsolatedAsyncioTestCase):
    """reconciliation.py:263 (IN_POSITION), reached from run()'s
    existing-IBKR-position adoption loop. The loop's own gate (:162) is
    `if status in _TERMINAL_SETUP_STATUSES: continue` -- and
    _TERMINAL_SETUP_STATUSES contains ERROR_REQUIRES_MANUAL_REVIEW but NOT
    MANUAL_REVIEW_REQUIRED (app/engine/reconciliation.py:632-638, unchanged
    by S5b-1 -- its own comment at :640-645 says this loop was explicitly
    out of scope for that fix). So the two alarm statuses behave
    differently here, unlike every other site in this file."""

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.setup_id = "ADOPT_2026_001"
        self.symbol = "ADPT"

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    async def _run_adoption(self) -> None:
        broker = _AdoptionBroker(
            BrokerPosition(
                symbol=self.symbol,
                quantity=100,
                average_price=95.0,
                current_price=100.0,
            )
        )
        await broker.connect()
        reconciliation = ReconciliationEngine(self.repository, self.event_store, broker)
        await reconciliation.run()

    def _setup_status(self) -> str:
        return str(self.repository.get_setup(self.setup_id)["status"])

    async def test_manual_review_required_is_overwritten_by_existing_position_adoption(
        self,
    ) -> None:
        """S5b-3 debt (NOT fixed by this lot): a management-only setup left
        in MANUAL_REVIEW_REQUIRED (e.g. because the broker position was
        briefly not found, adoption_blocked_position_not_found) is silently
        adopted into IN_POSITION on the next pass where the position
        reappears -- no event references the alarm that was overwritten."""
        _upsert_management_setup(
            self.repository,
            self.setup_id,
            self.symbol,
            SetupStatus.MANUAL_REVIEW_REQUIRED.value,
        )

        await self._run_adoption()

        self.assertEqual(self._setup_status(), SetupStatus.IN_POSITION.value)

    async def test_error_requires_manual_review_survives_existing_position_adoption(
        self,
    ) -> None:
        """STICKY: ERROR_REQUIRES_MANUAL_REVIEW IS protected here, because
        it is a member of _TERMINAL_SETUP_STATUSES (reconciliation.py:162
        skips the setup entirely)."""
        _upsert_management_setup(
            self.repository,
            self.setup_id,
            self.symbol,
            SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
        )

        await self._run_adoption()

        self.assertEqual(
            self._setup_status(), SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value
        )


class _StopAlwaysRejectedBroker(SimulatedBrokerConnector):
    """BUY (entry) submissions succeed normally; every SELL (stop)
    submission is rejected; cancelling the parent entry is never accepted
    either -- reproducing the real order_manager.py:527-563
    (_cancel_parent_for_failed_protection) branch where `cancelled` stays
    False: the entry order is left SUBMITTED locally while the setup is
    flagged ERROR_REQUIRES_MANUAL_REVIEW. This is the exact "structural
    fragility" audit 35 flagged for order_manager.py:466 as theoretical
    ("aucun site n'écrit actuellement MRR/ERMR pendant que l'entrée est
    encore active") -- S5b-2 confirms it is concretely reachable."""

    async def submit_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        if request.side == "SELL":
            return BrokerOrderResult(
                accepted=False, status="REJECTED", reason="Stop rejected by test broker"
            )
        return await super().submit_order(request)

    async def cancel_order(self, broker_order_id: str) -> BrokerOrderResult:
        return BrokerOrderResult(
            accepted=False,
            status="REJECTED",
            broker_order_id=broker_order_id,
            reason="Broker unreachable, cancel not confirmed",
        )


class _StopRejectedThenRecoveredBroker(_StopAlwaysRejectedBroker):
    """Same as above, but the FIRST stop rejection is the only one: a
    second SELL submission (the repair/retry attempt) is accepted
    normally. Models "the broker had a transient issue that cleared"."""

    def __init__(self) -> None:
        super().__init__()
        self._sell_rejections_remaining = 1

    async def submit_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        if request.side == "SELL" and self._sell_rejections_remaining > 0:
            self._sell_rejections_remaining -= 1
            return BrokerOrderResult(
                accepted=False, status="REJECTED", reason="Stop rejected by test broker"
            )
        return await SimulatedBrokerConnector.submit_order(self, request)


class _UnprotectedEntryFixture(unittest.IsolatedAsyncioTestCase):
    """Shared setup: produce the real, reachable state "entry order
    SUBMITTED locally, setup ERROR_REQUIRES_MANUAL_REVIEW" via
    OrderManager.place_entry_order's actual rejected-stop-and-uncancellable
    branch (not a hand-built fixture) -- then optionally force the setup
    down to MANUAL_REVIEW_REQUIRED to exercise that alarm too."""

    broker_factory = _StopAlwaysRejectedBroker

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.broker = self.broker_factory()
        await self.broker.connect()
        self.manager = OrderManager(
            repository=self.repository, event_store=self.event_store, broker=self.broker
        )
        config = valid_breakout_config()
        self.setup_id = config["setup_id"]
        self.symbol = config["symbol"]
        self.repository.upsert_setup(BreakoutRetestSetup(config).to_record())

        decision = RiskDecision(
            approved=True,
            reason="Risk approved",
            quantity=10,
            entry_price=14.44,
            stop_loss=13.85,
            position_amount_usd=144.40,
            risk_amount_usd=5.90,
        )
        setup = self.repository.get_setup(self.setup_id)
        self.order = await self.manager.place_entry_order(setup, decision)

        # Precondition check: the fixture must actually land where the real
        # incident does, or the rest of the test proves nothing.
        assert self.repository.get_order(self.order.id)["status"] == "SUBMITTED", (
            "fixture broken: entry order must survive as SUBMITTED"
        )
        assert self._setup_status() == SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value, (
            "fixture broken: setup must land in ERROR_REQUIRES_MANUAL_REVIEW"
        )

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _setup_status(self) -> str:
        return str(self.repository.get_setup(self.setup_id)["status"])

    def _force_manual_review_required(self) -> None:
        self.repository.update_setup_status(
            self.setup_id,
            SetupStatus.MANUAL_REVIEW_REQUIRED.value,
            "test setup: forced MANUAL_REVIEW_REQUIRED for coverage symmetry",
        )


class AttachMissingStopReviewStickyTests(_UnprotectedEntryFixture):
    """order_manager.py:466 (ENTRY_ORDER_PLACED), attach_missing_stop().
    Its only guards are on the ORDER (side BUY, status CREATED/SUBMITTED,
    no existing active stop) -- nothing reads setup_status. Reachable via
    the real, human-triggered API route
    POST /api/orders/{order_id}/attach-stop (app/api/routes_orders.py:78-85)."""

    async def test_error_requires_manual_review_is_overwritten_by_attach_missing_stop(
        self,
    ) -> None:
        """S5b-3 debt (NOT fixed by this lot)."""
        recovered_broker = SimulatedBrokerConnector()
        await recovered_broker.connect()
        recovery_manager = OrderManager(
            repository=self.repository, event_store=self.event_store, broker=recovered_broker
        )

        await recovery_manager.attach_missing_stop(self.order.id)

        self.assertEqual(self._setup_status(), SetupStatus.ENTRY_ORDER_PLACED.value)

    async def test_manual_review_required_is_overwritten_by_attach_missing_stop(self) -> None:
        """S5b-3 debt (NOT fixed by this lot)."""
        self._force_manual_review_required()
        recovered_broker = SimulatedBrokerConnector()
        await recovered_broker.connect()
        recovery_manager = OrderManager(
            repository=self.repository, event_store=self.event_store, broker=recovered_broker
        )

        await recovery_manager.attach_missing_stop(self.order.id)

        self.assertEqual(self._setup_status(), SetupStatus.ENTRY_ORDER_PLACED.value)


class PostFillProgressionDirectWriteReviewStickyTests(unittest.TestCase):
    """post_fill_progression.py:64 (ENTRY_FILLED) and :92 (IN_POSITION),
    tested directly against PostFillProgression -- same style as the
    existing tests/test_post_fill_progression.py, and the same
    "instantiate the real component directly" approach as
    SubmittedBranchReviewLockTests. Neither write reads setup_status
    anywhere in its own body.

    Why direct, not through the full simulate_fill_order() cascade: that
    full call chain (fill_executor.py:40-99, reached via the human-
    triggered POST /api/orders/{order_id}/simulate-fill route, itself only
    gated on `order["status"] == SUBMITTED`, never on setup_status) does
    reach both these writes for real -- but in the SAME call it always
    proceeds past them to a THIRD write (either mark_in_position's
    IN_POSITION if the retry stop succeeds, see
    SimulatedFillFullCascadeReviewStickyTests below, or place_stop_order's
    own ERROR_REQUIRES_MANUAL_REVIEW if the retry stop is rejected again),
    which masks whichever alarm value was current right after THIS site's
    write. Testing the component directly is the only way to observe each
    site's own effect in isolation rather than whatever the next write in
    the chain leaves behind."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.progression = PostFillProgression(self.repository, self.event_store)
        config = valid_breakout_config()
        self.setup_id = config["setup_id"]
        self.symbol = config["symbol"]
        self.repository.upsert_setup(BreakoutRetestSetup(config).to_record())
        self.order = OrderRecord(
            id="ord-fill-1",
            setup_id=self.setup_id,
            symbol=self.symbol,
            side="BUY",
            order_type=OrderType.STP_LMT.value,
            quantity=10,
            status=OrderStatus.SUBMITTED.value,
            broker_order_id="broker-1",
        )
        self.repository.upsert_order(self.order)

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _setup_status(self) -> str:
        return str(self.repository.get_setup(self.setup_id)["status"])

    def _pose(self, status: str) -> None:
        self.repository.update_setup_status(self.setup_id, status, "test setup")

    def test_error_requires_manual_review_is_overwritten_by_record_fill(self) -> None:
        """S5b-3 debt (NOT fixed by this lot)."""
        self._pose(SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value)

        self.progression.record_fill(
            order_id=self.order.id,
            setup_id=self.setup_id,
            quantity=10,
            fill_price=15.00,
            symbol=self.symbol,
        )

        self.assertEqual(self._setup_status(), SetupStatus.ENTRY_FILLED.value)

    def test_manual_review_required_is_overwritten_by_record_fill(self) -> None:
        """S5b-3 debt (NOT fixed by this lot)."""
        self._pose(SetupStatus.MANUAL_REVIEW_REQUIRED.value)

        self.progression.record_fill(
            order_id=self.order.id,
            setup_id=self.setup_id,
            quantity=10,
            fill_price=15.00,
            symbol=self.symbol,
        )

        self.assertEqual(self._setup_status(), SetupStatus.ENTRY_FILLED.value)

    def test_error_requires_manual_review_is_overwritten_by_mark_in_position(self) -> None:
        """S5b-3 debt (NOT fixed by this lot)."""
        self._pose(SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value)

        self.progression.mark_in_position(self.setup_id, protection_verified=True)

        self.assertEqual(self._setup_status(), SetupStatus.IN_POSITION.value)

    def test_manual_review_required_is_overwritten_by_mark_in_position(self) -> None:
        """S5b-3 debt (NOT fixed by this lot)."""
        self._pose(SetupStatus.MANUAL_REVIEW_REQUIRED.value)

        self.progression.mark_in_position(self.setup_id, protection_verified=True)

        self.assertEqual(self._setup_status(), SetupStatus.IN_POSITION.value)


class PlaceStopOrderDirectWriteReviewStickyTests(unittest.IsolatedAsyncioTestCase):
    """order_manager.py:374 (STOP_ORDER_PLACED), place_stop_order() called
    with its default update_setup_status=True -- exactly the call
    fill_executor.py:86 makes on the simulated-fill retry path. No
    setup_status read anywhere in place_stop_order's own body. Same
    "test the component directly, avoid a downstream write masking this
    one" reasoning as the class above."""

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.broker = SimulatedBrokerConnector()
        await self.broker.connect()
        self.manager = OrderManager(
            repository=self.repository, event_store=self.event_store, broker=self.broker
        )
        config = valid_breakout_config()
        self.setup_id = config["setup_id"]
        self.repository.upsert_setup(BreakoutRetestSetup(config).to_record())

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _setup_status(self) -> str:
        return str(self.repository.get_setup(self.setup_id)["status"])

    async def test_error_requires_manual_review_is_overwritten_by_place_stop_order(
        self,
    ) -> None:
        """S5b-3 debt (NOT fixed by this lot)."""
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value, "test setup"
        )
        setup = self.repository.get_setup(self.setup_id)

        await self.manager.place_stop_order(setup, quantity=10, stop_loss=13.85)

        self.assertEqual(self._setup_status(), SetupStatus.STOP_ORDER_PLACED.value)

    async def test_manual_review_required_is_overwritten_by_place_stop_order(self) -> None:
        """S5b-3 debt (NOT fixed by this lot)."""
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.MANUAL_REVIEW_REQUIRED.value, "test setup"
        )
        setup = self.repository.get_setup(self.setup_id)

        await self.manager.place_stop_order(setup, quantity=10, stop_loss=13.85)

        self.assertEqual(self._setup_status(), SetupStatus.STOP_ORDER_PLACED.value)


class SimulatedFillFullCascadeReviewStickyTests(_UnprotectedEntryFixture):
    """order_manager.py:374 (STOP_ORDER_PLACED) and post_fill_progression.
    py:92 (IN_POSITION), both reached in the SAME simulate_fill_order()
    call as the class above, once the retry stop submission succeeds
    instead of being rejected again (fill_executor.py:84-99): the call
    writes ENTRY_FILLED, then STOP_ORDER_PLACED, then immediately
    IN_POSITION -- all three overwriting whatever alarm was already
    there, none of it gated on setup_status, none of it logged as an
    overwrite. Only the final status (IN_POSITION) is observable at the
    end of the call; the intermediate ENTRY_FILLED/STOP_ORDER_PLACED
    values are real but transient."""

    broker_factory = _StopRejectedThenRecoveredBroker

    async def test_error_requires_manual_review_is_overwritten_by_full_cascade(self) -> None:
        """S5b-3 debt (NOT fixed by this lot)."""
        await self.manager.simulate_fill_order(self.order.id, fill_price=15.00)

        self.assertEqual(self._setup_status(), SetupStatus.IN_POSITION.value)

    async def test_manual_review_required_is_overwritten_by_full_cascade(self) -> None:
        """S5b-3 debt (NOT fixed by this lot)."""
        self._force_manual_review_required()

        await self.manager.simulate_fill_order(self.order.id, fill_price=15.00)

        self.assertEqual(self._setup_status(), SetupStatus.IN_POSITION.value)


class _ExplodingOrderManager:
    """Stand-in for OrderManager that fails the test loudly if
    place_entry_order is ever reached -- used to prove the entry gate
    short-circuits BEFORE touching order placement, not just that the
    final status happens to be unchanged."""

    async def place_entry_order(self, *args, **kwargs):
        raise AssertionError(
            "place_entry_order must not be reached when current_status is "
            "not in ENTRY_ELIGIBLE_STATUSES"
        )


class PlaceEntryOrderUnreachableFromAlarmTests(unittest.IsolatedAsyncioTestCase):
    """order_manager.py:180 (ENTRY_ORDER_PLACED), place_entry_order()'s own
    bracket-submission write. place_entry_order() itself has NO setup
    status guard -- but per audit 35 (re-verified here for S5b-2), its
    only two callers do:
    - EntryOrderExecutor.execute_entry_ready() (the automatic path) gates
      on `current_status not in ENTRY_ELIGIBLE_STATUSES` BEFORE calling it
      (entry_order_executor.py:56-57), and ENTRY_ELIGIBLE_STATUSES
      (app/models.py:71-79) does not include MANUAL_REVIEW_REQUIRED or
      ERROR_REQUIRES_MANUAL_REVIEW.
    - ManualOrderService._submit_buy() (the manual path) always builds a
      brand-new setup_id via new_id("man") (manual_order_service.py:121)
      for its synthetic setup -- it can never target an existing setup
      that is already in an alarm status.
    So, unlike the other three literal sites in order_manager.py, this one
    is genuinely unreachable from an alarm today. This test proves the
    automatic-path half of that claim directly, rather than just citing
    the source."""

    async def test_entry_ready_signal_is_gated_before_place_entry_order_for_manual_review(
        self,
    ) -> None:
        executor = EntryOrderExecutor(
            repository=None,
            event_store=None,
            risk_engine=None,
            order_manager=_ExplodingOrderManager(),
        )

        blocked = await executor.execute_entry_ready(
            {"setup_id": "X", "symbol": "TEST"},
            None,
            SetupStatus.MANUAL_REVIEW_REQUIRED,
        )

        self.assertTrue(blocked)

    async def test_entry_ready_signal_is_gated_before_place_entry_order_for_error_review(
        self,
    ) -> None:
        executor = EntryOrderExecutor(
            repository=None,
            event_store=None,
            risk_engine=None,
            order_manager=_ExplodingOrderManager(),
        )

        blocked = await executor.execute_entry_ready(
            {"setup_id": "X", "symbol": "TEST"},
            None,
            SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW,
        )

        self.assertTrue(blocked)


class SubmittedBranchStickyReferenceTest(unittest.TestCase):
    """The SUBMITTED branch of reconciliation.py's
    _update_setup_after_reconciled_order (the site S5b-1 fixed) writes its
    target status through a *variable*, not a literal -- it is invisible
    to tests/test_active_status_write_sites.py by design (see that file's
    module docstring, audit 39). It is NOT re-tested exhaustively here to
    avoid duplication (audit/ORDRE_S5b2.md section 5.3): the exhaustive
    coverage (both alarm statuses, plus the non-regression case for a
    non-alarm terminal status) already lives in
    tests/test_reconciliation.py::SubmittedBranchReviewLockTests
    (S5b-1). This is one direct proof kept in *this* file so that
    "at least one test here proves the sticky behaviour on a real path"
    does not depend on cross-file trust alone."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.reconciliation = ReconciliationEngine(
            self.repository, self.event_store, SimulatedBrokerConnector()
        )
        config = valid_breakout_config()
        self.setup_id = config["setup_id"]
        self.symbol = config["symbol"]
        self.repository.upsert_setup(BreakoutRetestSetup(config).to_record())

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def test_manual_review_required_survives_stop_order_reported_submitted(self) -> None:
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.MANUAL_REVIEW_REQUIRED.value, "test setup"
        )

        self.reconciliation._update_setup_after_reconciled_order(
            {
                "id": "ord_1",
                "setup_id": self.setup_id,
                "symbol": self.symbol,
                "side": "SELL",
                "quantity": 10,
                "broker_order_id": "9001",
            },
            "SUBMITTED",
        )

        setup = self.repository.get_setup(self.setup_id)
        self.assertEqual(setup["status"], SetupStatus.MANUAL_REVIEW_REQUIRED.value)


class ReconciliationFilledBranchUnreachableFromAlarmTests(unittest.TestCase):
    """reconciliation.py:507 (ENTRY_FILLED, the "fill price/quantity
    unavailable" fallback inside the FILLED branch) and, downstream of it
    in the same branch, the record_fill()/mark_in_position() calls at
    :528/:534. All of it sits behind one gate at the top of the FILLED
    branch (reconciliation.py:488-491): `if setup_status not in
    {ENTRY_ORDER_PLACED, ENTRY_PARTIALLY_FILLED}: ... return` -- neither
    alarm status is in that set, so a FILLED order can never reach :507 (or
    :528/:534) while the setup is in MANUAL_REVIEW_REQUIRED or
    ERROR_REQUIRES_MANUAL_REVIEW. Unlike the simulated-fill path
    (fill_executor.py, tested above), this is a real, working guard, not a
    gap."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.reconciliation = ReconciliationEngine(
            self.repository, self.event_store, SimulatedBrokerConnector()
        )
        config = valid_breakout_config()
        self.setup_id = config["setup_id"]
        self.symbol = config["symbol"]
        self.repository.upsert_setup(BreakoutRetestSetup(config).to_record())

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _setup_status(self) -> str:
        return str(self.repository.get_setup(self.setup_id)["status"])

    def _assert_filled_order_is_ignored(self, alarm_status: str) -> None:
        self.repository.update_setup_status(self.setup_id, alarm_status, "test setup")

        self.reconciliation._update_setup_after_reconciled_order(
            {
                "id": "ord_1",
                "setup_id": self.setup_id,
                "symbol": self.symbol,
                "side": "BUY",
                "quantity": 10,
                "broker_order_id": "9001",
            },
            OrderStatus.FILLED.value,
        )

        self.assertEqual(self._setup_status(), alarm_status)
        self.assertEqual(self.repository.list_positions(), [])

    def test_manual_review_required_blocks_filled_branch(self) -> None:
        self._assert_filled_order_is_ignored(SetupStatus.MANUAL_REVIEW_REQUIRED.value)

    def test_error_requires_manual_review_blocks_filled_branch(self) -> None:
        self._assert_filled_order_is_ignored(SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value)


class DisarmSetupLegitimateExitTests(unittest.TestCase):
    """disarm_setup (setup_engine.py:273-281) CAN and does exit an alarm
    status -- this is intentional, a human explicitly asking to stand a
    setup down (audit 36), not an automatic overwrite. Documented here so
    it reads as a deliberate design choice next to the GAP tests above,
    not an oversight."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.setups_folder = Path(self.tmp.name) / "setups"
        self.setups_folder.mkdir()
        self.setup_engine = SetupEngine(self.repository, self.event_store, self.setups_folder)
        config = valid_breakout_config()
        self.setup_id = config["setup_id"]
        self.repository.upsert_setup(BreakoutRetestSetup(config).to_record())

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _setup_status(self) -> str:
        return str(self.repository.get_setup(self.setup_id)["status"])

    def test_disarm_setup_exits_manual_review_required(self) -> None:
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.MANUAL_REVIEW_REQUIRED.value, "test setup"
        )

        self.setup_engine.disarm_setup(self.setup_id)

        self.assertEqual(self._setup_status(), SetupStatus.DISABLED.value)

    def test_disarm_setup_exits_error_requires_manual_review(self) -> None:
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value, "test setup"
        )

        self.setup_engine.disarm_setup(self.setup_id)

        self.assertEqual(self._setup_status(), SetupStatus.DISABLED.value)


if __name__ == "__main__":
    unittest.main()
