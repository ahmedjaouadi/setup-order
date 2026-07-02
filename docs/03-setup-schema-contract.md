# Setup Schema Contract

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

This document defines the target canonical setup shape for saved and armable setups.

## Canonical Setup Requirements

A final setup must include:

- `setup_id` or generated stable identifier;
- `symbol`;
- `setup_type`;
- `setup_role`;
- entry definition when the role allows entries;
- root `trailing_stop_loss`;
- risk parameters;
- validation status and blocking reasons;
- provenance when created from text, opportunity or scenario.

## Setup Roles

Valid roles:

```text
ENTRY_AND_MANAGEMENT
ENTRY_ONLY
MANAGEMENT_ONLY
```

Rules:

- `ENTRY_AND_MANAGEMENT` may create a new entry and manage the resulting position.
- `ENTRY_ONLY` may create a new entry but must not assume ongoing management unless configured.
- `MANAGEMENT_ONLY` may manage an existing broker position but must never create a BUY entry.

## Stop Fields

Canonical field:

```text
trailing_stop_loss.initial_stop
```

Legacy input aliases:

```text
initial_stop_loss
protective_stop
SL
stop_loss
```

Legacy aliases may appear only before canonical normalization. They must not be primary saved fields.

## Save Validation vs Arm Validation

- Save validation may accept incomplete drafts if the state is clearly non-armed and blocking reasons are recorded.
- Arm validation must require every execution safety contract needed for the setup role.
- No arming is allowed without canonical `trailing_stop_loss.initial_stop` when entry execution is possible.

## Forbidden Primary Fields

The canonical saved model must not treat these as primary fields:

```text
risk.initial_stop_loss
risk.protective_stop
```

They are legacy input aliases only.
