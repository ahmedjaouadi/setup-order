# Reconciliation Contract

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

Reconciliation compares local intent with broker truth and decides whether automation may continue.

## Compared State

Reconciliation must compare:

- local setups;
- local order history;
- local expected position state;
- TWS open positions;
- TWS open orders;
- TWS active stops;
- executions;
- broker sync age.

## State Results

Valid state families:

```text
OK
MISMATCH
MANUAL_REVIEW_REQUIRED
BLOCKED
```

## Mismatch Cases

The system must detect:

- broker position without matching local setup;
- local active setup without broker state when broker state is expected;
- position without stop;
- stop quantity mismatch;
- unknown open broker order;
- order cancelled manually in TWS;
- stop moved manually in TWS;
- duplicate local and broker intents;
- stale broker snapshot.

## Adoption Rules

An existing TWS position may be adopted only when:

- broker quantity is known;
- direction is known;
- stop protection is found or missing protection is explicitly marked;
- setup role is compatible with management;
- user review is required when ambiguity remains.

## Automation Rules

- `OK` may allow normal workflow.
- `MISMATCH` blocks new entries until resolved.
- `MANUAL_REVIEW_REQUIRED` allows only safe display or explicit management actions.
- `BLOCKED` prevents new execution actions.
