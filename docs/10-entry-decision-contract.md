# Entry Decision Contract

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

`entry_decision` is the single final object used by the GUI and order workflow to represent entry readiness.

## Required Fields

An entry decision must expose:

- status;
- `can_send_order`;
- blocking reasons;
- warnings;
- setup role;
- armed state;
- price checks;
- volume checks;
- session/data quality checks;
- risk readiness when available;
- display title and severity.

## Status Priority

Blocking status has priority over optimistic display.

Recommended status families:

```text
ENTRY_READY
BLOCKED
WAITING_FOR_SIGNAL
MISSED_ENTRY
MANAGEMENT_ONLY
NOT_ARMED
INVALID_SETUP
BROKER_STALE
MANUAL_REVIEW_REQUIRED
```

## GUI Rule

The GUI must never display `Entree possible` or equivalent unless the final `entry_decision` is truly `ENTRY_READY`.

## Order Rule

The order manager may consider entry only when:

```text
entry_decision.status = ENTRY_READY
entry_decision.can_send_order = true
blocking_reasons is empty
```

Other contracts still apply after this decision: risk, broker reality and order-manager preflight.
