from __future__ import annotations

import unittest

from app.broker.ib_models import BrokerOrderRequest, BrokerPosition
from app.engine.broker_reality import (
    broker_reality_blocking_reasons,
    build_broker_reality_report,
    freshen_broker_reality_report,
)


SETTINGS = {
    "broker_tracker": {
        "enabled": True,
        "refresh_seconds": 5,
        "stale_after_seconds": 15,
        "block_auto_execution_if_stale": True,
    },
    "execution_safety": {
        "block_new_entries_if_broker_tracker_stale": True,
        "block_new_entries_if_unprotected_order_exists": True,
        "block_new_entries_if_position_without_stop_exists": True,
        "block_new_entries_if_reconciliation_mismatch": True,
    },
}


def setup(status: str = "ENTRY_ORDER_PLACED") -> dict:
    return {
        "setup_id": "LUNR_20260630_001",
        "symbol": "LUNR",
        "status": status,
        "config": {
            "direction": "long",
            "risk": {},
            "trailing_stop_loss": {
                "enabled": True,
                "initial_stop": 18.05,
                "broker_order": {
                    "required_before_entry_transmission": True,
                },
            },
        },
    }


def local_entry(status: str = "SUBMITTED") -> dict:
    return {
        "id": "ord_local_entry",
        "setup_id": "LUNR_20260630_001",
        "symbol": "LUNR",
        "side": "BUY",
        "order_type": "STP_LMT",
        "quantity": 6,
        "status": status,
        "trigger_price": 20.58,
        "broker_order_id": "1001",
    }


class BrokerRealityTests(unittest.TestCase):
    def test_local_active_but_tws_order_missing_blocks_execution(self) -> None:
        report = build_broker_reality_report(
            local_setups=[setup()],
            local_orders=[local_entry()],
            broker_orders=[],
            broker_positions=[],
            broker_connected=True,
            settings=SETTINGS,
            now="2026-06-30T15:24:15+00:00",
        )

        row = report["rows"][0]
        self.assertEqual(row["reconciliation_status"], "MISMATCH")
        self.assertEqual(row["broker_entry_status"], "NO_BROKER_ORDER")
        self.assertTrue(report["auto_execution_blocked"])

    def test_configured_stop_without_tws_stop_is_not_protected(self) -> None:
        report = build_broker_reality_report(
            local_setups=[setup()],
            local_orders=[],
            broker_orders=[],
            broker_positions=[],
            broker_connected=True,
            settings=SETTINGS,
            now="2026-06-30T15:24:15+00:00",
        )

        row = report["rows"][0]
        self.assertEqual(row["configured_stop_price"], 18.05)
        self.assertEqual(row["broker_stop_status"], "MISSING")
        self.assertEqual(row["protection_status"], "STOP_MISSING")
        self.assertNotEqual(row["protection_status"], "PROTECTED")

    def test_prepared_not_transmitted_order_is_not_active(self) -> None:
        report = build_broker_reality_report(
            local_setups=[setup()],
            local_orders=[],
            broker_orders=[
                BrokerOrderRequest(
                    client_order_id="1001",
                    setup_id="broker",
                    symbol="LUNR",
                    side="BUY",
                    order_type="STP_LMT",
                    quantity=6,
                    status="Submitted",
                    transmit=False,
                    broker_order_id="1001",
                )
            ],
            broker_positions=[],
            broker_connected=True,
            settings=SETTINGS,
            now="2026-06-30T15:24:15+00:00",
        )

        row = report["rows"][0]
        self.assertEqual(row["broker_entry_status"], "PREPARED_NOT_TRANSMITTED")
        self.assertFalse(row["is_active"])

    def test_transmitted_entry_with_transmitted_stop_is_protected(self) -> None:
        report = build_broker_reality_report(
            local_setups=[setup()],
            local_orders=[local_entry()],
            broker_orders=[
                BrokerOrderRequest(
                    client_order_id="1001",
                    setup_id="broker",
                    symbol="LUNR",
                    side="BUY",
                    order_type="STP_LMT",
                    quantity=6,
                    status="Submitted",
                    broker_order_id="1001",
                ),
                BrokerOrderRequest(
                    client_order_id="1002",
                    setup_id="broker",
                    symbol="LUNR",
                    side="SELL",
                    order_type="STP",
                    quantity=6,
                    stop_price=18.05,
                    parent_id="1001",
                    status="Submitted",
                    broker_order_id="1002",
                ),
            ],
            broker_positions=[],
            broker_connected=True,
            settings=SETTINGS,
            now="2026-06-30T15:24:15+00:00",
        )

        row = report["rows"][0]
        self.assertEqual(row["broker_entry_status"], "TRANSMITTED")
        self.assertEqual(row["broker_stop_status"], "TRANSMITTED")
        self.assertEqual(row["protection_status"], "PROTECTED")

    def test_broker_tracker_stale_blocks_auto_execution(self) -> None:
        report = build_broker_reality_report(
            local_setups=[setup("WAITING_ENTRY_SIGNAL")],
            local_orders=[],
            broker_orders=[],
            broker_positions=[
                BrokerPosition(
                    symbol="LUNR",
                    quantity=0,
                    average_price=0,
                    current_price=20.58,
                )
            ],
            broker_connected=True,
            settings=SETTINGS,
            now="2026-06-30T15:24:15+00:00",
        )

        stale = freshen_broker_reality_report(
            report,
            settings=SETTINGS,
            now="2026-06-30T15:24:55+00:00",
        )

        self.assertEqual(stale["broker_tracker_status"], "STALE")
        self.assertTrue(stale["auto_execution_blocked"])
        self.assertIn("BROKER_TRACKER_STALE", stale["blocking_reasons"])

    def test_tws_position_visible_with_empty_local_state_is_reported(self) -> None:
        report = build_broker_reality_report(
            local_setups=[],
            local_orders=[],
            local_positions=[],
            broker_orders=[
                BrokerOrderRequest(
                    client_order_id="7001",
                    setup_id="broker",
                    symbol="LUNR",
                    side="SELL",
                    order_type="STP",
                    quantity=17,
                    stop_price=19.45,
                    status="Submitted",
                    broker_order_id="7001",
                )
            ],
            broker_positions=[
                BrokerPosition(
                    symbol="LUNR",
                    quantity=17,
                    average_price=20.671,
                    current_price=21.44,
                    market_price=21.44,
                    unrealized_pnl=36.9,
                    daily_pnl=18.0,
                )
            ],
            broker_connected=True,
            settings=SETTINGS,
            now="2026-06-30T15:24:15+00:00",
        )

        self.assertEqual(report["broker_positions_count"], 1)
        self.assertEqual(report["broker_active_orders"], 1)
        self.assertGreater(report["mismatch_count"], 0)
        row = report["rows"][0]
        self.assertEqual(row["symbol"], "LUNR")
        self.assertEqual(row["position_qty"], 17)
        self.assertEqual(row["broker_position_status"], "OPEN_POSITION")
        self.assertEqual(row["protection_status"], "POSITION_OPEN_STOP_ACTIVE")
        self.assertEqual(row["active_stop_price"], 19.45)
        self.assertEqual(row["remaining_risk"], 33.83)
        self.assertEqual(report["remaining_risk"], 33.83)
        self.assertEqual(report["remaining_risk_status"], "OK")
        self.assertEqual(report["unprotected_positions"], 0)
        self.assertEqual(report["active_stop_orders"], 1)
        self.assertEqual(row["unrealized_pnl"], 36.9)
        self.assertEqual(row["daily_pnl"], 18.0)
        self.assertIn("MISMATCH_POSITION_COUNT", row["mismatch_reasons"])

    def test_tws_pnl_available_is_fresh(self) -> None:
        report = build_broker_reality_report(
            local_setups=[],
            local_orders=[],
            local_positions=[],
            broker_orders=[],
            broker_positions=[],
            account_summary={
                "available": True,
                "today_pnl": 18.0,
                "unrealized_pnl": 36.9,
                "realized_pnl": 0.0,
            },
            broker_connected=True,
            settings=SETTINGS,
            now="2026-06-30T15:24:15+00:00",
        )

        self.assertEqual(report["pnl"]["source"], "TWS")
        self.assertEqual(report["pnl"]["daily_pnl"], 18.0)
        self.assertEqual(report["pnl"]["unrealized_pnl"], 36.9)
        self.assertEqual(report["pnl"]["total_pnl"], 36.9)
        self.assertEqual(report["pnl"]["status"], "OK")
        self.assertEqual(report["pnl"]["sync_status"], "OK")

    def test_missing_tws_pnl_snapshot_is_reported_stale(self) -> None:
        report = build_broker_reality_report(
            local_setups=[],
            local_orders=[],
            local_positions=[],
            broker_orders=[],
            broker_positions=[],
            account_summary={"available": False},
            broker_connected=True,
            settings=SETTINGS,
            now="2026-06-30T15:24:15+00:00",
        )

        self.assertEqual(report["pnl"]["source"], "TWS")
        self.assertEqual(report["pnl"]["status"], "STALE")
        self.assertEqual(report["pnl"]["reason"], "NO_RECENT_TWS_PNL_SNAPSHOT")

    def test_open_position_without_tws_stop_has_unknown_critical_risk(self) -> None:
        report = build_broker_reality_report(
            local_setups=[],
            local_orders=[],
            local_positions=[],
            broker_orders=[],
            broker_positions=[
                BrokerPosition(
                    symbol="LUNR",
                    quantity=17,
                    average_price=20.67,
                    current_price=21.44,
                    market_price=21.44,
                )
            ],
            broker_connected=True,
            settings=SETTINGS,
            now="2026-06-30T15:24:15+00:00",
        )

        row = report["rows"][0]
        self.assertEqual(row["protection_status"], "POSITION_OPEN_STOP_MISSING_CRITICAL")
        self.assertEqual(row["remaining_risk_status"], "UNKNOWN_CRITICAL")
        self.assertIsNone(report["remaining_risk"])
        self.assertEqual(report["remaining_risk_status"], "UNKNOWN_CRITICAL")
        self.assertEqual(report["unprotected_positions"], 1)

    def test_missing_broker_tracker_blocks_auto_execution(self) -> None:
        class EmptyRepository:
            def get_bot_state(self, key, default):
                return default

        reasons = broker_reality_blocking_reasons(EmptyRepository(), SETTINGS)

        self.assertEqual(reasons, ["BROKER_TRACKER_NOT_RUNNING"])

    def test_broker_active_orders_are_counted_when_local_orders_are_empty(self) -> None:
        report = build_broker_reality_report(
            local_setups=[],
            local_orders=[],
            local_positions=[],
            broker_orders=[
                BrokerOrderRequest(
                    client_order_id="8001",
                    setup_id="broker",
                    symbol="QCOM",
                    side="BUY",
                    order_type="LMT",
                    quantity=10,
                    limit_price=190.0,
                    status="Submitted",
                    broker_order_id="8001",
                )
            ],
            broker_positions=[],
            broker_connected=True,
            settings=SETTINGS,
            now="2026-06-30T15:24:15+00:00",
        )

        self.assertEqual(report["broker_active_orders"], 1)
        self.assertEqual(report["broker_prepared_not_transmitted_orders"], 0)
        self.assertEqual(report["mismatch_count"], 1)
        self.assertEqual(report["rows"][0]["mismatch_reasons"], ["BROKER_ORDER_WITHOUT_LOCAL_SETUP"])

    def test_open_orders_query_error_is_not_empty_ok(self) -> None:
        report = build_broker_reality_report(
            local_setups=[],
            local_orders=[],
            local_positions=[],
            broker_orders=[],
            broker_positions=[],
            broker_connected=True,
            order_query_error="TWS timeout on reqOpenOrders",
            settings=SETTINGS,
            now="2026-06-30T15:24:15+00:00",
        )

        self.assertIn(report["channels"]["orders"]["status"], {"ERROR", "PARTIAL"})
        self.assertTrue(report["auto_execution_blocked"])
        self.assertIn("BROKER_ORDERS_QUERY_FAILED", report["blocking_reasons"])
        self.assertIsNone(report["broker_active_orders"])

    def test_positions_query_error_is_not_empty_ok(self) -> None:
        report = build_broker_reality_report(
            local_setups=[],
            local_orders=[],
            local_positions=[],
            broker_orders=[],
            broker_positions=[],
            broker_connected=True,
            position_query_error="TWS timeout on reqPositions",
            settings=SETTINGS,
            now="2026-06-30T15:24:15+00:00",
        )

        self.assertIn(report["channels"]["positions"]["status"], {"ERROR", "PARTIAL"})
        self.assertTrue(report["auto_execution_blocked"])
        self.assertIn("BROKER_POSITIONS_QUERY_FAILED", report["blocking_reasons"])
        self.assertIsNone(report["broker_positions_count"])

    def test_safety_gate_flags_disconnect_condition(self) -> None:
        report = build_broker_reality_report(
            local_setups=[],
            local_orders=[],
            local_positions=[],
            broker_orders=[],
            broker_positions=[],
            broker_connected=False,
            settings=SETTINGS,
            now="2026-06-30T15:24:15+00:00",
        )

        gate = report["safety_gate"]
        self.assertTrue(gate["auto_execution_blocked"])
        self.assertTrue(gate["conditions"]["tws_disconnected"])
        self.assertFalse(gate["conditions"]["position_without_stop"])
        self.assertIn("TWS_DISCONNECTED", gate["blocking_reasons"])

    def test_safety_gate_flags_partial_query_failure(self) -> None:
        report = build_broker_reality_report(
            local_setups=[],
            local_orders=[],
            local_positions=[],
            broker_orders=[],
            broker_positions=[],
            broker_connected=True,
            order_query_error="TWS timeout",
            settings=SETTINGS,
            now="2026-06-30T15:24:15+00:00",
        )

        gate = report["safety_gate"]
        self.assertTrue(gate["auto_execution_blocked"])
        self.assertTrue(gate["conditions"]["broker_query_partial_failure"])
        self.assertFalse(gate["conditions"]["tws_disconnected"])

    def test_safety_gate_open_when_everything_is_healthy(self) -> None:
        report = build_broker_reality_report(
            local_setups=[],
            local_orders=[],
            local_positions=[],
            broker_orders=[],
            broker_positions=[],
            broker_connected=True,
            settings=SETTINGS,
            now="2026-06-30T15:24:15+00:00",
        )

        gate = report["safety_gate"]
        self.assertFalse(gate["auto_execution_blocked"])
        self.assertFalse(any(gate["conditions"].values()))

    def test_account_query_error_is_not_silently_ok(self) -> None:
        report = build_broker_reality_report(
            local_setups=[],
            local_orders=[],
            local_positions=[],
            broker_orders=[],
            broker_positions=[],
            broker_connected=True,
            account_query_error="TWS timeout on reqAccountSummary",
            settings=SETTINGS,
            now="2026-06-30T15:24:15+00:00",
        )

        self.assertIn(report["channels"]["account"]["status"], {"ERROR", "PARTIAL"})
        self.assertTrue(report["auto_execution_blocked"])
        self.assertIn("BROKER_ACCOUNT_QUERY_FAILED", report["blocking_reasons"])
        self.assertNotEqual(report["pnl"]["status"], "OK")


if __name__ == "__main__":
    unittest.main()
