from __future__ import annotations

import unittest

from app.engine.setup_diagnostics import (
    build_setup_analysis_trace,
    market_snapshot_payload,
)
from app.models import MarketSnapshot, SetupSignal, SignalAction, SetupStatus
from tests.test_setups import valid_breakout_config


class SetupDiagnosticsTests(unittest.TestCase):
    def test_builds_breakout_retest_trace(self) -> None:
        config = valid_breakout_config()
        setup = {
            "setup_id": config["setup_id"],
            "symbol": config["symbol"],
            "setup_type": config["setup_type"],
            "enabled": True,
            "config": config,
        }
        snapshot = MarketSnapshot(
            symbol="UEC",
            price=14.30,
            open=14.20,
            close=14.35,
            daily_close=14.60,
            bullish_candle=False,
        )
        signal = SetupSignal(
            action=SignalAction.ENTRY_READY,
            reason="Retest confirmed",
            entry_price=14.44,
            stop_loss=13.85,
        )

        trace = build_setup_analysis_trace(
            setup,
            snapshot,
            SetupStatus.WAITING_ENTRY_SIGNAL,
            signal,
        )

        labels = {check["label"] for check in trace["checks"]}
        self.assertEqual(trace["phase"], "Recherche signal entree")
        self.assertEqual(trace["next_step"], "Verifier le risque, construire le bracket entree + stop, puis envoyer l'ordre protege.")
        self.assertIn("Breakout journalier", labels)
        self.assertIn("Prix dans zone retest", labels)
        self.assertIn("Signal entree", labels)

    def test_market_snapshot_payload_keeps_uppercase_symbol_and_false_boolean(self) -> None:
        snapshot = MarketSnapshot(
            symbol="uec",
            price=14.30,
            bullish_candle=False,
            spread_bps=12.5,
        )

        payload = market_snapshot_payload(snapshot)

        self.assertEqual(payload["symbol"], "UEC")
        self.assertFalse(payload["bullish_candle"])
        self.assertEqual(payload["spread_bps"], 12.5)


if __name__ == "__main__":
    unittest.main()
