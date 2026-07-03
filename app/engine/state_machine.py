from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models import SetupRole, SetupStatus


ALLOWED_TRANSITIONS: dict[SetupStatus, set[SetupStatus]] = {
    SetupStatus.DRAFT: {SetupStatus.LOADED, SetupStatus.CANCELLED},
    SetupStatus.LOADED: {SetupStatus.VALIDATED, SetupStatus.ERROR},
    SetupStatus.VALIDATED: {
        SetupStatus.WAITING_ACTIVATION,
        SetupStatus.RECONCILING_EXISTING_POSITION,
        SetupStatus.WAITING_ENTRY_SIGNAL,
        SetupStatus.DISABLED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.DISABLED: {
        SetupStatus.WAITING_ACTIVATION,
        SetupStatus.RECONCILING_EXISTING_POSITION,
        SetupStatus.CANCELLED,
    },
    SetupStatus.WAITING_ACTIVATION: {
        SetupStatus.WAITING_BREAKOUT,
        SetupStatus.MISSED_BREAKOUT,
        SetupStatus.MISSED_BREAKOUT_WAIT_RETEST,
        SetupStatus.STALE_SETUP,
        SetupStatus.BLOCKED,
        SetupStatus.WAITING_RETEST,
        SetupStatus.WAITING_REBOUND,
        SetupStatus.WAITING_CONFIRMATION,
        SetupStatus.WAITING_ENTRY_SIGNAL,
        SetupStatus.EXPIRED,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.BLOCKED: {
        SetupStatus.WAITING_ACTIVATION,
        SetupStatus.RECONCILING_EXISTING_POSITION,
        SetupStatus.MISSED_BREAKOUT_WAIT_RETEST,
        SetupStatus.STALE_SETUP,
        SetupStatus.DISABLED,
        SetupStatus.EXPIRED,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.STALE_SETUP: {
        SetupStatus.WAITING_ACTIVATION,
        SetupStatus.MISSED_BREAKOUT_WAIT_RETEST,
        SetupStatus.BLOCKED,
        SetupStatus.DISABLED,
        SetupStatus.EXPIRED,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.MISSED_BREAKOUT_WAIT_RETEST: {
        SetupStatus.WAITING_ACTIVATION,
        SetupStatus.WAITING_RETEST,
        SetupStatus.REARMED_ON_NEW_BASE,
        SetupStatus.STALE_SETUP,
        SetupStatus.BLOCKED,
        SetupStatus.DISABLED,
        SetupStatus.EXPIRED,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.WAITING_BREAKOUT: {
        SetupStatus.MISSED_BREAKOUT,
        SetupStatus.WAITING_RETEST,
        SetupStatus.WAITING_ENTRY_SIGNAL,
        SetupStatus.EXPIRED,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.MISSED_BREAKOUT: {
        SetupStatus.WAITING_RETEST,
        SetupStatus.REARMED_ON_NEW_BASE,
        SetupStatus.EXPIRED,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.WAITING_RETEST: {
        SetupStatus.WAITING_CONFIRMATION,
        SetupStatus.REARMED_ON_NEW_BASE,
        SetupStatus.WAITING_ENTRY_SIGNAL,
        SetupStatus.EXPIRED,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.REARMED_ON_NEW_BASE: {
        SetupStatus.ENTRY_READY,
        SetupStatus.WAITING_ENTRY_SIGNAL,
        SetupStatus.EXPIRED,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.WAITING_REBOUND: {
        SetupStatus.WAITING_CONFIRMATION,
        SetupStatus.WAITING_ENTRY_SIGNAL,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.WAITING_CONFIRMATION: {
        SetupStatus.ENTRY_READY,
        SetupStatus.WAITING_ENTRY_SIGNAL,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.WAITING_ENTRY_SIGNAL: {
        SetupStatus.ENTRY_READY,
        SetupStatus.ENTRY_ORDER_PLACED,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.ENTRY_READY: {
        SetupStatus.ENTRY_ORDER_PLACED,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.ENTRY_ORDER_PLACED: {
        SetupStatus.ENTRY_PARTIALLY_FILLED,
        SetupStatus.ENTRY_FILLED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
    },
    SetupStatus.ENTRY_PARTIALLY_FILLED: {
        SetupStatus.ENTRY_FILLED,
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
        SetupStatus.MANUAL_REVIEW_REQUIRED,
    },
    SetupStatus.ENTRY_FILLED: {
        SetupStatus.STOP_ORDER_PLACED,
        SetupStatus.STOP_PLACED,
        SetupStatus.ERROR,
        SetupStatus.MANUAL_REVIEW_REQUIRED,
    },
    SetupStatus.STOP_ORDER_PLACED: {
        SetupStatus.IN_POSITION,
        SetupStatus.ERROR,
        SetupStatus.MANUAL_REVIEW_REQUIRED,
    },
    SetupStatus.STOP_PLACED: {
        SetupStatus.IN_POSITION,
        SetupStatus.ERROR,
        SetupStatus.MANUAL_REVIEW_REQUIRED,
    },
    SetupStatus.RECONCILING_EXISTING_POSITION: {
        SetupStatus.IN_POSITION,
        SetupStatus.BLOCKED,
        SetupStatus.INVALIDATED,
        SetupStatus.MANUAL_REVIEW_REQUIRED,
        SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW,
        SetupStatus.CANCELLED,
    },
    SetupStatus.IN_POSITION: {
        SetupStatus.MANAGING_POSITION,
        SetupStatus.PARTIAL_EXIT,
        SetupStatus.CLOSED,
        SetupStatus.ERROR,
        SetupStatus.MANUAL_REVIEW_REQUIRED,
    },
    SetupStatus.MANAGING_POSITION: {
        SetupStatus.PARTIAL_EXIT,
        SetupStatus.CLOSED,
        SetupStatus.ERROR,
        SetupStatus.MANUAL_REVIEW_REQUIRED,
    },
    SetupStatus.PARTIAL_EXIT: {
        SetupStatus.MANAGING_POSITION,
        SetupStatus.CLOSED,
        SetupStatus.ERROR,
    },
    SetupStatus.CLOSED: set(),
    SetupStatus.EXPIRED: set(),
    SetupStatus.INVALIDATED: set(),
    SetupStatus.CANCELLED: set(),
    SetupStatus.MANUAL_REVIEW_REQUIRED: {
        SetupStatus.CANCELLED,
        SetupStatus.ERROR,
        SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW,
    },
    SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW: {
        SetupStatus.CANCELLED,
        SetupStatus.MANUAL_REVIEW_REQUIRED,
    },
    SetupStatus.ERROR: {SetupStatus.CANCELLED, SetupStatus.MANUAL_REVIEW_REQUIRED},
}

TERMINAL_STATUSES = frozenset(
    {
        SetupStatus.CLOSED,
        SetupStatus.EXPIRED,
        SetupStatus.INVALIDATED,
        SetupStatus.CANCELLED,
    }
)
MANUAL_REVIEW_STATUSES = frozenset(
    {
        SetupStatus.MANUAL_REVIEW_REQUIRED,
        SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW,
    }
)
ENTRY_FLOW_STATUSES = frozenset(
    {
        SetupStatus.WAITING_ACTIVATION,
        SetupStatus.WAITING_BREAKOUT,
        SetupStatus.MISSED_BREAKOUT,
        SetupStatus.MISSED_BREAKOUT_WAIT_RETEST,
        SetupStatus.STALE_SETUP,
        SetupStatus.WAITING_RETEST,
        SetupStatus.WAITING_REBOUND,
        SetupStatus.WAITING_CONFIRMATION,
        SetupStatus.REARMED_ON_NEW_BASE,
        SetupStatus.WAITING_ENTRY_SIGNAL,
        SetupStatus.ENTRY_READY,
        SetupStatus.ENTRY_ORDER_PLACED,
        SetupStatus.ENTRY_PARTIALLY_FILLED,
        SetupStatus.ENTRY_FILLED,
        SetupStatus.STOP_ORDER_PLACED,
        SetupStatus.STOP_PLACED,
    }
)
POSITION_STATUSES = frozenset(
    {
        SetupStatus.RECONCILING_EXISTING_POSITION,
        SetupStatus.IN_POSITION,
        SetupStatus.MANAGING_POSITION,
        SetupStatus.PARTIAL_EXIT,
        SetupStatus.CLOSED,
    }
)


@dataclass(frozen=True, slots=True)
class TransitionDecision:
    current: SetupStatus
    target: SetupStatus
    allowed: bool
    reason: str


class InvalidTransitionError(ValueError):
    pass


class StateMachine:
    def can_transition(
        self,
        current: SetupStatus,
        target: SetupStatus,
        setup_role: SetupRole | str | Any | None = None,
    ) -> bool:
        return self.explain_transition(current, target, setup_role).allowed

    def explain_transition(
        self,
        current: SetupStatus,
        target: SetupStatus,
        setup_role: SetupRole | str | Any | None = None,
    ) -> TransitionDecision:
        role = _normalize_setup_role(setup_role)
        if role == SetupRole.MANAGEMENT_ONLY and target in ENTRY_FLOW_STATUSES:
            return TransitionDecision(
                current=current,
                target=target,
                allowed=False,
                reason="MANAGEMENT_ONLY setup cannot enter the entry order flow",
            )
        if current == target:
            return TransitionDecision(
                current=current,
                target=target,
                allowed=True,
                reason="Already in target status",
            )
        if target in ALLOWED_TRANSITIONS.get(current, set()):
            return TransitionDecision(
                current=current,
                target=target,
                allowed=True,
                reason="Transition allowed",
            )
        return TransitionDecision(
            current=current,
            target=target,
            allowed=False,
            reason=f"Invalid setup transition: {current.value} -> {target.value}",
        )

    def transition(
        self,
        current: SetupStatus,
        target: SetupStatus,
        setup_role: SetupRole | str | Any | None = None,
    ) -> SetupStatus:
        decision = self.explain_transition(current, target, setup_role)
        if not decision.allowed:
            raise InvalidTransitionError(decision.reason)
        return target

    def next_statuses(
        self,
        current: SetupStatus,
        setup_role: SetupRole | str | Any | None = None,
    ) -> set[SetupStatus]:
        return {
            target
            for target in ALLOWED_TRANSITIONS.get(current, set())
            if self.can_transition(current, target, setup_role)
        }

    @staticmethod
    def is_terminal(status: SetupStatus) -> bool:
        return status in TERMINAL_STATUSES

    @staticmethod
    def requires_manual_review(status: SetupStatus) -> bool:
        return status in MANUAL_REVIEW_STATUSES

    @staticmethod
    def is_entry_flow_status(status: SetupStatus) -> bool:
        return status in ENTRY_FLOW_STATUSES

    @staticmethod
    def is_position_status(status: SetupStatus) -> bool:
        return status in POSITION_STATUSES


def _normalize_setup_role(role: SetupRole | str | Any | None) -> SetupRole | None:
    if role in (None, ""):
        return None
    try:
        return SetupRole(str(role).strip())
    except ValueError:
        return None
