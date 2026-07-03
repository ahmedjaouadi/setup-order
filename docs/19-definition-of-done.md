# Definition Of Done

Status: V2.4 contract
Last updated: 2026-07-02

## Global Rule

A change is done only when it is implemented, tested, documented, visible in status and safe by default.

## Required For Behavior Changes

Every behavior-changing update must include:

- code change;
- automated tests when practical;
- documentation update;
- manual checklist update;
- changelog entry;
- implementation-status update;
- visible behavior validation;
- known limitation update when a gap remains.

## Required For Documentation Changes

Documentation-only changes must include:

- changed documents;
- archive or migration note when structure changes;
- changelog entry if the change affects architecture governance;
- implementation-status update if the current phase changes.

## Not Done

A change is not done when:

- it is only implemented but not tested;
- it is tested but undocumented;
- it changes safety behavior without updating contracts;
- it leaves stale instructions in `program.md`;
- it hides known gaps.
