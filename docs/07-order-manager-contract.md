# Order Manager Contract

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

The order manager is the only module allowed to submit, modify or cancel real broker orders.

## Exclusive Authority

No other module may call TWS order placement directly:

- not GUI;
- not API routes;
- not signal engine;
- not risk engine;
- not opportunity scanner;
- not forecasting;
- not setup conversion.

## Required Preflight

Before submitting an entry order, the order manager must verify:

- setup is armed;
- setup role allows entry;
- entry decision is `ENTRY_READY`;
- risk engine approved quantity;
- `trailing_stop_loss.initial_stop` exists;
- broker stop protection is attached or ready;
- broker state is fresh;
- no duplicate active entry order exists;
- order reference and trace identifiers are set.

## Bracket And Parent/Child Rules

Entry orders that create exposure must be protected with broker-side stop logic:

- bracket order when supported;
- parent/child linkage when required;
- explicit cancellation path if protective child cannot be created;
- clear broker diagnostics when IBKR rejects a leg.

## Identity Fields

Orders must track:

- local order id;
- `orderRef`;
- broker `orderId`;
- broker `permId` when available;
- parent/child relation;
- setup id;
- symbol;
- action;
- quantity;
- protection status.

## Safety Rules

- `MANAGEMENT_ONLY` must never create a BUY entry.
- No order is transmitted when stop protection is missing.
- Rejections must be visible in logs, GUI diagnostics and decision trace.
- Unknown broker state blocks new entries.
