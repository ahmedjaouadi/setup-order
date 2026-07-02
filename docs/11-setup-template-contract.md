# Setup Template Contract

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

The generated setup template must guide a human or expert system toward one canonical final setup without legacy stop-field drift.

## Required Template Elements

The universal template must include:

- `setup_type: CHOOSE_ONE_SETUP_TYPE`;
- complete `setup_type_options`;
- `entry.order_type: AUTO_SELECT`;
- `volume_confirmation.enabled: AUTO_SELECT`;
- `expected_output`;
- `_template` helper metadata;
- all supported setup blocks needed by the generator;
- root `trailing_stop_loss`;
- explicit instructions to return one final canonical setup.

## Trailing Stop Requirements

The generated template must include:

```text
trailing_stop_loss.enabled = true
trailing_stop_loss.initial_stop
trailing_stop_loss.never_lower_stop = true
```

## Forbidden Primary Fields

The generated final setup must not include these as primary fields:

```text
risk.initial_stop_loss
risk.protective_stop
```

`_template.required_by_setup_type` must not require either legacy path.

## Helper Removal

Before saving a final setup, helper fields must be removed or ignored:

- `_template`;
- `expected_output`;
- `setup_type_options`;
- policy hints that are not part of the canonical setup.

## Golden Tests

- Template contains root `trailing_stop_loss`.
- Template contains `trailing_stop_loss.initial_stop`.
- Template keeps legacy stop fields out of primary risk.
- Save/canonicalization strips helper metadata.
- A pasted wrapper still resolves to one canonical setup.
