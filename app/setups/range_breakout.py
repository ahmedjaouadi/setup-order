from __future__ import annotations

from app.models import MarketSnapshot, SetupSignal, SetupStatus, SignalAction
from app.setups.base_setup import BaseSetup


class RangeBreakoutSetup(BaseSetup):
    setup_type = "range_breakout"

    def estimated_entry_price(self) -> float | None:
        entry = self.config.get("entry", {})
        if entry.get("trigger_price") is not None:
            return float(entry["trigger_price"])
        range_config = self.config.get("range", {})
        if range_config.get("high") is not None:
            return float(range_config["high"]) + float(entry.get("trigger_offset", 0.02))
        return None

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        current_status: SetupStatus,
    ) -> SetupSignal:
        range_config = self.config.get("range", {})
        high = float(range_config["high"])
        low = float(range_config["low"])
        close = snapshot.close if snapshot.close is not None else snapshot.price
        if close < low:
            return SetupSignal(
                action=SignalAction.INVALIDATE,
                reason="Close below range low",
                target_status=SetupStatus.INVALIDATED,
            )
        if snapshot.price > high:
            offset = float(self.config.get("entry", {}).get("trigger_offset", 0.02))
            return SetupSignal(
                action=SignalAction.ENTRY_READY,
                reason="Range breakout confirmed",
                target_status=SetupStatus.ENTRY_READY,
                entry_price=round(high + offset, 2),
                stop_loss=self.stop_loss,
            )
        return SetupSignal.hold("Waiting for range breakout")
