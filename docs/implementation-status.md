# Implementation Status

## Derniere mise a jour
2026-07-06

## Phase actuelle
- Phase: V2.4 Architecture Stabilization
- Statut: ACCEPTED_WITH_BLOCKERS_REMAINING
- Objectif: reprendre le controle documentaire et architectural avant toute nouvelle feature.
- Priorite: stabiliser le modele canonique de setup, `trailing_stop_loss`, broker reality, risk engine, order manager, reconciliation, `entry_decision`, dashboard broker reality et golden tests.
- Hors scope temporaire: nouveaux providers forecasting, scanners avances, live trading, nouvelles extractions IA et nouvelles fonctions Model Lab.
- Note: V2.4.1 Legacy Stop Cleanup est accepte. La validation V2.4 globale garde les blockers non scopes a traiter separement.

## Implante
- Module: Etape 13 — Fiabilite du scan : suivi correct/faux visible et boucle de collecte surveillee
- Statut: ACCEPTED
- Fichiers: `app/opportunity_scanner/outcome_tracker.py`, `app/opportunity_scanner/outcome_repository.py`, `app/opportunities/scanner.py`, `app/background_jobs.py`, `app/storage/database.py`, `app/api/routes_techniques.py`, `app/api/routes_opportunities.py`, `app/gui/templates/opportunity_radar.html`, `app/gui/static/js/app.js`, `tests/test_scan_reliability.py`
- API: `GET /api/techniques/stats` (par technique ET global : detections_total, pending, evaluated, expired, correct = label_1r 1, wrong = label_1r 0, indeterminate = label null, hit_rate, expectancy_r, min_samples atteint — reutilise `aggregate_stats`, les compteurs viennent d'une seule agregation SQL) ; `GET /api/opportunities/{id}/outcomes` (verdict a posteriori d'une detection : PENDING / correct / faux + mfe/mae par horizon).
- 13.1 panne silencieuse: `stale_pending_count` + alarme `detection_outcomes_stale` (event WARNING, 1/jour) quand des outcomes PENDING restent non evalues > 3 jours apres leur echeance — le job `auto_evaluate_detection_outcomes` expose aussi `stale_pending` dans son resume.
- 13.4 jointure: colonne `opportunity_id` ajoutee a `detection_outcomes` (migration idempotente `_ensure_column` + index, marqueur `v2_7_detection_outcome_opportunity_link`), propagee depuis le scanner (`record_matches(..., opportunity_id=...)`).
- 13.3 UI Radar: panneau « Fiabilite du scan » — tuiles globales (corrects / faux / indetermines / en attente / hit rate) + tableau par technique ; sous `min_samples` (30), « ECHANTILLON INSUFFISANT » au lieu d'un pourcentage trompeur.
- 13.5 score + fiabilite: colonne « Fiabilite » dans la shortlist a cote du score/qualite — hit_rate historique de la technique detectrice (« correct N/M ») quand l'echantillon le permet.
- Tests: `python -m pytest tests/test_scan_reliability.py` (stats sur outcomes synthetiques melanges, opportunity_id propage jusqu'a l'outcome et requetable, routes stats/outcomes, detection de collecte morte + throttle de l'alarme) ; transitions PENDING->EVALUATED deja couvertes par `tests/test_detection_outcomes.py`.

- Module: Etape 12 — Shortlist actionnable : prix d'entree + SL proposes
- Statut: ACCEPTED
- Fichiers: `app/opportunities/shortlist_service.py`, `app/gui/static/js/app.js`, `tests/test_shortlist_levels.py`
- Comportement: chaque ligne de la shortlist expose `suggested_entry` (= `entry.trigger_price`), `suggested_limit`, `suggested_stop` (= `trailing_stop_loss.initial_stop`), `risk_per_share` (= entree - stop), `levels_status` (`READY`/`INCOMPLETE` avec `levels_ambiguities` du mapper) et `stop_source` (`SCENARIO` | `ATR_FALLBACK`). Source des niveaux : le scenario draft joint par `source_opportunity_id` quand il existe, sinon `OpportunityToScenarioMapper.map` a la volee (fonction pure, aucune persistance).
- Fallback: stop ATR `entree - k x atr_15m` (k = `opportunities.shortlist.atr_stop_multiplier`, defaut 1.5) marque `ATR_FALLBACK`, jamais de valeur inventee : sans ATR ni niveau => `INCOMPLETE` explicite.
- UI: colonnes « Entree », « SL » (suffixe ATR + tooltip provenance), « R/share » sur la table shortlist du Radar ; badge `INCOMPLETE` avec tooltip des ambiguites. Aucun bouton d'envoi d'ordre depuis la shortlist : les niveaux restent consultatifs (`execution_allowed: false` inchange), l'execution passe par le circuit setup ou l'ordre manuel (etape 11).
- Tests: `python -m pytest tests/test_shortlist_levels.py` (draft complet, mapper a la volee, fallback ATR marque, INCOMPLETE sans crash, multiplicateur configurable).

- Module: Etape 11 — Passage d'ordre manuel depuis l'UI
- Statut: ACCEPTED
- Fichiers: `app/engine/manual_order_service.py`, `app/engine/order_manager.py`, `app/api/routes_orders.py`, `app/engine/trading_engine.py`, `app/gui/templates/orders.html`, `app/gui/static/js/app.js`, `tests/test_manual_orders.py`
- API: `POST /api/orders/manual/preview` (calcul serveur du risque : entree pire cas, R/share, risque $, % du compte via net_liquidation, cost gate) et `POST /api/orders/manual` (payload Pydantic : symbol, side BUY/SELL, quantity, order_type MKT/LMT/STP/STP_LMT, limit/trigger selon type, stop_loss).
- Pipeline: un BUY manuel passe par fenetres horaires (`execution_window_block`), `trade_guards.evaluate_entry` (halt, circuit breakers, PDT, cooldown, exposition), limites de risque (`RiskLimits` : risque/trade, taille position, exposition totale), cost gate 24bis, puis `order_manager.place_entry_order` => bracket entree + stop de protection. Stop obligatoire pour un BUY (400 sinon) ; `allow_unprotected` honore uniquement sur le connecteur simule. Un SELL manuel est reduce-only (qty <= position) et passe par les gates halt/marche ferme.
- Ordres: `setup_id = man_<id>` pour identifier la ligne sur la page Ordres & Positions ; `OrderManager._entry_order_prices` porte maintenant les champs corrects pour MKT/LMT/STP/STP_LMT ; `place_manual_order` couvre le SELL manuel et le BUY non protege simule.
- Trace: chaque soumission (acceptee ou refusee) ecrit `decision_traces` avec `decision_type="MANUAL_ORDER"`, payload complet, risque calcule et verdict (`GO:MANUAL_ORDER_SUBMITTED` ou `{status}:{reason_code}`), plus events `manual_order_rejected`/`manual_order_transmitted`.
- UI: formulaire « Nouvel ordre manuel » sur la page Ordres & Positions ; bouton « Calculer le risque » (affichage $ et % du compte AVANT confirmation, transmission desactivee tant que le preview n'est pas OK) ; confirmation, doublee si `runtime.account_mode`/`broker_account_mode` est live.
- Tests: `python -m pytest tests/test_manual_orders.py` (BUY sans stop => 400 ; halt => 422 trace ; hors fenetre => refus ; risque > limite => refus ; ordre valide simule => bracket visible ; preview == calcul serveur ; SELL reduce-only).

- Module: Etape 10 — Ordres & Positions = miroir temps reel de TWS (10.1 + 10.2)
- Statut: ACCEPTED
- Fichiers: `app/engine/trading_engine.py`, `app/engine/stop_modification_service.py`, `app/engine/order_manager.py`, `app/engine/trade_guards.py`, `app/broker/tws_connector.py`, `app/api/routes_positions.py`, `app/storage/repositories.py`, `app/gui/templates/orders.html`, `app/gui/static/js/app.js`, `tests/test_orders_positions_broker_truth.py`, `tests/test_stop_modification.py`
- Comportement: broker connecte => les tableaux Positions et Ordres refletent la verite TWS (`_merge_position_snapshots` et `_orders_with_broker_overlay` avec autorite broker); les intentions locales sans ordre broker sont marquees `NO_BROKER_ORDER`/`LOCAL_ONLY`; broker deconnecte => fallback local. La page fusionne les anciens tableaux Ordres (16 colonnes) et Broker Reality en une vue unique par titre avec ligne depliable pour le detail, plus un tableau `Executions du jour` alimente par `recent_executions` du connecteur.
- Actions: modification de stop via `POST /api/positions/{symbol}/move-stop` et `PATCH /api/positions/{symbol}/stop` -> `StopModificationService` (broker d'abord via `modify_stop_order`, etat local seulement apres acceptation); cancel branche sur l'ID broker reel pour les lignes TWS non matchees (prefixe `broker_`); attach SL conserve; `Test fill` reste derriere `connector === "simulated"`.
- Securite: `never_lower_stop` refuse cote serveur toute baisse de stop; `TradeGuardsService.evaluate_stop_modification` (halt actif, marche ferme) bloque la modification avant tout envoi broker; chaque modification/refus est trace dans les events (`stop_modified`, `stop_modification_rejected` avec reason_code).
- Latence: `BROKER_RUNTIME_SNAPSHOT_TTL_SECONDS=5` aligne sur le heartbeat 5 s + push WebSocket a chaque heartbeat => la page suit TWS avec <= 5 s de latence percue; l'age de synchro broker est affiche dans le bandeau.
- Tests: `python -m pytest tests/test_stop_modification.py tests/test_orders_positions_broker_truth.py tests/test_trade_guards.py`

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

- Module: Detection etape 6 — conventions skills.md (28bis/32.2bis/32.2ter/30bis/2.5)
- Fichiers: `app/opportunity_scanner/context_tags.py`, `app/opportunity_scanner/data_quality_gate.py`, `app/decision_codes.py`, `app/opportunities/scanner.py`, `app/opportunity_scanner/outcome_tracker.py`, `app/opportunity_scanner/learning_loop.py`, `app/opportunity_scanner/technique_repository.py`, `app/opportunity_scanner/technique_service.py`, `app/storage/database.py`
- Comportement: chaque outcome embarque `context_tags` (time_bucket NY, rvol_bucket, spread_bucket, day_of_week, colonnes reservees market_regime/had_catalyst) dans `features_snapshot`; gate qualite AVANT toute evaluation de technique — snapshot stale/incoherent/bid>ask/sans prix -> `PAUSED` (REJECTED, aucun outcome); les outcomes ne sont enregistres qu'apres le filtre liquidite; refus qualifies traces en `SCANNER_GATE` `{status}:{reason_code}` via `app/decision_codes.py` (partage avec trade_guards, zero couplage detection->order manager); les variantes du learning loop ne mutent qu'UN seuil numerique a la fois (`mutated_field`+`factor` dans la trace `VARIANT_SPAWNED`, plafond `learning.max_variants_per_parent`); `detection_techniques` porte `config_version`/`revision`, tout PATCH de `rule_json` incremente `revision` et trace `TECHNIQUE_REVISION` avec before/after.
- Securite: `execution_allowed=false` partout, aucun import order manager dans la detection, aucun eval/exec, kill-switch `learning.enabled=false` = zero mutation, tests statiques dans `tests/test_techniques_security.py`.
- Tests: `python -m unittest tests.test_context_tags tests.test_scanner_data_quality_gate tests.test_detection_outcomes tests.test_learning_loop tests.test_techniques_api tests.test_techniques_security`

- Module: Detection etape 7 — features F1 + techniques seed + filtre spread
- Fichiers: `app/opportunity_scanner/feature_math.py`, `app/opportunities/scanner.py`, `app/features/store.py`, `app/broker/tws_connector.py`, `app/opportunity_scanner/rule_interpreter.py`, `app/opportunity_scanner/technique_seed.py`, `app/main.py`
- Comportement: snapshot scanner enrichi de `rvol` (canonique, fallback relative_volume/volume_ratio/volume_ratio_15m), `atr_pct`, `vwap`/`dist_vwap_pct` (VWAP session sur barres 15m RTH, None si barres/volume indisponibles), `time_bucket`, `price_above_ema20`, `price_above_sma50` (daily, `historical_sma_50` ajoute au FeatureStore); whitelist `ALIAS_GROUPS` etendue en consequence; operateur `in` (liste de chaines) ajoute a l'interpreteur — seule extension du langage de regles; nouvelles techniques seed `GAP_AND_GO_FULL` (skills.md 16) et `MOMENTUM_RVOL_CONFIRMED` (skills.md 6.2bis+10) avec penalite lunch declarative `any(time_bucket in [...], rvol >= 1.5)`; migration one-shot au demarrage (`technique_builtin_spread_filter_migration_v1` dans bot_state) ajoutant `spread_pct <= 0.5` aux builtins existants en base, revision +1 et trace `TECHNIQUE_REVISION` — independante du kill-switch learning.
- Tests: `python -m unittest tests.test_feature_math tests.test_rule_interpreter tests.test_technique_seed tests.test_opportunity_detection`

- Module: Detection etape 8 — scoring pondere skills.md 9.1
- Fichiers: `app/opportunity_scanner/scoring.py`, `app/opportunity_scanner/service.py`, `app/opportunity_scanner/schemas.py`, `app/gui/static/js/app.js`
- Comportement: `compute_quality_score` a 7 composants pondere (/100) avec `score_grade` (>=80 EXCELLENT / 65-79 ACCEPTABLE / 50-64 WEAK / <50 NO_GO) et `score_breakdown.unavailable` listant les sous-criteres non calculables en F1 (structure_quality, market_context, fundamental_context a 0); expose dans l'API et le panneau Radar comme champ additionnel; `discovery_score`/`risk_adjusted_score` conserves — `_status` bascule sur `quality_score` dans un second temps apres observation; le score ne remplace jamais les refus automatiques (gate qualite, filtre liquidite).
- Tests: `python -m unittest tests.test_scoring_v2`

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

- Module: Detection — F2/F3 et ML (gated)
- Ce qui marche: le tag `market_regime` et la colonne `had_catalyst` sont reserves dans les context_tags; le scoring liste ses sous-criteres indisponibles pour rester comparable.
- Limites: lots F2 (niveaux, compression, etat sequentiel) et F3 (spy/qqq vs vwap, vix, market_regime) et meta-labeling ML non demarres — declencheur inchange: >= 300 outcomes evalues sur >= 3 techniques distinctes. `detection_outcomes` demarre a 0 ligne: la collecte RTH continue est le chemin critique.

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
