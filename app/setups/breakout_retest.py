from __future__ import annotations

from app.models import (
    MarketSnapshot,
    SetupSignal,
    SetupStatus,
    SignalAction,
    ValidationResult,
)
from app.setups.base_setup import BaseSetup, bullish_confirmation


class BreakoutRetestSetup(BaseSetup):
    setup_type = "breakout_retest"

    def validate(self) -> ValidationResult:
        result = super().validate()
        errors = list(result.errors)
        breakout = self.config.get("breakout", {})
        retest = self.config.get("retest", {})
        if breakout.get("daily_close_above") is None:
            errors.append("breakout.daily_close_above is required")
        if retest.get("zone_min") is None or retest.get("zone_max") is None:
            errors.append("retest.zone_min and retest.zone_max are required")
        return ValidationResult(valid=not errors, errors=errors)

    def estimated_entry_price(self) -> float | None:
        entry = self.config.get("entry", {})
        if entry.get("trigger_price") is not None:
            return float(entry["trigger_price"])
        breakout = self.config.get("breakout", {})
        if breakout.get("daily_close_above") is not None:
            return float(breakout["daily_close_above"]) + float(entry.get("trigger_offset", 0.02))
        zones = self.config.get("zones", {})
        if zones.get("breakout_max") is not None:
            return float(zones["breakout_max"])
        return None

    def entry_zone_label(self) -> str:
        retest = self.config.get("retest", {})
        if retest.get("zone_min") is not None and retest.get("zone_max") is not None:
            return f"{float(retest['zone_min']):.2f}-{float(retest['zone_max']):.2f}"
        return super().entry_zone_label()

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        current_status: SetupStatus,
    ) -> SetupSignal:
        breakout = self.config.get("breakout", {})
        retest = self.config.get("retest", {})
        entry = self.config.get("entry", {})
        close = snapshot.close if snapshot.close is not None else snapshot.price
        no_close_below = float(retest.get("no_close_below", retest["zone_min"]))

        if current_status in {
            SetupStatus.WAITING_ACTIVATION,
            SetupStatus.WAITING_ENTRY_SIGNAL,
        } and close < no_close_below:
            return SetupSignal(
                action=SignalAction.INVALIDATE,
                reason="Close below retest invalidation",
                target_status=SetupStatus.INVALIDATED,
            )

        daily_close = snapshot.daily_close if snapshot.daily_close is not None else close
        if current_status == SetupStatus.WAITING_ACTIVATION:
            if daily_close > float(breakout["daily_close_above"]):
                return SetupSignal(
                    action=SignalAction.STATUS_CHANGE,
                    reason="Daily breakout confirmed",
                    target_status=SetupStatus.WAITING_ENTRY_SIGNAL,
                )
            return SetupSignal.hold("Waiting for daily breakout")

        if current_status == SetupStatus.WAITING_ENTRY_SIGNAL:
            in_retest = float(retest["zone_min"]) <= snapshot.price <= float(retest["zone_max"])
            if in_retest and bullish_confirmation(snapshot):
                reference_high = snapshot.high or snapshot.price
                trigger_offset = float(entry.get("trigger_offset", 0.02))
                return SetupSignal(
                    action=SignalAction.ENTRY_READY,
                    reason="Retest confirmed by bullish candle",
                    target_status=SetupStatus.ENTRY_READY,
                    entry_price=round(reference_high + trigger_offset, 2),
                    stop_loss=self.stop_loss,
                )
            return SetupSignal.hold("Waiting for retest confirmation")

        return SetupSignal.hold("No breakout action")

