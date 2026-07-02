# Trailing Stop-Loss Contract

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

`trailing_stop_loss` is the primary protection model for all new final setups.

## Canonical Field

The canonical initial stop is:

```text
trailing_stop_loss.initial_stop
```

The risk engine, validator, arming flow and order manager must use this canonical field.

## Required Shape

```text
trailing_stop_loss.enabled = true
trailing_stop_loss.initial_stop is not null
trailing_stop_loss.never_lower_stop = true
```

`never_lower_stop` is mandatory for executable setups.

## Legacy Aliases

Accepted input aliases:

```text
SL
stop_loss
initial_stop_loss
protective_stop
```

These aliases must be normalized into:

```text
trailing_stop_loss.initial_stop
```

They must not remain as primary final fields.

## Arming Rules

- No arming without `trailing_stop_loss.initial_stop`.
- No arming if stop direction is incoherent with position direction.
- No arming if `never_lower_stop` is absent or false for an executable setup.
- No arming if the setup role does not permit entry and the requested action is a new entry.

## Order Rules

- No entry order may be transmitted without broker stop protection ready or attached.
- Parent/child or bracket logic must be validated by the order manager before transmission.
- A rejected or missing stop must block or cancel the unsafe entry path.

## Never-Lower-Stop Rule

For long positions:

```text
new_stop >= current_stop
```

For short positions:

```text
new_stop <= current_stop
```

Stop widening is forbidden by default and requires explicit manual review if ever supported.

## Golden Tests

- Template contains root `trailing_stop_loss`.
- Template does not contain `risk.initial_stop_loss` or `risk.protective_stop` as primary fields.
- Legacy aliases map to `trailing_stop_loss.initial_stop`.
- Validator reads canonical stop fields.
- No entry order is transmitted without broker stop readiness.
