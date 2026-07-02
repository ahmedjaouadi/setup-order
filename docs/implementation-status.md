# Implementation Status

## Derniere mise a jour
2026-07-02

## Phase actuelle
- Phase: V2.4 Architecture Stabilization
- Statut: ACCEPTED_WITH_BLOCKERS_REMAINING
- Objectif: reprendre le controle documentaire et architectural avant toute nouvelle feature.
- Priorite: stabiliser le modele canonique de setup, `trailing_stop_loss`, broker reality, risk engine, order manager, reconciliation, `entry_decision`, dashboard broker reality et golden tests.
- Hors scope temporaire: nouveaux providers forecasting, scanners avances, live trading, nouvelles extractions IA et nouvelles fonctions Model Lab.
- Note: V2.4.1 Legacy Stop Cleanup est accepte. La validation V2.4 globale garde les blockers non scopes a traiter separement.

## Implante
- Module: V2.4.1 Legacy Stop Cleanup
- Statut: ACCEPTED
- Fichiers: `app/setups/creation_snapshot_service.py`, `app/storage/database.py`, `app/storage/repositories.py`, `app/gui/static/js/app.js`, `app/scoring/service.py`, `app/forecasting/forecast_service.py`, `app/model_lab/service.py`, `tests/test_v241_legacy_stop_cleanup.py`
- Template: le modele de configuration n'emet plus `risk.initial_stop_loss` ni `risk.protective_stop` comme champs principaux; le stop initial primaire est `trailing_stop_loss.initial_stop`.
- Comportement: `trailing_stop_loss.initial_stop` est la seule lecture runtime du stop initial dans le scope traite; les aliases `initial_stop_loss` et `protective_stop` restent limites a la migration legacy, aux tests d'alias et aux docs archivees.
- Storage: les snapshots de creation ecrivent `trailing_stop_loss.initial_stop` dans le payload et `trailing_stop_initial_stop` en colonne SQLite; les bases existantes avec `initial_stop_loss` sont migrees par backfill additif.
- GUI: la liste, la fiche setup, le snapshot de creation et le graphe affichent le stop initial canonique, sans fallback `protective_stop`.
- Scoring/Forecasting/Model Lab: les setups sont canonicalises avant lecture du stop; le replay Model Lab utilise le stop canonique pour placer le stop simule.
- Securite: aucun `setup.get("protective_stop")` ne reste dans `app`; `entry_decision`, `risk_engine` et `order_manager` empechent `can_send_order=true` quand `trailing_stop_loss.broker_order.trailing_stop_order_ready=false`.
- Tests: les golden associes passent, dont `python -m unittest tests.test_v241_legacy_stop_cleanup tests.test_signal_engine tests.test_forecasting tests.test_v23_forecast_stack tests.test_v23_forecast_stack_contract tests.test_v2_priority_modules`

- Module: Documentation architecture reset V2.4
- Fichiers: `program.md`, `docs/00-product-vision.md` a `docs/20-documentation-governance.md`, `docs/existing/program-v2.3-full-archive.md`
- Comportement: `program.md` devient une root specification courte; les details sont portes par des contrats specialises; les notes de phase historiques sont classees sous `docs/existing/`.
- Securite: le contrat `trailing_stop_loss` rend `trailing_stop_loss.initial_stop` canonique; `initial_stop_loss`, `protective_stop`, `SL` et `stop_loss` restent seulement des alias d'entree/migration.
- Runtime: aucun changement de code applicatif dans cette etape documentaire; l'enforcement scoped est porte par V2.4.1 ci-dessus.

- Module: Opportunity Scanner parallele au Market Context
- Fichiers: `app/opportunity_scanner/__init__.py`, `app/opportunity_scanner/service.py`, `app/opportunity_scanner/detectors.py`, `app/opportunity_scanner/scoring.py`, `app/opportunity_scanner/schemas.py`, `app/opportunity_scanner/repository.py`, `app/opportunities/scanner.py`, `app/opportunities/opportunity_to_scenario_mapper.py`, `app/market_context/service.py`, `app/api/routes_opportunities.py`, `config.yaml`, `app/settings.py`
- API: `/api/opportunities/scan`, `/api/opportunities/rebuild-shortlist`, `/api/opportunities/{symbol}/create-setup-candidate`
- Table: `opportunities` existante, avec `opportunity_status`, `opportunity_type`, `opportunity_score`, `reasons`, `warnings`, `recommended_next_action`, `source_snapshot` et `can_send_order=false` dans `payload_json`
- Comportement: les snapshots Market Context peuvent devenir des signaux non executables `OPPORTUNITY_DETECTED`, `WATCHLIST_OPPORTUNITY`, `WEAK_OPPORTUNITY` ou `NO_OPPORTUNITY`; les secteurs inconnus ajoutent `SECTOR_METADATA_MISSING` sans bloquer un fort momentum intraday; un prix etendu conserve la detection mais recommande `WAIT_FOR_RETEST`.
- Securite: `can_send_order` reste toujours faux; une opportunite peut seulement generer un brouillon de setup non arme, puis doit passer par setup valide, stop, risk engine, session et order manager.
- Tests: `python -m unittest tests.test_opportunity_scanner_market_context tests.test_market_context tests.test_v2_platform_services tests.test_v2_priority_modules`

- Module: Corrections generiques setup/entry/volume/Forecast Stack du 2026-06-28
- Fichiers: `app/setups/setup_type_registry.py`, `app/engine/setup_template_service.py`, `app/setups/setup_factory.py`, `app/setups/trailing_runner.py`, `config/schemas/setup.trailing_runner.schema.json`, `app/engine/entry_decision.py`, `app/engine/signal_engine.py`, `app/setups/momentum_breakout.py`, `app/forecasting/forecast_provider_status.py`, `app/forecasting/forecast_ensemble.py`, `app/gui/static/js/app.js`, `config.yaml`, `app/settings.py`
- Comportement: le template genere par la GUI est un squelette universel editable avec `setup_type: CHOOSE_ONE_SETUP_TYPE`, `setup_type_options`, un guide de choix, tous les blocs possibles, `entry.order_type=AUTO_SELECT`, `volume_confirmation.enabled=AUTO_SELECT`, une `volume_confirmation_policy_by_setup_type`, une section `expected_output` et des aides `_template` ignorees a la sauvegarde; le formulaire `Nouveau setup` recopie automatiquement `Ticker` depuis `symbol` lors du preview/sauvegarde quand le JSON collé contient le symbole; `trailing_runner` est supporte partout; la GUI consomme `entry_decision` comme decision finale; l'anti-chase expose des raisons standardisees; le volume affiche ratio ferme/projete, echantillon et liquidite separee; la Forecast Stack distingue worker, dependances, forecast, reliability warmup et execution advisory.
- Securite: `ENTRY_READY` n'est affiche depuis `entry_decision` que si le moteur produit une entree valide, sans blocking/missing condition, avec setup arme, entree active et role autorisant l'entree; les forecasts restent `ADVISORY_ONLY`.
- Tests: `python -m unittest tests.test_setup_template_service tests.test_setup_tools tests.test_setups tests.test_signal_engine tests.test_forecast_stack_missing_modules tests.test_v23_forecast_stack tests.test_v23_forecast_stack_contract`

- Module: Forecast Stack V2.3 foundation
- Fichiers: `app/forecasting/forecast_accuracy_*`, `base_forecaster.py`, `forecast_stack_config.py`, `forecast_provider_status.py`, `forecast_registry.py`, `forecast_request_builder.py`, `forecast_result_normalizer.py`, `forecast_cache.py`, `forecast_confidence.py`, `forecast_evaluator.py`, `app/model_lab/forecast_stack_benchmark.py`, `darts_experiment_runner.py`, `model_comparison_service.py`, `model_scorecard_service.py`, `model_drift_detector.py`
- API: `/api/forecasting/providers`, `/accuracy/*`, `/outcomes/evaluate-due`, `/scorecards/rebuild`, `/api/model-lab/forecast-stack/*` dont `/run-native`, `/api/model-lab/darts/*`
- Tables: `forecast_outcomes`, `forecast_accuracy_scorecards`, `forecast_stack_experiments`, `forecast_stack_results`
- GUI: `/forecasting/stack`, `/model-lab/forecast-stack`
- Securite: execution forcee en `scoring_only`; aucun provider n'a `use_for_execution=true`
- Scoring: `forecast_alignment_score` reste neutre sans scorecard suffisante, applique 35% du signal en grade C et le plein signal uniquement en grade A/B.
- Ledger: erreurs absolue/signee/pourcentage, retours reels, touch entry/stop, ordre stop-before-entry, calibration, mediane d'erreur et statut `enough_data`.
- Ensemble: consensus TimesFM/Chronos, risque probabiliste Lag-Llama, warnings de divergence et impact borne a `[-12,+12]`.
- Model Lab: MAE/RMSE/MAPE/direction, couverture quantile, calibration, Brier scores, metriques trading-aware, validation walk-forward sans fuite et selection par `symbol+timeframe+horizon` face aux baselines naive et ATR.
- GUI: statuts/configuration/derniere execution/erreur/fiabilite providers, resultats classes des experiences et resume forecast stack sur la fiche setup.
- Tests: `tests/test_v23_forecast_stack_contract.py` et `tests/test_forecast_stack_missing_modules.py` couvrent les contrats obligatoires des sections 60, 61, 63 et 67.
- Readiness: manifests optionnels P1/P2/P3, `install-forecasting.ps1`, auto-activation bornee par role, bridges Lag-Llama/Moirai et diagnostic `scripts/check_forecasting_stack.py`.
- Darts: `POST /api/model-lab/darts/run-experiment` accepte maintenant une serie brute, entraine les modeles Darts offline et ajoute les baselines naive/ATR.
- Providers offline: `POST /api/model-lab/forecast-stack/run-native` execute les adapters installes (TimesFM, Chronos, Lag-Llama, NeuralForecast, AutoGluon, Moirai) sur un holdout brut sans les autoriser dans l'execution d'ordres.

- Module: Setup Creation Market Snapshot
- Fichiers: `app/setups/creation_snapshot_service.py`
- API: `/api/setups/{setup_id}/creation-snapshot`, `/price-drift`, `/capture-creation-snapshot`
- Table: `setup_creation_snapshots`
- Comportement: capture automatique non bloquante a la creation; snapshot jamais ecrase lors d'une edition
- Tests: `tests/test_v23_forecast_stack.py`, `tests/test_v23_forecast_stack_contract.py`

- Module: Forecast Operationalization
- Fichiers: `app/forecasting/forecast_operational_service.py`, `forecast_signal_compiler.py`, `forecast_to_score_mapper.py`
- API: `POST /api/forecasting/run-for-setup/{setup_id}`
- Tables: `forecast_metrics` existante
- Tests: `tests/test_forecasting.py`, `tests/test_v2_priority_modules.py`

- Module: Opportunity Shortlist + Scenario Draft
- Fichiers: `app/opportunities/shortlist_service.py`, `scenario_generator.py`, `opportunity_to_scenario_mapper.py`, `opportunity_lifecycle_service.py`, `opportunity_expiration_policy.py`, `opportunity_explainer.py`
- API: `/api/opportunities/shortlist`, `/rebuild-shortlist`, `/generate-scenario-draft`, `/mark-reviewed`, `/expire`, `/explain`
- Tables: `opportunities`, `scenario_drafts`
- Tests: `tests/test_v2_priority_modules.py`

- Module: Backtest / Replay MVP
- Fichiers: `app/model_lab/service.py`
- API: `/api/backtests/run-mvp`, `/events`, `/trades`, `/summary`, `/report`
- Tables: `backtest_runs`, `backtest_events`, `backtest_trades`
- Tests: `tests/test_v2_priority_modules.py`

- Module: Model Lab Scorecards
- Fichiers: `app/model_lab/service.py`
- API: `/api/model-lab/run-timesfm-benchmark`, `/run-all-baselines`, `/scorecard/*`, `/selection-policy`
- Tables: `model_scorecards`, `model_selection_policy`
- Tests: `tests/test_v2_priority_modules.py`

- Module: Decision Trace
- Fichiers: `app/observability/decision_trace_*.py`, `decision_explainer.py`
- API: `/api/decision-trace/entity/{entity_type}/{entity_id}`
- Tables: `decision_traces`
- Tests: `tests/test_v2_priority_modules.py`

- Module: Daily Reports
- Fichiers: `app/reports/*`, `app/api/routes_reports.py`
- API: `/api/reports/daily/generate`, `/latest`, `/{date}`
- Tables: `daily_reports`
- Tests: compilation Python; tests fonctionnels a etendre

## Partiel
- Module: Session consistency et bootstrap Forecast Stack
- Ce qui marche: la configuration `entry_decision.session_consistency` est exposee; les statuts Forecast Stack distinguent maintenant `ACCURACY_HISTORY_WARMUP`, dependances et worker.
- Limites: le classifieur complet `signal_bar_session` vs `live_quote_session` reste dependant de champs de session non encore fournis par tous les snapshots; le bootstrap/backtest automatique par nouveau symbole n'est pas encore implemente en tache de fond.

- Module: Adapters forecasting externes
- Ce qui marche: statut explicite, degradation sure, activation automatique apres installation, modeles benchmark entrainables sans `model_path`, bridges foundation models et Darts natif offline.
- Limites: installation et poids externes non inclus dans le runtime principal; le premier chargement peut telecharger des poids et les APIs externes doivent etre revalidees lors d'une mise a niveau majeure.

- Module: GUI workflow Detecter -> Analyser -> Armer
- Ce qui marche: page `/opportunities` affiche shortlist, bloquees, expirees et scenarios generes; Market Context expose les badges et champs d'opportunite.
- Limites: les pages detail opportunite/scenario dediees restent a construire; les detecteurs historiques multi-timeframe avances ne sont pas encore tous separes.

## Prevu prochainement
- Module: Calibration paper-trading du Opportunity Scanner, pages detail opportunite/scenario, bootstrap Forecast Stack automatique par symbole et classifieur de session RTH/PRE/POST
- Priorite: Haute
- Dependances: historique OHLCV suffisant, taches background, stockage des fenetres bootstrap, champs de session fiables et journal d'opportunites evaluees.

## Risques connus
- Risque: Le replay MVP reste conservateur et simplifie.
- Mitigation: Les rapports indiquent `sample_source=replay_mvp` et ne remplacent pas une validation paper trading.

## Tests manuels a faire
- Scenario: Cliquer `Generer le modele de configuration` dans `/setups`.
- Resultat attendu: le JSON copie contient `setup_type: CHOOSE_ONE_SETUP_TYPE`, `setup_type_options`, `entry.order_type=AUTO_SELECT`, `volume_confirmation.enabled=AUTO_SELECT`, une `volume_confirmation_policy_by_setup_type`, une section `expected_output`, tous les setup types dont `trailing_runner`, un guide de choix, tous les blocs possibles et des regles `_template` demandant un seul setup final.
- Scenario: Provoquer un momentum breakout ou `ask > maximum_limit_price + stale_buffer`.
- Resultat attendu: la fiche setup affiche `entry_decision.display_title` bloque/missed et ne montre pas `Entree possible`.
- Scenario: Ouvrir `/forecasting/stack`.
- Resultat attendu: les colonnes Worker/Dependency/Input/Forecast/Reliability/Samples/Display/Execution indiquent `WARMUP` pour Samples 0, pas `INSUFFICIENT_DATA`.
- Scenario: Lancer `/api/opportunities/rebuild-shortlist`, generer un scenario draft, verifier qu'il n'est pas arme.
- Resultat attendu: scenario `DRAFT`, `selection.armed=false`, ambiguities presentes si stop absent.
- Scenario: Simuler `CAST` a `perf_stock_1d=13.29` sans metadata secteur.
- Resultat attendu: `opportunity_status=OPPORTUNITY_DETECTED`, `opportunity_type=INTRADAY_MOMENTUM_ANOMALY`, warning `SECTOR_METADATA_MISSING` et `can_send_order=false`.
