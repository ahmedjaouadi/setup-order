# Observability Contract

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

Every important decision must be traceable after the fact.

## Required Observability

The system must maintain:

- structured logs;
- runtime events;
- decision traces;
- audit trail for setup lifecycle;
- broker sync traces;
- order diagnostics;
- reconciliation diagnostics;
- runtime health status.

## Decision Trace Requirements

Decision traces should answer:

- what was evaluated;
- what data was used;
- which rules passed;
- which rules blocked;
- which warnings were emitted;
- whether broker state was fresh;
- which module made the final decision.

## Broker Diagnostics

Broker errors, rejections and manual mismatches must be visible in logs and GUI-facing diagnostics.

## Safety Rule

If a decision cannot be explained, it must not silently become executable.
