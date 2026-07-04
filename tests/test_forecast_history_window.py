from __future__ import annotations

import asyncio
import re
import unittest

from app.engine.trading_engine import CHART_TIMEFRAMES, TradingEngine


def _duration_to_calendar_days(duration: str) -> int:
    match = re.fullmatch(r"(\d+)\s*([DWMY])", duration.strip())
    assert match, f"unexpected IB duration: {duration!r}"
    value, unit = int(match.group(1)), match.group(2)
    return {"D": 1, "W": 7, "M": 30, "Y": 365}[unit] * value


class ForecastHistoryDurationTests(unittest.TestCase):
    def test_15m_window_covers_more_than_min_context_bars(self) -> None:
        # 96 bars over the chart's "10 D" window is borderline for thin symbols;
        # the forecast window must comfortably exceed the context requirement.
        duration = TradingEngine._forecast_history_duration("15 mins", 512)
        calendar_days = _duration_to_calendar_days(duration)
        chart_days = _duration_to_calendar_days(CHART_TIMEFRAMES["15m"]["duration"])

        self.assertGreater(calendar_days, chart_days)
        # ~26 regular-session bars/day; enough calendar days to yield >= 256 bars.
        trading_days = calendar_days * 5 // 7
        self.assertGreaterEqual(trading_days * 26, 256)

    def test_daily_bars_use_day_or_year_units(self) -> None:
        duration = TradingEngine._forecast_history_duration("1 day", 512)
        self.assertRegex(duration, r"^\d+\s*[DY]$")
        self.assertGreaterEqual(_duration_to_calendar_days(duration), 512)

    def test_unknown_bar_size_falls_back_to_intraday_sizing(self) -> None:
        duration = TradingEngine._forecast_history_duration("7 mins", 256)
        self.assertRegex(duration, r"^\d+\s*[DM]$")


class ForecastMarketHistoryWiringTests(unittest.TestCase):
    def test_forecast_history_requests_widened_duration(self) -> None:
        captured: dict[str, object] = {}

        class StubEngine:
            settings = type("S", (), {"raw": {"forecasting": {"context_bars": 256}}})()
            _normalize_chart_timeframe = staticmethod(TradingEngine._normalize_chart_timeframe)
            _forecast_history_duration = staticmethod(TradingEngine._forecast_history_duration)

            async def market_history(self, symbol, timeframe, *, duration=None):
                captured["symbol"] = symbol
                captured["timeframe"] = timeframe
                captured["duration"] = duration
                return {"symbol": symbol, "historical_bars": []}

        stub = StubEngine()
        result = asyncio.run(TradingEngine.forecast_market_history(stub, "NEWCO", "15m"))

        self.assertEqual(captured["symbol"], "NEWCO")
        self.assertEqual(captured["timeframe"], "15m")
        # A widened, decoupled window (not the chart's fixed "10 D").
        self.assertIsNotNone(captured["duration"])
        self.assertNotEqual(captured["duration"], CHART_TIMEFRAMES["15m"]["duration"])
        self.assertGreater(
            _duration_to_calendar_days(str(captured["duration"])),
            _duration_to_calendar_days(CHART_TIMEFRAMES["15m"]["duration"]),
        )
        self.assertEqual(result["symbol"], "NEWCO")


if __name__ == "__main__":
    unittest.main()
