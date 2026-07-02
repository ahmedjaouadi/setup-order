# Risk Engine Contract

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

The risk engine decides whether a setup can become an order plan and what quantity is allowed.

## Inputs

The risk engine must receive:

- canonical setup;
- `setup_role`;
- entry type and prices;
- `trailing_stop_loss.initial_stop`;
- account risk budget;
- existing broker position when any;
- current broker orders and active stops;
- data/session quality result.

## Price Rules

For `STP_LMT` entries, risk must use:

```text
worst_case_entry_price = limit_price
```

not the stop trigger.

For long entries:

```text
risk_per_share = worst_case_entry_price - trailing_stop_loss.initial_stop
```

For short entries:

```text
risk_per_share = trailing_stop_loss.initial_stop - worst_case_entry_price
```

`risk_per_share` must be positive.

## Quantity Rules

The approved quantity is the minimum of:

- budget-based maximum quantity;
- risk-based maximum quantity;
- broker/account constraints;
- setup-specific maximum quantity;
- remaining risk allowed for existing exposure.

## Refusal Rules

The risk engine must refuse when:

- canonical stop is missing;
- stop direction is invalid;
- risk per share is zero or negative;
- broker state is stale;
- setup role forbids the requested action;
- order would exceed configured budget or portfolio risk;
- existing position creates unresolved remaining-risk ambiguity.

## Output

The risk engine returns an approval object with:

- `approved`;
- approved quantity;
- worst-case entry price;
- risk per share;
- maximum loss estimate;
- blocking reasons;
- warnings;
- calculation trace.
