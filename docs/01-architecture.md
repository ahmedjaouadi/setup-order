# Architecture Contract

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

This document defines module boundaries for the target architecture. It does not describe every implementation detail.

## Logical Flow

```text
GUI / API
  -> setup engine
  -> signal engine
  -> entry decision
  -> risk engine
  -> order manager
  -> broker adapter
  -> TWS / IB Gateway
  -> reconciliation
  -> storage / observability / GUI
```

## Module Boundaries

- `gui`: renders state and sends user commands; it must not contain trading safety logic.
- `api`: validates request shape and delegates to services.
- `conversion`: normalizes raw input into canonical setup fields.
- `intelligence`: semantic validation, provenance and ambiguity reporting.
- `engine`: setup, signal, entry decision, risk, order and reconciliation workflows.
- `broker`: TWS abstraction and IBKR-specific mapping.
- `storage`: history, setup persistence, event store and SQLite repositories.
- `forecasting`: advisory signals only.
- `opportunities`: non-executable discovery and scenario drafts only.
- `observability`: decision traces, audit logs, health and runtime diagnostics.

## Hard Rules

- Only the order manager may send, modify or cancel broker orders.
- No direct TWS calls are allowed from GUI, route handlers, scanners, forecasting providers or setup conversion.
- Broker state is refreshed and reconciled before new entry execution.
- Runtime trading code must not depend on optional forecasting packages.
- Storage is not broker truth; it records intent, history and traces.

## Current Priority

V2.4 stabilizes core execution safety before new product features:

```text
canonical setup model
trailing_stop_loss
broker reality
risk engine
order manager
reconciliation
entry_decision
dashboard broker reality
golden tests
```
