# Change Log

This file records implementation changes that affect behavior, performance, or safety.

## 2026-07-02

### V2.4.1 legacy stop cleanup

- Marked the V2.4.1 Legacy Stop Cleanup step as accepted; broader V2.4 architecture stabilization still keeps non-scoped cleanup blockers tracked separately.
- The setup template must now emit `trailing_stop_loss.initial_stop` as the canonical initial stop and must not emit `risk.initial_stop_loss` or `risk.protective_stop` as primary fields.
- Legacy `initial_stop_loss` and `protective_stop` names remain accepted only as aliases/migration inputs, not as canonical runtime fields.
- Removed remaining scoped runtime fallbacks to `protective_stop` around setup creation snapshots, storage enrichment, GUI display/search/charting, scoring, forecasting references and Model Lab replay.
- Setup creation snapshots now persist the canonical `trailing_stop_loss.initial_stop` payload and use the SQLite column `trailing_stop_initial_stop`; existing `initial_stop_loss` columns are backfilled by an additive migration when present.
- The setups GUI now displays `trailing_stop_loss.initial_stop` instead of `protective_stop` in list/detail/snapshot/chart surfaces.
- Scoring, forecasting and Model Lab now canonicalize setup configs before reading stop fields.
- Entry decision, RiskEngine and OrderManager keep execution blocked when `trailing_stop_loss.broker_order.trailing_stop_order_ready=false`.
- Added regression coverage in `tests/test_v241_legacy_stop_cleanup.py`; the associated legacy-stop/golden tests pass.

### Documentation architecture reset and trailing_stop_loss contract preparation

- Archived the previous expanded `program.md` under `docs/existing/program-v2.3-full-archive.md`.
- Replaced `program.md` with a short V2.4 root specification focused on vision, architecture, non-negotiable safety rules and the contract index.
- Added the V2.4 contract set from `docs/00-product-vision.md` through `docs/20-documentation-governance.md`.
- Moved historical phase notes into `docs/existing/`.
- Added manual checklist coverage for the root `trailing_stop_loss` template contract and removal of legacy primary stop fields.
- No runtime behavior was changed in this documentation-only step.

## 2026-06-30

### Orders book clarity and faster live PnL

- Clarified `Orders & Positions` so the top `Ordres actifs` card still counts only `CREATED` and `SUBMITTED`, while the orders table now explicitly presents local history and shows separate visible counts for active vs historical rows.
- Added per-order diagnostics in the orders table from recent runtime events so cancelled/unprotected entries explain whether they were rejected, cancelled, or stopped before protective-stop attachment.
- Improved TWS order-result diagnostics to keep broker-side status details such as `Error 10349 ... Order TIF was set to DAY based on order preset`, which makes cases like QCOM easier to understand from the UI/runtime events.
- Added a short-lived broker account cache in `TradingEngine` and a live `Gain aujourd'hui` estimate based on `realized_pnl + current positions unrealized pnl`, so the dashboard reacts faster to price moves without waiting for every broker account refresh.
- Added regression coverage in `tests/test_account_metrics.py` and `tests/test_tws_logging.py`.

### Opportunity Scanner from Market Context

- Added `app/opportunity_scanner/` as the explicit Market Context -> Opportunity Scanner layer with detectors, scoring thresholds, schemas and a repository adapter.
- Market Context responses now expose non-executable opportunity fields and badges: `opportunity_status`, `opportunity_type`, `opportunity_score`, `reasons`, `warnings`, `recommended_next_action` and `can_send_order=false`.
- The existing `/api/opportunities` scanner now persists these context signals in the `opportunities` table payload while keeping table status compatible with the old shortlist pipeline.
- Added `POST /api/opportunities/{symbol}/create-setup-candidate` as an alias for turning the latest detected symbol opportunity into an unarmed scenario draft.
- Sector metadata gaps now add `SECTOR_METADATA_MISSING` without blocking strong intraday momentum detection; extended prices add `DO_NOT_CHASE_EXTENDED_PRICE` and recommend `WAIT_FOR_RETEST`.
- Added regression coverage for CAST-like moves, anti-chase/retest recommendation and sector-relative strength detection in `tests/test_opportunity_scanner_market_context.py`.

## 2026-06-28

### Universal setup template anti-bias refinement

- Changed the universal setup request template so `entry.order_type` is now `AUTO_SELECT` instead of a breakout-biased `STP_LMT`.
- Changed `volume_confirmation.enabled` to `AUTO_SELECT` in the copied template and added `volume_confirmation_policy_by_setup_type` so management-only setups are not biased toward entry-volume logic.
- Added an explicit `expected_output` section that tells the expert to return one final canonical setup only, replace `CHOOSE_ONE_SETUP_TYPE` / `AUTO_SELECT`, and remove helper metadata before saving.
- Extended canonical save unwrapping so helper metadata such as `expected_output` and `volume_confirmation_policy_by_setup_type` is stripped automatically if a template wrapper is pasted back into the app.
- The `Nouveau setup` form now copies the `Ticker` field from `symbol` when the pasted JSON already carries the symbol, so the manual save path stays aligned with the config payload.

## 2026-06-26

### Generic setup template, entry decisions, volume diagnostics and forecast status clarity

- Replaced the setup configuration generator with a universal editable skeleton sourced from a central setup type registry instead of a momentum-breakout-biased template.
- The copied setup JSON now starts with `setup_type: CHOOSE_ONE_SETUP_TYPE`, exposes `setup_type_options`, keeps all supported setup blocks visible in one place, and stores helper instructions under `_template` so save/canonicalization can strip them automatically.
- Added full `trailing_runner` alignment across backend supported types, JSON schema validation and the GUI type list.
- Added a normalized `entry_decision` payload to stock analysis results so the GUI displays the engine's final decision, not a reconstructed verdict from partial price/volume checks.
- Kept the generic anti-chase rule explicit for missed long breakouts with `PRICE_TOO_FAR_ABOVE_ENTRY` and `ASK_ABOVE_MAXIMUM_LIMIT_PLUS_STALE_BUFFER`.
- Enriched momentum volume diagnostics with closed ratio, projected live ratio, sample count, comparison mode and a separate execution liquidity status.
- Added `entry_decision` and `volume_confirmation_policy_by_setup_type` defaults to `config.yaml` and `DEFAULT_CONFIG`.
- Expanded Forecast Stack provider diagnostics with worker/dependency/input/forecast/reliability/execution fields; `Samples = 0` now appears as `ACCURACY_HISTORY_WARMUP`/`WARMUP` instead of generic `INSUFFICIENT_DATA`.
- Clarified Forecast Stack consensus policy as `ADVISORY_ONLY`; mixed consensus is a warning/scoring signal and never an execution trigger.
- Added regression coverage in `tests/test_setup_template_service.py`, `tests/test_setup_tools.py`, `tests/test_setups.py`, `tests/test_signal_engine.py`, `tests/test_forecast_stack_missing_modules.py`, `tests/test_v23_forecast_stack.py` and `tests/test_v23_forecast_stack_contract.py`.

## 2026-06-21

### V2.3 forecast stack completion

- Added install-ready optional provider tiers, automatic role-safe activation and a standalone readiness checker.
- Updated Chronos for quantile inference, added bundled Lag-Llama/Moirai compatibility bridges, and enabled ephemeral NeuralForecast/AutoGluon benchmark training without a saved model path.
- Added native offline Darts model fitting from raw series while retaining deterministic baselines and the no-execution boundary.
- Completed the Forecast Accuracy Ledger contract with target timestamps, realized returns, signed/percentage errors, entry/stop touch ordering, quality buckets, calibration and expanded scorecards.
- Embedded immutable creation snapshots in setup configuration while preserving entry prices and allowing warning-quality snapshots.
- Added normalized adapter capabilities/results, probabilistic Lag-Llama fields and safe optional-dependency degradation.
- Added TimesFM/Chronos consensus, Lag-Llama stop-risk penalties and reliability-gated score impact; all outputs remain scoring-only.
- Added probabilistic and trading-aware Model Lab metrics, no-leakage walk-forward validation and baseline-gated selection per symbol/timeframe/horizon.
- Expanded Research and setup detail views with provider health, reliability, experiment rankings and forecast-stack summaries.
- Added the section 60/61/67 contract suite in `tests/test_v23_forecast_stack_contract.py`.

## 2026-06-20

### UI consolidation hubs

- Reduced the top navigation to a smaller set of clearer destinations: `Dashboard`, `Radar`, `Setups`, `Orders & Positions`, `Observability`, `Research`, `Config`.
- Expanded `/opportunity-radar` into a single discovery page that now includes market context, hot-zone setups, scanner status/config, and the opportunity pipeline tables.
- Added `/observability` to group runtime health, metrics, portfolio risk, event logs, TWS exchanges, and decision traces in one place.
- Added `/research` to group forecasting catalog, provider stack, benchmarks, forecast-stack experiments, and backtests in one place.
- Redirected older split pages such as `/opportunities`, `/scanner`, `/logs`, `/system-health`, `/decision-trace`, `/forecasting/stack`, and `/model-lab/forecast-stack` to the new consolidated hubs.

### V2.3 forecast stack and creation snapshots

- Added the Forecast Accuracy Ledger with pending outcomes, horizon-based evaluation, reliability grades and persisted scorecards.
- Added immutable setup creation market snapshots with non-blocking warnings when no market quote is available.
- Added normalized provider statuses and enforced `scoring_only` / `use_for_execution=false` across the forecast stack.
- Gated `forecast_alignment_score` boosts by historical reliability: A/B full, C reduced, and no boost without sufficient history.
- Added offline forecast-stack comparisons, persisted experiments/results and an optional Darts status boundary outside the live runtime.
- Added `/forecasting/stack` and `/model-lab/forecast-stack` pages plus the corresponding APIs.
- Added focused regression coverage in `tests/test_v23_forecast_stack.py`.
- Stabilized semantic-analysis pagination when several revisions share the same timestamp by using SQLite insertion order as the tie-breaker.

### V2.1 priority modules

- Added explicit forecast operational helpers in `app/forecasting/` so cached forecasts become scoring signals only and never executable actions.
- Added opportunity shortlist, scenario draft generation, lifecycle expiration/review and explanation services in `app/opportunities/`.
- Added `GET /api/opportunities/shortlist`, `POST /api/opportunities/rebuild-shortlist`, `POST /api/opportunities/{opportunity_id}/generate-scenario-draft`, `mark-reviewed`, `expire` and `explain`.
- Generated scenarios are persisted as non-armed `DRAFT` objects and record a decision trace.
- Added Decision Trace service/model/repository wrappers and `GET /api/decision-trace/entity/{entity_type}/{entity_id}`.
- Added replay/backtest MVP support with conservative `STP_LMT` fills, persisted backtest events/trades and report endpoints.
- Added model scorecards and model selection policy persistence for TimesFM/baseline evaluation.
- Added daily report service and `/api/reports/daily/*` endpoints.
- Updated `/opportunities` GUI data loading to show top, blocked, expired and generated scenario sections.

## 2026-06-19

### Setup arm flow

- Added `POST /api/setups/{setup_id}/arm` in `app/api/routes_setups.py` so arming is distinct from saving.
- Added `POST /api/setups/{setup_id}/disarm` in `app/api/routes_setups.py` so disarming is also a runtime action distinct from saving.
- Added `TradingEngine.arm_setup()` in `app/engine/trading_engine.py` to keep the API layer thin and reusable.
- Added `TradingEngine.disarm_setup()` in `app/engine/trading_engine.py` and `SetupEngine.disarm_setup()` in `app/engine/setup_engine.py`.
- Added `Armer setup` and `Desarmer setup` controls in `app/gui/templates/setup_detail.html` and the setups list, wired in `app/gui/static/js/app.js`.
- Added `GET /api/setups/{setup_id}/arm-status` to expose arm/disarm eligibility before clicking.
- Blocked `Desarmer setup` when a setup has active orders or an open position, so active execution/management cannot be silently stopped.
- New setups saved or imported now start as `DISABLED` until explicitly armed.
- Saving an existing setup now preserves its runtime armed/disarmed status instead of reinitializing it.
- Added regression tests in `tests/test_setup_tools.py` and a FastAPI integration test in `tests/test_setup_arm_api.py`.
- Added optional Playwright browser coverage in `tests/test_setup_lifecycle_browser.py`; it runs when Playwright and a browser are available and otherwise skips cleanly.

### Faster entry detection

- Separated the hybrid signal cache from the 15m ATR cache in `app/broker/tws_connector.py`.
- Added a dedicated `hybrid_signal_seconds` TTL in `config.yaml` and `app/settings.py`.
- Kept the historical ATR cache behavior unchanged for `15m`, `1h`, and daily snapshots.
- Added a regression test in `tests/test_tws_logging.py` to ensure the hybrid signal cache uses its own shorter TTL.

### Forecast caching

- Added a forecast reuse path in `app/forecasting/forecast_service.py` for `3m`, `15m`, `1h`, and `1d`.
- Reuse now happens when a recent forecast already exists for the same `symbol + timeframe`.
- Added `cached_only` and `force_refresh` API controls in `app/api/routes_forecast.py`.
- The setup detail page now opens `Forecast stack summary` in cached-only mode and only runs the forecast stack when the user clicks `Recalculer`.
- This avoids unnecessary forecast recomputation for repeated UI refreshes and initial setup detail loads.

## Traceability rule

- Every behavior-changing code update should add an entry here.
- If the change affects a specific subsystem, include the path to the modified file and a one-line reason.
- If the change is user-visible, keep the language simple enough for non-developers to understand.
