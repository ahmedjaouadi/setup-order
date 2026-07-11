# CLAUDE.md — Carte du projet setup-order

## Description

Plateforme locale (usage personnel) de gestion semi-automatique de setups de trading actions via IBKR/TWS.
Backend FastAPI + SQLite, frontend vanilla JS servi par Jinja2, pipeline de sécurité strict avant tout ordre
(validation, risk engine, trade guards, broker-truth). Spécification de référence : `program.md` + contrats dans `docs/`.

## Structure des dossiers

```
app/
  main.py               # Assemblage FastAPI: wiring services, routers, jobs de fond
  settings.py           # Chargement config.yaml
  background_jobs.py    # Jobs périodiques (scan, revalidation, heartbeat)
  api/                  # Routers FastAPI (routes_setups, routes_platform, routes_v2_pages, websocket...)
  engine/               # Cœur trading: trading_engine, setup_engine, risk_engine, order_manager,
                        # state_machine, trade_guards, broker_reality, reconciliation, manual_order_service
  broker/               # Connecteur IBKR: tws_connector, order_mapper, ib_models (voir Pièges)
  storage/              # database.py (SQLite), repositories.py, event_store.py, instance_lock.py
  setups/               # Modèles/factory de setups, templates, snapshot de création
  conversion/           # Normalisation canonique des champs de setup avant validation
  intelligence/         # Analyse LLM des setups (service, repository, api_models)
  market_data/          # Snapshots, bougies, indicateurs, timeframes
  market_context/       # Contexte marché (secteurs, force relative, heatmap)
  opportunity_scanner/  # Détection d'opportunités, techniques, learning loop, outcomes
  opportunities/        # Scénarios & cycle de vie des opportunités
  forecasting/          # Providers de forecast (Chronos, TimesFM...), jamais exécutoire
  model_lab/            # Benchmarks/scorecards de modèles (Darts), hors exécution
  scoring/ features/ portfolio_risk/ data_quality/ observability/ reports/ alerts/ event_bus/ utils/
  gui/
    templates/          # Pages Jinja2 (base.html charge app.js, setup_detail.html, setups.html...)
    static/js/app.js    # TOUT le frontend (~8200 lignes) — refactoring en cours
    static/css/styles.css
config/ + config.yaml   # Configuration runtime
data/                   # Données runtime (SQLite, setups JSON) — ne pas committer de secrets
docs/                   # Contrats 00→20, skills.md, Lecture_des_donnees_TWS_IBKR.md, audits refactoring
scripts/                # Outils CLI (check_forecasting_stack.py...)
tests/                  # ~71 fichiers unittest (test_*.py)
run.py / start.bat      # Lanceurs (port auto à partir de 8000)
```

## Où se trouve quoi

| Fonctionnalité | Fichier(s) | Fonctions/objets principaux |
|---|---|---|
| Détail d'un setup (rendu) | `app.js` (provisoire) | `renderSetupDetail`, `renderSetupDetailSummary`, `buildSetupDetailInfo`, `wireSetupDetailJsonButton` |
| Copie presse-papiers | `app.js` (provisoire) | `copySetupTemplateToClipboard`, `copySetupDetailInfoToClipboard`, `fallbackCopyTextToClipboard` |
| Appels API frontend | `app/gui/static/js/api-client.js` | `api`, `optionalApi`, `formatErrorDetail` (`connectWebSocket` reste dans app.js) |
| Messages de validation setup | `app/gui/static/js/setup-messages.js` | `formatSetupValidationDetail`, `humanizeSetupValidationMessage` |
| Helpers UI / toast | `app/gui/static/js/ui-helpers.js` | `toast`, `onClick`, `setText`, `escapeHtml`, `money`, `openModal`/`closeModal`, badges de statut |
| Liste des setups | `app.js` (provisoire) | `renderSetups`, `filterSetups`, `renderSetupsColumnControls`, `armSetupById`/`disarmSetupById` |
| Graphique setup (canvas) | `app.js` (provisoire) | `drawSetupChart`, `renderSetupChart`, `wireSetupChartInteractions`, `drawTimesfmForecastChart` |
| Ordres / positions / exécutions (UI) | `app.js` (provisoire) | `renderOrders`, `renderPositions`, `renderExecutions`, `wireManualOrderForm` |
| Dashboard / métriques (UI) | `app.js` (provisoire) | `renderDashboard`, `renderMetrics`, `renderEngineHealth`, `initDashboardPremium` |
| Intelligence setup (UI) | `app.js` (provisoire) | `renderSetupIntelligencePanel`, `fetchSetupIntelligence`, `renderSetupIntelligenceComparison` |
| État global frontend partagé | `app/gui/static/js/state.js` | `latestSnapshot`, `currentSetupDetailInfo`... (lecture = import du binding, écriture = `setX(...)`) |
| Routes API setups | `app/api/routes_setups.py` | save/arm/disarm/preview, shortlist niveaux |
| Routes plateforme (runtime, ordres, métriques) | `app/api/routes_platform.py` | snapshot, orders, positions, metrics, events |
| Pages HTML | `app/api/routes_v2_pages.py` + `app/gui/templates/` | une route par template |
| Moteur trading / cycle | `app/engine/trading_engine.py`, `setup_engine.py`, `state_machine.py` | tick, transitions d'état |
| Garde-fous avant ordre | `app/engine/risk_engine.py`, `trade_guards.py` | circuit breakers, exposition, horaires, PDT |
| Ordres broker | `app/engine/order_manager.py`, `app/broker/tws_connector.py`, `order_mapper.py` | placement, mapping IBKR |
| Vérité broker / réconciliation | `app/engine/broker_reality.py`, `reconciliation.py` | positions/ordres réels vs locaux |
| Persistance | `app/storage/database.py`, `repositories.py`, `event_store.py` | SQLite, WAL, events |
| Validation canonique setup | `app/conversion/canonical_model_builder.py`, `canonical_field_registry.py` | normalisation avant validation |

## Commandes

- Lancer l'app : `start.bat` (ou `python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`)
- URL locale : `http://127.0.0.1:8000` (port auto ≥ 8000 via start.bat)
- Tests : `python -m unittest discover -s tests`
- Stack forecasting optionnel : `./install-forecasting.ps1 -Tier p1`, check via `python scripts/check_forecasting_stack.py`

## Conventions

- Python : modules par domaine, imports explicites, dataclasses/pydantic, tests unittest par fonctionnalité.
- JS : vanilla, pas de bundler ni framework ; `app.js` chargé en `<script>` classique via `base.html`
  (avec query-string de cache-busting `?v=...` à incrémenter quand on touche au JS/CSS).
- Nommage JS : `renderX` (rendu DOM), `wireX` (branchement listeners), `fetchX` (appels API), `drawX` (canvas).
- Erreurs UI : `toast(message)` ; les appels API passent par `api()` qui lève avec détail formaté.
- Textes UI et commits en français sans accents dans les messages de commit.
- Une page = un template Jinja2 héritant de `base.html` ; le routage JS se fait dans `init()` selon les IDs présents.

## Pièges connus

- **Clipboard** : l'écriture presse-papiers doit rester dans la fenêtre d'activation utilisateur.
  Ne JAMAIS mettre d'`await` d'appel réseau avant `navigator.clipboard.*` ou `execCommand`.
  Pour des données asynchrones, utiliser `ClipboardItem` avec une promesse de Blob (voir `copySetupDetailInfoToClipboard`).
- **Données TWS/IBKR** : positions ≠ ordres ouverts ≠ exécutions ≠ compte (4 flux distincts).
  Lire `docs/Lecture_des_donnees_TWS_IBKR.md` AVANT tout travail sur ce code. Ne pas retoucher le parsing
  (reqAllOpenOrders, permId si orderId=0, sentinelles UNSET_DOUBLE, auxPrice≠stop hors STP/TRAIL, ib.fills()).
- **Table events** : volumineuse ; toute requête doit être indexée/limitée (jamais de COUNT/SELECT non borné).
- **Verrou SQLite** : une seule instance app par base (instance_lock) ; busy_timeout géré dans database.py.
- **Forecasting** : toujours `execution_allowed: false` ; ne jamais brancher un forecast sur l'exécution d'ordres.

## Règles de travail

- Avant toute modification : consulter le tableau "Où se trouve quoi" ci-dessus pour aller directement au bon fichier. Ne pas explorer à l'aveugle.
- Lire uniquement le module concerné, jamais l'ensemble des fichiers.
- Toute nouvelle fonction doit être placée dans le module de son domaine (jamais dans un fichier fourre-tout) et ajoutée au tableau "Où se trouve quoi".
- Tailles : viser des fichiers de 100-300 lignes et des fonctions de moins de 50 lignes. Si un fichier approche 500 lignes, proposer un découpage AVANT d'y ajouter du code.
- Un changement de comportement = tester la fonctionnalité avant de conclure. Ne jamais dire "corrigé" sans vérification.
- Piège clipboard : aucune écriture presse-papiers après un await d'appel réseau.
- Refactoring et corrections de bugs : toujours dans des commits séparés.
