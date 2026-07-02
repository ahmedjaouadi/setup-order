# Intelligence And Conversion Contract

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

The intelligence and conversion layers transform raw input into canonical setup data while preserving provenance and ambiguity.

## Responsibilities

- Resolve aliases into canonical fields.
- Build a canonical setup model.
- Run semantic validation.
- Record provenance for extracted fields.
- Separate save validation from arm validation.
- Report ambiguities instead of silently guessing.

## Stop Alias Rule

Input aliases:

```text
SL
stop_loss
initial_stop_loss
protective_stop
```

must resolve to:

```text
trailing_stop_loss.initial_stop
```

The canonical validator must read the canonical path.

## Validation Modes

- `save_validation`: accepts drafts when safe, with blocking reasons.
- `arm_validation`: requires all execution-critical fields.

## LLM Boundary

LLM extraction is optional and provider-dependent. It must not bypass deterministic canonical validation.

## Historical Notes

Implementation phase notes live under `docs/existing/`:

- `canonical-normalization-phase-1.md`
- `semantic-validation-phase-1.md`
- `intelligence-api-semantic-persistence-phase-1.md`
- `intelligence-gui-history-phase-1.md`
