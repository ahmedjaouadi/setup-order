from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.broker.ib_models import BrokerOrderRequest, BrokerOrderResult, BrokerPosition
from app.broker.tws_connector import SimulatedBrokerConnector
from app.engine.entry_order_executor import EntryOrderExecutor
from app.engine.order_manager import OrderManager
from app.engine.post_fill_progression import PostFillProgression
from app.engine.reconciliation import ADOPTION_STOP_NOT_FOUND_MESSAGE, ReconciliationEngine
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

S5b-3a (audit 44) added a central guard in
TradingRepository.update_setup_status (app/storage/repositories.py) that
blocks any write of an ACTIVE status over an existing review alarm unless
the caller passes allow_from_review=True. The tests that used to be marked
"S5b-3 debt" / GAP here (documenting that the alarm did NOT survive) are
inverted in this lot to assert that it now does survive -- the guard is
central, so it protects every one of these call sites without any of them
being individually modified. The one exception left as debt is
attach_missing_stop (order_manager.py:466): S5b-3a does not decide whether
it should receive allow_from_review=True as a legitimate repair path
(S5b-3b will); in the meantime its write is also blocked, as an inherent
side effect of the guard being central rather than call-site-specific --
its tests are inverted here too, for the same reason as the others, not
because attach_missing_stop itself was touched.
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
    *,
    last_event: str = "test setup",
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
            last_event=last_event,
            config=config,
        )
    )


class _AdoptionBroker(SimulatedBrokerConnector):
    """Reports exactly one broker position, no open orders -- enough for
    the reconciliation.run() existing-position adoption loop to reach its
    IN_POSITION write (reconciliation.py:289), with no fresh stop found this
    cycle (root C, audit 74: this models "alarm survives, no repair yet")."""

    def __init__(self, position: BrokerPosition) -> None:
        super().__init__()
        self._adoption_position = position

    async def positions(self) -> list[BrokerPosition]:
        return [self._adoption_position]

    async def open_orders(self) -> list[BrokerOrderRequest]:
        return []


class _AdoptionBrokerWithRestoredStop(_AdoptionBroker):
    """Same one broker position as _AdoptionBroker, but this cycle also
    reports one active protective SELL stop order for the symbol -- models
    the human having reposed the stop in TWS after the adoption loop
    alarmed for cause #3 (audit 73/74, root C: S59.3a)."""

    def __init__(
        self, position: BrokerPosition, *, stop_price: float, broker_order_id: str
    ) -> None:
        super().__init__(position)
        self._stop_price = stop_price
        self._stop_broker_order_id = broker_order_id

    async def open_orders(self) -> list[BrokerOrderRequest]:
        return [
            BrokerOrderRequest(
                client_order_id="restored-stop-1",
                setup_id="",
                symbol=self._adoption_position.symbol,
                side="SELL",
                order_type="STP",
                quantity=int(self._adoption_position.quantity),
                stop_price=self._stop_price,
                broker_order_id=self._stop_broker_order_id,
            )
        ]


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

    async def _run_adoption(self, broker: _AdoptionBroker | None = None) -> None:
        broker = broker or _AdoptionBroker(
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

    def _setup_last_event(self) -> str:
        return str(self.repository.get_setup(self.setup_id)["last_event"])

    async def test_manual_review_required_survives_existing_position_adoption_without_restored_stop(
        self,
    ) -> None:
        """S5b-3a (audit 44) + root C (audit 73/74, cause #3, S59.3a): the
        central guard blocks this write, and C-2's new clearing condition
        does not fire either because _AdoptionBroker reports no open orders
        this cycle -- _matching_stop_order finds nothing, so there is no
        fresh proof the stop was actually repaired. A management-only setup
        alarmed for cause #3 ("Broker stop order not found") stays alarmed
        until the stop is genuinely seen again at the broker."""
        _upsert_management_setup(
            self.repository,
            self.setup_id,
            self.symbol,
            SetupStatus.MANUAL_REVIEW_REQUIRED.value,
            last_event=ADOPTION_STOP_NOT_FOUND_MESSAGE,
        )

        await self._run_adoption()

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)

    async def test_manual_review_required_is_cleared_when_stop_reappears(self) -> None:
        """C-2 (audit 74, root C, S59.3a): the real, atteignable case. A
        management-only setup alarmed for cause #3 ("Broker stop order not
        found") is adopted into IN_POSITION once the human reposes the stop
        in TWS and it shows up as an active SELL stop order in the broker's
        open orders this cycle -- the inverse of the test just above. This
        is the one new site (besides attach_missing_stop) allowed to pass
        allow_from_review=True to update_setup_status."""
        _upsert_management_setup(
            self.repository,
            self.setup_id,
            self.symbol,
            SetupStatus.MANUAL_REVIEW_REQUIRED.value,
            last_event=ADOPTION_STOP_NOT_FOUND_MESSAGE,
        )
        broker = _AdoptionBrokerWithRestoredStop(
            BrokerPosition(
                symbol=self.symbol,
                quantity=100,
                average_price=95.0,
                current_price=100.0,
            ),
            stop_price=90.0,
            broker_order_id="restored-9001",
        )

        await self._run_adoption(broker)

        self.assertEqual(self._setup_status(), SetupStatus.IN_POSITION.value)
        cleared_events = self.repository.list_events(
            event_type="adoption_review_cleared_stop_restored"
        )
        self.assertEqual(len(cleared_events), 1)
        self.assertEqual(cleared_events[0]["setup_id"], self.setup_id)
        self.assertEqual(cleared_events[0]["symbol"], self.symbol)
        self.assertEqual(
            cleared_events[0]["data"]["stop_order_id"], "restored-9001"
        )

    async def test_other_cause_alarm_survives_even_with_position_and_stop(self) -> None:
        """THE CRITICAL TEST (audit 73 Q2 / ORDRE_C2 section 5.2): a setup
        alarmed for a DIFFERENT cause -- not cause #3 -- must never be
        cleared by this path, even though the geometry (open position, active
        stop present) looks identical to the real case above. Only the
        last_event filter tells the two apart; without it, this test would
        wrongly clear an alarm whose cause never went away."""
        _upsert_management_setup(
            self.repository,
            self.setup_id,
            self.symbol,
            SetupStatus.MANUAL_REVIEW_REQUIRED.value,
            last_event="Sell filled but broker position quantity disagrees",
        )
        broker = _AdoptionBrokerWithRestoredStop(
            BrokerPosition(
                symbol=self.symbol,
                quantity=100,
                average_price=95.0,
                current_price=100.0,
            ),
            stop_price=90.0,
            broker_order_id="restored-9002",
        )

        await self._run_adoption(broker)

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)
        self.assertEqual(
            self._setup_last_event(),
            "Sell filled but broker position quantity disagrees",
        )
        self.assertEqual(
            self.repository.list_events(
                event_type="adoption_review_cleared_stop_restored"
            ),
            [],
        )

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


class _StopImmediatelyCancelledBroker(SimulatedBrokerConnector):
    """Every SELL (stop) submission is *accepted* by the broker but reported
    back with status CANCELLED, not SUBMITTED -- an edge case
    place_stop_order's own accepted/REJECTED/ERROR check (order_manager.py:
    347-350) does not treat as a failure, so before S5b-3b attach_missing_stop
    would have written ENTRY_ORDER_PLACED here too (audit 45's documented
    blind spot: "not in {REJECTED, ERROR}" also matches CANCELLED and
    FILLED, neither of which is an active stop)."""

    async def submit_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        if request.side == "SELL":
            return BrokerOrderResult(
                accepted=True,
                status="CANCELLED",
                broker_order_id="cancelled-1",
                reason="Cancelled immediately by test broker",
            )
        return await super().submit_order(request)


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
    POST /api/orders/{order_id}/attach-stop (app/api/routes_orders.py:78-85).

    S5b-3b (audit 46): attach_missing_stop now passes allow_from_review=True
    to update_setup_status, but ONLY in the branch where stop_order.status
    proves the repaired stop is actually active at the broker (CREATED or
    SUBMITTED). This is the guard's one documented legitimate exception, not
    a bypass -- see order_manager.py:454-476."""

    async def test_error_requires_manual_review_is_cleared_by_active_stop_repair(
        self,
    ) -> None:
        """S5b-3b (audit 46): the repair's own stop submission succeeds
        (SimulatedBrokerConnector reports SUBMITTED) -- stop_is_active is
        True, so the review alarm is legitimately cleared via
        allow_from_review=True."""
        recovered_broker = SimulatedBrokerConnector()
        await recovered_broker.connect()
        recovery_manager = OrderManager(
            repository=self.repository, event_store=self.event_store, broker=recovered_broker
        )

        stop_order = await recovery_manager.attach_missing_stop(self.order.id)

        self.assertEqual(stop_order.status, OrderStatus.SUBMITTED.value)
        self.assertEqual(self._setup_status(), SetupStatus.ENTRY_ORDER_PLACED.value)

    async def test_manual_review_required_is_cleared_by_active_stop_repair(self) -> None:
        """S5b-3b (audit 46): same as above, from MANUAL_REVIEW_REQUIRED
        instead of ERROR_REQUIRES_MANUAL_REVIEW."""
        self._force_manual_review_required()
        recovered_broker = SimulatedBrokerConnector()
        await recovered_broker.connect()
        recovery_manager = OrderManager(
            repository=self.repository, event_store=self.event_store, broker=recovered_broker
        )

        stop_order = await recovery_manager.attach_missing_stop(self.order.id)

        self.assertEqual(stop_order.status, OrderStatus.SUBMITTED.value)
        self.assertEqual(self._setup_status(), SetupStatus.ENTRY_ORDER_PLACED.value)

    async def test_rejected_repair_attempt_preserves_alarm(self) -> None:
        """Non-regression: when the repair's OWN stop submission is
        REJECTED, stop_is_active is False -- no active status is written,
        the setup is routed to _cancel_parent_for_failed_protection exactly
        as before this lot. Confirms the positive stop_is_active condition
        did not change behaviour for the failure case it replaces."""
        rejecting_broker = _StopAlwaysRejectedBroker()
        await rejecting_broker.connect()
        recovery_manager = OrderManager(
            repository=self.repository, event_store=self.event_store, broker=rejecting_broker
        )

        stop_order = await recovery_manager.attach_missing_stop(self.order.id)

        self.assertEqual(stop_order.status, OrderStatus.REJECTED.value)
        self.assertEqual(
            self._setup_status(), SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value
        )

    async def test_cancelled_repair_attempt_does_not_clear_alarm(self) -> None:
        """S5b-3b (audit 46) tightening: stop_order.status == CANCELLED is
        NOT in {CREATED, SUBMITTED}, so stop_is_active is False even though
        CANCELLED was not in the old {REJECTED, ERROR} failure set. Before
        this lot's positive condition, this broker response would have
        fallen into the `else` branch and written ENTRY_ORDER_PLACED over
        the alarm despite the stop not actually being active -- the blind
        spot audit 45 flagged. Proves it is now closed: the setup is routed
        to _cancel_parent_for_failed_protection exactly like a REJECTED
        repair (that helper always writes ERROR_REQUIRES_MANUAL_REVIEW,
        unconditionally, regardless of which alarm was there before --
        unchanged, out-of-scope behaviour, see ORDRE_S5b3b.md section 2).
        What matters here is what it does NOT write: ENTRY_ORDER_PLACED."""
        self._force_manual_review_required()
        cancelling_broker = _StopImmediatelyCancelledBroker()
        await cancelling_broker.connect()
        recovery_manager = OrderManager(
            repository=self.repository, event_store=self.event_store, broker=cancelling_broker
        )

        stop_order = await recovery_manager.attach_missing_stop(self.order.id)

        self.assertEqual(stop_order.status, OrderStatus.CANCELLED.value)
        self.assertNotEqual(self._setup_status(), SetupStatus.ENTRY_ORDER_PLACED.value)
        self.assertEqual(
            self._setup_status(), SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value
        )


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

    def test_error_requires_manual_review_survives_record_fill(self) -> None:
        """S5b-3a (audit 44): the central guard now blocks this write."""
        self._pose(SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value)

        self.progression.record_fill(
            order_id=self.order.id,
            setup_id=self.setup_id,
            quantity=10,
            fill_price=15.00,
            symbol=self.symbol,
        )

        self.assertEqual(
            self._setup_status(), SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value
        )

    def test_manual_review_required_survives_record_fill(self) -> None:
        """S5b-3a (audit 44): the central guard now blocks this write."""
        self._pose(SetupStatus.MANUAL_REVIEW_REQUIRED.value)

        self.progression.record_fill(
            order_id=self.order.id,
            setup_id=self.setup_id,
            quantity=10,
            fill_price=15.00,
            symbol=self.symbol,
        )

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)

    def test_error_requires_manual_review_survives_mark_in_position(self) -> None:
        """S5b-3a (audit 44): the central guard now blocks this write."""
        self._pose(SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value)

        self.progression.mark_in_position(self.setup_id, protection_verified=True)

        self.assertEqual(
            self._setup_status(), SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value
        )

    def test_manual_review_required_survives_mark_in_position(self) -> None:
        """S5b-3a (audit 44): the central guard now blocks this write."""
        self._pose(SetupStatus.MANUAL_REVIEW_REQUIRED.value)

        self.progression.mark_in_position(self.setup_id, protection_verified=True)

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)


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

    async def test_error_requires_manual_review_survives_place_stop_order(
        self,
    ) -> None:
        """S5b-3a (audit 44): the central guard now blocks this write."""
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value, "test setup"
        )
        setup = self.repository.get_setup(self.setup_id)

        await self.manager.place_stop_order(setup, quantity=10, stop_loss=13.85)

        self.assertEqual(
            self._setup_status(), SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value
        )

    async def test_manual_review_required_survives_place_stop_order(self) -> None:
        """S5b-3a (audit 44): the central guard now blocks this write."""
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.MANUAL_REVIEW_REQUIRED.value, "test setup"
        )
        setup = self.repository.get_setup(self.setup_id)

        await self.manager.place_stop_order(setup, quantity=10, stop_loss=13.85)

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)


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

    async def test_error_requires_manual_review_survives_full_cascade(self) -> None:
        """S5b-3a (audit 44): the central guard blocks all three writes in
        this cascade (record_fill's ENTRY_FILLED, place_stop_order's
        STOP_ORDER_PLACED, mark_in_position's IN_POSITION), so the alarm
        set before the fill survives the whole call."""
        await self.manager.simulate_fill_order(self.order.id, fill_price=15.00)

        self.assertEqual(
            self._setup_status(), SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value
        )

    async def test_manual_review_required_survives_full_cascade(self) -> None:
        """S5b-3a (audit 44): same as above, forced to MANUAL_REVIEW_REQUIRED
        instead of the fixture's default ERROR_REQUIRES_MANUAL_REVIEW."""
        self._force_manual_review_required()

        await self.manager.simulate_fill_order(self.order.id, fill_price=15.00)

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)


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
    _update_setup_after_reconciled_order (the review-lock the site S5b-1
    fixed) never writes anything when setup_status is a review alarm -- it
    only records reconciliation_skipped_review_locked and returns; the
    computed `target_status` variable is invisible to
    tests/test_active_status_write_sites.py by design (see that file's
    module docstring, audit 39) and, since A6-SEC (audit 51), is no longer
    written anywhere in this branch even for a terminal setup -- a still-open
    broker order for a terminal setup now writes a literal
    MANUAL_REVIEW_REQUIRED instead of resurrecting an ACTIF status. It is
    NOT re-tested exhaustively here to avoid duplication
    (audit/ORDRE_S5b2.md section 5.3): the exhaustive coverage (both alarm
    statuses, plus the terminal-status-with-open-order cases) already lives
    in tests/test_reconciliation.py::SubmittedBranchReviewLockTests
    (S5b-1 + A6-SEC). This is one direct proof kept in *this* file so that
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


class CentralReviewGuardDirectTests(unittest.TestCase):
    """S5b-3a (audit 44): direct coverage of the guard itself inside
    TradingRepository.update_setup_status, independent of any particular
    engine call site -- proves the guard's own contract (block, preserve,
    trace; let non-alarm writes through unchanged; let allow_from_review
    escape the block) rather than relying only on the behavioural proofs
    through real engine paths above."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        config = valid_breakout_config()
        self.setup_id = config["setup_id"]
        self.repository.upsert_setup(BreakoutRetestSetup(config).to_record())

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _setup_status(self) -> str:
        return str(self.repository.get_setup(self.setup_id)["status"])

    def test_guard_blocks_manual_review_required_to_in_position_and_logs(self) -> None:
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.MANUAL_REVIEW_REQUIRED.value, "test setup"
        )

        with self.assertLogs("app.storage.repositories", level="WARNING") as logs:
            self.repository.update_setup_status(
                self.setup_id, SetupStatus.IN_POSITION.value, "should be blocked"
            )

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)
        self.assertTrue(any("Blocked write" in message for message in logs.output))

    def test_guard_blocks_error_requires_manual_review_to_in_position_and_logs(self) -> None:
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value, "test setup"
        )

        with self.assertLogs("app.storage.repositories", level="WARNING") as logs:
            self.repository.update_setup_status(
                self.setup_id, SetupStatus.IN_POSITION.value, "should be blocked"
            )

        self.assertEqual(
            self._setup_status(), SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value
        )
        self.assertTrue(any("Blocked write" in message for message in logs.output))

    def test_guard_blocks_manual_review_required_to_closed_and_logs(self) -> None:
        """A-1b (audit 64): CLOSED joined _ACTIVE_STATUSES so a position
        close cannot silently clear a review alarm -- "l'alarme prime"
        (audit 61 P3)."""
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.MANUAL_REVIEW_REQUIRED.value, "test setup"
        )

        with self.assertLogs("app.storage.repositories", level="WARNING") as logs:
            self.repository.update_setup_status(
                self.setup_id, SetupStatus.CLOSED.value, "should be blocked"
            )

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)
        self.assertTrue(any("Blocked write" in message for message in logs.output))

    def test_guard_blocks_error_requires_manual_review_to_closed_and_logs(self) -> None:
        """A-1b (audit 64): same as above, from ERROR_REQUIRES_MANUAL_REVIEW."""
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value, "test setup"
        )

        with self.assertLogs("app.storage.repositories", level="WARNING") as logs:
            self.repository.update_setup_status(
                self.setup_id, SetupStatus.CLOSED.value, "should be blocked"
            )

        self.assertEqual(
            self._setup_status(), SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value
        )
        self.assertTrue(any("Blocked write" in message for message in logs.output))

    def test_non_alarm_active_write_is_unaffected(self) -> None:
        """Non-regression: a non-alarm source status writing an ACTIF target
        must behave exactly as before the guard was added."""
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.ENTRY_ORDER_PLACED.value, "test setup"
        )

        self.repository.update_setup_status(
            self.setup_id, SetupStatus.IN_POSITION.value, "normal progression"
        )

        self.assertEqual(self._setup_status(), SetupStatus.IN_POSITION.value)

    def test_non_alarm_closed_write_is_unaffected(self) -> None:
        """A-1b (audit 64): non-regression companion to the above for CLOSED
        -- a normal close from a non-alarm status must still write CLOSED."""
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.ENTRY_ORDER_PLACED.value, "test setup"
        )

        self.repository.update_setup_status(
            self.setup_id, SetupStatus.CLOSED.value, "normal close"
        )

        self.assertEqual(self._setup_status(), SetupStatus.CLOSED.value)

    def test_allow_from_review_escapes_the_block(self) -> None:
        """Proves the escape hatch works, even though no caller uses it yet
        in this lot (attach_missing_stop's adoption of it is S5b-3b)."""
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.MANUAL_REVIEW_REQUIRED.value, "test setup"
        )

        self.repository.update_setup_status(
            self.setup_id,
            SetupStatus.IN_POSITION.value,
            "explicitly allowed",
            allow_from_review=True,
        )

        self.assertEqual(self._setup_status(), SetupStatus.IN_POSITION.value)


class UpsertSetupConfigSaveRatchetTests(unittest.TestCase):
    """S5b-3a (audit 44): freezes the audit 43 Q1 finding that upsert_setup
    is unreachable from an alarm to an active status, so a future change to
    SetupEngine._status_after_config_save that starts computing a fresh
    status (instead of failing to DISABLED for a new setup or recopying the
    existing status for a known one) cannot silently reopen this path
    without breaking a test. This is a ratchet, not a guard: upsert_setup
    itself is not touched (out of scope, see ORDRE_S5b3a.md section 1)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)
        self.event_store = EventStore(self.repository)
        self.setups_folder = Path(self.tmp.name) / "setups"
        self.setups_folder.mkdir()
        self.setup_engine = SetupEngine(self.repository, self.event_store, self.setups_folder)
        self.config = valid_breakout_config()
        self.setup_id = self.config["setup_id"]
        self.repository.upsert_setup(BreakoutRetestSetup(self.config).to_record())

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def _setup_status(self) -> str:
        return str(self.repository.get_setup(self.setup_id)["status"])

    def test_config_save_does_not_overwrite_manual_review_required(self) -> None:
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.MANUAL_REVIEW_REQUIRED.value, "test setup"
        )
        edited_config = dict(self.config)
        edited_config["risk"] = dict(edited_config["risk"], max_risk_usd=20)

        validation = self.setup_engine.create_or_update_from_config(edited_config)

        self.assertTrue(validation.valid, validation.errors)
        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)


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
