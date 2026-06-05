from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models import (
    MarketSnapshot,
    SetupRecord,
    SetupRole,
    SetupSignal,
    SetupStatus,
    ValidationResult,
)


class BaseSetup(ABC):
    setup_type: str = "base"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @property
    def setup_id(self) -> str:
        return str(self.config.get("setup_id", "")).strip()

    @property
    def symbol(self) -> str:
        return str(self.config.get("symbol", "")).strip().upper()

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    @property
    def mode(self) -> str:
        return str(self.config.get("mode", "simulation"))

    @property
    def setup_role(self) -> SetupRole:
        raw_role = str(
            self.config.get("setup_role", SetupRole.ENTRY_AND_MANAGEMENT.value)
        )
        try:
            return SetupRole(raw_role)
        except ValueError:
            return SetupRole.ENTRY_AND_MANAGEMENT

    @property
    def allows_entry(self) -> bool:
        return self.setup_role in {
            SetupRole.ENTRY_AND_MANAGEMENT,
            SetupRole.ENTRY_ONLY,
        }

    @property
    def stop_loss(self) -> float | None:
        risk = self.config.get("risk", {})
        stop = risk.get("initial_stop_loss", risk.get("protective_stop"))
        return float(stop) if stop is not None else None

    def initial_status(self) -> SetupStatus:
        if not self.enabled:
            return SetupStatus.DISABLED
        if self.setup_role == SetupRole.MANAGEMENT_ONLY:
            return SetupStatus.RECONCILING_EXISTING_POSITION
        return SetupStatus.WAITING_ACTIVATION

    def validate(self) -> ValidationResult:
        errors: list[str] = []
        if not self.setup_id:
            errors.append("setup_id is required")
        if not self.symbol:
            errors.append("symbol is required")
        if self.config.get("setup_type") != self.setup_type:
            errors.append(f"setup_type must be {self.setup_type}")
        if self.mode not in {"simulation", "paper", "live"}:
            errors.append("mode must be simulation, paper or live")
        if str(self.config.get("setup_role", self.setup_role.value)) not in {
            role.value for role in SetupRole
        }:
            errors.append(
                "setup_role must be ENTRY_AND_MANAGEMENT, ENTRY_ONLY or MANAGEMENT_ONLY"
            )
        entry = self.config.get("entry", {})
        if not isinstance(entry, dict):
            errors.append("entry section must be a mapping")
            entry = {}
        entry_enabled = bool(entry.get("enabled", True))
        if self.setup_role == SetupRole.MANAGEMENT_ONLY and entry_enabled:
            errors.append("MANAGEMENT_ONLY setup cannot enable entry orders")
        if self.allows_entry and not entry_enabled:
            errors.append("entry.enabled must be true when setup_role allows entries")
        risk = self.config.get("risk")
        if not isinstance(risk, dict):
            errors.append("risk section is required")
        else:
            if self.allows_entry:
                if float(risk.get("max_position_amount_usd", 0) or 0) <= 0:
                    errors.append("risk.max_position_amount_usd must be positive")
                if float(risk.get("max_risk_usd", 0) or 0) <= 0:
                    errors.append("risk.max_risk_usd must be positive")
            if self.stop_loss is None or self.stop_loss <= 0:
                if self.setup_role == SetupRole.MANAGEMENT_ONLY:
                    errors.append("risk.protective_stop must be positive")
                else:
                    errors.append("risk.initial_stop_loss must be positive")
        if self.allows_entry:
            entry_price = self.worst_case_entry_price()
            if entry_price is None or entry_price <= 0:
                errors.append("estimated entry price is required")
            if entry_price and self.stop_loss and self.stop_loss >= entry_price:
                errors.append("stop loss must be below estimated entry price for long setup")
        return ValidationResult(valid=not errors, errors=errors)

    def estimated_entry_price(self) -> float | None:
        entry = self.config.get("entry", {})
        explicit = entry.get("entry_price") or entry.get("trigger_price")
        if explicit is not None:
            return float(explicit)
        zones = self.config.get("zones", {})
        if "breakout_max" in zones:
            return float(zones["breakout_max"])
        return None

    def maximum_limit_price(self) -> float | None:
        entry_price = self.estimated_entry_price()
        if entry_price is None:
            return None
        entry = self.config.get("entry", {})
        if str(entry.get("order_type", "STP_LMT")) != "STP_LMT":
            return None
        if entry.get("maximum_limit_price") is not None:
            return float(entry["maximum_limit_price"])
        if entry.get("limit_price") is not None:
            return float(entry["limit_price"])
        return entry_price + float(entry.get("limit_offset", 0.0) or 0.0)

    def worst_case_entry_price(self) -> float | None:
        return self.maximum_limit_price() or self.estimated_entry_price()

    def entry_zone_label(self) -> str:
        zones = self.config.get("zones", {})
        retest_min = zones.get("retest_min")
        retest_max = zones.get("retest_max")
        if retest_min is not None and retest_max is not None:
            return f"{float(retest_min):.2f}-{float(retest_max):.2f}"
        entry_price = self.estimated_entry_price()
        return f"{entry_price:.2f}" if entry_price else ""

    def to_record(self, status: SetupStatus | None = None) -> SetupRecord:
        return SetupRecord(
            setup_id=self.setup_id,
            symbol=self.symbol,
            setup_type=self.setup_type,
            enabled=self.enabled,
            mode=self.mode,
            status=(status or self.initial_status()).value,
            entry_zone=self.entry_zone_label(),
            stop_loss=self.stop_loss,
            risk_amount=float(self.config.get("risk", {}).get("max_risk_usd", 0) or 0),
            order_status="",
            position_status="",
            last_event="Setup loaded",
            config=self.config,
        )

    @abstractmethod
    def evaluate(
        self,
        snapshot: MarketSnapshot,
        current_status: SetupStatus,
    ) -> SetupSignal:
        raise NotImplementedError


def bullish_confirmation(snapshot: MarketSnapshot) -> bool:
    if snapshot.bullish_candle:
        return True
    if snapshot.close is not None and snapshot.open is not None:
        return snapshot.close > snapshot.open
    return False
