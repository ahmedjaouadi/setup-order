# GUI Dashboard Contract

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

The dashboard must make broker reality and protection state visible before the user trusts automation.

## Required Broker Reality Panels

The GUI must display:

- TWS connection status;
- broker sync age;
- open TWS positions;
- open TWS orders;
- active broker stops;
- account or position P&L;
- remaining risk;
- protection status;
- reconciliation status.

## Status Badges

Use clear status families:

```text
OK
WARNING
BLOCKED
CRITICAL
MANUAL_REVIEW_REQUIRED
```

## Display Rules

- Broker-derived values must be labelled as broker/TWS values.
- Local setup intent must be visually separate from broker truth.
- Unknown or stale broker state must not look healthy.
- Missing stop protection must be visible and high severity.
- Entry readiness must come from `entry_decision`, not reconstructed GUI checks.

## User Actions

Actions that can affect broker state must show blocking reasons and preflight state before execution.
