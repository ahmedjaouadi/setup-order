from __future__ import annotations

from app.models import MarketSnapshot, SetupSignal, SetupStatus, SignalAction
from app.setups.base_setup import BaseSetup


class RunnerBaseSetup(BaseSetup):
    def estimated_entry_price(self) -> float | None:
        entry = self.config.get("entry", {})
        if entry.get("entry_price") is not None:
            return float(entry["entry_price"])
        return self.stop_loss + 1 if self.stop_loss else None

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        current_status: SetupStatus,
    ) -> SetupSignal:
        if current_status not in {SetupStatus.IN_POSITION, SetupStatus.MANAGING_POSITION}:
            return SetupSignal.hold("Runner waits for an existing position")
        steps = self.config.get("management", {}).get("stop_management", {}).get("steps", [])
        candidates = [
            float(step["new_stop"])
            for step in steps
            if snapshot.price >= float(step["when_price_above"])
        ]
        if not candidates:
            return SetupSignal.hold("No stop step reached")
        return SetupSignal(
            action=SignalAction.RAISE_STOP,
            reason="Runner stop step reached",
            target_status=SetupStatus.MANAGING_POSITION,
            new_stop=max(candidates),
        )


class RunnerSetup(RunnerBaseSetup):
    setup_type = "runner"


class TrailingRunnerSetup(RunnerBaseSetup):
    setup_type = "trailing_runner"
