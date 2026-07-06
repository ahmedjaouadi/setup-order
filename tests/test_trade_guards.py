from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.engine.trade_guards import (
    REASON_CONFLICT_WITH_OPEN_POSITION,
    REASON_COOLDOWN_AFTER_STOP,
    REASON_DAILY_LOSS_LIMIT,
    REASON_EXPOSURE_LIMIT,
    REASON_HALT_ACTIVE,
    REASON_MAX_TRADES_REACHED,
    STATUS_NO_GO,
    STATUS_PAUSED,
    STATUS_WAIT,
    TradeGuardsService,
)
from app.models import PositionRecord, utc_now_iso
from app.storage.database import Database
from app.storage.repositories import TradingRepository

NOW = datetime(2026, 7, 1, 15, 0, tzinfo=UTC)  # 11:00 New York, a Wednesday


def guard_settings(**overrides) -> dict:
    settings = {
        "risk": {"max_risk_per_trade_usd": 15},
        "trade_guards": {
            "enabled": True,
            "halt": {"enabled": True, "resume_cooldown_minutes": 5},
            "circuit_breakers": {
                "enabled": True,
                "max_daily_loss_R": 3.0,
                "max_consecutive_losses": 3,
                "max_trades_per_day": 5,
                "cooldown_after_stop_minutes": 30,
            },
            "pdt": {"enabled": False, "max_day_trades_per_5_days": 3},
            "exposure": {
                "enabled": True,
                "block_if_position_on_same_symbol": True,
                "max_open_positions": 3,
                "max_total_open_risk_R": 2.0,
                "max_positions_same_sector": 2,
                "correlated_groups": [],
            },
        },
    }
    settings["trade_guards"].update(overrides)
    return settings


class TradeGuardsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "state.sqlite")
        self.database.initialize()
        self.repository = TradingRepository(self.database)

    def tearDown(self) -> None:
        self.database.close()
        self.tmp.cleanup()

    def service(self, settings: dict | None = None) -> TradeGuardsService:
        return TradeGuardsService(self.repository, settings or guard_settings())

    def open_position(
        self,
        symbol: str,
        *,
        setup_id: str = "",
        risk_remaining: float = 10.0,
    ) -> None:
        self.repository.upsert_position(
            PositionRecord(
                symbol=symbol,
                setup_id=setup_id or f"{symbol}_setup",
                quantity=10,
                average_price=20.0,
                current_price=20.0,
                unrealized_pnl=0.0,
                current_stop=19.0,
                risk_remaining=risk_remaining,
                status="OPEN",
                updated_at=utc_now_iso(),
            )
        )


class CircuitBreakerTests(TradeGuardsTestCase):
    def test_no_verdict_when_clean(self) -> None:
        self.assertIsNone(self.service().evaluate_entry("ABCD", now=NOW))

    def test_daily_loss_limit_trips_breaker(self) -> None:
        service = self.service()
        # 3R = 45 USD of realized losses trips the kill switch.
        service.circuit_breakers.record_position_closed("AAAA", -20.0, now=NOW)
        service.circuit_breakers.record_position_closed(
            "BBBB", -26.0, now=NOW + timedelta(minutes=5)
        )
        verdict = service.evaluate_entry("CCCC", now=NOW + timedelta(minutes=10))
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict.status, STATUS_PAUSED)
        self.assertEqual(verdict.reason_code, REASON_DAILY_LOSS_LIMIT)

    def test_consecutive_losses_trip_breaker(self) -> None:
        service = self.service()
        for index in range(3):
            service.circuit_breakers.record_position_closed(
                f"SYM{index}", -1.0, now=NOW + timedelta(minutes=index)
            )
        verdict = service.evaluate_entry("DDDD", now=NOW + timedelta(hours=2))
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict.status, STATUS_PAUSED)
        self.assertEqual(verdict.reason_code, REASON_DAILY_LOSS_LIMIT)

    def test_win_resets_consecutive_losses(self) -> None:
        service = self.service()
        service.circuit_breakers.record_position_closed("AAAA", -1.0, now=NOW)
        service.circuit_breakers.record_position_closed("BBBB", -1.0, now=NOW)
        service.circuit_breakers.record_position_closed("CCCC", 5.0, now=NOW)
        service.circuit_breakers.record_position_closed("DDDD", -1.0, now=NOW)
        self.assertIsNone(service.evaluate_entry("EEEE", now=NOW + timedelta(hours=1)))

    def test_max_trades_per_day_trips_breaker(self) -> None:
        service = self.service()
        for index in range(5):
            service.circuit_breakers.record_entry_submitted(f"SYM{index}", now=NOW)
        verdict = service.evaluate_entry("FFFF", now=NOW)
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict.status, STATUS_PAUSED)
        self.assertEqual(verdict.reason_code, REASON_MAX_TRADES_REACHED)

    def test_breaker_stays_tripped_intraday_and_resets_next_day(self) -> None:
        service = self.service()
        service.circuit_breakers.record_position_closed("AAAA", -50.0, now=NOW)
        later_same_day = NOW + timedelta(hours=4)
        self.assertIsNotNone(service.evaluate_entry("BBBB", now=later_same_day))
        next_day = NOW + timedelta(days=1)
        self.assertIsNone(service.evaluate_entry("BBBB", now=next_day))

    def test_cooldown_after_stop_on_same_symbol(self) -> None:
        service = self.service()
        service.circuit_breakers.record_position_closed("AAAA", -5.0, now=NOW)
        verdict = service.evaluate_entry("AAAA", now=NOW + timedelta(minutes=10))
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict.status, STATUS_WAIT)
        self.assertEqual(verdict.reason_code, REASON_COOLDOWN_AFTER_STOP)
        # Other symbols are not blocked by the per-ticker cooldown.
        self.assertIsNone(service.evaluate_entry("BBBB", now=NOW + timedelta(minutes=10)))
        # After the cooldown the symbol becomes tradable again.
        self.assertIsNone(service.evaluate_entry("AAAA", now=NOW + timedelta(minutes=31)))

    def test_pdt_limit_blocks_when_enabled(self) -> None:
        settings = guard_settings(pdt={"enabled": True, "max_day_trades_per_5_days": 3})
        service = self.service(settings)
        for index in range(3):
            service.circuit_breakers.record_entry_submitted(f"SYM{index}", now=NOW)
        verdict = service.evaluate_entry("GGGG", now=NOW)
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict.status, STATUS_NO_GO)
        self.assertEqual(verdict.reason_code, REASON_MAX_TRADES_REACHED)
        self.assertEqual(verdict.decision_status, "PDT_LIMIT_REACHED")


class HaltGateTests(TradeGuardsTestCase):
    def test_halt_active_pauses_symbol(self) -> None:
        service = self.service()
        service.set_halt_state("HLTD", halted=True)
        verdict = service.evaluate_entry("HLTD", now=NOW)
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict.status, STATUS_PAUSED)
        self.assertEqual(verdict.reason_code, REASON_HALT_ACTIVE)

    def test_resume_cooldown_after_halt(self) -> None:
        service = self.service()
        service.set_halt_state("HLTD", halted=True)
        service.set_halt_state("HLTD", halted=False)
        verdict = service.evaluate_entry("HLTD", now=datetime.now(UTC))
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict.status, STATUS_WAIT)
        self.assertEqual(verdict.reason_code, REASON_HALT_ACTIVE)
        self.assertEqual(verdict.decision_status, "HALT_RESUME_COOLDOWN")
        # Once the cooldown elapsed the gate opens again.
        later = datetime.now(UTC) + timedelta(minutes=6)
        self.assertIsNone(service.evaluate_entry("HLTD", now=later))


class ExposureLimitTests(TradeGuardsTestCase):
    def test_same_symbol_conflict(self) -> None:
        self.open_position("AAAA")
        verdict = self.service().evaluate_entry("AAAA", now=NOW)
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict.status, STATUS_NO_GO)
        self.assertEqual(verdict.reason_code, REASON_CONFLICT_WITH_OPEN_POSITION)

    def test_max_open_positions(self) -> None:
        for symbol in ("AAAA", "BBBB", "CCCC"):
            self.open_position(symbol, risk_remaining=1.0)
        verdict = self.service().evaluate_entry("DDDD", now=NOW)
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict.reason_code, REASON_EXPOSURE_LIMIT)

    def test_total_open_risk_limit(self) -> None:
        # 2R = 30 USD max total open risk; one open position already risks 25.
        self.open_position("AAAA", risk_remaining=25.0)
        setup = {"config": {"risk": {"max_risk_usd": 15}}}
        verdict = self.service().evaluate_entry("BBBB", setup=setup, now=NOW)
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict.status, STATUS_NO_GO)
        self.assertEqual(verdict.reason_code, REASON_EXPOSURE_LIMIT)

    def test_open_risk_within_limit_passes(self) -> None:
        self.open_position("AAAA", risk_remaining=10.0)
        setup = {"config": {"risk": {"max_risk_usd": 15}}}
        self.assertIsNone(self.service().evaluate_entry("BBBB", setup=setup, now=NOW))

    def test_correlated_group_conflict(self) -> None:
        settings = guard_settings()
        settings["trade_guards"]["exposure"]["correlated_groups"] = [["AAAA", "BBBB"]]
        self.open_position("AAAA", risk_remaining=1.0)
        verdict = self.service(settings).evaluate_entry("BBBB", now=NOW)
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict.reason_code, REASON_CONFLICT_WITH_OPEN_POSITION)

    def test_disabled_guards_do_nothing(self) -> None:
        self.open_position("AAAA")
        service = TradeGuardsService(self.repository, {})
        self.assertIsNone(service.evaluate_entry("AAAA", now=NOW))


if __name__ == "__main__":
    unittest.main()
