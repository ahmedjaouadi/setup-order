# Product Vision

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

`setup-order` exists to turn analysis into controlled trading setups, then into broker orders only when every safety contract is satisfied.

The product is a local decision and execution assistant. It is not an autonomous buy bot.

## Target Workflow

```text
analysis
  -> opportunity
  -> scenario
  -> setup
  -> order plan
  -> broker order
  -> position
```

## Domain Distinctions

- `Analysis`: raw text, human notes, research or LLM output.
- `Opportunity`: non-executable market detection.
- `Scenario`: structured draft hypothesis.
- `Setup`: validated trading configuration that may be armed.
- `OrderPlan`: calculated intent, not yet submitted to TWS.
- `BrokerOrder`: real order submitted to IBKR/TWS.
- `Position`: real exposure detected from IBKR/TWS.

## Product Boundaries

- The user remains responsible for arming and reviewing setups.
- Paper mode is the default target while contracts are not fully enforced.
- Forecasting, opportunity detection and analysis can only inform validation and scoring.
- No analysis, opportunity, scenario or forecast may bypass setup validation, risk validation, broker reconciliation and order-manager preflight.

## Success Criteria

- The GUI makes broker reality, protection status and blocking reasons visible.
- Every executable path is risk-controlled and traceable.
- The system refuses unsafe or ambiguous states by default.
