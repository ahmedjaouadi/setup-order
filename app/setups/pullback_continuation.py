from __future__ import annotations

from app.models import MarketSnapshot, SetupSignal, SetupStatus, SignalAction
from app.setups.base_setup import BaseSetup, bullish_confirmation


class PullbackContinuationSetup(BaseSetup):
    setup_type = "pullback_continuation"

    def estimated_entry_price(self) -> float | None:
        entry = self.config.get("entry", {})
        if entry.get("trigger_price") is not None:
            return float(entry["trigger_price"])
        pullback = self.config.get("pullback", {})
        return float(pullback["entry_reference"]) if pullback.get("entry_reference") else None

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        current_status: SetupStatus,
    ) -> SetupSignal:
        if snapshot.ema_20 is None or snapshot.ema_50 is None:
            return SetupSignal.hold("Waiting for EMA data")
        if snapshot.price < snapshot.ema_50:
            return SetupSignal(
                action=SignalAction.INVALIDATE,
                reason="Price lost EMA 50 trend filter",
                target_status=SetupStatus.INVALIDATED,
            )
        if current_status == SetupStatus.WAITING_ACTIVATION and snapshot.ema_20 > snapshot.ema_50:
            return SetupSignal(
                action=SignalAction.STATUS_CHANGE,
                reason="Trend filter confirmed",
                target_status=SetupStatus.WAITING_ENTRY_SIGNAL,
            )
        if current_status == SetupStatus.WAITING_ENTRY_SIGNAL:
            if snapshot.price <= snapshot.ema_20 and bullish_confirmation(snapshot):
                reference_high = snapshot.high or snapshot.price
                offset = float(self.config.get("entry", {}).get("trigger_offset", 0.02))
                return SetupSignal(
                    action=SignalAction.ENTRY_READY,
                    reason="Pullback continuation confirmed",
                    target_status=SetupStatus.ENTRY_READY,
                    entry_price=round(reference_high + offset, 2),
                    stop_loss=self.stop_loss,
                )
        return SetupSignal.hold("Waiting for pullback")

