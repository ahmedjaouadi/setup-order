# Model Lab And Backtest Contract

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

Model Lab and backtests evaluate ideas offline. They do not grant execution permission.

## Required Capabilities

- replay MVP;
- setup-aware backtests;
- forecast-stack comparisons;
- baseline comparisons;
- model scorecards;
- no-leakage walk-forward validation;
- selection policy per symbol, timeframe and horizon.

## Baseline Rule

A model cannot be selected just because it is modern.

It must be compared against:

- naive baseline;
- ATR baseline;
- enough evaluated samples;
- trading-aware metrics.

## Selection Rule

No model becomes preferred without a scorecard that shows it adds value for the relevant symbol, timeframe and horizon.

## Runtime Boundary

Model Lab jobs may be slow and must stay outside the critical live TWS loop.

## Required Trace

Backtest outputs must expose:

- run id;
- dataset/source description;
- assumptions;
- events;
- trades;
- summary metrics;
- limitations.
