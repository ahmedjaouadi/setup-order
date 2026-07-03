# Broker Reality Contract

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

IBKR/TWS is the source of truth for real trading state.

## Broker Truth

TWS is authoritative for:

- open positions;
- open orders;
- active stops;
- executions;
- account P&L.

Local state records setup intent, history and UI state. It does not override broker reality.

## Required Broker Snapshot

Before a new entry can be allowed, the application must know:

- connection status;
- broker sync timestamp and age;
- open positions for the symbol;
- open orders for the symbol;
- active stop protection;
- account or position P&L source and fallback status.

## Stale State Rules

If broker state is stale, disconnected or inconsistent:

```text
new entries = BLOCKED
position management = MANUAL_REVIEW_REQUIRED or MANAGE_ONLY
```

## GUI Contract

The GUI must label broker-derived state clearly:

- TWS positions;
- TWS open orders;
- TWS active stops;
- broker sync age;
- protection status;
- warnings and blocking reasons.

## Safety Rules

- Auto-execution is blocked when the broker tracker is stale.
- A local setup cannot hide an unprotected broker position.
- A manual TWS modification must be reconciled before the system continues automated actions.
