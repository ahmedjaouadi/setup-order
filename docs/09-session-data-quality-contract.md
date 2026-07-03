# Session Data Quality Contract

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

Entry decisions and risk checks must know whether the market data context is fresh, coherent and tradable.

## Required Dimensions

Data quality must evaluate:

- RTH, premarket and after-hours session;
- stale quote age;
- missing bid or ask;
- spread width;
- incomplete candle;
- signal bar session vs live quote session;
- halt or abnormal market state when detectable;
- mismatch between snapshot source and current price source.

## Blocking Conditions

New entries must be blocked when:

- live quote is stale beyond configured threshold;
- bid/ask is missing for an order type that needs it;
- spread is too wide;
- broker data is disconnected;
- signal depends on an unclosed bar that is not allowed;
- session mismatch makes the signal invalid.

## Warning Conditions

Warnings may be emitted for:

- premarket or after-hours liquidity;
- partial historical data;
- projected volume estimates;
- metadata gaps;
- fallback data source use.

## Output

Data quality returns:

- status;
- blocking reasons;
- warnings;
- source timestamps;
- session label;
- data freshness age.
