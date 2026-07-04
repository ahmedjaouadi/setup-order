from __future__ import annotations

from app.models import (
    MarketSnapshot,
    SetupSignal,
    SetupStatus,
    SignalAction,
    ValidationResult,
)
from app.setups.base_setup import BaseSetup, bullish_confirmation


class AggressiveReboundSetup(BaseSetup):
    setup_type = "aggressive_rebound"

    def validate(self) -> ValidationResult:
        result = super().validate()
        errors = list(result.errors)
        support = self.config.get("support_zone", {})
        if "min" not in support or "max" not in support:
            errors.append("support_zone.min and support_zone.max are required")
        return ValidationResult(valid=not errors, errors=errors)

    def estimated_entry_price(self) -> float | None:
        entry = self.config.get("entry", {})
        if entry.get("trigger_price") is not None:
            return float(entry["trigger_price"])
        support = self.config.get("support_zone", {})
        if "max" in support:
            return float(support["max"])
        return None

    def entry_zone_label(self) -> str:
        support = self.config.get("support_zone", {})
        if "min" in support and "max" in support:
            return f"{float(support['min']):.2f}-{float(support['max']):.2f}"
        return super().entry_zone_label()

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        current_status: SetupStatus,
    ) -> SetupSignal:
        support = self.config.get("support_zone", {})
        low = float(support["min"])
        high = float(support["max"])
        invalidation = self.config.get("invalidation", {})
        close_below = float(invalidation.get("close_below", low))
        close = snapshot.close if snapshot.close is not None else snapshot.price

        if close < close_below:
            return SetupSignal(
                action=SignalAction.INVALIDATE,
                reason="Close below support invalidation",
                target_status=SetupStatus.INVALIDATED,
            )
        if current_status == SetupStatus.WAITING_ACTIVATION and low <= snapshot.price <= high:
            return SetupSignal(
                action=SignalAction.STATUS_CHANGE,
                reason="Price touched support zone",
                target_status=SetupStatus.WAITING_ENTRY_SIGNAL,
            )
        if current_status == SetupStatus.WAITING_ENTRY_SIGNAL:
            previous_high = snapshot.previous_high or snapshot.high or high
            if bullish_confirmation(snapshot) and close > previous_high:
                entry = previous_high + float(
                    self.config.get("entry", {}).get("trigger_offset", 0.02)
                )
                return SetupSignal(
                    action=SignalAction.ENTRY_READY,
                    reason="Bullish rebound confirmed",
                    target_status=SetupStatus.ENTRY_READY,
                    entry_price=round(entry, 2),
                    stop_loss=self.stop_loss,
                )
        return SetupSignal.hold("Waiting for support rebound")
