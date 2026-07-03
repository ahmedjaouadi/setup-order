from __future__ import annotations

import unittest

from app.opportunity_scanner import MarketContextOpportunityScanner


class MarketContextOpportunityScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scanner = MarketContextOpportunityScanner()

    def test_cast_like_move_detects_opportunity_without_sector_metadata(self) -> None:
        signal = self.scanner.evaluate(
            {
                "symbol": "CAST",
                "perf_stock_1d": 13.29,
                "sector": "UNKNOWN",
                "metadata_status": "SECTOR_UNKNOWN",
            }
        )

        self.assertEqual(signal["opportunity_status"], "OPPORTUNITY_DETECTED")
        self.assertEqual(signal["opportunity_type"], "INTRADAY_MOMENTUM_ANOMALY")
        self.assertIn("SECTOR_METADATA_MISSING", signal["warnings"])
        self.assertFalse(signal["can_send_order"])

    def test_extended_price_keeps_detection_but_recommends_waiting_for_retest(self) -> None:
        signal = self.scanner.evaluate(
            {
                "symbol": "FAST",
                "perf_stock_1d": 12,
                "sector": "Technology",
                "metadata_status": "SECTOR_OK",
                "price_too_far_above_entry": True,
            }
        )

        self.assertEqual(signal["opportunity_status"], "OPPORTUNITY_DETECTED")
        self.assertEqual(signal["recommended_next_action"], "WAIT_FOR_RETEST")
        self.assertIn("DO_NOT_CHASE_EXTENDED_PRICE", signal["warnings"])
        self.assertFalse(signal["can_send_order"])

    def test_known_sector_calculates_relative_strength_leader(self) -> None:
        signal = self.scanner.evaluate(
            {
                "symbol": "LEAD",
                "perf_stock_1d": 5,
                "perf_sector_1d": 1,
                "perf_spy_1d": 0.5,
                "sector": "Technology",
                "sector_etf": "XLK",
                "metadata_status": "SECTOR_OK",
            }
        )

        self.assertEqual(signal["source_snapshot"]["relative_strength_vs_sector"], 4)
        self.assertEqual(signal["source_snapshot"]["relative_strength_vs_spy"], 4.5)
        self.assertIn("RELATIVE_STRENGTH_LEADER", signal["opportunity_types"])
        self.assertEqual(signal["opportunity_status"], "OPPORTUNITY_DETECTED")


if __name__ == "__main__":
    unittest.main()
