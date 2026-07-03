# Opportunity Scanner Contract

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

The opportunity scanner detects market interest before a setup exists. Its output is discovery only.

## Non-Executable Boundary

Every opportunity must keep:

```text
can_send_order = false
```

An opportunity may only create a draft scenario or setup candidate.

## Required Fields

Opportunity output should include:

- symbol;
- opportunity status;
- opportunity type;
- score;
- reasons;
- warnings;
- recommended next action;
- source snapshot;
- expiration or freshness information;
- `can_send_order=false`.

## Anti-Chase Rule

Extended prices must not become execution permission. They may produce:

```text
DO_NOT_CHASE_EXTENDED_PRICE
WAIT_FOR_RETEST
```

## Scenario Draft Rule

Generated scenarios remain:

```text
status = DRAFT
selection.armed = false
```

They must pass setup validation, trailing stop-loss validation, risk validation, session/data quality checks, broker reconciliation and order-manager preflight before any execution path exists.
