# Forecasting Contract

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

Forecasting is advisory. It can influence analysis, score and warnings, but it must not execute trades.

## Execution Boundary

Every provider must keep:

```text
use_for_execution = false
```

Forecast output may not bypass setup validation, risk engine, broker reality or order manager.

## Provider Status

Providers must degrade safely when unavailable:

```text
AVAILABLE
MODEL_NOT_INSTALLED
MODEL_NOT_CONFIGURED
MODEL_NOT_LOADED
MODEL_ERROR
DISABLED_BY_CONFIG
EXPERIMENTAL_ONLY
```

Optional dependencies must not break the runtime application.

## Reliability Rules

- No strong score boost without enough evaluated history.
- Warmup states such as `ACCURACY_HISTORY_WARMUP` are normal.
- Forecast score impact must remain bounded.
- Experimental providers must not influence live execution.

## GUI Rule

The GUI must show provider status, reliability, sample count, last run and last error clearly enough for the user to know whether a forecast is trustworthy.
