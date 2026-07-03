# Documentation Governance

Status: V2.4 contract
Last updated: 2026-07-02

## Purpose

This document defines where information belongs so `program.md` stays short.

## Document Roles

- `program.md`: root vision, architecture, non-negotiable rules and contract index.
- `docs/00...20`: official product, architecture and safety contracts.
- `docs/existing/`: historical implementation notes and archived phase documents.
- `docs/implementation-status.md`: current implemented, partial and planned state.
- `docs/change-log.md`: chronological change history.
- `docs/known-limitations.md`: known gaps and risks.
- `docs/manual-test-checklist.md`: manual validation steps.
- `docs/module-roadmap.md`: module priorities and sequencing.
- `docs/program-alignment-notes.md`: gaps between code, docs and target architecture.

## Root Document Rule

`program.md` must remain short. It must not contain:

- long YAML or JSON examples;
- detailed SQL;
- module-by-module roadmap;
- changelog;
- exhaustive GUI detail;
- detailed Forecast Stack implementation;
- detailed Opportunity Scanner implementation;
- full test lists.

## Contract Rule

Specialized contract files are the source of truth for business and safety behavior.

Historical phase notes are useful context, but they do not override current contracts.

## Update Rule

Every behavior-changing update must update the relevant contract and living docs:

```text
docs/change-log.md
docs/implementation-status.md
docs/known-limitations.md
docs/manual-test-checklist.md
docs/module-roadmap.md
docs/program-alignment-notes.md
```

Documentation-only architecture changes must at minimum update changelog and implementation status.
