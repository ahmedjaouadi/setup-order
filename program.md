# program.md - Root Specification

Version: 2.4
Status: Architecture Stabilization
Target mode: paper by default, live only after validation
Last updated: 2026-07-02

## 1. Purpose

This application is a local trading setup platform connected to Interactive Brokers TWS / IB Gateway.

It is not a simple auto-buy script.

Its role is to transform market analysis, opportunities and structured scenarios into validated, risk-controlled and traceable trading setups.

Execution is allowed only after:

1. canonical setup validation;
2. trailing stop-loss validation;
3. risk engine approval;
4. data/session quality approval;
5. broker reality reconciliation;
6. order manager preflight;
7. explicit armed state.

## 2. Core Pipeline

```text
Analysis / Text / Manual Input
        |
Canonical Normalization
        |
Semantic Validation
        |
Scenario Draft
        |
Setup Validation
        |
User Review / GUI
        |
Arming
        |
Signal Engine
        |
Entry Decision
        |
Risk Engine
        |
Order Plan
        |
Order Manager
        |
IBKR / TWS
        |
Broker Reconciliation
        |
Position Management
        |
GUI / Logs / Decision Trace
```

## 3. Domain Boundaries

The system must strictly separate:

```text
Analysis      = raw text, comments, research or LLM output.
Opportunity   = non-executable market detection.
Scenario      = structured draft hypothesis.
Setup         = validated and optionally armable trading configuration.
Order Plan    = calculated order intention, not yet sent to TWS.
Broker Order  = real order submitted to IBKR/TWS.
Position      = real exposure detected from IBKR/TWS.
```

No opportunity, analysis or forecast can directly create an IBKR order.

## 4. Current Repository State

The current implementation includes:

- FastAPI backend;
- HTML/JS GUI;
- SQLite local storage;
- broker abstraction;
- setup engine;
- market data and market context;
- canonical conversion;
- semantic validation and intelligence persistence;
- opportunity scanner;
- forecasting stack;
- model lab / replay MVP;
- setup save / arm / disarm flow.

The codebase is the source of truth for current behavior.
This document is the source of truth for target architecture and safety rules.
When both differ, `docs/implementation-status.md` must explicitly document the gap.

## 5. Non-Negotiable Safety Rules

### 5.1 Broker Reality

IBKR/TWS is the source of truth for:

- open positions;
- open orders;
- active stops;
- executions;
- account P&L.

Local storage is history and intent, not broker truth.

If broker state is stale, disconnected or inconsistent:

```text
new entries = BLOCKED
position management = MANUAL_REVIEW_REQUIRED or MANAGE_ONLY
```

### 5.2 Setup Roles

Valid setup roles:

```text
ENTRY_AND_MANAGEMENT
ENTRY_ONLY
MANAGEMENT_ONLY
```

A `MANAGEMENT_ONLY` setup must never create a BUY order.

### 5.3 Trailing Stop-Loss

All new final setups must use:

```text
trailing_stop_loss.initial_stop
```

as the canonical initial stop field.

Legacy fields are accepted only as input aliases:

```text
initial_stop_loss
protective_stop
SL
stop_loss
```

They must be normalized into:

```text
trailing_stop_loss.initial_stop
```

The validator must not rely on legacy fields.

### 5.4 No Order Without Protection

No entry order may be transmitted unless:

```text
trailing_stop_loss.enabled = true
trailing_stop_loss.initial_stop is not null
risk calculation is valid
broker stop/trailing stop is ready or attached
order manager preflight passes
```

### 5.5 Never Lower Stop

For long positions:

```text
new_stop >= current_stop
```

For short positions:

```text
new_stop <= current_stop
```

Any stop-widening is forbidden by default.

### 5.6 Forecasting Boundary

Forecasting is advisory only.

```text
use_for_execution = false
```

for every forecasting provider.

### 5.7 Opportunity Boundary

An opportunity is never executable.

```text
opportunity.can_send_order = false
```

An opportunity may only generate a draft scenario.

## 6. Required Contracts

The detailed architecture is split into specialized documents:

```text
docs/00-product-vision.md
docs/01-architecture.md
docs/02-core-domain-model.md
docs/03-setup-schema-contract.md
docs/04-trailing-stop-loss-contract.md
docs/05-broker-reality-contract.md
docs/06-risk-engine-contract.md
docs/07-order-manager-contract.md
docs/08-reconciliation-contract.md
docs/09-session-data-quality-contract.md
docs/10-entry-decision-contract.md
docs/11-setup-template-contract.md
docs/12-gui-dashboard-contract.md
docs/13-intelligence-conversion-contract.md
docs/14-opportunity-scanner-contract.md
docs/15-forecasting-contract.md
docs/16-model-lab-backtest-contract.md
docs/17-observability-contract.md
docs/18-tests-golden-contract.md
docs/19-definition-of-done.md
docs/20-documentation-governance.md
```

Implementation notes and historical phase reports remain under `docs/existing/`.

## 7. Current Phase - V2.4 Architecture Stabilization

Priority: regain control of the core application before adding new features.

Scope:

```text
1. canonical setup model
2. trailing_stop_loss contract
3. broker reality tracker
4. risk engine
5. order manager
6. reconciliation
7. entry_decision
8. dashboard broker reality
9. golden tests
```

Temporarily out of scope:

```text
- new forecasting providers
- new advanced scanners
- live trading
- new AI extraction features
- new Model Lab features
```

## 8. V2.4 Definition Of Done

V2.4 is complete only when:

```text
- generated setup template includes root trailing_stop_loss
- generated setup template does not include risk.initial_stop_loss or risk.protective_stop as primary fields
- legacy stop aliases map to trailing_stop_loss.initial_stop
- validator reads only canonical fields
- no entry can be armed without trailing_stop_loss.initial_stop
- no order can be transmitted without broker stop protection ready
- GUI displays broker reality from TWS
- broker stale/mismatch blocks new entries
- golden tests pass
- manual checklist updated
- implementation-status updated
- change-log updated
```

## 9. Documentation Governance

`program.md` must remain short.

Feature details belong in specialized files under `docs/`.

Every behavior-changing update must update:

```text
docs/change-log.md
docs/implementation-status.md
docs/known-limitations.md
docs/manual-test-checklist.md
docs/module-roadmap.md
docs/program-alignment-notes.md
```

A feature is not considered complete unless it is:

```text
implemented
tested
documented
visible in status
covered by manual checklist
safe by default
```
