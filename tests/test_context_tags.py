from __future__ import annotations

import unittest
from datetime import UTC, datetime

from app.opportunity_scanner.context_tags import build_context_tags


# July 2026 is EDT (UTC-4): NY = UTC - 4h. 2026-07-06 is a Monday.
# Wall-clock helper: give a NY hour/minute on that Monday, get the UTC instant.
def _ny(hour: int, minute: int = 0, *, day: int = 6) -> datetime:
    return datetime(2026, 7, day, hour + 4, minute, tzinfo=UTC)


class TimeBucketTests(unittest.TestCase):
    def test_each_bucket(self) -> None:
        cases = {
            (9, 30): "OPEN",
            (9, 45): "OPEN",
            (10, 0): "MORNING",
            (11, 0): "MORNING",
            (11, 30): "LUNCH",
            (13, 59): "LUNCH",
            (14, 0): "AFTERNOON",
            (14, 30): "AFTERNOON",
            (15, 0): "POWER_HOUR",
            (15, 59): "POWER_HOUR",
            (16, 0): "OFF_HOURS",
            (8, 0): "OFF_HOURS",
        }
        for (hour, minute), expected in cases.items():
            with self.subTest(hour=hour, minute=minute):
                tags = build_context_tags({}, _ny(hour, minute))
                self.assertEqual(tags["time_bucket"], expected)

    def test_boundaries_are_lower_inclusive(self) -> None:
        # 11:30 is the first minute of LUNCH; 14:00 the first of AFTERNOON.
        self.assertEqual(build_context_tags({}, _ny(11, 30))["time_bucket"], "LUNCH")
        self.assertEqual(build_context_tags({}, _ny(14, 0))["time_bucket"], "AFTERNOON")

    def test_weekend_is_off_hours(self) -> None:
        # 2026-07-04 is a Saturday; any wall-clock time is OFF_HOURS.
        saturday_noon = datetime(2026, 7, 4, 16, 0, tzinfo=UTC)  # 12:00 NY
        self.assertEqual(build_context_tags({}, saturday_noon)["time_bucket"], "OFF_HOURS")

    def test_timezone_conversion(self) -> None:
        # 18:00 UTC in July == 14:00 NY == AFTERNOON, not a UTC-based bucket.
        tags = build_context_tags({}, datetime(2026, 7, 6, 18, 0, tzinfo=UTC))
        self.assertEqual(tags["time_bucket"], "AFTERNOON")


class RvolBucketTests(unittest.TestCase):
    def test_buckets_and_boundaries(self) -> None:
        cases = {
            0.5: "<0.8",
            0.79: "<0.8",
            0.8: "0.8-1.2",  # lower bound inclusive
            1.0: "0.8-1.2",
            1.2: "1.2-2.0",  # lower bound inclusive
            1.9: "1.2-2.0",
            2.0: "1.2-2.0",  # upper bound inclusive
            2.01: ">2.0",
            5.0: ">2.0",
        }
        for value, expected in cases.items():
            with self.subTest(rvol=value):
                tags = build_context_tags({"rvol": value}, _ny(10))
                self.assertEqual(tags["rvol_bucket"], expected)

    def test_field_priority(self) -> None:
        # rvol wins over relative_volume/volume_ratio.
        tags = build_context_tags(
            {"relative_volume": 0.5, "volume_ratio": 0.5, "rvol": 3.0}, _ny(10)
        )
        self.assertEqual(tags["rvol_bucket"], ">2.0")
        # Falls back to relative_volume, then volume_ratio.
        self.assertEqual(
            build_context_tags({"relative_volume": 1.5}, _ny(10))["rvol_bucket"], "1.2-2.0"
        )
        self.assertEqual(build_context_tags({"volume_ratio": 0.7}, _ny(10))["rvol_bucket"], "<0.8")

    def test_missing_is_unknown(self) -> None:
        self.assertEqual(build_context_tags({}, _ny(10))["rvol_bucket"], "UNKNOWN")


class SpreadBucketTests(unittest.TestCase):
    def test_buckets_and_boundaries(self) -> None:
        cases = {
            0.05: "tight",
            0.1: "tight",  # <= 0.1
            0.2: "normal",
            0.3: "normal",  # <= 0.3
            0.31: "wide",
            1.0: "wide",
        }
        for value, expected in cases.items():
            with self.subTest(spread=value):
                tags = build_context_tags({"spread_pct": value}, _ny(10))
                self.assertEqual(tags["spread_bucket"], expected)

    def test_from_bid_ask_when_spread_pct_missing(self) -> None:
        tags = build_context_tags({"bid": 99.95, "ask": 100.05}, _ny(10))
        self.assertIn(tags["spread_bucket"], {"tight", "normal"})

    def test_missing_is_unknown(self) -> None:
        self.assertEqual(build_context_tags({}, _ny(10))["spread_bucket"], "UNKNOWN")

    def test_inverted_bid_ask_is_unknown(self) -> None:
        self.assertEqual(
            build_context_tags({"bid": 100.1, "ask": 99.9}, _ny(10))["spread_bucket"], "UNKNOWN"
        )


class DayOfWeekAndReservedTests(unittest.TestCase):
    def test_day_of_week_is_new_york(self) -> None:
        # 2026-07-03 22:00 NY (Friday) == 2026-07-04 02:00 UTC. Must read FRI.
        friday_night_utc = datetime(2026, 7, 4, 2, 0, tzinfo=UTC)
        self.assertEqual(build_context_tags({}, friday_night_utc)["day_of_week"], "FRI")
        self.assertEqual(build_context_tags({}, _ny(10, day=6))["day_of_week"], "MON")

    def test_reserved_columns_present(self) -> None:
        tags = build_context_tags({}, _ny(10))
        self.assertEqual(tags["market_regime"], "UNKNOWN")
        self.assertIsNone(tags["had_catalyst"])

    def test_uses_snapshot_timestamp_when_now_omitted(self) -> None:
        # 15:30 UTC in July == 11:30 NY == LUNCH.
        snapshot = {"timestamp": "2026-07-06T15:30:00+00:00"}
        self.assertEqual(build_context_tags(snapshot)["time_bucket"], "LUNCH")

    def test_never_raises_on_garbage(self) -> None:
        tags = build_context_tags({"rvol": "n/a", "spread_pct": None, "timestamp": "bad"})
        self.assertEqual(tags["rvol_bucket"], "UNKNOWN")
        self.assertEqual(tags["spread_bucket"], "UNKNOWN")
        self.assertIn("time_bucket", tags)


if __name__ == "__main__":
    unittest.main()
