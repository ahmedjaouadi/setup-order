# Opportunity Scanner Contract

Status: V2.4 contract
Last updated: 2026-07-06

## Purpose

The opportunity scanner detects market interest before a setup exists. Its output is discovery only.

## Non-Executable Boundary

Every opportunity must keep:

```text
can_send_order = false
```

An opportunity may only create a draft scenario or setup candidate.

## Required Fields

Opportunity output should include:

- symbol;
- opportunity status;
- opportunity type;
- score;
- reasons;
- warnings;
- recommended next action;
- source snapshot;
- expiration or freshness information;
- `can_send_order=false`.

## Anti-Chase Rule

Extended prices must not become execution permission. They may produce:

```text
DO_NOT_CHASE_EXTENDED_PRICE
WAIT_FOR_RETEST
```

## Scenario Draft Rule

Generated scenarios remain:

```text
status = DRAFT
selection.armed = false
```

They must pass setup validation, trailing stop-loss validation, risk validation, session/data quality checks, broker reconciliation and order-manager preflight before any execution path exists.

## Data Quality Gate (skills.md 28bis)

Before any technique evaluation, the scanner runs `snapshot_quality_issues` (`app/opportunity_scanner/data_quality_gate.py`) on the context snapshot:

- staleness (`timestamp` older than `opportunity_scanner.data_quality.staleness_max_seconds`, default 1800 s) -> `STALE_DATA`;
- OHLC coherence, `bid < ask`, positive spread, price present and > 0 -> `MISSING_MARKET_DATA`.

A failing snapshot produces no technique evaluation and **no detection outcome**; the opportunity is `REJECTED` with `payload["data_quality"] = {status: "PAUSED", reason_code, issues}`. Detection outcomes are only recorded after the liquidity filter passes (`filters.blocked == false`) and the gate passes.

## Decision Codes and Traces (skills.md 2.5)

Status and reason codes are shared constants from `app/decision_codes.py` (also used by `app/engine/trade_guards.py`; detection never imports the order manager). Every qualified refusal (quality gate, liquidity filter) is traced in `decision_traces` with `decision_type="SCANNER_GATE"` and `final_decision="{status}:{reason_code}"`. Rule non-matches remain silent (volumetry).

## Context Tags (skills.md 32.2bis)

Every recorded detection outcome embeds `context_tags` inside `features_snapshot` (queryable via `json_extract`): `time_bucket` (NY time: OPEN / MORNING / LUNCH / AFTERNOON / POWER_HOUR / OFF_HOURS), `rvol_bucket` (`<0.8` / `0.8-1.2` / `1.2-2.0` / `>2.0`), `spread_bucket` (tight / normal / wide), `day_of_week` (NY day), plus reserved columns `market_regime` (UNKNOWN until F3) and `had_catalyst` (None until news/earnings sources exist).

## Rule Language

Declarative rules only — no `eval`/`exec`. Fields are whitelisted in `rule_interpreter.ALIAS_GROUPS`; any extension requires whitelist + truth-table tests. Allowed operators: `>=`, `>`, `<=`, `<`, `==`, `between`, `in` (value = non-empty list of strings). A missing field never raises: the condition simply does not match.

F1 snapshot fields available to rules: `rvol` (canonical, falls back to `relative_volume` / `volume_ratio` / `volume_ratio_15m`), `atr_pct`, `dist_vwap_pct` (session VWAP over RTH 15m bars, None when bars/volume unavailable), `time_bucket`, `price_above_ema20`, `price_above_sma50` (daily). Sequential state (breakout/retest/reclaim) is F2 feature work, never rule-language work.

## Technique Versioning (skills.md 30bis)

`detection_techniques` carries `config_version` and `revision`. Any change to `rule_json` (and only `rule_json`) increments `revision` and records a `TECHNIQUE_REVISION` trace with rule before/after, so past decisions can be replayed with the exact rule of the time. Learning-loop variants are born at `revision=1` with lineage in `parent_id`. Builtins in existing databases received the `spread_pct <= 0.5` filter through a one-shot startup migration (bot_state key `technique_builtin_spread_filter_migration_v1`), traced and revision-bumped; the migration runs regardless of the learning kill-switch because it is not learning.

## Learning Loop Mutation (skills.md 32.2ter)

Variants mutate exactly ONE numeric leaf condition of the parent rule (factor +/-20 % by default), capped by `learning.max_variants_per_parent` (default 4). `mutated_field` and `factor` are stored in the `VARIANT_SPAWNED` trace. `learning.enabled=false` stops all mutation.

## Quality Score (skills.md 9.1)

`compute_quality_score` returns `quality_score` (/100), `score_grade` (>=80 EXCELLENT / 65-79 ACCEPTABLE / 50-64 WEAK / <50 NO_GO) and a `score_breakdown` with 7 weighted components; sub-criteria not computable in F1 contribute 0 and are listed in `score_breakdown.unavailable`. The score never overrides automatic refusals (quality gate, liquidity filter). Legacy `discovery_score` / `risk_adjusted_score` remain during the overlap phase; `_status` still depends on them until the new score has been observed.
