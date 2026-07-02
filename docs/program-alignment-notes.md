# Program Alignment Notes

This note complements `program.md` and describes the current repository state in practical terms.

## Documentation reset 2026-07-02

- `program.md` is now the short V2.4 root specification.
- Detailed contracts live in `docs/00-product-vision.md` through `docs/20-documentation-governance.md`.
- The previous expanded `program.md` is archived as `docs/existing/program-v2.3-full-archive.md`.
- Phase notes are historical implementation notes under `docs/existing/`.
- The `trailing_stop_loss` contract is documented before the runtime code correction phase.

## V2.4.1 legacy stop cleanup alignment

- Status: accepted for the scoped Legacy Stop Cleanup phase; broader V2.4 blockers remain tracked outside this cleanup.
- The setup template must emit `trailing_stop_loss.initial_stop` and must not emit `risk.initial_stop_loss` or `risk.protective_stop` as primary fields.
- Legacy stop fields remain accepted only as aliases/migration inputs so older payloads can be read and converted.
- Scoring, forecasting and Model Lab normalize setup configs to the canonical model before reading stop values.
- `entry_decision`, `risk_engine` and `order_manager` block execution when `trailing_stop_loss.broker_order.trailing_stop_order_ready=false`.
- The associated V2.4.1 legacy-stop/golden tests pass.

## What exists today

- `FastAPI` application entrypoint in `app/main.py`
- local configuration in `config.yaml`
- canonical field normalization in `app/conversion/`
- broker abstraction in `app/broker/`
- trading and execution logic in `app/engine/`
- setup definitions and templates in `app/setups/` and `config/schemas/`
- market context helpers in `app/market_context/`
- market data utilities in `app/market_data/`
- semantic validation and intelligence services in `app/intelligence/`
- forecasting services in `app/forecasting/`
- opportunity audit and radar features in `app/opportunity_audit/` and `app/api/routes_opportunity_*.py`
- setup save, arm and disarm endpoints in `app/api/routes_setups.py`
- separate `Sauvegarder setup`, `Armer setup` and `Desarmer setup` actions in `app/gui/templates/setup_detail.html` and the setups list
- `GET /api/setups/{setup_id}/arm-status` exposes preflight arm/disarm validation for the setup detail page
- new saved/imported setups start `DISABLED`; existing setups preserve their runtime armed/disarmed status when saved
- the setup detail `Forecast stack summary` panel opens from cache only and recalculates the runtime forecast stack only on `Recalculer`
- optional Playwright browser coverage for the setup lifecycle lives in `tests/test_setup_lifecycle_browser.py`
- local SQLite storage in `app/storage/` and `data/`
- HTML dashboard templates and static assets in `app/gui/`
- automated tests in `tests/`
- V2.3 forecast accuracy outcomes/scorecards and provider status services in `app/forecasting/`
- immutable setup creation snapshots in `app/setups/creation_snapshot_service.py`
- offline forecast-stack experiments in `app/model_lab/forecast_stack_benchmark.py`
- universal setup template registry in `app/setups/setup_type_registry.py`; the setup template generator now emits `setup_type: CHOOSE_ONE_SETUP_TYPE`, includes `setup_type_options`, `expected_output`, `volume_confirmation_policy_by_setup_type`, exposes all setup blocks in one editable JSON, keeps `entry.order_type` / `volume_confirmation.enabled` on `AUTO_SELECT`, copies `Ticker` from `symbol` in the new-setup form when possible, and includes `trailing_runner`
- the setup template uses `trailing_stop_loss.initial_stop` as the primary initial stop field and no longer emits `risk.initial_stop_loss` or `risk.protective_stop` as primary fields
- final entry decisions are exposed through `metadata.entry_decision` on stock analysis events and consumed by the setup detail GUI
- Forecast Stack provider diagnostics expose worker/dependency/input/forecast/reliability/execution fields, with warmup separated from missing input data

## Key implementation facts

- The default runtime mode is `paper`.
- The database is file-based SQLite.
- Setups are stored as JSON files under `data/setups/`.
- Field aliases are centralized in `config/field_aliases.yaml`.
- Setup validation now includes canonical mapping before business validation.
- Stop-field aliases are for input compatibility/migration only; runtime logic should consume the canonical `trailing_stop_loss.initial_stop` value after normalization.
- The repository already contains semantic validation scaffolding and API endpoints for intelligence features.

## How to read `program.md`

- Treat it as the root target specification and safety index.
- Treat the specialized `docs/00...20` files as detailed contracts.
- Treat the codebase as the current implementation.
- When the document and the code differ, the codebase should be considered authoritative for current behavior.
- New work should update both the implementation and the specification so they stay aligned.
- As of 2026-07-02, V2.4 architecture stabilization is the active phase; automatic Forecast Stack bootstrap per new symbol and full session mismatch classification are deferred until after core safety stabilization.

## Recommended follow-up

- Keep a short implementation note in this file whenever a major subsystem is added.
- Update `program.md` only for architecture and safety rules.
- Prefer smaller companion docs for feature-specific design decisions.
- Record every behavior-changing fix in `docs/change-log.md` with the date, the affected subsystem, and the reason for the change.
- If a fix changes performance or detection timing, document the before/after behavior explicitly so the trace stays actionable.
