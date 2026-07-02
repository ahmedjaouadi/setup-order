# Golden Tests Contract

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

Golden tests protect the non-negotiable contracts while the implementation is stabilized.

## Blocking Test Cases

The V2.4 suite must cover:

- generated template contains root `trailing_stop_loss`;
- generated template contains `trailing_stop_loss.initial_stop`;
- generated template does not contain `risk.initial_stop_loss` as a primary field;
- generated template does not contain `risk.protective_stop` as a primary field;
- `_template.required_by_setup_type` does not require legacy stop paths;
- legacy stop aliases map to `trailing_stop_loss.initial_stop`;
- validator reads canonical stop fields;
- no arming without `trailing_stop_loss.initial_stop`;
- no order without broker stop protection ready or attached;
- no BUY from `MANAGEMENT_ONLY`;
- GUI shows entry ready only when `entry_decision` is `ENTRY_READY`;
- opportunity keeps `can_send_order=false`;
- forecast providers keep `use_for_execution=false`;
- broker stale or mismatch blocks new entries.

## Test Policy

Contract tests should be small, deterministic and close to the boundary they protect.

When a test exposes a gap, document it in `docs/implementation-status.md` until the code is corrected.
