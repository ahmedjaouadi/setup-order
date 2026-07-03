# Core Domain Model

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

This document fixes the vocabulary used by code, GUI and documentation.

## Entities

### Analysis

Raw human text, research notes, external analysis or LLM output. It is never executable.

### Opportunity

Detected market interest. It may carry score, reasons, warnings and a recommended next action, but it must always keep `can_send_order=false`.

### Scenario

Structured hypothesis derived from analysis or an opportunity. It is a draft and must not be armed automatically.

### Setup

Canonical trading configuration saved by the application. A setup can become armable only after validation.

### EntryDecision

Final engine verdict for entry readiness. The GUI must display entry readiness only from this object.

### OrderPlan

Calculated order intention after risk approval. It is still local and not a broker order.

### BrokerOrder

Real order submitted to IBKR/TWS. Its source of truth is the broker.

### Position

Real exposure detected from IBKR/TWS. Local setup state may describe management intent, not truth.

### StopProtection

Real or planned stop protection attached to a position or entry. V2.4 canonical initial stop is `trailing_stop_loss.initial_stop`.

### ReconciliationState

Comparison result between local intent and broker truth. Valid families include `OK`, `MISMATCH`, `MANUAL_REVIEW_REQUIRED` and `BLOCKED`.

## Naming Rule

Do not use `setup`, `scenario`, `opportunity`, `order` and `position` interchangeably. A lower-level object cannot inherit permissions from a higher-level draft object.
