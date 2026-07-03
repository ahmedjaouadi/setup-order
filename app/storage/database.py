from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any, Iterator, Iterable


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._connection: sqlite3.Connection | None = None
        self._lock = RLock()

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(
                self.path,
                check_same_thread=False,
                isolation_level=None,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
        return self._connection

    def initialize(self) -> None:
        with self._lock:
            conn = self.connection
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS setups (
                    setup_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    setup_type TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    entry_zone TEXT NOT NULL DEFAULT '',
                    stop_loss REAL,
                    risk_amount REAL,
                    order_status TEXT NOT NULL DEFAULT '',
                    position_status TEXT NOT NULL DEFAULT '',
                    last_event TEXT NOT NULL DEFAULT '',
                    config_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    setup_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    trigger_price REAL,
                    limit_price REAL,
                    stop_price REAL,
                    broker_order_id TEXT,
                    broker_perm_id TEXT,
                    parent_id TEXT,
                    oca_group TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY,
                    setup_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    average_price REAL NOT NULL,
                    current_price REAL NOT NULL,
                    unrealized_pnl REAL NOT NULL,
                    current_stop REAL,
                    risk_remaining REAL NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    setup_id TEXT,
                    symbol TEXT,
                    message TEXT NOT NULL,
                    data_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS symbol_metadata (
                    symbol TEXT PRIMARY KEY,
                    company_name TEXT,
                    sector TEXT,
                    industry TEXT,
                    sector_etf TEXT,
                    market_cap REAL,
                    country TEXT,
                    exchange TEXT,
                    index_membership TEXT,
                    custom_priority INTEGER,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS corporate_dividends (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    dividend_12m_past REAL,
                    dividend_12m_forward REAL,
                    next_dividend_date TEXT,
                    next_dividend_amount REAL,
                    source TEXT NOT NULL,
                    raw_payload_json TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS corporate_earnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    event_time TEXT,
                    timing TEXT,
                    fiscal_period TEXT,
                    source TEXT NOT NULL,
                    raw_payload_json TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economic_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_date TEXT NOT NULL,
                    event_time TEXT,
                    country TEXT,
                    currency TEXT,
                    event_name TEXT NOT NULL,
                    importance TEXT,
                    actual TEXT,
                    forecast TEXT,
                    previous TEXT,
                    source TEXT NOT NULL,
                    raw_payload_json TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS semantic_analyses (
                    analysis_id TEXT PRIMARY KEY,
                    setup_id TEXT,
                    symbol TEXT NOT NULL,
                    request_id TEXT,
                    idempotency_key TEXT,
                    analysis_hash TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    raw_input_text TEXT NOT NULL,
                    primary_scenario_id TEXT,
                    save_validation_json TEXT NOT NULL,
                    arm_validation_json TEXT NOT NULL,
                    issues_json TEXT NOT NULL,
                    confidence_json TEXT NOT NULL DEFAULT '{}',
                    schema_version TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    canonical_mapper_version TEXT NOT NULL,
                    prompt_version TEXT,
                    llm_model TEXT,
                    previous_analysis_id TEXT,
                    provider_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(previous_analysis_id) REFERENCES semantic_analyses(analysis_id)
                );

                CREATE TABLE IF NOT EXISTS extracted_scenarios (
                    scenario_id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    scenario_name TEXT NOT NULL,
                    scenario_role TEXT NOT NULL,
                    setup_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    selected INTEGER NOT NULL DEFAULT 0,
                    armed INTEGER NOT NULL DEFAULT 0,
                    confidence_json TEXT NOT NULL DEFAULT '{}',
                    canonical_config_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(analysis_id) REFERENCES semantic_analyses(analysis_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS extracted_fields (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    analysis_id TEXT NOT NULL,
                    scenario_id TEXT,
                    raw_key TEXT NOT NULL,
                    normalized_key TEXT NOT NULL,
                    canonical_path TEXT NOT NULL,
                    raw_value TEXT,
                    parsed_value_json TEXT,
                    source_text TEXT,
                    source_line_start INTEGER,
                    source_line_end INTEGER,
                    extraction_method TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    validation_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(analysis_id) REFERENCES semantic_analyses(analysis_id) ON DELETE CASCADE,
                    FOREIGN KEY(scenario_id) REFERENCES extracted_scenarios(scenario_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS ambiguities (
                    ambiguity_id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL,
                    scenario_id TEXT,
                    field_path TEXT NOT NULL,
                    message TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL,
                    resolution_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(analysis_id) REFERENCES semantic_analyses(analysis_id) ON DELETE CASCADE,
                    FOREIGN KEY(scenario_id) REFERENCES extracted_scenarios(scenario_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS forecast_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    setup_id TEXT,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    model_version TEXT,
                    target TEXT NOT NULL,
                    context_bars INTEGER NOT NULL,
                    horizon_bars INTEGER NOT NULL,
                    input_start_time TEXT,
                    input_end_time TEXT,
                    generated_at TEXT NOT NULL,
                    current_price REAL,
                    forecast_expected_return_pct REAL,
                    forecast_last_price REAL,
                    metric_score INTEGER NOT NULL,
                    forecast_status TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    q10_above_support INTEGER,
                    median_above_entry_trigger INTEGER,
                    forecast_payload_json TEXT NOT NULL,
                    used_for_decision INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS opportunities (
                    opportunity_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    opportunity_type TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    status TEXT NOT NULL,
                    score REAL,
                    detected_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS scenario_drafts (
                    scenario_id TEXT PRIMARY KEY,
                    source_opportunity_id TEXT,
                    symbol TEXT NOT NULL,
                    setup_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    scenario_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reviewed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS feature_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS data_quality_events (
                    event_id TEXT PRIMARY KEY,
                    symbol TEXT,
                    severity TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS setup_scores (
                    score_id TEXT PRIMARY KEY,
                    setup_id TEXT,
                    scenario_id TEXT,
                    opportunity_id TEXT,
                    symbol TEXT,
                    overall_score REAL NOT NULL,
                    score_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_runs (
                    forecast_id TEXT PRIMARY KEY,
                    setup_id TEXT,
                    scenario_id TEXT,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    forecast_json TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS forecast_ensembles (
                    ensemble_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    status TEXT NOT NULL,
                    member_forecast_ids_json TEXT NOT NULL DEFAULT '[]',
                    ensemble_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS backtest_runs (
                    backtest_id TEXT PRIMARY KEY,
                    setup_id TEXT,
                    scenario_id TEXT,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    config_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS backtest_trades (
                    trade_id TEXT PRIMARY KEY,
                    backtest_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    entry_time TEXT,
                    exit_time TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    quantity INTEGER,
                    pnl REAL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS backtest_events (
                    event_id TEXT PRIMARY KEY,
                    backtest_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    symbol TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_benchmarks (
                    benchmark_id TEXT PRIMARY KEY,
                    model_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    beats_baseline INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_scorecards (
                    scorecard_id TEXT PRIMARY KEY,
                    model_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    horizon_bars INTEGER NOT NULL,
                    metrics_json TEXT NOT NULL,
                    baseline_comparison_json TEXT NOT NULL,
                    selection_decision TEXT NOT NULL,
                    sample_size INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_selection_policy (
                    policy_id TEXT PRIMARY KEY,
                    model_name TEXT NOT NULL,
                    symbol TEXT,
                    timeframe TEXT,
                    horizon_bars INTEGER,
                    selection_decision TEXT NOT NULL,
                    weight_multiplier REAL NOT NULL,
                    reason TEXT NOT NULL,
                    policy_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS decision_traces (
                    trace_id TEXT PRIMARY KEY,
                    symbol TEXT,
                    setup_id TEXT,
                    scenario_id TEXT,
                    opportunity_id TEXT,
                    decision_type TEXT NOT NULL,
                    final_decision TEXT NOT NULL,
                    trace_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runtime_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    aggregate_type TEXT,
                    aggregate_id TEXT,
                    symbol TEXT,
                    payload_json TEXT NOT NULL,
                    correlation_id TEXT,
                    causation_id TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    total_exposure_usd REAL NOT NULL,
                    open_positions_count INTEGER NOT NULL,
                    sector_exposure_json TEXT,
                    symbol_exposure_json TEXT,
                    correlation_json TEXT,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    size_reductions_json TEXT NOT NULL DEFAULT '{}',
                    risk_status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS daily_reports (
                    report_id TEXT PRIMARY KEY,
                    report_date TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    markdown TEXT NOT NULL DEFAULT '',
                    html TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecast_outcomes (
                    outcome_id TEXT PRIMARY KEY,
                    forecast_id INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    model_version TEXT,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    horizon_bars INTEGER NOT NULL,
                    generated_at TEXT NOT NULL,
                    evaluation_due_at TEXT NOT NULL,
                    evaluated_at TEXT,
                    forecast_price_start REAL,
                    forecast_price_end REAL,
                    actual_price_end REAL,
                    forecast_direction TEXT,
                    actual_direction TEXT,
                    direction_correct INTEGER,
                    absolute_error REAL,
                    squared_error REAL,
                    percentage_error REAL,
                    forecast_target_time TEXT,
                    predicted_return_pct REAL,
                    direction_confidence REAL,
                    actual_return_pct REAL,
                    signed_error REAL,
                    entry_price_reference REAL,
                    stop_price_reference REAL,
                    entry_touched_before_horizon INTEGER,
                    stop_touched_before_horizon INTEGER,
                    stop_touched_before_entry INTEGER,
                    quality_bucket TEXT,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(forecast_id)
                );

                CREATE TABLE IF NOT EXISTS forecast_accuracy_scorecards (
                    scorecard_id TEXT PRIMARY KEY,
                    model_name TEXT NOT NULL,
                    symbol TEXT,
                    timeframe TEXT,
                    horizon_bars INTEGER,
                    sample_size INTEGER NOT NULL,
                    direction_accuracy REAL,
                    mae REAL,
                    rmse REAL,
                    mape REAL,
                    median_absolute_error REAL,
                    entry_touch_accuracy REAL,
                    stop_before_entry_error_rate REAL,
                    calibration_score REAL,
                    enough_data INTEGER NOT NULL DEFAULT 0,
                    min_required_samples INTEGER NOT NULL DEFAULT 30,
                    reliability_grade TEXT NOT NULL,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS setup_creation_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    setup_id TEXT NOT NULL UNIQUE,
                    scenario_id TEXT,
                    opportunity_id TEXT,
                    symbol TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    last_price REAL,
                    bid REAL,
                    ask REAL,
                    mid_price REAL,
                    spread_pct REAL,
                    volume REAL,
                    volume_ratio REAL,
                    atr_15m REAL,
                    atr_1h REAL,
                    vwap REAL,
                    entry_trigger_price REAL,
                    entry_limit_price REAL,
                    trailing_stop_initial_stop REAL,
                    distance_to_trigger_pct REAL,
                    distance_to_limit_pct REAL,
                    distance_to_stop_pct REAL,
                    data_quality_status TEXT NOT NULL,
                    data_quality_issues_json TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS forecast_stack_experiments (
                    experiment_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    symbols_json TEXT NOT NULL,
                    timeframes_json TEXT NOT NULL,
                    horizons_json TEXT NOT NULL,
                    models_json TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    summary_json TEXT
                );

                CREATE TABLE IF NOT EXISTS forecast_stack_results (
                    result_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    horizon_bars INTEGER NOT NULL,
                    metrics_json TEXT NOT NULL,
                    trading_metrics_json TEXT,
                    rank_overall INTEGER,
                    selected_for_symbol INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_setups_status ON setups(status);
                CREATE INDEX IF NOT EXISTS idx_orders_setup_id ON orders(setup_id);
                CREATE INDEX IF NOT EXISTS idx_orders_broker_perm_id ON orders(broker_perm_id);
                CREATE INDEX IF NOT EXISTS idx_events_setup_id ON events(setup_id);
                CREATE INDEX IF NOT EXISTS idx_events_symbol ON events(symbol);
                CREATE INDEX IF NOT EXISTS idx_events_level ON events(level);
                CREATE INDEX IF NOT EXISTS idx_corporate_dividends_symbol ON corporate_dividends(symbol);
                CREATE INDEX IF NOT EXISTS idx_corporate_earnings_symbol ON corporate_earnings(symbol);
                CREATE INDEX IF NOT EXISTS idx_economic_events_date ON economic_events(event_date);
                CREATE INDEX IF NOT EXISTS idx_semantic_analyses_setup_id ON semantic_analyses(setup_id);
                CREATE INDEX IF NOT EXISTS idx_semantic_analyses_analysis_hash ON semantic_analyses(analysis_hash);
                CREATE INDEX IF NOT EXISTS idx_semantic_analyses_request_id ON semantic_analyses(request_id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_semantic_analyses_idempotency_key
                    ON semantic_analyses(idempotency_key)
                    WHERE idempotency_key IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_extracted_scenarios_analysis_id ON extracted_scenarios(analysis_id);
                CREATE INDEX IF NOT EXISTS idx_extracted_fields_analysis_id ON extracted_fields(analysis_id);
                CREATE INDEX IF NOT EXISTS idx_extracted_fields_scenario_id ON extracted_fields(scenario_id);
                CREATE INDEX IF NOT EXISTS idx_extracted_fields_canonical_path ON extracted_fields(canonical_path);
                CREATE INDEX IF NOT EXISTS idx_ambiguities_analysis_id ON ambiguities(analysis_id);
                CREATE INDEX IF NOT EXISTS idx_ambiguities_scenario_id ON ambiguities(scenario_id);
                CREATE INDEX IF NOT EXISTS idx_forecast_metrics_symbol_timeframe
                    ON forecast_metrics(symbol, timeframe, generated_at);
                CREATE INDEX IF NOT EXISTS idx_forecast_metrics_setup_id
                    ON forecast_metrics(setup_id);
                CREATE INDEX IF NOT EXISTS idx_opportunities_symbol_status
                    ON opportunities(symbol, status, detected_at);
                CREATE INDEX IF NOT EXISTS idx_scenario_drafts_opportunity
                    ON scenario_drafts(source_opportunity_id);
                CREATE INDEX IF NOT EXISTS idx_feature_snapshots_symbol_timeframe
                    ON feature_snapshots(symbol, timeframe, created_at);
                CREATE INDEX IF NOT EXISTS idx_data_quality_events_symbol
                    ON data_quality_events(symbol, created_at);
                CREATE INDEX IF NOT EXISTS idx_setup_scores_setup_id
                    ON setup_scores(setup_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_forecast_runs_symbol_timeframe
                    ON forecast_runs(symbol, timeframe, created_at);
                CREATE INDEX IF NOT EXISTS idx_backtest_runs_setup_id
                    ON backtest_runs(setup_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_backtest_events_run
                    ON backtest_events(backtest_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_model_benchmarks_model_symbol
                    ON model_benchmarks(model_name, symbol, timeframe);
                CREATE INDEX IF NOT EXISTS idx_model_scorecards_lookup
                    ON model_scorecards(model_name, symbol, timeframe, horizon_bars);
                CREATE INDEX IF NOT EXISTS idx_decision_traces_symbol
                    ON decision_traces(symbol);
                CREATE INDEX IF NOT EXISTS idx_decision_traces_setup
                    ON decision_traces(setup_id);
                CREATE INDEX IF NOT EXISTS idx_decision_traces_created
                    ON decision_traces(created_at);
                CREATE INDEX IF NOT EXISTS idx_runtime_events_type
                    ON runtime_events(event_type);
                CREATE INDEX IF NOT EXISTS idx_runtime_events_symbol
                    ON runtime_events(symbol);
                CREATE INDEX IF NOT EXISTS idx_runtime_events_created
                    ON runtime_events(created_at);
                CREATE INDEX IF NOT EXISTS idx_forecast_outcomes_due
                    ON forecast_outcomes(status, evaluation_due_at);
                CREATE INDEX IF NOT EXISTS idx_forecast_outcomes_lookup
                    ON forecast_outcomes(model_name, symbol, timeframe, horizon_bars);
                CREATE INDEX IF NOT EXISTS idx_forecast_accuracy_lookup
                    ON forecast_accuracy_scorecards(model_name, symbol, timeframe, horizon_bars);
                CREATE INDEX IF NOT EXISTS idx_setup_creation_snapshots_symbol
                    ON setup_creation_snapshots(symbol, captured_at);
                CREATE INDEX IF NOT EXISTS idx_forecast_stack_results_experiment
                    ON forecast_stack_results(experiment_id, rank_overall);
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migrations (version, applied_at)
                VALUES ('intelligence_v1', CURRENT_TIMESTAMP)
                """
            )
            self._ensure_column(
                conn,
                "semantic_analyses",
                "confidence_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(
                conn,
                "extracted_scenarios",
                "confidence_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(
                conn,
                "ambiguities",
                "metadata_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(
                conn,
                "portfolio_snapshots",
                "warnings_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            self._ensure_column(
                conn,
                "portfolio_snapshots",
                "size_reductions_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            for column, definition in (
                ("forecast_target_time", "TEXT"),
                ("predicted_return_pct", "REAL"),
                ("direction_confidence", "REAL"),
                ("actual_return_pct", "REAL"),
                ("signed_error", "REAL"),
                ("entry_price_reference", "REAL"),
                ("stop_price_reference", "REAL"),
                ("entry_touched_before_horizon", "INTEGER"),
                ("stop_touched_before_horizon", "INTEGER"),
                ("stop_touched_before_entry", "INTEGER"),
                ("quality_bucket", "TEXT"),
            ):
                self._ensure_column(conn, "forecast_outcomes", column, definition)
            for column, definition in (
                ("median_absolute_error", "REAL"),
                ("entry_touch_accuracy", "REAL"),
                ("stop_before_entry_error_rate", "REAL"),
                ("calibration_score", "REAL"),
                ("enough_data", "INTEGER NOT NULL DEFAULT 0"),
                ("min_required_samples", "INTEGER NOT NULL DEFAULT 30"),
            ):
                self._ensure_column(
                    conn,
                    "forecast_accuracy_scorecards",
                    column,
                    definition,
                )
            self._ensure_column(
                conn,
                "setup_creation_snapshots",
                "trailing_stop_initial_stop",
                "REAL",
            )
            self._ensure_column(
                conn,
                "setups",
                "status_reason",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn,
                "setups",
                "last_revalidated_at",
                "TEXT",
            )
            self._migrate_setup_creation_snapshot_stop_column(conn)
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migrations (version, applied_at)
                VALUES ('intelligence_v2_confidence', CURRENT_TIMESTAMP)
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migrations (version, applied_at)
                VALUES ('intelligence_v3_ambiguity_metadata', CURRENT_TIMESTAMP)
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migrations (version, applied_at)
                VALUES ('runtime_v2_events_and_traces', CURRENT_TIMESTAMP)
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migrations (version, applied_at)
                VALUES ('v2_1_shortlist_backtest_model_scorecards_reports', CURRENT_TIMESTAMP)
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migrations (version, applied_at)
                VALUES ('v2_3_forecast_accuracy_snapshots_stack', CURRENT_TIMESTAMP)
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migrations (version, applied_at)
                VALUES ('v2_3_forecast_accuracy_complete_contract', CURRENT_TIMESTAMP)
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migrations (version, applied_at)
                VALUES ('v2_4_1_snapshot_trailing_stop_initial_stop', CURRENT_TIMESTAMP)
                """
            )

    def execute(
        self,
        statement: str,
        parameters: Iterable[Any] = (),
    ) -> sqlite3.Cursor:
        with self._lock:
            return self.connection.execute(statement, tuple(parameters))

    def executemany(
        self,
        statement: str,
        rows: Iterable[Iterable[Any]],
    ) -> sqlite3.Cursor:
        with self._lock:
            return self.connection.executemany(statement, rows)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self.connection
            conn.execute("BEGIN")
            try:
                yield conn
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_definition: str,
    ) -> None:
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name in columns:
            return
        conn.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
        )

    @staticmethod
    def _column_exists(
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
    ) -> bool:
        return any(
            row["name"] == column_name
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        )

    @classmethod
    def _migrate_setup_creation_snapshot_stop_column(
        cls,
        conn: sqlite3.Connection,
    ) -> None:
        if not cls._column_exists(conn, "setup_creation_snapshots", "initial_stop_loss"):
            return
        conn.execute(
            """
            UPDATE setup_creation_snapshots
            SET trailing_stop_initial_stop = initial_stop_loss
            WHERE trailing_stop_initial_stop IS NULL
              AND initial_stop_loss IS NOT NULL
            """
        )
