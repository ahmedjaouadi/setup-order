# Audit des tailles de fichiers — candidats au découpage

Généré en Phase 2a de la mission `instructions-refactoring-architecture.md` (2026-07-10).
Comptage : tous les fichiers `*.py`, `*.js`, `*.html`, `*.css` du projet, hors `.venv*`, caches, `tmp/`, `.codex*`, `lightning_logs/`.

## Fichiers > 500 lignes (du plus gros au plus petit)

| # | Fichier | Lignes | Type | Remarques |
|---|---|---|---|---|
| 1 | `app/gui/static/js/app.js` | 8 744 | JS frontend | **Cible prioritaire** — tout le frontend dans un seul fichier |
| 2 | `app/gui/static/css/styles.css` | 3 922 | CSS | Découpage par page/composant possible ; risque faible mais gain limité — à arbitrer |
| 3 | `app/broker/tws_connector.py` | 3 081 | Python | ⚠️ Contient le parsing TWS/IBKR **à ne pas retoucher** (consigne du 2026-07-08). Déplacement de code = risque élevé ; à traiter en dernier ou laisser tel quel avec justification |
| 4 | `app/engine/trading_engine.py` | 2 356 | Python | Cœur du moteur — découpage par domaine (tick, jobs, wiring) envisageable |
| 5 | `app/intelligence/service.py` | 1 994 | Python | Service d'analyse LLM |
| 6 | `app/storage/repositories.py` | 1 565 | Python | Plusieurs repositories dans un fichier → un fichier par repository |
| 7 | `app/engine/broker_reality.py` | 1 379 | Python | Vérité broker |
| 8 | `tests/test_tws_logging.py` | 1 367 | Test | Les tests longs sont tolérables ; découpage optionnel |
| 9 | `app/forecasting/forecast_service.py` | 1 280 | Python | |
| 10 | `app/forecasting/adapters.py` | 1 140 | Python | Un adapter par provider → un fichier par provider |
| 11 | `app/opportunities/scanner.py` | 946 | Python | |
| 12 | `app/setups/momentum_breakout.py` | 941 | Python | |
| 13 | `app/storage/database.py` | 881 | Python | |
| 14 | `app/settings.py` | 759 | Python | |
| 15 | `app/model_lab/service.py` | 732 | Python | |
| 16 | `app/engine/stock_market_monitor.py` | 709 | Python | |
| 17 | `app/engine/setup_lifecycle_service.py` | 692 | Python | |
| 18 | `app/engine/setup_diagnostics.py` | 659 | Python | |
| 19 | `app/engine/trade_guards.py` | 653 | Python | Sécurité — refactorer avec une extrême prudence |
| 20 | `tests/test_intelligence_service.py` | 646 | Test | Optionnel |
| 21 | `app/engine/order_manager.py` | 645 | Python | Sécurité — extrême prudence |
| 22 | `app/intelligence/semantic_validation_service.py` | 615 | Python | |
| 23 | `app/market_context/service.py` | 608 | Python | |
| 24 | `app/engine/session_policy.py` | 584 | Python | |
| 25 | `app/engine/reconciliation.py` | 568 | Python | |
| 26 | `app/conversion/canonical_model_builder.py` | 540 | Python | |
| 27 | `tests/test_forecasting.py` | 528 | Test | Optionnel |
| 28 | `app/engine/setup_template_service.py` | 505 | Python | À la limite du seuil — probablement à laisser tel quel |

## Ordre de traitement proposé

1. `app.js` (Phases 2b→4 en cours) — pire cas, bénéfice maximal.
2. Backend par gain/risque décroissant : `repositories.py`, `forecasting/adapters.py`, `trading_engine.py`, `intelligence/service.py`, `broker_reality.py`, `forecast_service.py`, puis le reste au fil de l'eau.
3. Fichiers laissés tels quels sauf demande explicite : `tws_connector.py` (consigne de non-retouche du parsing), `trade_guards.py` / `order_manager.py` (code de sécurité, bénéfice < risque), tests > 500 lignes, `styles.css` (à arbitrer).

Chaque fichier traité suit le cycle complet : cartographie (`docs/<fichier>-map.md`) → plan validé → extraction incrémentale avec tests.
