from __future__ import annotations

import unittest
from copy import deepcopy

from app.models import MarketSnapshot, SetupStatus, SignalAction
from app.opportunity_audit import (
    ExpectedOpportunity,
    OpportunityReplayEngine,
    ReplaySetup,
)
from tests.test_setups import valid_breakout_config


class OpportunityReplayEngineTests(unittest.TestCase):
    def test_replay_evolves_status_and_detects_retest_entry(self) -> None:
        config = valid_breakout_config()
        engine = OpportunityReplayEngine()

        report = engine.run(
            setups=[
                ReplaySetup(
                    config=config,
                    initial_status=SetupStatus.WAITING_ACTIVATION,
                )
            ],
            snapshots=[
                MarketSnapshot(
                    symbol="UEC",
                    price=14.70,
                    close=14.70,
                    daily_close=14.70,
                ),
                breakout_retest_entry_snapshot(),
            ],
            expected_opportunities=[
                ExpectedOpportunity(
                    setup_id=config["setup_id"],
                    by_snapshot_index=1,
                    label="Breakout then bullish retest should trigger entry",
                )
            ],
        )

        self.assertEqual(report.summary["snapshots_replayed"], 2)
        self.assertEqual(report.summary["entries_detected"], 1)
        self.assertEqual(report.summary["missed_opportunities"], 0)
        self.assertEqual(report.steps[0].evaluations[0].action, "STATUS_CHANGE")
        self.assertEqual(
            report.steps[0].evaluations[0].status_after,
            SetupStatus.WAITING_ENTRY_SIGNAL.value,
        )
        entry = report.entry_evaluations[0]
        self.assertEqual(entry.snapshot_index, 1)
        self.assertEqual(entry.status_before, SetupStatus.WAITING_ENTRY_SIGNAL.value)
        self.assertEqual(entry.status_after, SetupStatus.ENTRY_READY.value)
        self.assertEqual(entry.entry_price, 14.44)
        self.assertEqual(entry.opportunity_score["label"], "READY")
        self.assertEqual(entry.opportunity_score["percent"], 100.0)

    def test_audit_flags_expected_entry_not_detected(self) -> None:
        config = valid_breakout_config()
        engine = OpportunityReplayEngine()

        report = engine.run(
            setups=[
                ReplaySetup(
                    config=config,
                    initial_status=SetupStatus.WAITING_ENTRY_SIGNAL,
                )
            ],
            snapshots=[
                MarketSnapshot(
                    symbol="UEC",
                    price=14.80,
                    open=14.75,
                    high=14.85,
                    close=14.78,
                    daily_close=14.78,
                    bullish_candle=True,
                )
            ],
            expected_opportunities=[
                ExpectedOpportunity(
                    setup_id=config["setup_id"],
                    by_snapshot_index=0,
                    label="Price outside retest zone should be reported as missed",
                )
            ],
        )

        self.assertEqual(report.summary["entries_detected"], 0)
        self.assertEqual(report.summary["missed_opportunities"], 1)
        missed = report.missed_opportunities[0]
        self.assertEqual(missed.expected.setup_id, config["setup_id"])
        self.assertIn("Expected ENTRY_READY", missed.reason)
        self.assertIn("Waiting for retest confirmation", missed.reason)
        self.assertIsNotNone(missed.last_evaluation)
        assert missed.last_evaluation is not None
        self.assertEqual(missed.last_evaluation.action, SignalAction.HOLD.value)

    def test_replay_can_keep_original_statuses_without_evolution(self) -> None:
        config = valid_breakout_config()
        engine = OpportunityReplayEngine()

        report = engine.run(
            setups=[
                ReplaySetup(
                    config=config,
                    initial_status=SetupStatus.WAITING_ACTIVATION,
                )
            ],
            snapshots=[
                MarketSnapshot(
                    symbol="UEC",
                    price=14.70,
                    close=14.70,
                    daily_close=14.70,
                ),
                breakout_retest_entry_snapshot(),
            ],
            expected_opportunities=[
                ExpectedOpportunity(setup_id=config["setup_id"], by_snapshot_index=1)
            ],
            evolve_status=False,
        )

        self.assertEqual(report.summary["entries_detected"], 0)
        self.assertEqual(report.summary["missed_opportunities"], 1)
        self.assertEqual(
            [evaluation.status_before for evaluation in report.evaluations],
            [
                SetupStatus.WAITING_ACTIVATION.value,
                SetupStatus.WAITING_ACTIVATION.value,
            ],
        )

    def test_report_to_dict_is_api_ready(self) -> None:
        config = deepcopy(valid_breakout_config())
        engine = OpportunityReplayEngine()

        report = engine.run(
            setups=[
                ReplaySetup(
                    config=config,
                    initial_status=SetupStatus.WAITING_ENTRY_SIGNAL,
                )
            ],
            snapshots=[breakout_retest_entry_snapshot()],
        )

        payload = report.to_dict()

        self.assertEqual(payload["summary"]["entries_detected"], 1)
        self.assertEqual(payload["steps"][0]["snapshot"]["symbol"], "UEC")
        self.assertEqual(
            payload["steps"][0]["evaluations"][0]["trace"]["checks"][-2]["label"],
            "Signal entree",
        )


def breakout_retest_entry_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        symbol="UEC",
        price=14.35,
        open=14.20,
        high=14.42,
        close=14.38,
        daily_close=14.70,
        bullish_candle=True,
        session="RTH",
        market_open_time="2026-06-13T09:30:00-04:00",
        current_time="2026-06-13T10:15:00-04:00",
    )


if __name__ == "__main__":
    unittest.main()
