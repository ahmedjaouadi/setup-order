# program.md - Specification detaillee du programme d'automatisation de setups de trading avec Interactive Brokers TWS

Version : 2.3
Statut : Document de conception etendu V2.3 pour une application locale de detection, analyse, scoring, backtest, forecasting et execution controlee de setups
Objectif : Definir une architecture modulaire, evolutive, performante et maintenable pour detecter rapidement des opportunites de marche, analyser profondement les setup orders, les scorer, les backtester, les enrichir par forecasting multi-modeles et les executer de maniere controlee via Interactive Brokers TWS, avec interface GUI HTML, stockage local, suivi complet, intelligence semantique, observabilite et garde-fous de production.

## Etat d'implementation V2.3 au 2026-06-30

Implemente : Forecast Accuracy Ledger complet (direction, erreurs, touch entry/stop, calibration et scorecards), snapshot marche immutable et embarque dans le setup, statuts normalises des providers optionnels, adapters a sortie normalisee, activation automatique limitee au role du provider quand ses dependances sont pretes, bridges Chronos/Lag-Llama/Moirai, entrainement benchmark NeuralForecast/AutoGluon sans `model_path`, orchestration Darts native offline, consensus TimesFM/Chronos/Lag-Llama, comparaison offline avec metriques probabilistes, validation walk-forward sans fuite, politique de selection par symbole/timeframe/horizon, tables/API associees et vues consolidees dans `/research` et la fiche setup. Les corrections du 2026-06-28 ajoutent aussi un generateur de squelette setup universel editable avec `setup_type: CHOOSE_ONE_SETUP_TYPE`, `setup_type_options`, `expected_output`, `volume_confirmation_policy_by_setup_type`, `entry.order_type=AUTO_SELECT`, `volume_confirmation.enabled=AUTO_SELECT`, tous les blocs setup visibles dans un seul JSON, des aides `_template` ignorees a la sauvegarde, le support coherent de `trailing_runner`, un objet final `entry_decision` consomme par la GUI, des raisons anti-chase standardisees, un diagnostic volume separe entre liquidite d'execution et confirmation momentum, et des statuts Forecast Stack precis (`worker_status`, `dependency_status`, `forecast_status`, `reliability_status`, `ACCURACY_HISTORY_WARMUP`). La livraison du 2026-06-30 ajoute un noyau `app/opportunity_scanner` parallele au Market Context : il transforme `perf_stock_1d`, RS secteur/SPY, volume, spread, metadata et signaux anti-chase en `opportunity_status`, `opportunity_type`, `opportunity_score`, `reasons`, `warnings`, `recommended_next_action` et `can_send_order=false`, puis expose ces champs dans Market Context et les persiste via la table `opportunities`.

Partiel : les packages et poids externes restent volontairement optionnels et ne sont pas installes avec le runtime principal. Une fois les manifests optionnels installes, Chronos et Lag-Llama s'activent uniquement en scoring, NeuralForecast/AutoGluon/Darts uniquement dans Model Lab et Moirai/Uni2TS uniquement en benchmark experimental. Les poids Hugging Face sont telecharges au premier lancement si aucun chemin local n'est fourni. Le classifieur complet de coherence de session (`RTH_PREVIOUS_DAY` vs `PRE_MARKET_CURRENT_DAY`) et le bootstrap/backtest automatique Forecast Stack par nouveau symbole restent a finaliser. Le scanner d'opportunites possede le noyau Market Context, les endpoints et le brouillon de scenario, mais les pages detail opportunite/scenario et les detecteurs historiques multi-timeframe avances restent a completer.

Prevu : calibrer le Opportunity Scanner sur un historique paper trading, ajouter les details opportunite/scenario, puis ajouter le bootstrap Forecast Stack automatique par symbole et enrichir les snapshots avec des champs de session fiables.

Limites connues : l'evaluation d'un forecast arrive a echeance requiert encore une observation fournie au service (prix ou chemin high/low) ; le premier lancement d'un foundation model exige un acces au depot de poids ou un cache local ; aucun modele forecasting, y compris TimesFM, ne peut envoyer un ordre ; `ACCURACY_HISTORY_WARMUP` est normal tant que le ledger ne possede pas assez d'outcomes evalues ; une opportunite detectee par Market Context reste un objet de decouverte et ne peut pas devenir ordre sans setup valide, stop, risk engine, session et order manager.

Prochain module recommande : campagne paper-trading de calibration du Opportunity Scanner et pages detail opportunite/scenario, puis bootstrap Forecast Stack automatique par symbole.

## Historique des corrections - versions 1.1 a 2.0

Ce document integre les corrections structurantes suivantes :

- separation explicite entre un setup de **nouvelle entree** et un setup de **gestion d'une position deja ouverte** ;
- ajout de `setup_role` avec les roles `ENTRY_AND_MANAGEMENT`, `ENTRY_ONLY` et `MANAGEMENT_ONLY` ;
- ajout du mecanisme `adopt_existing_ibkr_position` ;
- interdiction de placer un nouvel ordre d'achat depuis un setup `MANAGEMENT_ONLY` ;
- ajout des etats `RECONCILING_EXISTING_POSITION`, `MANUAL_REVIEW_REQUIRED` et `ERROR_REQUIRES_MANUAL_REVIEW` ;
- calcul de quantite corrige pour les ordres `STP_LMT` : utilisation du **prix limite maximal** et non du trigger ;
- affichage GUI corrige : separation entre trigger, limite maximale, stop, role du setup, quantite maximale et risque maximal ;
- regles de securite ajoutees lorsqu'une position existe chez IBKR mais que l'etat local est absent ou incoherent ;
- ajout d'exemples complets pour la gestion d'une position existante et pour une nouvelle entree momentum breakout ;
- suppression des exemples lies a un ticker reel ;
- ajout d’un moteur generique d’ajustements declaratifs : aucune regle Python ne doit contenir un symbole ou un niveau de prix specifique ;
- remplacement des exemples operationnels par des parametres `<SYMBOL>`, `<PRICE_LEVEL>` et `<RISK_LIMIT>` ;
- alignement du document avec l'etat actuel du depot, qui inclut aussi la couche `intelligence`, le radar d'opportunites, le moteur de prevision, les routes API associees et la normalisation canonique des champs.
- ajout d'une trace documentaire explicite des changements de comportement dans `docs/change-log.md`.

Ajouts structurants de la version 2.0 :

- ajout d'une couche `opportunity_scanner` pour detecter automatiquement les candidats de marche avant meme qu'un setup manuel existe ;
- ajout d'un `setup_quality_engine` pour noter chaque opportunite et chaque setup selon la technique, le volume, la liquidite, le risque, le contexte marche et l'alignement forecast ;
- ajout d'un `feature_store` pour stocker les indicateurs pre-calcules et eviter de recalculer tout a chaque tick ;
- ajout d'une couche `data_quality` pour detecter bad ticks, donnees obsoletes, bougies incompletes, spread excessif, sessions incorrectes, halts, splits et anomalies ;
- ajout d'un laboratoire `research/model_lab` pour backtesting, replay historique, walk-forward validation, benchmarks de modeles et rapports de performance ;
- ajout d'un moteur `forecasting` multi-modeles : TimesFM, Chronos, Lag-Llama, Moirai/Uni2TS, NeuralForecast, AutoGluon et baselines deterministes ;
- ajout d'une politique stricte : le forecasting ne declenche jamais un BUY directement, il renforce ou degrade uniquement la qualite d'un setup ;
- ajout d'un moteur `portfolio_risk` pour exposition totale, concentration sectorielle, correlation entre positions et garde-fous globaux ;
- ajout d'une couche `observability` : health checks, metrics, decision trace, latence, audit report et suivi de l'etat runtime ;
- ajout d'un `event_bus` interne pour remplacer les boucles globales lourdes par des evenements : tick, candle close, signal, ordre, position, reconciliation, risque ;
- ajout de pages GUI orientees opportunites : `/opportunities`, `/scanner`, `/radar`, `/backtests`, `/model-lab`, `/market-context`, `/decision-trace` ;
- ajout de schemas SQLite, endpoints API, tests obligatoires et definition of done pour guider le developpeur module par module.

## 0. Contexte du depot actuel

Le projet dans ce depot n'est plus seulement un script de setup. Il s'agit deja d'une base applicative locale avec :

- backend `FastAPI` ;
- interface HTML/JS ;
- stockage `SQLite` local ;
- couches `broker`, `engine`, `market_data`, `market_context`, `intelligence`, `forecasting`, `conversion`, `storage` et `gui` ;
- configuration centralisee dans `config.yaml` ;
- dossiers de schemas JSON pour valider les setups ;
- tests automatisees couvrant la securite, la conversion canonique, les moteurs metier et l'intelligence semantique.

Les sections suivantes decrivent la cible fonctionnelle et les garde-fous. Lorsqu'il existe un ecart entre la cible theorique et l'implementation actuelle, ce document doit le signaler explicitement au lieu de le masquer.
# 1. Objectif gÃ©nÃ©ral du programme

Le programme doit permettre de transformer des setups de trading dÃ©crits sous forme de rÃ¨gles en un systÃ¨me automatisÃ© capable de :

- se connecter Ã  Interactive Brokers TWS ou IB Gateway ;
- surveiller plusieurs actions simultanÃ©ment ;
- gÃ©rer plusieurs types de setups ;
- distinguer les setups destinÃ©s Ã  ouvrir une nouvelle position des setups destinÃ©s uniquement Ã  gÃ©rer une position dÃ©jÃ  ouverte ;
- dÃ©tecter les conditions dâ€™entrÃ©e ;
- placer automatiquement des ordres uniquement lorsque le rÃ´le du setup lâ€™autorise ;
- associer un stop-loss de protection ;
- adopter et suivre une position existante chez IBKR sans dÃ©clencher un nouvel achat ;
- remonter le stop-loss selon des rÃ¨gles dÃ©finies ;
- enregistrer toutes les donnÃ©es importantes dans des fichiers ;
- afficher lâ€™Ã©tat du systÃ¨me dans une interface GUI HTML ;
- permettre Ã  lâ€™utilisateur de savoir quel setup est actif, en attente, exÃ©cutÃ©, invalidÃ© ou terminÃ© ;
- Ãªtre extensible pour ajouter plus tard de nouveaux types de setups, indicateurs, stratÃ©gies, alertes ou modules IA.

Le programme ne doit pas Ãªtre un simple script dâ€™achat automatique. Il doit Ãªtre conÃ§u comme une plateforme modulaire de gestion de setups.

---

# 2. Principes fondamentaux

## 2.1 PrioritÃ© Ã  la sÃ©curitÃ©

Le programme doit toujours protÃ©ger le capital avant de chercher la performance.

RÃ¨gles obligatoires :

- aucun ordre dâ€™entrÃ©e ne doit Ãªtre envoyÃ© sans rÃ¨gle de stop-loss dÃ©finie ;
- aucun setup `MANAGEMENT_ONLY` ne doit envoyer un ordre dâ€™entrÃ©e ;
- aprÃ¨s exÃ©cution dâ€™une entrÃ©e, un stop-loss rÃ©el doit Ãªtre placÃ© chez IBKR ;
- lors de lâ€™adoption dâ€™une position existante, le programme doit vÃ©rifier la quantitÃ© rÃ©elle, le stop existant et lâ€™Ã©tat de marchÃ© avant toute action ;
- le programme ne doit jamais baisser un stop-loss dÃ©jÃ  remontÃ© ;
- le programme doit empÃªcher les ordres dupliquÃ©s ;
- le programme doit vÃ©rifier les positions rÃ©elles chez IBKR avant toute dÃ©cision importante ;
- le programme doit bloquer le trading si les donnÃ©es de marchÃ© sont trop anciennes ;
- le programme doit bloquer le trading si la connexion TWS est instable ;
- le programme doit respecter une limite de perte maximale journaliÃ¨re ;
- le programme doit permettre un mode `paper trading` obligatoire pendant les tests.

## 2.2 ModularitÃ©

Chaque responsabilitÃ© doit Ãªtre isolÃ©e dans un module dÃ©diÃ©.

Le programme ne doit pas mÃ©langer :

- la connexion TWS ;
- la logique de setup ;
- la gestion des ordres ;
- la gestion du risque ;
- lâ€™interface GUI ;
- le stockage sur fichier ;
- les logs ;
- les alertes.

Chaque module doit pouvoir Ã©voluer indÃ©pendamment.

## 2.3 Ã‰volutivitÃ©

Le programme doit pouvoir commencer avec une seule action et Ã©voluer vers :

- plusieurs actions ;
- plusieurs setups par action ;
- plusieurs timeframes ;
- plusieurs types dâ€™ordres ;
- plusieurs rÃ¨gles de sortie ;
- plusieurs comptes IBKR ;
- plusieurs sources de donnÃ©es ;
- un futur moteur IA ou scanner automatique.

## 2.4 TraÃ§abilitÃ© complÃ¨te

Chaque dÃ©cision du programme doit Ãªtre enregistrÃ©e.

Exemples :

- setup chargÃ© ;
- condition dÃ©tectÃ©e ;
- condition refusÃ©e ;
- ordre envoyÃ© ;
- ordre acceptÃ© ;
- ordre rejetÃ© ;
- entrÃ©e exÃ©cutÃ©e ;
- stop-loss placÃ© ;
- stop-loss modifiÃ© ;
- position fermÃ©e ;
- erreur TWS ;
- reconnexion ;
- synchronisation avec IBKR ;
- changement manuel via GUI.

---

# 3. Architecture globale recommandÃ©e

Architecture logique :

```text
+------------------------------------------------------+
|                    GUI HTML                          |
| Dashboard / Setups / Positions / Orders / Logs       |
+---------------------------+--------------------------+
                            |
                            v
+------------------------------------------------------+
|                    Backend API                       |
| FastAPI / Flask                                      |
+---------------------------+--------------------------+
                            |
                            v
+------------------------------------------------------+
|                 Trading Engine                       |
| State Machine / Setup Engine / Risk Engine           |
+---------------------------+--------------------------+
                            |
        +-------------------+-------------------+
        |                   |                   |
        v                   v                   v
+---------------+   +---------------+   +---------------+
| TWS Connector |   | Storage Layer  |   | Alert Manager |
| IB API        |   | JSON/SQLite    |   | Telegram/etc. |
+---------------+   +---------------+   +---------------+
        |
        v
+------------------------------------------------------+
|                Interactive Brokers TWS               |
|                or IB Gateway                         |
+------------------------------------------------------+
```

---

# 4. Technologies recommandÃ©es

## 4.1 Langage principal

Langage recommandÃ© :

```text
Python 3.11 ou supÃ©rieur
```

Raisons :

- trÃ¨s bon Ã©cosystÃ¨me trading ;
- compatible avec lâ€™API Interactive Brokers ;
- facile Ã  connecter Ã  une interface web ;
- adaptÃ© au prototypage rapide ;
- extensible vers IA, backtesting, analyse technique et data science.

## 4.2 Connexion Interactive Brokers

Options possibles :

| Option | Avantage | InconvÃ©nient |
|---|---|---|
| API officielle IBKR | Plus robuste, support officiel | Plus verbeuse |
| ib_async | Moderne, proche de ib_insync | Librairie tierce |
| ib_insync | Simple et connue | Projet original moins maintenu |

Recommandation :

- V1 : `ib_async` ou `ib_insync` si lâ€™utilisateur maÃ®trise dÃ©jÃ  cette bibliothÃ¨que ;
- pour une version de production sÃ©rieuse : prÃ©voir une couche dâ€™abstraction pour pouvoir migrer vers lâ€™API officielle sans rÃ©Ã©crire toute lâ€™application.

Important : le code mÃ©tier ne doit jamais dÃ©pendre directement de `ib_insync` Ã  travers toute lâ€™application. CrÃ©er un module `broker_connector`.

## 4.3 Backend web

Recommandation :

```text
FastAPI
```

Raisons :

- rapide ;
- moderne ;
- compatible WebSocket ;
- trÃ¨s bon pour une interface en temps rÃ©el ;
- documentation automatique ;
- sÃ©paration claire API / moteur de trading.

## 4.4 Interface GUI HTML

Options :

| Option | Usage |
|---|---|
| HTML + CSS + JavaScript simple | V1 rapide |
| React | V2 plus avancÃ©e |
| Bootstrap / Tailwind | Interface propre rapidement |
| WebSocket | Mise Ã  jour temps rÃ©el |

Recommandation V1 :

```text
FastAPI + HTML + JavaScript + WebSocket
```

Recommandation V2 :

```text
FastAPI + React + WebSocket
```

## 4.5 Stockage des donnÃ©es

Lâ€™utilisateur souhaite un stockage dans un fichier.

Deux options possibles :

### Option simple

```text
Fichiers JSON / CSV
```

Avantage :

- simple ;
- lisible ;
- facile Ã  sauvegarder ;
- suffisant pour V1.

InconvÃ©nient :

- moins robuste ;
- risque de corruption si plusieurs Ã©critures simultanÃ©es ;
- requÃªtes moins pratiques.

### Option recommandÃ©e

```text
SQLite
```

SQLite reste un fichier local, mais offre :

- transactions ;
- tables structurÃ©es ;
- requÃªtes fiables ;
- meilleure robustesse ;
- meilleur suivi historique ;
- facile Ã  sauvegarder.

Recommandation finale :

```text
Utiliser SQLite comme fichier principal de stockage.
Utiliser JSON/YAML pour les fichiers de configuration des setups.
Utiliser CSV uniquement pour les exports.
```

Structure recommandÃ©e :

```text
data/
  trading_state.sqlite
  setups/
    SYMBOL_A.yaml
    SYMBOL_B.yaml
    SYMBOL_C.yaml
  exports/
    trades_2026-05.csv
  logs/
    app.log
    orders.log
    errors.log
```

---

# 5. Structure recommandÃ©e du projet

```text
trading-bot/
â”‚
â”œâ”€â”€ program.md
â”œâ”€â”€ README.md
â”œâ”€â”€ requirements.txt
â”œâ”€â”€ config.yaml
â”‚
â”œâ”€â”€ app/
â”‚   â”œâ”€â”€ main.py
â”‚   â”œâ”€â”€ settings.py
â”‚   â”‚
â”‚   â”œâ”€â”€ broker/
â”‚   â”‚   â”œâ”€â”€ tws_connector.py
â”‚   â”‚   â”œâ”€â”€ ib_models.py
â”‚   â”‚   â”œâ”€â”€ order_mapper.py
â”‚   â”‚   â””â”€â”€ broker_errors.py
â”‚   â”‚
â”‚   â”œâ”€â”€ engine/
â”‚   â”‚   â”œâ”€â”€ trading_engine.py
â”‚   â”‚   â”œâ”€â”€ setup_engine.py
â”‚   â”‚   â”œâ”€â”€ state_machine.py
â”‚   â”‚   â”œâ”€â”€ rule_engine.py
â”‚   â”‚   â”œâ”€â”€ action_executor.py
â”‚   â”‚   â”œâ”€â”€ signal_engine.py
â”‚   â”‚   â”œâ”€â”€ risk_engine.py
â”‚   â”‚   â”œâ”€â”€ position_manager.py
â”‚   â”‚   â”œâ”€â”€ order_manager.py
â”‚   â”‚   â”œâ”€â”€ reconciliation.py
â”‚   â”‚   â””â”€â”€ adoption_service.py
â”‚   â”‚
â”‚   â”œâ”€â”€ setups/
â”‚   â”‚   â”œâ”€â”€ base_setup.py
â”‚   â”‚   â”œâ”€â”€ setup_roles.py
â”‚   â”‚   â”œâ”€â”€ aggressive_rebound.py
â”‚   â”‚   â”œâ”€â”€ breakout_retest.py
â”‚   â”‚   â”œâ”€â”€ momentum_breakout.py
â”‚   â”‚   â”œâ”€â”€ pullback_continuation.py
â”‚   â”‚   â”œâ”€â”€ position_management.py
â”‚   â”‚   â”œâ”€â”€ trailing_runner.py
â”‚   â”‚   â””â”€â”€ setup_factory.py
â”‚   â”‚
â”‚   â”œâ”€â”€ market_data/
â”‚   â”‚   â”œâ”€â”€ market_data_service.py
â”‚   â”‚   â”œâ”€â”€ candle_builder.py
â”‚   â”‚   â”œâ”€â”€ indicators.py
â”‚   â”‚   â””â”€â”€ timeframe_manager.py
â”‚   â”‚
â”‚   â”œâ”€â”€ storage/
â”‚   â”‚   â”œâ”€â”€ database.py
â”‚   â”‚   â”œâ”€â”€ repositories.py
â”‚   â”‚   â”œâ”€â”€ event_store.py
â”‚   â”‚   â””â”€â”€ file_exporter.py
â”‚   â”‚
â”‚   â”œâ”€â”€ api/
â”‚   â”‚   â”œâ”€â”€ routes_dashboard.py
â”‚   â”‚   â”œâ”€â”€ routes_setups.py
â”‚   â”‚   â”œâ”€â”€ routes_orders.py
â”‚   â”‚   â”œâ”€â”€ routes_positions.py
â”‚   â”‚   â””â”€â”€ websocket.py
â”‚   â”‚
â”‚   â”œâ”€â”€ gui/
â”‚   â”‚   â”œâ”€â”€ templates/
â”‚   â”‚   â”‚   â”œâ”€â”€ index.html
â”‚   â”‚   â”‚   â”œâ”€â”€ setups.html
â”‚   â”‚   â”‚   â”œâ”€â”€ positions.html
â”‚   â”‚   â”‚   â”œâ”€â”€ orders.html
â”‚   â”‚   â”‚   â””â”€â”€ logs.html
â”‚   â”‚   â”‚
â”‚   â”‚   â””â”€â”€ static/
â”‚   â”‚       â”œâ”€â”€ css/
â”‚   â”‚       â”œâ”€â”€ js/
â”‚   â”‚       â””â”€â”€ img/
â”‚   â”‚
â”‚   â”œâ”€â”€ alerts/
â”‚   â”‚   â”œâ”€â”€ alert_manager.py
â”‚   â”‚   â”œâ”€â”€ telegram_alert.py
â”‚   â”‚   â””â”€â”€ email_alert.py
â”‚   â”‚
â”‚   â””â”€â”€ utils/
â”‚       â”œâ”€â”€ logger.py
â”‚       â”œâ”€â”€ clock.py
â”‚       â”œâ”€â”€ validators.py
â”‚       â””â”€â”€ id_generator.py
â”‚
â”œâ”€â”€ data/
â”‚   â”œâ”€â”€ trading_state.sqlite
â”‚   â”œâ”€â”€ setups/
â”‚   â”œâ”€â”€ exports/
â”‚   â””â”€â”€ logs/
â”‚
â””â”€â”€ tests/
    â”œâ”€â”€ test_setups.py
    â”œâ”€â”€ test_risk_engine.py
    â”œâ”€â”€ test_order_manager.py
    â””â”€â”€ test_state_machine.py
```

---

# 6. Concepts principaux du programme

## 6.1 Setup

Un setup reprÃ©sente une rÃ¨gle de trading complÃ¨te, mais il ne signifie pas forcÃ©ment Â« acheter Â».

Un setup peut avoir lâ€™un des rÃ´les suivants :

```text
ENTRY_AND_MANAGEMENT
ENTRY_ONLY
MANAGEMENT_ONLY
```

Le rÃ´le doit Ãªtre stockÃ© dans :

```yaml
setup_role: "ENTRY_AND_MANAGEMENT"
```

### `ENTRY_AND_MANAGEMENT`

Utilisation :

```text
DÃ©tecter une opportunitÃ©
â†’ placer une entrÃ©e
â†’ placer le stop initial
â†’ gÃ©rer la position jusquâ€™Ã  la clÃ´ture
```

### `ENTRY_ONLY`

Utilisation :

```text
DÃ©tecter une opportunitÃ©
â†’ placer une entrÃ©e protÃ©gÃ©e
â†’ transfÃ©rer ensuite la position vers un autre module de gestion
```

Ce mode est facultatif en V1.

### `MANAGEMENT_ONLY`

Utilisation :

```text
Adopter une position dÃ©jÃ  existante chez IBKR
â†’ vÃ©rifier la quantitÃ© rÃ©elle
â†’ vÃ©rifier le stop rÃ©el
â†’ gÃ©rer le stop et les objectifs
â†’ ne jamais envoyer de nouvel ordre dâ€™achat
```

Ce mode est indispensable pour gÃ©rer correctement une position dÃ©jÃ  ouverte avant le dÃ©marrage du programme ou ouverte manuellement dans TWS.

## 6.2 Contenu obligatoire dâ€™un setup

Un setup doit contenir :

- identifiant unique ;
- symbole ;
- type de setup ;
- rÃ´le du setup ;
- direction ;
- mode `simulation`, `paper` ou `live` ;
- conditions dâ€™activation ;
- conditions dâ€™entrÃ©e si le rÃ´le autorise une entrÃ©e ;
- conditions dâ€™invalidation ;
- stop-loss initial ou stop protecteur ;
- rÃ¨gles de gestion du stop ;
- objectifs informatifs ou take-profit ;
- taille maximale ;
- risque maximal ;
- statut ;
- historique des Ã©vÃ©nements.

Exemple gÃ©nÃ©rique de setup dâ€™entrÃ©e :

```yaml
setup_id: "<SYMBOL>_BREAKOUT_RETEST_<UNIQUE_ID>"
symbol: "<SYMBOL>"
enabled: true
mode: "paper"

setup_type: "breakout_retest"
setup_role: "ENTRY_AND_MANAGEMENT"
direction: "long"

timeframes:
  signal: "<SIGNAL_TIMEFRAME>"
  confirmation: "<CONFIRMATION_TIMEFRAME>"

zones:
  breakout_min: <BREAKOUT_ZONE_MIN>
  breakout_max: <BREAKOUT_ZONE_MAX>
  retest_min: <RETEST_ZONE_MIN>
  retest_max: <RETEST_ZONE_MAX>
  invalidation: <INVALIDATION_PRICE>

entry:
  enabled: true
  order_type: "STP_LMT"
  trigger_offset: <TRIGGER_OFFSET>
  limit_offset: <LIMIT_OFFSET>
  require_daily_close_above: <BREAKOUT_CONFIRMATION_PRICE>
  require_retest: true
  require_bullish_confirmation: true

risk:
  max_position_amount_usd: <MAX_POSITION_AMOUNT_USD>
  max_risk_usd: <MAX_RISK_USD>
  risk_model: "TRAILING_STOP_INITIAL_RISK"

trailing_stop_loss:
  enabled: true
  mode: "AUTO_INTELLIGENT"
  never_lower_stop: true
  initial_stop: <INITIAL_TRAILING_STOP>
  current_stop: <INITIAL_TRAILING_STOP>
  broker_order:
    order_type: "TRAIL_OR_MANAGED_STOP"
    attach_to_entry_order: true
    required_before_entry_transmission: true

management:
  take_profit_mode: "none"
  never_lower_stop: true

  stop_management:
    mode: "step_based"
    steps:
      - step_id: "STEP_1"
        when_price_above: <STEP_1_TRIGGER_PRICE>
        confirmation: "<CONFIRMATION_RULE>"
        new_stop: <STEP_1_NEW_STOP>
      - step_id: "STEP_2"
        when_price_above: <STEP_2_TRIGGER_PRICE>
        confirmation: "<CONFIRMATION_RULE>"
        new_stop: <STEP_2_NEW_STOP>
```

## 6.3 Setup de gestion dâ€™une position existante

Un setup `MANAGEMENT_ONLY` doit contenir une source de position :

```yaml
setup_type: "position_management"
setup_role: "MANAGEMENT_ONLY"

position_source:
  mode: "adopt_existing_ibkr_position"
  require_existing_position: true
  reconcile_on_load: true
  block_if_position_not_found: true

entry:
  enabled: false
```

RÃ¨gle obligatoire :

```text
MANAGEMENT_ONLY â†’ aucun BUY autorisÃ©
```

Le programme doit refuser le chargement si :

```text
setup_role = MANAGEMENT_ONLY
et
entry.enabled = true
```

## 6.4 Setup actif

Un setup est considÃ©rÃ© comme actif lorsquâ€™il est chargÃ©, validÃ©, activÃ© et suivi par le moteur.

Statuts possibles :

```text
DRAFT
LOADED
VALIDATED
DISABLED
WAITING_ACTIVATION
WAITING_BREAKOUT
WAITING_RETEST
WAITING_REBOUND
WAITING_CONFIRMATION
ENTRY_READY
ENTRY_ORDER_PLACED
ENTRY_PARTIALLY_FILLED
ENTRY_FILLED
STOP_ORDER_PLACED
RECONCILING_EXISTING_POSITION
IN_POSITION
MANAGING_POSITION
MANUAL_REVIEW_REQUIRED
ERROR_REQUIRES_MANUAL_REVIEW
PARTIAL_EXIT
CLOSED
INVALIDATED
CANCELLED
ERROR
```

## 6.5 Position

Une position est la dÃ©tention rÃ©elle dâ€™un actif chez IBKR.

La position rÃ©elle doit toujours Ãªtre synchronisÃ©e avec IBKR.

Le programme ne doit jamais se baser uniquement sur son fichier local pour supposer quâ€™une position existe.

Lorsquâ€™une position est adoptÃ©e, le programme doit enregistrer :

```text
symbol
quantity
average_cost
current_market_price
current_stop
ibkr_account
adopted_from_ibkr
adoption_timestamp
reconciliation_status
linked_setup_id
```

## 6.6 Ordre

Un ordre correspond Ã  une instruction envoyÃ©e Ã  IBKR.

Types dâ€™ordres Ã  gÃ©rer en V1 :

```text
BUY MKT
BUY LMT
BUY STP
BUY STP LMT
SELL STP
SELL LMT
SELL TRAIL
```

Types Ã  gÃ©rer en V2 :

```text
BRACKET
OCA
PARTIAL TAKE PROFIT
TRAILING CUSTOM
```

---

# 7. Moteur de setups

## 7.1 RÃ´le du Setup Engine

Le `Setup Engine` doit :

- charger les setups ;
- vÃ©rifier la validitÃ© de leur configuration ;
- crÃ©er le bon type de stratÃ©gie ;
- appliquer le rÃ´le du setup avant toute dÃ©cision ;
- adopter une position IBKR existante pour les setups `MANAGEMENT_ONLY` ;
- suivre lâ€™Ã©tat de chaque setup ;
- dÃ©clencher les transitions dâ€™Ã©tat ;
- demander au module `Order Manager` de placer les ordres ;
- demander au module `Risk Engine` de calculer la quantitÃ© ;
- enregistrer chaque Ã©vÃ©nement.

## 7.2 Setup Factory

Le programme doit utiliser une fabrique (`factory`) :

```python
class SetupFactory:
    def create(setup_config):
        setup_type = setup_config["setup_type"]
        setup_role = setup_config["setup_role"]

        if setup_role == "MANAGEMENT_ONLY":
            return PositionManagementSetup(setup_config)

        if setup_type == "aggressive_rebound":
            return AggressiveReboundSetup(setup_config)

        if setup_type == "breakout_retest":
            return BreakoutRetestSetup(setup_config)

        if setup_type == "momentum_breakout":
            return MomentumBreakoutSetup(setup_config)

        if setup_type == "pullback_continuation":
            return PullbackContinuationSetup(setup_config)

        raise UnknownSetupTypeError()
```

Avantage :

- ajout facile de nouveaux setups ;
- sÃ©paration claire entre les stratÃ©gies ;
- code plus maintenable ;
- meilleure testabilitÃ©.

---

# 8. Types de setups Ã  prÃ©voir

Le programme doit Ãªtre conÃ§u pour gÃ©rer plusieurs types de setups.

## 8.1 Setup agressif sur rebond de support

Objectif :

Acheter proche dâ€™une zone de support lorsque le prix montre un rebond confirmÃ©.

Exemple humain :

```text
EntrÃ©e agressive seulement si le prix tient 12.50â€“13.00 $ et montre un rebond clair.
```

RÃ¨gles automatisables :

```yaml
setup_type: "aggressive_rebound"

support_zone:
  min: 12.50
  max: 13.00

conditions:
  price_touched_zone: true
  no_close_below_support: true
  bullish_candle_required: true
  close_above_previous_high: true

entry:
  trigger: "signal_candle_high + offset"
  order_type: "STP_LMT"

invalidation:
  close_below: 12.50
  hard_stop: 11.65
```

Ã‰tats :

```text
WAITING_PRICE_IN_ZONE
WAITING_REBOUND_CONFIRMATION
ENTRY_ORDER_PLACED
IN_POSITION
INVALIDATED
```

## 8.2 Setup breakout + retest

Objectif :

Acheter aprÃ¨s cassure dâ€™une rÃ©sistance puis retour contrÃ´lÃ© sur lâ€™ancienne rÃ©sistance devenue support.

Exemple humain :

```text
Attendre une clÃ´ture au-dessus de 14.10â€“14.50 $, puis idÃ©alement un retest rÃ©ussi.
```

RÃ¨gles automatisables :

```yaml
setup_type: "breakout_retest"

breakout:
  daily_close_above: 14.50

retest:
  zone_min: 14.10
  zone_max: 14.50
  max_days_after_breakout: 5

confirmation:
  timeframe: "15m"
  bullish_candle_required: true
  no_close_below: 14.10

entry:
  trigger: "confirmation_candle_high + 0.02"
```

Ã‰tats :

```text
WAITING_BREAKOUT
WAITING_RETEST
WAITING_CONFIRMATION
ENTRY_ORDER_PLACED
IN_POSITION
INVALIDATED
```

## 8.3 Setup pullback continuation

Objectif :

Acheter une action dÃ©jÃ  haussiÃ¨re lors dâ€™un retour vers une moyenne mobile, VWAP ou zone de prix.

RÃ¨gles possibles :

```yaml
setup_type: "pullback_continuation"

trend_filter:
  price_above_ema_20: true
  ema_20_above_ema_50: true

pullback:
  touch_ema_20: true
  max_pullback_percent: 8

confirmation:
  bullish_reversal_candle: true

entry:
  trigger: "signal_candle_high + offset"

stop:
  below_recent_swing_low: true
```

## 8.4 Setup momentum breakout

Objectif :

Acheter une cassure avec volume et momentum.

Ce type de setup sert Ã  une **nouvelle entrÃ©e**. Il ne doit pas Ãªtre utilisÃ© pour gÃ©rer une position dÃ©jÃ  ouverte.

RÃ¨gles possibles :

```yaml
setup_type: "momentum_breakout"
setup_role: "ENTRY_AND_MANAGEMENT"

breakout:
  resistance: <RESISTANCE_PRICE>
  confirmation_mode: "<CONFIRMATION_MODE>"
  volume_ratio_min: <MIN_VOLUME_RATIO>
  volume_average_period: <VOLUME_AVERAGE_PERIOD>
  volume_timeframe: "<VOLUME_TIMEFRAME>"
  relative_strength_required: <BOOLEAN>

entry:
  enabled: true
  order_type: "STP_LMT"
  trigger_offset: <TRIGGER_OFFSET>
  limit_offset: <LIMIT_OFFSET>
  cancel_if_not_filled_after_minutes: <ENTRY_TIMEOUT_MINUTES>

risk:
  max_position_amount_usd: <MAX_POSITION_AMOUNT_USD>
  max_risk_usd: <MAX_RISK_USD>
  risk_model: "TRAILING_STOP_INITIAL_RISK"

trailing_stop_loss:
  enabled: true
  mode: "AUTO_INTELLIGENT"
  never_lower_stop: true
  initial_stop: <INITIAL_TRAILING_STOP>
  current_stop: <INITIAL_TRAILING_STOP>
  broker_order:
    order_type: "TRAIL_OR_MANAGED_STOP"
    attach_to_entry_order: true
    required_before_entry_transmission: true
```

Formules gÃ©nÃ©riques :

```text
trigger_price = resistance + trigger_offset
limit_price   = trigger_price + limit_offset
```

## 8.5 Setup range breakout

Objectif :

Acheter la sortie dâ€™une boÃ®te de consolidation.

RÃ¨gles possibles :

```yaml
setup_type: "range_breakout"

range:
  high: 14.50
  low: 12.50
  min_days_inside_range: 5

entry:
  trigger: "range.high + offset"

invalidation:
  close_back_inside_range: true
```

## 8.6 Politique runner sans take-profit

Objectif :

Laisser courir une position gagnante et remonter le stop progressivement.

Le runner est une **politique de gestion**, pas nÃ©cessairement une stratÃ©gie dâ€™entrÃ©e autonome.

RÃ¨gles possibles :

```yaml
management:
  take_profit_mode: "none"
  never_lower_stop: true

  stop_management:
    mode: "step_based"
    steps:
      - when_price_above: <TRIGGER_PRICE>
        confirmation: "<CONFIRMATION_RULE>"
        new_stop: <NEW_STOP_PRICE>
```

## 8.7 Setup de gestion dâ€™une position existante

Objectif :

Adopter une position dÃ©jÃ  prÃ©sente chez IBKR et la gÃ©rer sans crÃ©er une nouvelle entrÃ©e.

RÃ¨gles possibles :

```yaml
setup_type: "position_management"
setup_role: "MANAGEMENT_ONLY"

position_source:
  mode: "adopt_existing_ibkr_position"
  require_existing_position: true
  reconcile_on_load: true
  block_if_position_not_found: true

entry:
  enabled: false

trailing_stop_loss:
  enabled: true
  mode: "AUTO_INTELLIGENT"
  never_lower_stop: true
  initial_stop: <INITIAL_TRAILING_STOP>
  current_stop: <CURRENT_TRAILING_STOP>
  broker_order:
    order_type: "TRAIL_OR_MANAGED_STOP"
    required_before_entry_transmission: true

risk:
  emergency_exit_if_stop_fails: true
  if_market_price_below_stop: "MANUAL_REVIEW_REQUIRED"
```

---

# 9. Machine Ã  Ã©tats

Chaque setup doit fonctionner avec une machine Ã  Ã©tats.

## 9.1 Pourquoi utiliser une machine Ã  Ã©tats

Une machine Ã  Ã©tats permet de savoir exactement oÃ¹ se trouve chaque setup.

Exemple :

```text
Le prix nâ€™a pas encore cassÃ© la rÃ©sistance.
Le breakout est confirmÃ©.
Le retest est en cours.
Le signal dâ€™entrÃ©e est validÃ©.
Lâ€™ordre dâ€™entrÃ©e est placÃ©.
Lâ€™entrÃ©e est exÃ©cutÃ©e.
Le stop est placÃ©.
La position est en gestion.
La position est fermÃ©e.
```

Sans machine Ã  Ã©tats, le programme risque :

- dâ€™acheter deux fois ;
- dâ€™oublier un stop ;
- de mal interprÃ©ter un retest ;
- de mÃ©langer plusieurs setups ;
- de perdre le suivi aprÃ¨s redÃ©marrage.

## 9.2 Ã‰tats globaux recommandÃ©s

```text
DRAFT
LOADED
VALIDATED
DISABLED
WAITING_MARKET_OPEN
WAITING_ACTIVATION
WAITING_BREAKOUT
WAITING_RETEST
WAITING_REBOUND
WAITING_CONFIRMATION
ENTRY_READY
ENTRY_ORDER_PLACED
ENTRY_PARTIALLY_FILLED
ENTRY_FILLED
STOP_ORDER_PLACED
IN_POSITION
MANAGING_POSITION
WAITING_EXIT
EXIT_ORDER_PLACED
PARTIALLY_CLOSED
CLOSED
INVALIDATED
CANCELLED
ERROR
```

## 9.3 Exemple pour breakout + retest

```text
LOADED
  â†“
VALIDATED
  â†“
WAITING_BREAKOUT
  â†“ clÃ´ture journaliÃ¨re > niveau de cassure
WAITING_RETEST
  â†“ price returns to retest zone
WAITING_CONFIRMATION
  â†“ bullish candle confirmed
ENTRY_READY
  â†“ risk approved
ENTRY_ORDER_PLACED
  â†“ order filled
ENTRY_FILLED
  â†“ stop accepted by IBKR
STOP_ORDER_PLACED
  â†“
IN_POSITION
  â†“
MANAGING_POSITION
  â†“ stop hit or manual exit
CLOSED
```

## 9.4 Transitions interdites

Le programme doit interdire certaines transitions dangereuses :

```text
WAITING_BREAKOUT â†’ IN_POSITION
ENTRY_FILLED â†’ CLOSED sans STOP_ORDER_PLACED
IN_POSITION â†’ ENTRY_ORDER_PLACED
CLOSED â†’ IN_POSITION
INVALIDATED â†’ ENTRY_ORDER_PLACED
MANAGEMENT_ONLY â†’ ENTRY_ORDER_PLACED
MANAGEMENT_ONLY â†’ BUY_ORDER_PLACED
```

## 9.5 Machine Ã  Ã©tats pour une position existante

```text
LOADED
  â†“
VALIDATED
  â†“
RECONCILING_EXISTING_POSITION
  â†“ position trouvÃ©e chez IBKR et cohÃ©rente
IN_POSITION
  â†“
MANAGING_POSITION
  â†“ stop hit ou sortie manuelle
CLOSED
```

Cas dâ€™erreur :

```text
RECONCILING_EXISTING_POSITION
  â†“ position introuvable, stop absent, quantitÃ© incohÃ©rente ou prix dÃ©jÃ  sous le stop demandÃ©
MANUAL_REVIEW_REQUIRED
```

Le setup ne doit pas acheter pour Â« corriger Â» une position introuvable.

---

# 10. Market Data

## 10.1 RÃ´le du module Market Data

Le module `Market Data` doit :

- rÃ©cupÃ©rer les prix temps rÃ©el ou diffÃ©rÃ©s ;
- construire les bougies ;
- gÃ©rer plusieurs timeframes ;
- dÃ©tecter les donnÃ©es obsolÃ¨tes ;
- sauvegarder les prix utiles ;
- fournir les donnÃ©es au moteur de signaux.

## 10.2 Timeframes Ã  gÃ©rer

V1 :

```text
1m
5m
15m
1d
```

V2 :

```text
30m
1h
1w
```

## 10.3 DonnÃ©es minimales dâ€™une bougie

Chaque bougie doit contenir :

```text
symbol
timeframe
timestamp_open
timestamp_close
open
high
low
close
volume
is_closed
source
```

## 10.4 RÃ¨gle importante : ne pas utiliser une bougie non clÃ´turÃ©e pour une condition de clÃ´ture

Exemple :

```text
Condition : clÃ´ture journaliÃ¨re > 14.50
```

Le programme doit attendre la clÃ´ture rÃ©elle de la bougie journaliÃ¨re.

Il ne doit pas valider la condition parce que le prix intrajournalier est temporairement au-dessus de 14.50.

## 10.5 DonnÃ©es obsolÃ¨tes

Le programme doit bloquer les dÃ©cisions si :

```text
dernier prix reÃ§u > 20 secondes
```

ou, pour les bougies :

```text
derniÃ¨re bougie attendue non reÃ§ue
```

RÃ¨gle :

```text
Pas de donnÃ©es fiables = pas de nouvel ordre.
```

---

# 11. Signal Engine

## 11.1 RÃ´le

Le `Signal Engine` transforme les donnÃ©es de marchÃ© en signaux exploitables.

Il ne doit pas envoyer dâ€™ordres directement.

Il retourne uniquement :

```text
SIGNAL_VALID
SIGNAL_INVALID
SIGNAL_PENDING
SIGNAL_ERROR
```

## 11.2 Exemple de dÃ©tection dâ€™un rebond

Un rebond peut Ãªtre dÃ©fini par :

```text
1. le prix touche la zone de support ;
2. aucune clÃ´ture 15m ne passe sous la borne basse ;
3. une bougie haussiÃ¨re se forme ;
4. la clÃ´ture passe au-dessus du plus haut de la bougie prÃ©cÃ©dente ;
5. le volume nâ€™est pas anormalement faible.
```

## 11.3 Exemple de dÃ©tection dâ€™un retest rÃ©ussi

Un retest rÃ©ussi peut Ãªtre dÃ©fini par :

```text
1. breakout validÃ© prÃ©cÃ©demment ;
2. le prix revient dans la zone de retest ;
3. le prix ne clÃ´ture pas sous la zone ;
4. une bougie de rÃ©action haussiÃ¨re apparaÃ®t ;
5. lâ€™entrÃ©e est placÃ©e au-dessus du plus haut de la bougie de confirmation.
```

## 11.4 Signaux interdits

Le moteur doit refuser un signal si :

- le marchÃ© est fermÃ© ;
- les donnÃ©es sont obsolÃ¨tes ;
- le setup est dÃ©sactivÃ© ;
- le setup est dÃ©jÃ  en position ;
- le risque dÃ©passe la limite ;
- un ordre dâ€™entrÃ©e existe dÃ©jÃ  ;
- le stop-loss initial nâ€™est pas dÃ©fini ;
- la liquiditÃ© est insuffisante ;
- le spread est trop large ;
- la position maximale sur ce symbole est dÃ©jÃ  atteinte.

---

# 12. Risk Engine

## 12.1 RÃ´le

Le `Risk Engine` calcule la quantitÃ© autorisÃ©e et dÃ©cide si un trade peut Ãªtre pris.

Il doit appliquer Ã  la fois :

- limite par budget ;
- limite par risque ;
- limite par nombre de positions ;
- limite de perte journaliÃ¨re ;
- limite dâ€™exposition totale ;
- taille minimale et maximale.

## 12.2 Calcul de quantitÃ© pour une nouvelle entrÃ©e

Le calcul doit utiliser le **pire prix dâ€™exÃ©cution autorisÃ©**.

Pour un ordre `STP_LMT` :

```text
worst_case_entry_price = limit_price
```

Pour un ordre `LMT` :

```text
worst_case_entry_price = limit_price
```

Pour un ordre `MKT` :

```text
worst_case_entry_price = prix_estime + marge_de_slippage
```

Formule :

```text
quantitÃ© selon budget = budget_max / worst_case_entry_price

risque par action = worst_case_entry_price - stop_loss

quantitÃ© selon risque = risque_max / risque_par_action

quantitÃ© finale = floor(min(quantitÃ© selon budget, quantitÃ© selon risque))
```

Le trigger dâ€™un ordre `STP_LMT` ne doit jamais Ãªtre utilisÃ© comme prix de risque si le prix limite maximal est supÃ©rieur.

## 12.3 Calcul gÃ©nÃ©rique corrigÃ© avec STP_LMT

```text
trigger_price           = resistance + trigger_offset
entry_limit_price       = trigger_price + limit_offset
worst_case_entry_price  = entry_limit_price

risk_per_share          = worst_case_entry_price - trailing_stop_loss.initial_stop

quantity_by_budget      = floor(
                            max_position_amount_usd /
                            worst_case_entry_price
                          )

quantity_by_risk        = floor(
                            max_risk_usd /
                            risk_per_share
                          )

maximum_quantity        = min(quantity_by_budget, quantity_by_risk)
maximum_risk            = maximum_quantity Ã— risk_per_share
```

Le programme doit refuser toute quantitÃ© qui dÃ©passe `max_risk_usd`.

## 12.4 Calcul de risque pour une position existante

Pour un setup `MANAGEMENT_ONLY`, le programme ne calcule pas une nouvelle quantitÃ© dâ€™achat.

Il mesure lâ€™exposition existante :

```text
quantity = quantitÃ© rÃ©elle rÃ©cupÃ©rÃ©e depuis IBKR
average_cost = coÃ»t moyen rÃ©cupÃ©rÃ© depuis IBKR
trailing_stop = stop rÃ©el ou trailing_stop_loss.current_stop demandÃ©
open_risk = max(0, average_cost - trailing_stop) Ã— quantity
remaining_market_risk = max(0, current_market_price - trailing_stop) Ã— quantity
```

Si le prix courant est infÃ©rieur ou Ã©gal au stop demandÃ© :

```text
current_market_price <= trailing_stop_loss.current_stop
```

le programme doit passer en :

```text
MANUAL_REVIEW_REQUIRED
```

Il ne doit pas crÃ©er automatiquement un nouvel achat et ne doit pas supposer quâ€™un ordre stop peut Ãªtre placÃ© sans contrÃ´le.

## 12.5 Conditions de refus

Le trade doit Ãªtre refusÃ© si :

```text
quantitÃ© finale < 1
risque_par_action <= 0
stop_loss >= worst_case_entry_price
budget insuffisant
max_daily_loss atteint
max_open_positions atteint
donnÃ©es de marchÃ© invalides
spread trop grand
setup_role = MANAGEMENT_ONLY
ordre dâ€™entrÃ©e dÃ©jÃ  existant
position existante incompatible avec une nouvelle entrÃ©e
```

---

# 13. Order Manager

## 13.1 RÃ´le

Le module `Order Manager` est le seul module autorisÃ© Ã  envoyer, modifier ou annuler des ordres.

Aucun setup ne doit appeler directement lâ€™API TWS.

## 13.2 ResponsabilitÃ©s

Le module `Order Manager` doit :

- crÃ©er les ordres IBKR ;
- mapper les ordres internes vers les ordres TWS ;
- ajouter `orderRef` ;
- enregistrer `orderId`, `permId`, `parentId`, `parentPermId` ;
- suivre les exÃ©cutions partielles ;
- gÃ©rer les rejets ;
- modifier les stops ;
- annuler les ordres expirÃ©s ;
- Ã©viter les doublons ;
- valider que lâ€™ordre correspond toujours au setup.

## 13.3 orderRef

Chaque ordre doit avoir un `orderRef` clair.

Format recommandÃ© :

```text
BOT_<SYMBOL>_<SETUP_TYPE>_<SETUP_ID>_<ACTION>
```

Exemple :

```text
BOT_<SYMBOL>_<SETUP_TYPE>_<SETUP_ID>_ENTRY
BOT_<SYMBOL>_<SETUP_TYPE>_<SETUP_ID>_STOP
```

## 13.4 Ordre dâ€™entrÃ©e recommandÃ©

Pour une entrÃ©e automatique :

```text
BUY STP LMT
```

Exemple :

```text
Trigger : 14.58
Limit   : 14.63
```

Avantage :

- Ã©vite dâ€™acheter trop haut en cas de spike ;
- force une confirmation au-dessus du niveau.

InconvÃ©nient :

- peut ne pas Ãªtre exÃ©cutÃ© si le prix part trop vite.

## 13.5 Stop-loss recommandÃ©

Pour la protection :

```text
SELL STP
```

Avantage :

- favorise la sortie ;
- plus sÃ»r quâ€™un stop-limit dans un mouvement violent.

InconvÃ©nient :

- prix dâ€™exÃ©cution non garanti.

## 13.6 Annulation automatique des ordres non exÃ©cutÃ©s

Chaque setup doit pouvoir dÃ©finir :

```yaml
entry_order:
  cancel_if_not_filled_after_minutes: 30
```

Si lâ€™ordre nâ€™est pas exÃ©cutÃ© aprÃ¨s ce dÃ©lai :

```text
ENTRY_ORDER_PLACED â†’ CANCELLED
```

ou :

```text
ENTRY_ORDER_PLACED â†’ WAITING_CONFIRMATION
```

selon la configuration.

## 13.7 VÃ©rification obligatoire du rÃ´le avant placement dâ€™ordre

Avant tout ordre dâ€™entrÃ©e, le module `Order Manager` doit vÃ©rifier :

```text
setup_role in ["ENTRY_AND_MANAGEMENT", "ENTRY_ONLY"]
entry.enabled = true
setup status = ENTRY_READY
aucune position conflictuelle
aucun ordre dâ€™entrÃ©e dupliquÃ©
```

Interdiction absolue :

```text
setup_role = MANAGEMENT_ONLY
â†’ BUY interdit
```

## 13.8 Champs calculÃ©s pour un ordre STP_LMT

Pour Ã©viter un affichage ambigu, stocker sÃ©parÃ©ment :

```text
entry_trigger_price
entry_limit_price
worst_case_entry_price
trailing_stop_loss.initial_stop
maximum_quantity
maximum_risk_usd
```

---

# 14. Gestion du stop-loss

## 14.1 Stop initial

Le stop initial doit Ãªtre placÃ© immÃ©diatement aprÃ¨s lâ€™exÃ©cution de lâ€™entrÃ©e.

RÃ¨gle obligatoire :

```text
Aucune position ouverte ne doit rester sans stop rÃ©el chez IBKR.
```

## 14.2 Modification du stop

Le programme peut modifier le stop si :

- la position existe rÃ©ellement ;
- lâ€™ordre stop existe rÃ©ellement ;
- le nouveau stop est supÃ©rieur Ã  lâ€™ancien pour une position longue ;
- la quantitÃ© du stop ne dÃ©passe pas la quantitÃ© dÃ©tenue ;
- la modification est acceptÃ©e par IBKR.

## 14.3 Interdiction de baisser le stop

Pour une position longue :

```text
new_stop > current_stop
```

Sinon, la modification doit Ãªtre refusÃ©e.

Exception possible :

```yaml
allow_stop_widening: false
```

La valeur par dÃ©faut doit toujours Ãªtre `false`.

## 14.4 Modes de gestion du stop

Le programme doit prÃ©voir plusieurs modes.

### Mode fixe

```yaml
stop_management:
  mode: "fixed"
```

Le stop ne change jamais.

### Mode par paliers

```yaml
stop_management:
  mode: "step_based"

steps:
  - when_price_above: 15.30
    new_stop: 14.45
```

### Mode trailing simple

```yaml
stop_management:
  mode: "trailing_percent"
  percent: 8
```

### Mode ATR

```yaml
stop_management:
  mode: "atr"
  atr_period: 14
  atr_multiplier: 2.5
```

### Mode higher-low

```yaml
stop_management:
  mode: "higher_low"
  timeframe: "15m"
  buffer: 0.15
```

## 14.5 Recommandation pour le style de lâ€™utilisateur

Comme lâ€™utilisateur souhaite souvent laisser courir sans take-profit fixe, le mode recommandÃ© est :

```text
step_based + higher_low
```

Exemple :

```yaml
management:
  take_profit_mode: "none"
  never_lower_stop: true

  stop_management:
    primary_mode: "step_based"
    secondary_mode: "higher_low"
```

## 14.6 Stop dâ€™une position existante

Pour un setup `MANAGEMENT_ONLY`, le programme doit :

```text
1. rÃ©cupÃ©rer la position rÃ©elle chez IBKR ;
2. rÃ©cupÃ©rer les ordres ouverts liÃ©s au symbole ;
3. identifier le stop protecteur existant ;
4. vÃ©rifier la quantitÃ© du stop ;
5. comparer le stop rÃ©el au stop demandÃ© ;
6. modifier ou crÃ©er le stop uniquement si la situation est cohÃ©rente ;
7. passer en MANUAL_REVIEW_REQUIRED si le prix courant est dÃ©jÃ  infÃ©rieur ou Ã©gal au stop demandÃ©.
```

RÃ¨gle :

```text
Ne jamais placer aveuglÃ©ment un SELL STP au-dessus ou au niveau du prix courant.
```

---

# 15. Gestion des objectifs

Les objectifs peuvent Ãªtre utilisÃ©s de plusieurs maniÃ¨res.

## 15.1 Objectifs informatifs

Le programme envoie une alerte, mais ne vend rien.

```yaml
targets:
  - name: "objectif_1"
    zone_min: 14.80
    zone_max: 15.30
    action: "notify_only"
```

## 15.2 Objectifs avec remontÃ©e du stop

```yaml
targets:
  - name: "objectif_1"
    zone_min: 14.80
    zone_max: 15.30
    action: "notify_and_raise_stop"
    new_stop: 13.95
```

## 15.3 Sorties partielles

```yaml
targets:
  - name: "objectif_1"
    price: 15.30
    action: "sell_percent"
    percent: 25
```

Ã€ gÃ©rer prudemment.

AprÃ¨s une sortie partielle :

```text
nouvelle quantitÃ© position = quantitÃ© initiale - quantitÃ© vendue
nouvelle quantitÃ© stop = nouvelle quantitÃ© position
```

Le programme doit toujours synchroniser le stop avec la position restante.

---

# 16. Reconciliation Engine

## 16.1 RÃ´le

Le `Reconciliation Engine` est indispensable.

Il compare :

```text
donnÃ©es locales
positions IBKR
ordres ouverts IBKR
exÃ©cutions IBKR
```

## 16.2 Quand lancer la rÃ©conciliation

La rÃ©conciliation doit Ãªtre lancÃ©e :

- au dÃ©marrage ;
- aprÃ¨s reconnexion TWS ;
- aprÃ¨s erreur dâ€™ordre ;
- aprÃ¨s modification manuelle dans TWS ;
- pÃ©riodiquement toutes les 30 Ã  60 secondes ;
- avant toute nouvelle entrÃ©e ;
- avant toute modification de stop.

## 16.3 Cas Ã  dÃ©tecter

Le moteur doit dÃ©tecter :

```text
position locale absente mais position IBKR existante
position locale existante mais position IBKR absente
stop local absent mais stop IBKR prÃ©sent
stop local prÃ©sent mais stop IBKR absent
quantitÃ© stop diffÃ©rente de quantitÃ© position
ordre local non retrouvÃ© chez IBKR
ordre IBKR inconnu du programme
ordre modifiÃ© manuellement dans TWS
```

## 16.4 Actions possibles

Selon le cas :

```text
SYNC_LOCAL_FROM_IBKR
ADOPT_EXISTING_POSITION
CREATE_MISSING_STOP
UPDATE_EXISTING_STOP
CANCEL_UNKNOWN_ORDER
MARK_SETUP_ERROR
PAUSE_TRADING
ASK_USER_ACTION
```

Pour la V1, les cas ambigus doivent mettre le setup en Ã©tat :

```text
ERROR_REQUIRES_MANUAL_REVIEW
```

## 16.5 Adoption dâ€™une position existante

Lorsquâ€™un setup utilise :

```yaml
position_source:
  mode: "adopt_existing_ibkr_position"
```

la rÃ©conciliation doit :

```text
1. chercher la position rÃ©elle chez IBKR par compte et symbole ;
2. rÃ©cupÃ©rer la quantitÃ© et le coÃ»t moyen ;
3. vÃ©rifier quâ€™une seule position compatible existe ;
4. chercher un stop ouvert existant ;
5. comparer la quantitÃ© du stop Ã  la quantitÃ© dÃ©tenue ;
6. enregistrer la position adoptÃ©e dans SQLite ;
7. rattacher la position au setup ;
8. passer Ã  IN_POSITION ou MANUAL_REVIEW_REQUIRED.
```

Aucune entrÃ©e ne doit Ãªtre placÃ©e pendant cette procÃ©dure.

---

# 17. Stockage sur fichier

## 17.1 Format recommandÃ©

MÃªme si lâ€™utilisateur demande un fichier, il est prÃ©fÃ©rable dâ€™utiliser :

```text
SQLite = fichier structurÃ©
```

Fichier principal :

```text
data/trading_state.sqlite
```

## 17.2 Tables recommandÃ©es

### Table `setups`

```sql
CREATE TABLE setups (
    setup_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    setup_type TEXT NOT NULL,
    setup_role TEXT NOT NULL,
    status TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    enabled INTEGER NOT NULL
);
```

### Table `orders`

```sql
CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    setup_id TEXT,
    symbol TEXT,
    action TEXT,
    order_type TEXT,
    quantity INTEGER,
    limit_price REAL,
    stop_price REAL,
    status TEXT,
    ib_order_id INTEGER,
    ib_perm_id INTEGER,
    ib_parent_id INTEGER,
    ib_parent_perm_id INTEGER,
    ib_oca_group TEXT,
    order_ref TEXT,
    created_at TEXT,
    updated_at TEXT
);
```

### Table `positions`

```sql
CREATE TABLE positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    setup_id TEXT,
    symbol TEXT,
    account_id TEXT,
    quantity INTEGER,
    avg_price REAL,
    current_market_price REAL,
    current_stop REAL,
    adopted_from_ibkr INTEGER NOT NULL DEFAULT 0,
    reconciliation_status TEXT,
    status TEXT,
    opened_at TEXT,
    adopted_at TEXT,
    closed_at TEXT
);
```

### Table `events`

```sql
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    setup_id TEXT,
    symbol TEXT,
    event_type TEXT,
    message TEXT,
    payload_json TEXT,
    created_at TEXT
);
```

### Table `candles`

```sql
CREATE TABLE candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    timeframe TEXT,
    timestamp_open TEXT,
    timestamp_close TEXT,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume INTEGER,
    is_closed INTEGER
);
```

## 17.3 Logs

En plus de SQLite, garder des logs texte :

```text
data/logs/app.log
data/logs/orders.log
data/logs/errors.log
```

Format recommandÃ© :

```text
timestamp | level | module | setup_id | symbol | message
```

---

# 18. Interface GUI HTML

## 18.1 Objectif de la GUI

La GUI doit permettre Ã  lâ€™utilisateur de :

- voir lâ€™Ã©tat global du bot ;
- voir si TWS est connectÃ© ;
- voir les setups actifs ;
- ajouter un setup ;
- modifier un setup ;
- activer ou dÃ©sactiver un setup ;
- suivre les positions ;
- suivre les ordres ;
- voir les logs ;
- voir les erreurs ;
- forcer une synchronisation ;
- mettre le bot en pause ;
- fermer une position manuellement ;
- annuler un ordre ;
- remonter un stop manuellement.

## 18.2 Pages recommandÃ©es

```text
/dashboard
/setups
/setups/{setup_id}
/positions
/orders
/logs
/settings
/risk
```

## 18.3 Dashboard

Le dashboard doit afficher :

```text
TWS connection status
Mode: paper/live
Market status
Nombre de setups actifs
Nombre de positions ouvertes
PnL journalier
Perte journaliÃ¨re autorisÃ©e restante
Derniers Ã©vÃ©nements
DerniÃ¨res erreurs
```

Exemple visuel :

```text
+----------------------------------------------------+
| TWS: CONNECTED | MODE: PAPER | BOT: RUNNING        |
+----------------------------------------------------+
| Active setups: 5 | Open positions: 2 | Daily PnL: +34 |
+----------------------------------------------------+
| SYMBOL_A | BREAKOUT_RETEST   | WAITING_RETEST      |
| SYMBOL_B | AGGRESSIVE_REBOUND | IN_POSITION         |
| SYMBOL_C | MOMENTUM_BREAKOUT  | ENTRY_ORDER_PLACED  |
+----------------------------------------------------+
```

## 18.4 Page Setups

Colonnes recommandÃ©es :

```text
Setup ID
Symbol
Setup Type
Setup Role
Status
Entry Trigger
Maximum Limit Price
Protective Stop
Maximum Quantity
Maximum Risk
Order Status
Position Status
Reconciliation Status
Last Event
Actions
```

Actions possibles :

```text
View
Edit
Enable
Disable
Cancel
Duplicate
Delete
```

## 18.5 Page dÃ©tail dâ€™un setup

Afficher :

```text
Configuration complÃ¨te
RÃ´le du setup
Ã‰tat actuel
Historique des transitions
Ordres liÃ©s
Position liÃ©e
Origine de la position : bot, manuelle ou adoptÃ©e depuis IBKR
Trigger dâ€™entrÃ©e
Prix limite maximal
Prix utilisÃ© pour le calcul du risque
QuantitÃ© maximale
Stop actuel
Risque maximal
Risque restant
Ã‰tat de rÃ©conciliation
Objectifs atteints
Logs spÃ©cifiques
```

Boutons :

```text
Enable setup
Disable setup
Adopt existing IBKR position
Cancel entry order
Force sync
Move stop
Close position
Mark as closed
```

## 18.6 Page Positions

Afficher :

```text
Symbol
Quantity
Average Price
Current Price
Unrealized PnL
Current Stop
Risk Remaining
Setup ID
Status
Actions
```

## 18.7 Page Orders

Afficher :

```text
Symbol
Setup ID
Order Type
Action
Quantity
Status
IB Order ID
IB Perm ID
Parent ID
OCA Group
Created At
Updated At
```

## 18.8 Page Logs

Filtres :

```text
symbol
setup_id
level
event_type
date
```

Niveaux :

```text
INFO
WARNING
ERROR
CRITICAL
TRADE
ORDER
RISK
SYNC
```

## 18.9 WebSocket

La GUI doit recevoir les mises Ã  jour en temps rÃ©el.

Ã‰vÃ©nements WebSocket :

```text
connection_status_changed
setup_status_changed
order_status_changed
position_updated
risk_limit_reached
error_detected
log_event_created
```

---

# 19. Configuration globale

Fichier :

```text
config.yaml
```

Exemple :

```yaml
app:
  environment: "development"
  mode: "paper"
  timezone: "Africa/Tunis"

broker:
  host: "127.0.0.1"
  port: 7497
  client_id: 1001
  reconnect: true
  reconnect_interval_seconds: 5

risk:
  max_open_positions: 5
  max_position_amount_usd: 250
  max_risk_per_trade_usd: 15
  max_daily_loss_usd: 50
  max_total_exposure_usd: 1000
  allow_short: false

market:
  allow_premarket: false
  allow_after_hours: false
  stale_data_seconds: 20

orders:
  default_entry_order_type: "STP_LMT"
  default_stop_order_type: "STP"
  cancel_unfilled_entry_after_minutes: 30
  calculate_stp_lmt_risk_from_limit_price: true
  block_buy_for_management_only: true

storage:
  database_file: "data/trading_state.sqlite"
  setups_folder: "data/setups"
  logs_folder: "data/logs"

gui:
  host: "127.0.0.1"
  port: 8000
  require_login: true

alerts:
  enabled: true
  telegram_enabled: false
  email_enabled: false
```

---

# 20. Validation des setups

Avant dâ€™activer un setup, le programme doit vÃ©rifier :

```text
symbol non vide
setup_type supportÃ©
setup_role supportÃ©
mode dÃ©fini
timeframe supportÃ©
prix cohÃ©rents
budget max dÃ©fini si nouvelle entrÃ©e
risque max dÃ©fini
rÃ¨gles dâ€™invalidation prÃ©sentes
aucun setup actif contradictoire sur le mÃªme symbole
```

## 20.1 Validation dâ€™un setup dâ€™entrÃ©e

Pour :

```text
ENTRY_AND_MANAGEMENT
ENTRY_ONLY
```

vÃ©rifier :

```text
entry.enabled = true
ordre dâ€™entrÃ©e supportÃ©
stop-loss initial dÃ©fini
stop-loss infÃ©rieur au pire prix dâ€™exÃ©cution autorisÃ© pour une position longue
worst_case_entry_price calculable
quantitÃ© calculable
```

Pour `STP_LMT` :

```text
trigger_price = resistance + trigger_offset
limit_price = trigger_price + limit_offset
worst_case_entry_price = limit_price
```

## 20.2 Validation dâ€™un setup de gestion seule

Pour :

```text
MANAGEMENT_ONLY
```

vÃ©rifier :

```text
entry.enabled = false
position_source.mode = adopt_existing_ibkr_position
position_source.reconcile_on_load = true
trailing_stop_loss.initial_stop defini avant armement
aucun BUY autorisÃ©
```

Exemple de setup invalide :

```yaml
setup_role: "MANAGEMENT_ONLY"

entry:
  enabled: true
```

Raison :

```text
Un setup de gestion seule ne doit jamais crÃ©er une nouvelle entrÃ©e.
```

## 20.3 Cas nÃ©cessitant une revue manuelle

Passer en :

```text
MANUAL_REVIEW_REQUIRED
```

si :

```text
position IBKR introuvable
plusieurs positions incompatibles
stop IBKR absent et crÃ©ation non sÃ»re
quantitÃ© stop diffÃ©rente de la quantitÃ© dÃ©tenue
prix courant infÃ©rieur ou Ã©gal au stop demandÃ©
ordre IBKR inconnu ou incohÃ©rent
```

---

# 21. RÃ¨gles de comportement du bot

## 21.1 Au dÃ©marrage

Le bot doit :

```text
1. charger config.yaml
2. initialiser les logs
3. ouvrir la base SQLite
4. charger les setups YAML
5. valider chaque setup, y compris son rÃ´le
6. se connecter Ã  TWS
7. rÃ©cupÃ©rer positions IBKR
8. rÃ©cupÃ©rer ordres ouverts IBKR
9. lancer la rÃ©conciliation
10. adopter les positions IBKR demandÃ©es par les setups MANAGEMENT_ONLY
11. vÃ©rifier les stops existants
12. placer les setups incohÃ©rents en MANUAL_REVIEW_REQUIRED
13. dÃ©marrer le backend GUI
14. dÃ©marrer le moteur de trading
```

## 21.2 Si TWS est dÃ©connectÃ©

Le bot doit :

```text
1. marquer TWS comme DISCONNECTED
2. arrÃªter lâ€™envoi de nouveaux ordres
3. continuer Ã  afficher la GUI
4. tenter une reconnexion
5. aprÃ¨s reconnexion, lancer une rÃ©conciliation
6. reprendre seulement si lâ€™Ã©tat est cohÃ©rent
```

## 21.3 Si un ordre est rejetÃ©

Le bot doit :

```text
1. enregistrer lâ€™erreur
2. afficher lâ€™erreur dans la GUI
3. mettre le setup en ERROR
4. bloquer toute nouvelle action automatique sur ce setup
5. demander une revue manuelle
```

## 21.4 Si une entrÃ©e est exÃ©cutÃ©e mais le stop Ã©choue

Cas critique.

Action obligatoire :

```text
1. tenter de replacer immÃ©diatement le stop
2. si Ã©chec, envoyer une alerte critique
3. mettre le bot en pause pour ce symbole
4. afficher le risque dans la GUI
5. demander intervention manuelle
```

Option stricte :

```text
Si le stop ne peut pas Ãªtre placÃ© aprÃ¨s N tentatives, vendre immÃ©diatement la position au marchÃ©.
```

Cette option doit Ãªtre configurable :

```yaml
safety:
  emergency_exit_if_stop_fails: true
  max_stop_submit_retries: 3
```

## 21.5 Si lâ€™utilisateur modifie un ordre manuellement dans TWS

Le bot doit le dÃ©tecter pendant la rÃ©conciliation.

Actions possibles :

```text
1. accepter la modification et synchroniser localement ;
2. remettre lâ€™ordre Ã  la valeur attendue ;
3. mettre le setup en pause ;
4. demander confirmation dans la GUI.
```

Pour la V1, recommandation :

```text
Modification manuelle dÃ©tectÃ©e â†’ setup en MANUAL_REVIEW_REQUIRED
```

## 21.6 Chargement dâ€™un setup MANAGEMENT_ONLY

Le programme doit exÃ©cuter :

```text
1. vÃ©rifier que entry.enabled = false ;
2. passer Ã  RECONCILING_EXISTING_POSITION ;
3. chercher la position rÃ©elle chez IBKR ;
4. si absente, passer Ã  MANUAL_REVIEW_REQUIRED ;
5. si prÃ©sente, rÃ©cupÃ©rer quantitÃ© et coÃ»t moyen ;
6. identifier le stop existant ;
7. vÃ©rifier le prix courant ;
8. si prix courant <= stop demandÃ©, passer Ã  MANUAL_REVIEW_REQUIRED ;
9. sinon crÃ©er ou mettre Ã  jour le stop si nÃ©cessaire ;
10. passer Ã  IN_POSITION.
```

Interdiction :

```text
Ne jamais transformer automatiquement MANAGEMENT_ONLY en momentum_breakout.
Ne jamais acheter pour compenser une position introuvable.
```

---

# 22. Performance et Ã©volutivitÃ©

## 22.1 Ã‰viter les boucles inutiles

Ne pas faire :

```python
while True:
    check_everything()
    sleep(1)
```

PrÃ©fÃ©rer :

- Ã©vÃ©nements de marchÃ© ;
- callbacks dâ€™ordres ;
- WebSocket ;
- tÃ¢ches planifiÃ©es lÃ©gÃ¨res ;
- vÃ©rifications ciblÃ©es.

## 22.2 SÃ©parer les frÃ©quences

Exemple :

```text
Market data temps rÃ©el        : Ã©vÃ©nementiel
VÃ©rification setups           : Ã  chaque nouvelle bougie
RÃ©conciliation IBKR           : toutes les 30-60 secondes
Actualisation GUI             : Ã©vÃ©nementielle ou 1-2 secondes
Export CSV                    : Ã  la demande
```

## 22.3 Index SQLite

CrÃ©er des index sur :

```sql
CREATE INDEX idx_setups_status ON setups(status);
CREATE INDEX idx_orders_setup_id ON orders(setup_id);
CREATE INDEX idx_orders_ib_perm_id ON orders(ib_perm_id);
CREATE INDEX idx_events_setup_id ON events(setup_id);
CREATE INDEX idx_candles_symbol_timeframe ON candles(symbol, timeframe);
```

## 22.4 Architecture async

Le programme doit utiliser une architecture asynchrone si possible :

```text
asyncio
FastAPI async
broker connector async
market data async
WebSocket async
```

Objectif :

- Ã©viter le blocage de la GUI ;
- Ã©viter le blocage du moteur de trading ;
- gÃ©rer plusieurs symboles simultanÃ©ment ;
- amÃ©liorer la rÃ©activitÃ©.

---

# 23. Gestion multi-symboles

Le programme doit pouvoir suivre plusieurs symboles :

```text
SYMBOL_A
SYMBOL_B
SYMBOL_C
SYMBOL_D
SYMBOL_E
SYMBOL_F
SYMBOL_G
SYMBOL_H
```

Chaque symbole peut avoir :

- zÃ©ro setup actif ;
- un setup actif ;
- plusieurs setups en attente ;
- une position ouverte ;
- plusieurs ordres historiques.

RÃ¨gle recommandÃ©e V1 :

```text
Une seule position ouverte par symbole.
```

RÃ¨gle V2 possible :

```text
Plusieurs setups par symbole autorisÃ©s, mais un seul peut Ãªtre en position.
```

---

# 24. Gestion des conflits

## 24.1 Conflit entre setups

Exemple :

```text
<SYMBOL> setup breakout actif
<SYMBOL> setup pullback actif
```

Si les deux dÃ©clenchent une entrÃ©e, risque dâ€™acheter deux fois.

Solution V1 :

```text
Interdire plusieurs setups actifs avec entrÃ©e automatique sur le mÃªme symbole.
```

Solution V2 :

```text
Autoriser plusieurs setups mais utiliser un Symbol Lock.
```

## 24.2 Symbol Lock

```text
symbol_lock["<SYMBOL>"] = True
```

Quand une entrÃ©e est placÃ©e sur `<SYMBOL>` :

```text
aucun autre setup associÃ© Ã  <SYMBOL> ne peut placer un ordre dâ€™entrÃ©e
```

---

# 25. Modes de fonctionnement

## 25.1 Mode simulation interne

Aucune connexion TWS.

UtilitÃ© :

- tester la logique ;
- rejouer des donnÃ©es historiques ;
- valider les transitions dâ€™Ã©tat.

## 25.2 Mode paper

Connexion au compte paper trading de TWS.

UtilitÃ© :

- tester les ordres rÃ©els ;
- valider les comportements IBKR ;
- tester les erreurs ;
- valider la GUI.

## 25.3 Mode live

Trading rÃ©el.

Conditions obligatoires avant activation :

```text
tests unitaires OK
tests paper OK
logs OK
reconciliation OK
contrÃ´les de risque OK
emergency stop OK
```

---

# 26. Bouton Emergency Stop

La GUI doit contenir un bouton clair :

```text
EMERGENCY STOP
```

Effets possibles :

```text
1. arrÃªter toute nouvelle entrÃ©e ;
2. annuler tous les ordres dâ€™entrÃ©e non exÃ©cutÃ©s ;
3. conserver les stops existants ;
4. ne pas fermer automatiquement les positions, sauf option activÃ©e.
```

Option configurable :

```yaml
emergency_stop:
  cancel_entry_orders: true
  keep_existing_stops: true
  close_positions_market: false
```

---

# 27. Alertes

Le systÃ¨me dâ€™alertes doit envoyer des notifications pour :

```text
TWS connectÃ©
TWS dÃ©connectÃ©
setup activÃ©
setup invalidÃ©
signal dÃ©tectÃ©
ordre envoyÃ©
ordre exÃ©cutÃ©
stop placÃ©
stop modifiÃ©
position fermÃ©e
erreur critique
perte journaliÃ¨re atteinte
modification manuelle dÃ©tectÃ©e
```

Canaux possibles :

```text
GUI
logs
Telegram
email
fichier texte
```

V1 :

```text
GUI + logs
```

V2 :

```text
Telegram + email
```

---

# 28. Tests obligatoires

## 28.1 Tests unitaires

Tester :

```text
calcul de quantitÃ© avec worst_case_entry_price
validation setup selon setup_role
interdiction de BUY avec MANAGEMENT_ONLY
adoption dâ€™une position IBKR existante
transitions dâ€™Ã©tat
dÃ©tection breakout
dÃ©tection retest
dÃ©tection rebond
refus du risque
interdiction de baisser le stop
mapping ordre interne â†’ IBKR
```

## 28.2 Tests dâ€™intÃ©gration

Tester :

```text
connexion TWS
rÃ©cupÃ©ration position
rÃ©cupÃ©ration ordre
placement ordre paper
annulation ordre paper
modification stop paper
reconnexion TWS
rÃ©conciliation
```

## 28.3 Tests de scÃ©narios

ScÃ©narios :

```text
entrÃ©e exÃ©cutÃ©e normalement
entrÃ©e non exÃ©cutÃ©e puis annulÃ©e
entrÃ©e partiellement exÃ©cutÃ©e
stop placÃ© correctement
stop rejetÃ©
TWS dÃ©connectÃ© aprÃ¨s entrÃ©e
TWS reconnectÃ© avec position ouverte
position existante adoptÃ©e correctement
position demandÃ©e introuvable chez IBKR
prix courant dÃ©jÃ  infÃ©rieur au trailing_stop_loss.current_stop
ordre stop absent ou quantitÃ© incohÃ©rente
ordre modifiÃ© manuellement
setup invalidÃ© avant entrÃ©e
perte journaliÃ¨re maximale atteinte
```

---

# 29. Roadmap de dÃ©veloppement recommandÃ©e

## Phase 1 â€” Base technique

Objectif :

CrÃ©er la structure du projet.

Livrables :

```text
structure dossiers
config.yaml
logger
SQLite
FastAPI minimal
page Dashboard minimale
connexion TWS
```

## Phase 2 â€” Setup Engine minimal

Objectif :

GÃ©rer un setup simple.

Livrables :

```text
chargement YAML
validation setup selon setup_role
machine Ã  Ã©tats
adoption dâ€™une position IBKR existante
stockage Ã©vÃ©nements
affichage setup dans GUI
```

## Phase 3 â€” Market Data

Objectif :

Construire les bougies et alimenter les setups.

Livrables :

```text
rÃ©cupÃ©ration prix
construction bougies 1m/5m/15m
dÃ©tection bougie clÃ´turÃ©e
stockage candles
```

## Phase 4 â€” Ordres en paper trading

Objectif :

Placer un ordre dâ€™entrÃ©e et un stop.

Livrables :

```text
BUY STP LMT
SELL STP
suivi orderStatus
suivi execDetails
sauvegarde orderId/permId
```

## Phase 5 â€” Gestion de position

Objectif :

Suivre une position ouverte.

Livrables :

```text
position manager
adoption service pour position existante
stop manager
remontÃ©e stop par paliers
synchronisation quantitÃ© stop
gestion MANUAL_REVIEW_REQUIRED
```

## Phase 6 â€” GUI complÃ¨te

Objectif :

Interface utilisable.

Livrables :

```text
dashboard
page Setups
page Orders
page Positions
page Logs
actions manuelles
WebSocket
```

## Phase 7 â€” RÃ©conciliation

Objectif :

Robustesse aprÃ¨s erreur ou redÃ©marrage.

Livrables :

```text
sync positions IBKR
sync ordres ouverts
dÃ©tection incohÃ©rences
manual review
```

## Phase 8 â€” Multi-setups

Objectif :

Supporter plusieurs types de setups.

Livrables :

```text
aggressive_rebound
breakout_retest
pullback_continuation
momentum_breakout
runner
```

## Phase 9 â€” Tests complets en paper trading

Objectif :

Valider avant rÃ©el.

Livrables :

```text
journal de tests
scÃ©narios dâ€™erreur
rapport stabilitÃ©
```

## Phase 10 â€” Passage contrÃ´lÃ© en live

Objectif :

Trading rÃ©el limitÃ©.

Conditions :

```text
montants faibles
1 ou 2 symboles max
risque trÃ¨s rÃ©duit
surveillance manuelle
logs renforcÃ©s
```

---

# 30. ModÃ¨le gÃ©nÃ©rique de fichier setup complet

Le fichier suivant est un **template**, pas un setup liÃ© Ã  une action particuliÃ¨re. Les champs entre chevrons doivent Ãªtre fournis par lâ€™utilisateur ou par la GUI.

```yaml
setup_id: "<SYMBOL>_<SETUP_TYPE>_<UNIQUE_ID>"
symbol: "<SYMBOL>"
enabled: true
mode: "<simulation|paper|live>"

setup_type: "breakout_retest"
setup_role: "ENTRY_AND_MANAGEMENT"
direction: "long"

timeframes:
  signal: "<SIGNAL_TIMEFRAME>"
  confirmation: "<CONFIRMATION_TIMEFRAME>"

market_rules:
  allow_premarket: false
  allow_after_hours: false
  require_regular_market_hours: true

breakout:
  enabled: true
  daily_close_above: <BREAKOUT_CONFIRMATION_PRICE>
  valid_for_days: <BREAKOUT_VALIDITY_DAYS>

retest:
  enabled: true
  zone_min: <RETEST_ZONE_MIN>
  zone_max: <RETEST_ZONE_MAX>
  no_close_below: <RETEST_INVALIDATION_PRICE>
  max_retest_days: <MAX_RETEST_DAYS>

confirmation:
  bullish_candle_required: true
  close_above_previous_high: true
  min_volume_ratio: <MIN_VOLUME_RATIO>

entry:
  enabled: true
  order_type: "STP_LMT"
  trigger_source: "confirmation_candle_high"
  trigger_offset: <TRIGGER_OFFSET>
  limit_offset: <LIMIT_OFFSET>
  cancel_if_not_filled_after_minutes: <ENTRY_TIMEOUT_MINUTES>

risk:
  max_position_amount_usd: <MAX_POSITION_AMOUNT_USD>
  max_risk_usd: <MAX_RISK_USD>
  risk_model: "TRAILING_STOP_INITIAL_RISK"
  emergency_exit_if_stop_fails: true

trailing_stop_loss:
  enabled: true
  mode: "AUTO_INTELLIGENT"
  never_lower_stop: true
  initial_stop: <INITIAL_TRAILING_STOP>
  current_stop: <INITIAL_TRAILING_STOP>

management:
  take_profit_mode: "none"
  never_lower_stop: true

  stop_management:
    mode: "step_based"

    steps:
      - step_id: "STEP_1"
        when_price_above: <STEP_1_TRIGGER_PRICE>
        confirmation: "<CONFIRMATION_RULE>"
        new_stop: <STEP_1_NEW_STOP>

      - step_id: "STEP_2"
        when_price_above: <STEP_2_TRIGGER_PRICE>
        confirmation: "<CONFIRMATION_RULE>"
        new_stop: <STEP_2_NEW_STOP>

targets:
  - target_id: "TARGET_1"
    zone_min: <TARGET_1_ZONE_MIN>
    zone_max: <TARGET_1_ZONE_MAX>
    action: "notify_and_raise_stop"

  - target_id: "TARGET_2"
    zone_min: <TARGET_2_ZONE_MIN>
    zone_max: <TARGET_2_ZONE_MAX>
    action: "notify_only"
```

---

# 31. Exemple de comportement complet

## Cas normal

```text
1. Le setup `<SYMBOL>` est chargÃ©.
2. Le setup passe Ã  VALIDATED.
3. Le bot attend la condition `<BREAKOUT_CONFIRMATION_RULE>`.
4. La condition valide le breakout.
5. Le setup passe Ã  WAITING_RETEST.
6. Le prix revient dans `<RETEST_ZONE>`.
7. Le prix ne clÃ´ture pas sous `<RETEST_INVALIDATION_PRICE>`.
8. Une bougie 15m haussiÃ¨re confirme le retest.
9. Le setup passe Ã  ENTRY_READY.
10. Le module `Risk Engine` calcule la quantitÃ©.
11. Le module `Order Manager` place BUY STP LMT.
12. Lâ€™ordre est exÃ©cutÃ©.
13. Le module `Order Manager` place SELL STP.
14. Le setup passe Ã  IN_POSITION.
15. Le prix atteint `<STEP_1_TRIGGER_PRICE>`.
16. Le bot remonte le stop Ã  `<STEP_1_NEW_STOP>`.
17. Le prix atteint `<STEP_2_TRIGGER_PRICE>`.
18. Le bot remonte le stop Ã  `<STEP_2_NEW_STOP>`.
19. Le stop est touchÃ©.
20. La position est fermÃ©e.
21. Le setup passe Ã  CLOSED.
22. Tous les Ã©vÃ©nements sont enregistrÃ©s.
```

## Cas invalidation avant entrÃ©e

```text
1. Le setup attend le retest.
2. Le prix clÃ´ture sous `<INVALIDATION_PRICE>`.
3. Le setup est invalidÃ©.
4. Aucun ordre nâ€™est envoyÃ©.
5. Le statut devient INVALIDATED.
```

## Cas TWS dÃ©connectÃ©

```text
1. Le bot dÃ©tecte la perte de connexion.
2. Les nouvelles entrÃ©es sont bloquÃ©es.
3. La GUI affiche TWS DISCONNECTED.
4. Le bot tente une reconnexion.
5. AprÃ¨s reconnexion, le moteur `Reconciliation Engine` compare IBKR et SQLite.
6. Le bot reprend uniquement si lâ€™Ã©tat est cohÃ©rent.
```

---

# 32. RÃ¨gles de qualitÃ© de code

Le projet doit respecter :

```text
type hints Python
classes simples
fonctions courtes
logs clairs
tests unitaires
sÃ©paration responsabilitÃ©s
pas de logique trading dans la GUI
pas dâ€™appel direct TWS depuis les setups
pas de valeur magique dans le code
configuration externe YAML
```

## 32.1 Exemple mauvais design

```python
if price > hardcoded_price_level:
    ib.placeOrder(...)
```

ProblÃ¨me :

- pas de validation ;
- pas de risque ;
- pas de machine Ã  Ã©tats ;
- pas de stop ;
- pas de trace ;
- pas de modularitÃ©.

## 32.2 Exemple bon design

```python
signal = signal_engine.evaluate(setup, market_data)

if signal.is_valid:
    risk_decision = risk_engine.evaluate(setup, signal)

    if risk_decision.approved:
        order_manager.place_entry_order(setup, risk_decision)
```

---

# 33. RÃ¨gles minimales avant trading rÃ©el

Avant dâ€™activer le mode live :

```text
1. au moins 2 semaines de paper trading sans bug critique ;
2. tous les ordres doivent avoir un stop associÃ© ;
3. aucun ordre dupliquÃ© observÃ© ;
4. reconnexion TWS testÃ©e ;
5. redÃ©marrage programme testÃ© ;
6. modification manuelle TWS dÃ©tectÃ©e ;
7. perte journaliÃ¨re maximale testÃ©e ;
8. emergency stop testÃ© ;
9. logs vÃ©rifiÃ©s ;
10. export des trades vÃ©rifiÃ©.
```

---

# 34. Ajustements gÃ©nÃ©riques et templates rÃ©utilisables

## 34.1 Principe impÃ©ratif

Les ajustements ne doivent jamais Ãªtre codÃ©s pour une action spÃ©cifique.

Interdiction :

```python
if symbol == "ABC" and price > 15.80:
    move_stop(15.20)
```

ImplÃ©mentation correcte :

```python
for rule in setup.management.stop_management.rules:
    if rule_engine.evaluate(rule.when, market_context):
        action_executor.execute(rule.action, setup_context)
```

Le code Python reste gÃ©nÃ©rique. Les niveaux de prix, le symbole, le timeframe, les confirmations et les actions sont fournis dans le fichier de configuration du setup.

## 34.2 ModÃ¨le gÃ©nÃ©rique dâ€™une rÃ¨gle dâ€™ajustement

```yaml
rule_id: "<RULE_ID>"
enabled: true
priority: <INTEGER>
execute_once: true

when:
  metric: "last_price"
  operator: ">="
  value: <TRIGGER_PRICE>

confirmation:
  type: "<none|candle_close|consecutive_closes|volume_ratio|higher_low>"
  timeframe: "<TIMEFRAME>"
  required_count: <OPTIONAL_INTEGER>

action:
  type: "<raise_stop|notify|partial_exit|close_position|pause_setup>"
  value: <OPTIONAL_PRICE_OR_PERCENT>

constraints:
  never_lower_stop: true
  require_existing_position: true
  require_fresh_market_data: true
```

## 34.3 Conditions gÃ©nÃ©riques supportÃ©es

Le `Rule Engine` doit prendre en charge des conditions combinables.

### MÃ©triques de prix

```text
last_price
bid
ask
mid_price
candle_open
candle_high
candle_low
candle_close
previous_candle_high
previous_candle_low
swing_high
swing_low
```

### MÃ©triques de volume et momentum

```text
volume
volume_ratio
relative_strength
atr
ema
sma
vwap
```

### OpÃ©rateurs

```text
>
>=
<
<=
==
between
crosses_above
crosses_below
```

### Combinaisons logiques

```text
all
any
not
```

Exemple :

```yaml
when:
  all:
    - metric: "last_price"
      operator: ">="
      value: <TRIGGER_PRICE>

    - metric: "volume_ratio"
      operator: ">="
      value: <MIN_VOLUME_RATIO>
```

## 34.4 Actions gÃ©nÃ©riques supportÃ©es

```text
place_entry_order
cancel_entry_order
raise_stop
notify
partial_exit
close_position
pause_setup
invalidate_setup
request_manual_review
```

Chaque action doit Ãªtre exÃ©cutÃ©e par un composant gÃ©nÃ©rique :

```text
Action Executor
```

Aucune stratÃ©gie ne doit appeler directement lâ€™API TWS.

## 34.5 Template gÃ©nÃ©rique : gestion dâ€™une position dÃ©jÃ  ouverte

Ce template doit Ãªtre utilisable pour nâ€™importe quel symbole.

```json
{
  "setup_id": "<SYMBOL>_POSITION_MANAGEMENT_<UNIQUE_ID>",
  "symbol": "<SYMBOL>",
  "setup_type": "position_management",
  "setup_role": "MANAGEMENT_ONLY",
  "direction": "long",
  "enabled": true,
  "mode": "<simulation|paper|live>",

  "position_source": {
    "mode": "adopt_existing_ibkr_position",
    "require_existing_position": true,
    "reconcile_on_load": true,
    "block_if_position_not_found": true
  },

  "entry": {
    "enabled": false
  },

  "risk": {
    "emergency_exit_if_stop_fails": true,
    "if_market_price_below_stop": "MANUAL_REVIEW_REQUIRED"
  },

  "trailing_stop_loss": {
    "enabled": true,
    "mode": "AUTO_INTELLIGENT",
    "never_lower_stop": true,
    "initial_stop": "<INITIAL_TRAILING_STOP>",
    "current_stop": "<CURRENT_TRAILING_STOP>",
    "broker_order": {
      "order_type": "TRAIL_OR_MANAGED_STOP",
      "required_before_entry_transmission": true
    }
  },

  "management": {
    "never_lower_stop": true,
    "take_profit_mode": "none",

    "stop_management": {
      "mode": "rule_based",
      "rules": [
        {
          "rule_id": "STEP_1",
          "execute_once": true,
          "when": {
            "metric": "last_price",
            "operator": ">=",
            "value": "<STEP_1_TRIGGER_PRICE>"
          },
          "confirmation": {
            "type": "candle_close",
            "timeframe": "<CONFIRMATION_TIMEFRAME>"
          },
          "action": {
            "type": "raise_stop",
            "value": "<STEP_1_NEW_STOP>"
          }
        },
        {
          "rule_id": "STEP_2",
          "execute_once": true,
          "when": {
            "metric": "last_price",
            "operator": ">=",
            "value": "<STEP_2_TRIGGER_PRICE>"
          },
          "confirmation": {
            "type": "candle_close",
            "timeframe": "<CONFIRMATION_TIMEFRAME>"
          },
          "action": {
            "type": "raise_stop",
            "value": "<STEP_2_NEW_STOP>"
          }
        }
      ]
    }
  },

  "safety": {
    "place_or_update_real_ibkr_stop": true,
    "pause_if_stop_is_missing": true,
    "manual_review_if_market_price_below_stop": true
  }
}
```

Comportement attendu :

```text
RECONCILING_EXISTING_POSITION
â†’ rÃ©cupÃ©rer la position rÃ©elle associÃ©e Ã  <SYMBOL>
â†’ vÃ©rifier quantitÃ© et stop
â†’ IN_POSITION si cohÃ©rent
â†’ MANUAL_REVIEW_REQUIRED sinon
```

## 34.6 Template gÃ©nÃ©rique : nouvelle entrÃ©e momentum breakout

Ce template doit Ãªtre utilisable pour nâ€™importe quel symbole.

```json
{
  "setup_id": "<SYMBOL>_MOMENTUM_BREAKOUT_<UNIQUE_ID>",
  "symbol": "<SYMBOL>",
  "setup_type": "momentum_breakout",
  "setup_role": "ENTRY_AND_MANAGEMENT",
  "direction": "long",
  "enabled": true,
  "mode": "<simulation|paper|live>",

  "breakout": {
    "resistance": "<RESISTANCE_PRICE>",
    "confirmation_mode": "<CONFIRMATION_MODE>",
    "volume_ratio_min": "<MIN_VOLUME_RATIO>",
    "volume_average_period": "<VOLUME_AVERAGE_PERIOD>",
    "volume_timeframe": "<VOLUME_TIMEFRAME>",
    "relative_strength_required": "<BOOLEAN>"
  },

  "entry": {
    "enabled": true,
    "order_type": "STP_LMT",
    "trigger_offset": "<TRIGGER_OFFSET>",
    "limit_offset": "<LIMIT_OFFSET>",
    "cancel_if_not_filled_after_minutes": "<ENTRY_TIMEOUT_MINUTES>"
  },

  "risk": {
    "max_position_amount_usd": "<MAX_POSITION_AMOUNT_USD>",
    "max_risk_usd": "<MAX_RISK_USD>",
    "risk_model": "TRAILING_STOP_INITIAL_RISK",
    "emergency_exit_if_stop_fails": true
  },

  "trailing_stop_loss": {
    "enabled": true,
    "mode": "AUTO_INTELLIGENT",
    "never_lower_stop": true,
    "initial_stop": "<INITIAL_TRAILING_STOP>",
    "current_stop": "<INITIAL_TRAILING_STOP>",
    "broker_order": {
      "order_type": "TRAIL_OR_MANAGED_STOP",
      "attach_to_entry_order": true,
      "required_before_entry_transmission": true
    }
  },

  "management": {
    "never_lower_stop": true,
    "take_profit_mode": "none",
    "stop_management": {
      "mode": "rule_based",
      "rules": "<GENERIC_ADJUSTMENT_RULES>"
    }
  }
}
```

Calcul gÃ©nÃ©rique attendu :

```text
trigger_price           = resistance + trigger_offset
entry_limit_price       = trigger_price + limit_offset
worst_case_entry_price  = entry_limit_price
risk_per_share          = worst_case_entry_price - trailing_stop_loss.initial_stop
maximum_quantity        = floor(
                            min(
                              max_position_amount_usd / worst_case_entry_price,
                              max_risk_usd / risk_per_share
                            )
                          )
maximum_risk            = maximum_quantity Ã— risk_per_share
```

## 34.7 Affichage GUI gÃ©nÃ©rique attendu

Ne pas afficher seulement :

```text
entry_zone: <TRIGGER_PRICE>
```

Afficher :

```text
Symbol                  : <SYMBOL>
Setup role              : ENTRY_AND_MANAGEMENT ou MANAGEMENT_ONLY
Current state           : <STATE>
Entry trigger           : <TRIGGER_PRICE>
Maximum limit price     : <LIMIT_PRICE>
Worst-case entry price  : <WORST_CASE_ENTRY_PRICE>
Protective stop         : <PROTECTIVE_STOP>
Maximum quantity        : <MAXIMUM_QUANTITY>
Maximum risk            : <MAXIMUM_RISK>
Position source         : bot, manuel ou adopted_from_ibkr
Reconciliation status   : OK, PENDING ou MANUAL_REVIEW_REQUIRED
Executed adjustment     : <LAST_EXECUTED_RULE_ID>
Next adjustment         : <NEXT_PENDING_RULE_ID>
```

## 34.8 Stockage de lâ€™Ã©tat dâ€™exÃ©cution des rÃ¨gles

Pour Ã©viter quâ€™une mÃªme rÃ¨gle soit exÃ©cutÃ©e plusieurs fois, enregistrer :

```text
rule_id
setup_id
status
triggered_at
executed_at
execution_result
previous_stop
new_stop
error_message
```

Statuts possibles :

```text
PENDING
TRIGGERED
EXECUTED
SKIPPED
FAILED
CANCELLED
```

## 34.9 Validation des ajustements

Avant lâ€™activation dâ€™un setup, vÃ©rifier :

```text
rule_id unique dans le setup
action supportÃ©e
mÃ©trique supportÃ©e
opÃ©rateur supportÃ©
timeframe supportÃ©
valeur requise prÃ©sente
nouveau stop cohÃ©rent avec la direction
rÃ¨gles triÃ©es par prioritÃ©
aucune rÃ¨gle ne baisse le stop si never_lower_stop = true
```

---

# 35. Recommandation finale

Le programme doit Ãªtre dÃ©veloppÃ© comme une plateforme de trading automatisÃ© pilotÃ©e par setups, et non comme un script unique.

RÃ¨gle dâ€™architecture impÃ©rative :

```text
Aucun symbole, niveau de prix ou ajustement propre Ã  une action ne doit Ãªtre codÃ© dans la logique Python.
Toutes les variations entre setups doivent provenir de la configuration JSON/YAML et Ãªtre interprÃ©tÃ©es par le `Rule Engine`.
```

La bonne logique est :

```text
Configuration YAML
      â†“
Setup Engine
      â†“
Signal Engine
      â†“
Risk Engine
      â†“
Order Manager
      â†“
TWS
      â†“
Reconciliation Engine
      â†“
GUI + Logs + Storage
```

PrioritÃ© de dÃ©veloppement :

```text
1. sÃ©curitÃ©
2. traÃ§abilitÃ©
3. stabilitÃ©
4. modularitÃ©
5. performance
6. extension multi-setups
7. automatisation avancÃ©e
8. IA ou scanner automatique
```

Le programme doit toujours Ãªtre capable de rÃ©pondre Ã  ces questions :

```text
Quel setup est actif ?
Quel est son rÃ´le : entrÃ©e, entrÃ©e + gestion ou gestion seule ?
Pourquoi le bot attend ?
Pourquoi le bot a refusÃ© une entrÃ©e ?
Pourquoi un setup est en MANUAL_REVIEW_REQUIRED ?
Quel ordre est liÃ© Ã  quel setup ?
Quel stop protÃ¨ge quelle position ?
La position a-t-elle Ã©tÃ© crÃ©Ã©e par le bot ou adoptÃ©e depuis IBKR ?
Quel prix a Ã©tÃ© utilisÃ© pour calculer le risque ?
Quelle est la quantitÃ© maximale autorisÃ©e ?
Quel est le risque actuel ?
Quelle action a Ã©tÃ© faite automatiquement ?
Quelle action a Ã©tÃ© faite manuellement ?
Lâ€™Ã©tat local correspond-il Ã  IBKR ?
```

Si le programme peut rÃ©pondre clairement Ã  ces questions, il sera robuste, contrÃ´lable et Ã©volutif.


---

# 36. Convertisseur gÃ©nÃ©rique de texte libre vers setup structurÃ©

## 36.1 ProblÃ¨me Ã  rÃ©soudre

La GUI accepte un setup saisi en langage naturel. Le convertisseur doit reconnaÃ®tre les formulations usuelles en franÃ§ais et en anglais. Il ne doit pas exiger une phrase exacte.

Exemple obligatoire Ã  reconnaÃ®tre :

```text
SL : $19.70
```

RÃ©sultat attendu :

```json
{
  "trailing_stop_loss": {
    "enabled": true,
    "mode": "AUTO_INTELLIGENT",
    "never_lower_stop": true,
    "initial_stop": 19.70
  }
}
```

Le message `Add a stop loss in the setup text` ne doit apparaÃ®tre que si aucun stop-loss nâ€™a rÃ©ellement Ã©tÃ© dÃ©tectÃ©.

## 36.2 Pipeline obligatoire

```text
Texte brut
  â†“
Normalisation
  â†“
Extraction structurÃ©e
  â†“
Validation mÃ©tier
  â†“
RÃ©solution des ambiguÃ¯tÃ©s
  â†“
PrÃ©visualisation GUI Ã©ditable
  â†“
Sauvegarde explicite
```

## 36.3 Modules Ã  ajouter

```text
app/
  conversion/
    text_setup_converter.py
    text_normalizer.py
    synonym_registry.py
    extraction_engine.py
    ambiguity_resolver.py
    conversion_validator.py
    conversion_models.py
```

## 36.4 Registre de synonymes extensible

Le registre doit Ãªtre chargÃ© depuis YAML ou JSON afin dâ€™Ã©viter les rÃ¨gles codÃ©es en dur.

```yaml
stop_loss:
  - "sl"
  - "stop"
  - "stop loss"
  - "stop-loss"
  - "protective stop"
  - "stop protecteur"
  - "invalidation"
  - "niveau d'invalidation"

entry:
  - "entrÃ©e"
  - "entry"
  - "acheter"
  - "buy"
  - "rentrer"
  - "entrer"

confirmation:
  - "confirmation"
  - "clÃ´ture"
  - "close"
  - "bougie clÃ´turÃ©e"
  - "candle close"
```

## 36.5 Formats monÃ©taires acceptÃ©s

```text
$19.70
19.70 $
19,70 $
USD 19.70
19.70 USD
```

Format interne :

```text
float
```

Exemple :

```text
"19,70 $" â†’ 19.70
```

## 36.6 Motifs minimaux pour le stop-loss

```python
STOP_PATTERNS = [
    r"\bsl\b\s*[:=]?\s*\$?\s*(?P<price>\d+(?:[.,]\d+)?)",
    r"\bstop(?:[-\s]?loss)?\b\s*(?:[:=]|sous|Ã |a)?\s*\$?\s*(?P<price>\d+(?:[.,]\d+)?)",
    r"\binvalidation\b\s*(?:[:=]|sous|Ã |a)?\s*\$?\s*(?P<price>\d+(?:[.,]\d+)?)",
]
```

Ces motifs constituent le premier niveau. Un extracteur sÃ©mantique peut complÃ©ter lâ€™analyse pour les formulations plus complexes.

## 36.7 RÃ©sultat de conversion

```json
{
  "conversion_status": "NEEDS_REVIEW",
  "setup_draft": {},
  "extracted_fields": [],
  "blocking_errors": [],
  "ambiguities": [],
  "warnings": [],
  "unparsed_fragments": []
}
```

Statuts :

```text
READY_TO_SAVE
NEEDS_REVIEW
INVALID
```

## 36.8 DiffÃ©rence entre erreur, ambiguÃ¯tÃ© et warning

### Erreur bloquante

```text
symbole absent
aucun stop-loss dÃ©tectÃ©
stop-loss incohÃ©rent avec lâ€™entrÃ©e
ordre dâ€™entrÃ©e impossible Ã  dÃ©terminer
```

### AmbiguÃ¯tÃ© Ã  confirmer

```text
EntrÃ©e : 21.55â€“21.70
SL : 19.70â€“19.90
volume correct
rebond clair
ne retombe pas immÃ©diatement
marchÃ© gÃ©nÃ©ral faible
```

### Warning non bloquant

```text
aucun objectif dÃ©fini
aucune rÃ¨gle de remontÃ©e de stop
relative strength non renseignÃ©e
```

Le convertisseur ne doit jamais inventer un seuil numÃ©rique absent du texte.

## 36.9 PrÃ©visualisation GUI obligatoire

AprÃ¨s clic sur `Convertir`, afficher une fiche Ã©ditable :

```text
Symbole
Type de setup
RÃ´le du setup
Condition dâ€™entrÃ©e
Timeframe de confirmation
Trigger
Prix limite maximal
Stop-loss
Budget maximal
Risque maximal
Conditions de no-go
AmbiguÃ¯tÃ©s
Warnings
```

Boutons :

```text
Modifier
Valider le brouillon
Sauvegarder
Annuler
```

Le bouton `Sauvegarder` doit rester dÃ©sactivÃ© uniquement en prÃ©sence dâ€™une erreur bloquante.

## 36.10 ReprÃ©sentation gÃ©nÃ©rique dâ€™une entrÃ©e confirmÃ©e

```yaml
entry:
  type: "confirmed_breakout"

  timing:
    wait_after_market_open:
      timeframe: "15m"
      bars: 2

  confirmation:
    type: "candle_close_above"
    timeframe: "15m"
    price: <CONFIRMATION_PRICE>

  order:
    type: "STP_LMT"
    trigger_price: <ENTRY_TRIGGER_PRICE>
    limit_price: <ENTRY_LIMIT_PRICE>
```

Cette structure permet de distinguer :

```text
toucher un prix
```

et :

```text
attendre la clÃ´ture dâ€™une bougie au-dessus du seuil
```

## 36.11 Conditions de no-go gÃ©nÃ©riques

```yaml
no_go_rules:
  - rule_id: "OPEN_BELOW_SUPPORT"
    when:
      metric: "market_open_price"
      operator: "<"
      value: <SUPPORT_PRICE>
    action:
      type: "pause_setup"

  - rule_id: "CLOSE_BELOW_INTRADAY_FLOOR"
    when:
      metric: "candle_close"
      timeframe: "15m"
      operator: "<"
      value: <INTRADAY_FLOOR_PRICE>
    action:
      type: "invalidate_setup"
```

## 36.12 Tests unitaires obligatoires

```python
@pytest.mark.parametrize(
    ("raw_text", "expected_stop"),
    [
        ("SL : $19.70", 19.70),
        ("SL=19,70 $", 19.70),
        ("Stop-loss : 19.70", 19.70),
        ("Stop loss sous 19.70", 19.70),
        ("Invalidation sous $19.70", 19.70),
    ],
)
def test_extract_stop_loss_variants(raw_text, expected_stop):
    ...
```

Ajouter aussi :

```text
test_missing_stop_loss_is_blocking_error
test_stop_loss_range_requires_review
test_extract_wait_two_15m_candles
test_extract_close_above_confirmation
test_qualitative_volume_requires_review
```

## 36.13 RÃ¨gle dâ€™architecture

```text
Extraire automatiquement ce qui est certain.
Demander confirmation pour ce qui est ambigu.
Refuser uniquement ce qui est rÃ©ellement invalide.
Ne jamais inventer une rÃ¨gle quantitative absente du texte.
Ne jamais lier le convertisseur Ã  un symbole particulier.
```


---

# 37. Conversion des analyses longues contenant plusieurs scÃ©narios

## 37.1 ProblÃ¨me Ã  rÃ©soudre

Une analyse peut contenir plusieurs informations de nature diffÃ©rente :

- un ancien plan explicitement invalidÃ© ;
- du contexte de marchÃ© ou des fondamentaux ;
- un tableau de niveaux techniques ;
- un scÃ©nario recommandÃ© ;
- une variante plus prudente ;
- des rÃ¨gles de no-go ;
- des rÃ¨gles de gestion post-entrÃ©e ;
- une dÃ©cision finale rÃ©sumant le scÃ©nario privilÃ©giÃ©.

Le convertisseur ne doit jamais extraire tous les prix dans un objet unique. Il doit comprendre le rÃ´le de chaque bloc.

Exemple :

```text
Ancien plan : entrÃ©e Ã  21.55 ; stop-loss Ã  19.70.
Ce plan nâ€™est plus valide.
```

RÃ©sultat interdit :

```json
{
  "entry_trigger": 21.55,
  "trailing_stop_loss": {
    "initial_stop": 19.70
  },
  "status": "ACTIVE"
}
```

RÃ©sultat correct :

```json
{
  "historical_plan": {
    "entry_trigger": 21.55,
    "trailing_stop_loss": {
      "initial_stop": 19.70
    },
    "status": "INVALIDATED"
  }
}
```

## 37.2 Architecture Ã  ajouter

```text
app/
  conversion/
    document_segmenter.py
    discourse_classifier.py
    precedence_resolver.py
    scenario_builder.py
    qualitative_rule_resolver.py
    provenance_tracker.py
```

Pipeline :

```text
Texte libre
  â†“
Normalisation
  â†“
Segmentation par titres, listes, tableaux et paragraphes
  â†“
Classification du rÃ´le de chaque bloc
  â†“
Extraction avec provenance
  â†“
RÃ©solution des prioritÃ©s et invalidations
  â†“
Construction dâ€™un bundle de scÃ©narios
  â†“
Validation mÃ©tier dÃ©terministe
  â†“
PrÃ©visualisation GUI
  â†“
SÃ©lection explicite du scÃ©nario Ã  activer
```

## 37.3 Types de blocs

```text
HISTORICAL_PLAN
INVALIDATION_NOTICE
MARKET_CONTEXT
REFERENCE_LEVELS
PRIMARY_SETUP
ALTERNATIVE_SETUP
NO_GO_RULES
POST_ENTRY_MANAGEMENT
FINAL_DECISION
QUALITATIVE_COMMENT
```

Exemples de classification :

```text
"Ton ancien plan Ã©tait"
â†’ HISTORICAL_PLAN

"Ce plan nâ€™est plus valide"
â†’ INVALIDATION_NOTICE

"Mon plan recommandÃ©"
â†’ PRIMARY_SETUP

"Variante plus prudente"
â†’ ALTERNATIVE_SETUP

"Cas No-Go"
â†’ NO_GO_RULES

"Gestion aprÃ¨s lâ€™entrÃ©e"
â†’ POST_ENTRY_MANAGEMENT

"DÃ©cision finale"
â†’ FINAL_DECISION
```

## 37.4 RÃ©solution des prioritÃ©s

PrioritÃ© sÃ©mantique :

```text
FINAL_DECISION
  > PRIMARY_SETUP
  > ALTERNATIVE_SETUP
  > POST_ENTRY_MANAGEMENT
  > NO_GO_RULES
  > REFERENCE_LEVELS
  > MARKET_CONTEXT
  > HISTORICAL_PLAN
```

RÃ¨gles obligatoires :

```text
Un HISTORICAL_PLAN nâ€™active jamais un ordre.
Un bloc INVALIDATED nâ€™est jamais transformÃ© en setup actif.
Un niveau REFERENCE_LEVELS nâ€™est pas automatiquement une entrÃ©e ou un stop.
Une variante reste STANDBY tant que lâ€™utilisateur ne la sÃ©lectionne pas.
La dÃ©cision finale peut confirmer ou remplacer les paramÃ¨tres extraits avant elle.
```

## 37.5 Bundle de scÃ©narios

Le convertisseur doit produire :

```json
{
  "conversion_status": "NEEDS_REVIEW",
  "analysis_bundle": {
    "symbol": "<SYMBOL>",
    "historical_plans": [],
    "reference_levels": [],
    "scenarios": [],
    "global_no_go_rules": [],
    "ambiguities": [],
    "warnings": []
  }
}
```

Chaque scÃ©nario :

```json
{
  "scenario_id": "<UNIQUE_ID>",
  "name": "<SCENARIO_NAME>",
  "scenario_role": "PRIMARY",
  "priority": 1,
  "activation_policy": "USER_SELECTION_REQUIRED",
  "status": "DRAFT",
  "entry_rules": [],
  "risk": {},
  "no_go_rules": [],
  "management_rules": [],
  "provenance": []
}
```

Statuts :

```text
DRAFT
NEEDS_REVIEW
READY_TO_ACTIVATE
STANDBY
ACTIVE
INVALIDATED
ARCHIVED
```

## 37.6 Extraction avec provenance

Chaque valeur extraite conserve son origine :

```json
{
  "field": "trailing_stop_loss.initial_stop",
  "value": 18.50,
  "source_text": "Stop-loss : $18.50",
  "source_block_type": "PRIMARY_SETUP",
  "confidence": 0.99
}
```

Le systÃ¨me peut ainsi afficher pourquoi une valeur a Ã©tÃ© retenue et Ã©viter de confondre un niveau informatif avec un ordre.

## 37.7 RÃ©solution des conflits

Exemple :

```text
ancien stop-loss : 19.70
nouveau stop-loss recommandÃ© : 18.50
variante prudente : stop-loss 19.40
```

RÃ©sultat :

```text
historical_plan.stop_loss = 19.70
primary_scenario.stop_loss = 18.50
alternative_scenario.stop_loss = 19.40
```

Les valeurs ne doivent jamais Ãªtre fusionnÃ©es.

## 37.8 RÃ¨gles temporelles

SchÃ©ma gÃ©nÃ©rique :

```yaml
timing_rules:
  - rule_id: "WAIT_AFTER_OPEN"
    type: "wait_after_market_open"
    duration_minutes: 30

  - rule_id: "WAIT_FIRST_BARS"
    type: "wait_closed_bars_after_market_open"
    timeframe: "15m"
    bars: 2
```

Le moteur doit comprendre :

```text
attendre 30 minutes aprÃ¨s lâ€™ouverture
laisser passer les deux premiÃ¨res bougies de 15 minutes
clÃ´ture 15 minutes au-dessus dâ€™un niveau
maintien au-dessus dâ€™une zone
reprise rapide aprÃ¨s ouverture
```

## 37.9 Reclaim

Un reclaim est diffÃ©rent dâ€™une simple cassure.

```yaml
entry_model:
  type: "reclaim"

  reclaim:
    close_above:
      timeframe: "15m"
      price: <RECLAIM_PRICE>

    hold_zone:
      min: <HOLD_ZONE_MIN>
      max: <HOLD_ZONE_MAX>

    optional_quality_filters:
      higher_low:
        required: false
        timeframe: "15m"

      buyer_volume:
        required: false
        min_volume_ratio: null
```

Les filtres qualitatifs non chiffrÃ©s deviennent des ambiguÃ¯tÃ©s ou des filtres facultatifs Ã  confirmer.

## 37.10 MÃ¨ches tolÃ©rÃ©es et clÃ´tures interdites

```yaml
support_hold:
  level: <SUPPORT_LEVEL>
  timeframe: "15m"
  wick_below_allowed: true
  close_below_allowed: false

hard_invalidation:
  type: "candle_close_below"
  timeframe: "15m"
  price: <HARD_INVALIDATION_PRICE>
```

Le moteur ne doit pas confondre une mÃ¨che avec une clÃ´ture.

## 37.11 Formulations qualitatives

Ã€ transformer en paramÃ¨tres ou ambiguÃ¯tÃ©s :

```text
reprise rapide
volume acheteur correct
higher low
retest propre
cassure propre
rejet immÃ©diat
grosse mÃ¨che vendeuse
premiÃ¨res heures
```

Exemple :

```json
{
  "ambiguity_id": "AMB_FAST_RECOVERY",
  "raw_text": "sans reprise rapide",
  "required_fields": [
    "recovery_window_minutes",
    "minimum_recovery_price"
  ],
  "requires_user_confirmation": true
}
```

## 37.12 Profils rÃ©utilisables

```yaml
qualitative_profiles:
  intraday_default:
    rapid_recovery_minutes: 30
    immediate_rejection_bars: 1
    first_hours_minutes: 120
    large_upper_wick_ratio: 0.50
    clean_breakout_required_closes: 1
    clean_retest_required_closes: 1
    buyer_volume_ratio_min: 1.20
    higher_low_timeframe: "15m"
```

Un profil ne doit jamais Ãªtre appliquÃ© silencieusement. La GUI doit indiquer le profil choisi.

## 37.13 No-go rules

```yaml
no_go_rules:
  - rule_id: "OPEN_BELOW_SUPPORT_NO_RECOVERY"
    type: "open_below_without_recovery"
    support_price: <SUPPORT_PRICE>
    recovery_window_minutes: <MINUTES>
    required_recovery_price: <RECOVERY_PRICE>
    action: "PAUSE_SETUP"

  - rule_id: "CLOSE_BELOW_HARD_FLOOR"
    type: "candle_close_below"
    timeframe: "15m"
    price: <HARD_FLOOR_PRICE>
    action: "INVALIDATE_SETUP"

  - rule_id: "RECLAIM_REJECTED"
    type: "reclaim_rejected"
    timeframe: "15m"
    reclaim_price: <RECLAIM_PRICE>
    max_bars_after_touch: <BARS>
    action: "PAUSE_SETUP"
```

## 37.14 Gestion post-entrÃ©e

Actions supportÃ©es :

```text
KEEP_STOP
RAISE_STOP_FIXED
RAISE_STOP_TO_LAST_HIGHER_LOW
ENABLE_TRAILING_HIGHER_LOWS
NOTIFY_ONLY
```

Exemple :

```yaml
management_rules:
  - rule_id: "KEEP_INITIAL_STOP"
    when:
      type: "candle_close_above"
      timeframe: "15m"
      price: <PRICE_1>
    action:
      type: "KEEP_STOP"

  - rule_id: "RAISE_STOP_AFTER_RECLAIM"
    when:
      all:
        - type: "candle_close_above"
          timeframe: "15m"
          price: <PRICE_2>
        - type: "hold_above"
          timeframe: "15m"
          price: <PRICE_2>
          bars: <BARS>
    action:
      type: "RAISE_STOP_FIXED"
      price: <NEW_STOP>

  - rule_id: "TRAIL_HIGHER_LOWS"
    when:
      type: "confirmed_breakout"
      timeframe: "15m"
      price: <PRICE_3>
    action:
      type: "ENABLE_TRAILING_HIGHER_LOWS"
      timeframe: "15m"
      buffer: <BUFFER>
```

## 37.15 GUI

Afficher des cartes distinctes :

```text
Ancien plan invalidÃ©
Niveaux informatifs
ScÃ©nario principal
Variante prudente
No-go rules
Gestion post-entrÃ©e
AmbiguÃ¯tÃ©s Ã  rÃ©soudre
```

Actions disponibles :

```text
Activer
Modifier
Mettre en standby
Archiver
```

RÃ¨gle :

```text
Un seul scÃ©nario dâ€™entrÃ©e peut Ãªtre ACTIVE par symbole.
Les autres restent STANDBY.
```

## 37.16 Algorithme

```python
def convert_analysis_text(raw_text: str) -> AnalysisBundle:
    normalized = normalize_text(raw_text)
    blocks = segment_document(normalized)

    classified_blocks = [
        classify_discourse_role(block)
        for block in blocks
    ]

    extracted = [
        extract_fields_with_provenance(block)
        for block in classified_blocks
    ]

    resolved = resolve_precedence_and_invalidations(extracted)
    bundle = build_scenario_bundle(resolved)
    validation = validate_bundle(bundle)

    return apply_validation_result(bundle, validation)
```

## 37.17 Extracteur LLM facultatif

Pour les analyses longues, un extracteur LLM peut complÃ©ter les expressions rÃ©guliÃ¨res.

```text
Regex + parseur de structure
  â†“
Extracteur LLM sous JSON Schema strict
  â†“
Validateur dÃ©terministe
  â†“
PrÃ©visualisation utilisateur
  â†“
Activation explicite
```

RÃ¨gles :

```text
Le LLM produit uniquement un brouillon.
Le LLM ne place jamais un ordre.
Le validateur dÃ©terministe vÃ©rifie chaque valeur.
Les ambiguÃ¯tÃ©s restent visibles.
Lâ€™utilisateur sÃ©lectionne explicitement le scÃ©nario actif.
```

## 37.18 Tests obligatoires

```text
ancien plan invalidÃ© non activÃ©
ancien stop non fusionnÃ© avec nouveau stop
variante prudente conservÃ©e en STANDBY
tableau technique classÃ© REFERENCE_LEVELS
attente de 30 minutes extraite
deux bougies de 15 minutes extraites
mÃ¨che tolÃ©rÃ©e mais clÃ´ture interdite
no-go rules sÃ©parÃ©es des entry rules
gestion post-entrÃ©e sÃ©parÃ©e de lâ€™entrÃ©e
higher low qualitatif marquÃ© NEEDS_REVIEW sans profil choisi
volume correct marquÃ© NEEDS_REVIEW sans seuil choisi
dÃ©cision finale prioritaire
```

## 37.19 RÃ¨gle de sÃ©curitÃ©

```text
Un texte dâ€™analyse nâ€™est jamais directement exÃ©cutable.
Il devient dâ€™abord un bundle de scÃ©narios.
Chaque scÃ©nario est prÃ©visualisÃ©, validÃ© et explicitement activÃ©.
Un ancien plan invalidÃ© ne doit jamais redevenir actif.
```


---

# 38. SÃ©lection, activation et modification complÃ¨te des scÃ©narios depuis la GUI

## 38.1 Objectif

AprÃ¨s conversion dâ€™une analyse, le programme doit permettre Ã  lâ€™utilisateur de :

- visualiser tous les scÃ©narios dÃ©tectÃ©s ;
- sÃ©lectionner un ou plusieurs scÃ©narios ;
- modifier tous les paramÃ¨tres exposÃ©s ;
- ajouter ou supprimer des rÃ¨gles ;
- activer immÃ©diatement un scÃ©nario ;
- armer un scÃ©nario pour activation automatique future ;
- mettre un scÃ©nario en standby ;
- dÃ©sactiver temporairement un scÃ©nario ;
- archiver un scÃ©nario ;
- dupliquer un scÃ©nario afin de tester une variante ;
- comparer plusieurs scÃ©narios avant activation ;
- enregistrer les modifications sans perdre la provenance du texte initial.

La GUI ne doit pas imposer un scÃ©nario unique. Elle doit laisser lâ€™utilisateur choisir le ou les scÃ©narios adaptÃ©s Ã  sa stratÃ©gie.

## 38.2 DiffÃ©rence entre sÃ©lection et activation

Le programme doit distinguer :

```text
SELECTED
ARMED
ACTIVE
STANDBY
PAUSED
BLOCKED_BY_CONFLICT
INVALIDATED
ARCHIVED
```

DÃ©finition :

| Statut | Signification |
|---|---|
| `SELECTED` | Le scÃ©nario est choisi dans la GUI pour revue ou Ã©dition |
| `ARMED` | Le scÃ©nario est prÃªt Ã  surveiller le marchÃ© et peut devenir actif si ses conditions sont remplies |
| `ACTIVE` | Le scÃ©nario contrÃ´le actuellement un ordre, une entrÃ©e ou une position |
| `STANDBY` | Le scÃ©nario est conservÃ© mais ne peut pas envoyer dâ€™ordre |
| `PAUSED` | Le scÃ©nario est temporairement dÃ©sactivÃ© par lâ€™utilisateur ou par une rÃ¨gle |
| `BLOCKED_BY_CONFLICT` | Le scÃ©nario est valide mais bloquÃ© par une rÃ¨gle de concurrence |
| `INVALIDATED` | Le scÃ©nario nâ€™est plus exploitable selon ses rÃ¨gles |
| `ARCHIVED` | Le scÃ©nario est conservÃ© uniquement pour historique |

Un scÃ©nario peut Ãªtre `SELECTED` sans Ãªtre `ARMED`.

Un scÃ©nario peut Ãªtre `ARMED` sans Ãªtre `ACTIVE`.

## 38.3 SÃ©lection multiple

Le programme doit permettre de sÃ©lectionner plusieurs scÃ©narios simultanÃ©ment.

Exemples :

```text
ScÃ©nario principal reclaim
+
ScÃ©nario prudent breakout + retest
```

ou :

```text
ScÃ©nario support rebound
+
ScÃ©nario momentum breakout
+
ScÃ©nario de gestion de position existante
```

Cependant, la sÃ©lection multiple ne signifie pas que tous les scÃ©narios peuvent envoyer un ordre au mÃªme moment.

## 38.4 Politiques de concurrence

Chaque symbole doit avoir une politique de concurrence configurable.

Valeurs possibles :

```text
SINGLE_ACTIVE_ENTRY_PER_SYMBOL
FIRST_TRIGGER_WINS
PRIORITY_BASED
MANUAL_CONFIRMATION_BEFORE_ENTRY
ALLOW_MULTIPLE_ENTRIES
```

### `SINGLE_ACTIVE_ENTRY_PER_SYMBOL`

Un seul scÃ©nario dâ€™entrÃ©e peut Ãªtre actif pour un symbole.

Les autres scÃ©narios restent :

```text
ARMED
```

ou :

```text
BLOCKED_BY_CONFLICT
```

### `FIRST_TRIGGER_WINS`

Plusieurs scÃ©narios sont armÃ©s.

Le premier scÃ©nario validÃ© obtient le verrou du symbole.

Les autres sont automatiquement :

```text
STANDBY
```

ou :

```text
CANCELLED_BY_COMPETING_SCENARIO
```

selon la configuration.

### `PRIORITY_BASED`

Chaque scÃ©nario reÃ§oit une prioritÃ©.

Exemple :

```text
ScÃ©nario prudent       : prioritÃ© 100
ScÃ©nario spÃ©culatif    : prioritÃ© 50
```

Si deux scÃ©narios deviennent valides simultanÃ©ment, le scÃ©nario avec la prioritÃ© la plus Ã©levÃ©e prend le verrou.

### `MANUAL_CONFIRMATION_BEFORE_ENTRY`

Le programme dÃ©tecte le signal mais demande une validation dans la GUI avant de placer lâ€™ordre.

### `ALLOW_MULTIPLE_ENTRIES`

Ã€ rÃ©server Ã  une version avancÃ©e.

Le programme peut exÃ©cuter plusieurs scÃ©narios sur le mÃªme symbole uniquement si :

- le cumul de risque reste sous la limite globale ;
- les tailles de position sont recalculÃ©es ;
- les ordres restent traÃ§ables sÃ©parÃ©ment ;
- la gestion de stop est compatible ;
- lâ€™utilisateur a explicitement activÃ© cette option.

Valeur par dÃ©faut recommandÃ©e :

```text
SINGLE_ACTIVE_ENTRY_PER_SYMBOL
```

## 38.5 Symbol Lock et Scenario Lock

Le moteur doit gÃ©rer deux verrous.

### Symbol Lock

```text
symbol_lock["<SYMBOL>"]
```

EmpÃªche deux entrÃ©es incompatibles sur un mÃªme symbole.

### Scenario Lock

```text
scenario_lock["<SCENARIO_ID>"]
```

EmpÃªche lâ€™exÃ©cution concurrente de deux actions contradictoires dans un mÃªme scÃ©nario.

Exemple :

```text
raise_stop
```

et :

```text
close_position
```

ne doivent pas Ãªtre envoyÃ©s simultanÃ©ment.

## 38.6 Configuration globale de concurrence

Ajouter dans `config.yaml` :

```yaml
scenario_management:
  default_conflict_policy: "SINGLE_ACTIVE_ENTRY_PER_SYMBOL"
  allow_multiple_selected_scenarios: true
  allow_multiple_armed_scenarios: true
  allow_multiple_active_entries_per_symbol: false
  require_manual_confirmation_before_live_entry: false
  automatically_pause_competing_scenarios_after_fill: true
  reactivate_competing_scenarios_after_close: false
```

## 38.7 ModÃ¨le JSON enrichi dâ€™un scÃ©nario

```json
{
  "scenario_id": "<SCENARIO_ID>",
  "symbol": "<SYMBOL>",
  "name": "<SCENARIO_NAME>",
  "scenario_role": "PRIMARY",
  "setup_type": "<SETUP_TYPE>",

  "selection": {
    "selected": true,
    "armed": true,
    "status": "ARMED",
    "priority": 100,
    "conflict_policy": "FIRST_TRIGGER_WINS"
  },

  "activation": {
    "activation_mode": "AUTOMATIC",
    "require_manual_confirmation": false,
    "valid_from": null,
    "valid_until": null,
    "regular_market_hours_only": true
  },

  "parameters": {
    "entry": {},
    "risk": {},
    "timing_rules": [],
    "no_go_rules": [],
    "management_rules": [],
    "targets": []
  },

  "metadata": {
    "source": "text_converter",
    "created_at": "<TIMESTAMP>",
    "updated_at": "<TIMESTAMP>",
    "version": 1
  }
}
```

## 38.8 Interface GUI : Ã©cran de sÃ©lection des scÃ©narios

CrÃ©er une page :

```text
/scenarios
```

et une page de dÃ©tail :

```text
/scenarios/{scenario_id}
```

### Liste des scÃ©narios

Colonnes :

```text
Checkbox de sÃ©lection
Symbole
Nom du scÃ©nario
Type
RÃ´le
Statut
PrioritÃ©
Politique de conflit
Trigger principal
Stop initial
Budget maximal
Risque maximal
DerniÃ¨re modification
Actions
```

Actions :

```text
SÃ©lectionner
DÃ©sÃ©lectionner
Armer
DÃ©sarmer
Activer
Mettre en standby
Mettre en pause
Modifier
Dupliquer
Archiver
Supprimer
Comparer
```

## 38.9 Interface GUI : cartes aprÃ¨s conversion

AprÃ¨s clic sur `Convertir`, afficher des cartes sÃ©parÃ©es :

```text
ScÃ©nario principal
Variante prudente
Autres scÃ©narios dÃ©tectÃ©s
Ancien plan invalidÃ©
Niveaux informatifs
No-go rules
Gestion aprÃ¨s entrÃ©e
AmbiguÃ¯tÃ©s Ã  rÃ©soudre
Warnings
```

Chaque carte doit contenir :

```text
Checkbox : sÃ©lectionner ce scÃ©nario
Badge : PRIMARY / ALTERNATIVE / HISTORICAL
Badge : READY / NEEDS_REVIEW / INVALIDATED
Bouton : Modifier
Bouton : Armer
Bouton : Mettre en standby
Bouton : Archiver
```

Un ancien plan invalidÃ© doit Ãªtre visible mais non activable.

## 38.10 Modification complÃ¨te des paramÃ¨tres

La GUI doit permettre de modifier tous les paramÃ¨tres configurables.

### IdentitÃ© du scÃ©nario

```text
Nom
Description
Symbole
Direction long / short
Type de setup
RÃ´le du scÃ©nario
PrioritÃ©
Tags
```

### Activation

```text
Mode simulation / paper / live
Activation automatique ou manuelle
Date de dÃ©but
Date dâ€™expiration
Regular trading hours uniquement
Premarket autorisÃ©
After-hours autorisÃ©
Politique de concurrence
```

### EntrÃ©e

```text
Type dâ€™entrÃ©e
Type dâ€™ordre
Trigger
Prix limite maximal
Offsets
Zone dâ€™entrÃ©e min / max
Confirmation requise
Timeframe
Nombre de bougies
Attente aprÃ¨s ouverture
Retest obligatoire
Reclaim obligatoire
Higher low obligatoire ou facultatif
Volume ratio minimal
Spread maximal
Slippage maximal
Expiration de lâ€™ordre
```

### Risque

```text
Stop initial
Type de stop
Risque maximal en USD
Budget maximal
QuantitÃ© maximale
Exposition maximale
Perte journaliÃ¨re maximale
Emergency exit si le stop Ã©choue
TolÃ©rance de slippage
```

### No-go rules

```text
Ajouter une rÃ¨gle
Modifier une rÃ¨gle
DÃ©sactiver une rÃ¨gle
Supprimer une rÃ¨gle
RÃ©ordonner les prioritÃ©s
```

### Gestion post-entrÃ©e

```text
Take-profit activÃ© ou non
Objectifs informatifs
Sorties partielles
RemontÃ©e du stop fixe
RemontÃ©e du stop par paliers
Trailing stop
Trailing higher lows
ATR
Break-even
Notification uniquement
```

### Alertes

```text
Alerte GUI
Email
Telegram
Niveau dâ€™alerte
Notification avant entrÃ©e
Notification aprÃ¨s exÃ©cution
Notification aprÃ¨s changement du stop
Notification en cas de blocage
```

## 38.11 Ã‰diteur de rÃ¨gles gÃ©nÃ©rique

CrÃ©er un composant GUI :

```text
Rule Builder
```

Le Rule Builder doit permettre :

```text
Ajouter une condition
Ajouter un groupe ALL
Ajouter un groupe ANY
Ajouter une nÃ©gation NOT
Choisir une mÃ©trique
Choisir un opÃ©rateur
Saisir une valeur
Choisir un timeframe
Ajouter une action
Ajouter une confirmation
DÃ©finir une prioritÃ©
Activer ou dÃ©sactiver la rÃ¨gle
```

Exemple visuel logique :

```text
WHEN
  ALL
    candle_close(15m) >= 19.70
    hold_above(15m, 19.50, bars=1)

THEN
  PLACE_ENTRY_ORDER

WITH
  order_type = STP_LMT
  trigger = 19.75
  limit = 20.00
```

## 38.12 Champs avancÃ©s du Rule Builder

MÃ©triques :

```text
last_price
bid
ask
mid_price
market_open_price
candle_open
candle_high
candle_low
candle_close
volume
volume_ratio
atr
ema
sma
vwap
higher_low
lower_high
spread
relative_strength
```

OpÃ©rateurs :

```text
>
>=
<
<=
==
between
crosses_above
crosses_below
exists
not_exists
```

Actions :

```text
PLACE_ENTRY_ORDER
CANCEL_ENTRY_ORDER
PAUSE_SETUP
INVALIDATE_SETUP
RAISE_STOP_FIXED
RAISE_STOP_TO_HIGHER_LOW
ENABLE_TRAILING_STOP
ENABLE_TRAILING_HIGHER_LOWS
PARTIAL_EXIT
CLOSE_POSITION
NOTIFY_ONLY
REQUEST_MANUAL_CONFIRMATION
```

## 38.13 Ã‰dition en deux modes

La GUI doit proposer deux modes.

### Mode simple

Pour lâ€™utilisateur qui veut modifier uniquement :

```text
EntrÃ©e
Stop-loss
Budget
Risque
Confirmation
No-go
Paliers de stop
```

### Mode avancÃ©

Pour afficher :

```text
toutes les rÃ¨gles
tous les paramÃ¨tres
JSON brut
YAML brut
prioritÃ©s
profils qualitatifs
verrous
politiques de concurrence
```

Le mode avancÃ© doit afficher une validation en temps rÃ©el.

## 38.14 Ã‰dition JSON / YAML

Ajouter deux onglets :

```text
Formulaire
JSON
YAML
```

Le programme doit :

- synchroniser les trois vues ;
- valider le schÃ©ma avant sauvegarde ;
- signaler prÃ©cisÃ©ment les champs invalides ;
- proposer un diff avant application ;
- conserver la version prÃ©cÃ©dente ;
- permettre un rollback.

## 38.15 Versioning des scÃ©narios

Chaque modification doit crÃ©er une nouvelle version.

Table :

```sql
CREATE TABLE scenario_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    changed_by TEXT,
    change_source TEXT,
    change_reason TEXT,
    created_at TEXT NOT NULL
);
```

Sources possibles :

```text
GUI_FORM
GUI_JSON
GUI_YAML
TEXT_CONVERTER
RULE_ENGINE
MANUAL_OVERRIDE
IMPORT
```

## 38.16 Historique et rollback

La GUI doit permettre :

```text
Voir lâ€™historique
Comparer deux versions
Restaurer une version
Dupliquer une version
Exporter JSON
Exporter YAML
```

Chaque modification doit afficher :

```text
Ancienne valeur
Nouvelle valeur
Utilisateur ou source
Date
Raison
```

## 38.17 Validation avant sauvegarde

La sauvegarde doit Ãªtre refusÃ©e si :

```text
stop initial absent pour un scÃ©nario dâ€™entrÃ©e
stop incohÃ©rent avec la direction
risque maximal absent
budget maximal absent si requis
ordre STP_LMT sans prix limite maximal
rÃ¨gle inconnue
timeframe inconnu
mÃ©trique inconnue
action inconnue
plusieurs scÃ©narios ACTIVE incompatibles
```

La sauvegarde peut Ãªtre autorisÃ©e avec warning si :

```text
aucun objectif dÃ©fini
aucune sortie partielle
aucune rÃ¨gle de remontÃ©e du stop
relative strength dÃ©sactivÃ©e
profil qualitatif incomplet
```

## 38.18 Validation avant activation

La validation avant activation est plus stricte que la sauvegarde.

Un scÃ©nario peut Ãªtre :

```text
SAVED_AS_DRAFT
```

mais non :

```text
ARMED
```

si des ambiguÃ¯tÃ©s restent ouvertes.

Pour armer un scÃ©nario :

```text
aucune erreur bloquante
aucune ambiguÃ¯tÃ© obligatoire non rÃ©solue
risk engine validÃ©
donnÃ©es de marchÃ© disponibles
conflits vÃ©rifiÃ©s
```

## 38.19 API REST recommandÃ©e

Ajouter :

```text
GET    /api/scenarios
GET    /api/scenarios/{scenario_id}
POST   /api/scenarios
PUT    /api/scenarios/{scenario_id}
PATCH  /api/scenarios/{scenario_id}
DELETE /api/scenarios/{scenario_id}

POST   /api/scenarios/{scenario_id}/select
POST   /api/scenarios/{scenario_id}/deselect
POST   /api/scenarios/{scenario_id}/arm
POST   /api/scenarios/{scenario_id}/disarm
POST   /api/scenarios/{scenario_id}/activate
POST   /api/scenarios/{scenario_id}/pause
POST   /api/scenarios/{scenario_id}/standby
POST   /api/scenarios/{scenario_id}/archive
POST   /api/scenarios/{scenario_id}/duplicate

GET    /api/scenarios/{scenario_id}/versions
POST   /api/scenarios/{scenario_id}/rollback/{version}
GET    /api/scenarios/{scenario_id}/diff/{version_a}/{version_b}

POST   /api/scenarios/compare
POST   /api/scenarios/validate
POST   /api/scenarios/resolve-conflicts
```

## 38.20 WebSocket

Ã‰vÃ©nements :

```text
scenario_created
scenario_updated
scenario_selected
scenario_armed
scenario_disarmed
scenario_activated
scenario_paused
scenario_standby
scenario_invalidated
scenario_archived
scenario_blocked_by_conflict
scenario_version_created
scenario_rollback_completed
```

## 38.21 Stockage SQLite

Ajouter :

```sql
CREATE TABLE scenarios (
    scenario_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    scenario_role TEXT NOT NULL,
    setup_type TEXT NOT NULL,
    status TEXT NOT NULL,
    selected INTEGER NOT NULL DEFAULT 0,
    armed INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 0,
    conflict_policy TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Ajouter :

```sql
CREATE TABLE scenario_locks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lock_type TEXT NOT NULL,
    lock_key TEXT NOT NULL,
    owner_scenario_id TEXT NOT NULL,
    status TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    released_at TEXT
);
```

## 38.22 Comparaison de scÃ©narios

La GUI doit permettre de comparer plusieurs scÃ©narios.

Colonnes recommandÃ©es :

```text
Nom
Type
Trigger
Zone dâ€™entrÃ©e
Stop
Risque par action
Budget
QuantitÃ© maximale
Confirmation
No-go rules
Gestion post-entrÃ©e
PrioritÃ©
Statut
```

## 38.23 Flux utilisateur recommandÃ©

```text
1. Coller une analyse.
2. Cliquer sur Convertir.
3. Voir les scÃ©narios dÃ©tectÃ©s.
4. Corriger les ambiguÃ¯tÃ©s.
5. Modifier les paramÃ¨tres souhaitÃ©s.
6. SÃ©lectionner un ou plusieurs scÃ©narios.
7. Choisir la politique de concurrence.
8. Enregistrer comme brouillon.
9. Armer les scÃ©narios retenus.
10. Laisser le moteur surveiller les conditions.
11. Activer automatiquement ou manuellement selon la configuration.
```

## 38.24 SÃ©curitÃ©

RÃ¨gles obligatoires :

```text
La sÃ©lection multiple est autorisÃ©e.
Lâ€™activation concurrente est contrÃ´lÃ©e.
Un seul ordre dâ€™entrÃ©e incompatible par symbole est autorisÃ© par dÃ©faut.
Toute modification aprÃ¨s armement entraÃ®ne une nouvelle validation.
Toute modification dâ€™un scÃ©nario ACTIVE doit Ãªtre journalisÃ©e.
Toute modification du stop doit respecter never_lower_stop.
Toute activation live peut demander une confirmation manuelle configurable.
```

## 38.25 Tests obligatoires

Ajouter :

```text
sÃ©lection multiple autorisÃ©e
armement multiple autorisÃ©
un seul scÃ©nario actif selon SINGLE_ACTIVE_ENTRY_PER_SYMBOL
premier signal gagnant selon FIRST_TRIGGER_WINS
prioritÃ© respectÃ©e selon PRIORITY_BASED
activation concurrente bloquÃ©e si incompatible
modification dâ€™un scÃ©nario crÃ©e une nouvelle version
rollback restaure la configuration prÃ©cÃ©dente
Ã©dition formulaire synchronisÃ©e avec JSON et YAML
validation refuse un stop incohÃ©rent
validation refuse une ambiguÃ¯tÃ© obligatoire avant armement
modification dâ€™un scÃ©nario actif journalisÃ©e
verrou symbole libÃ©rÃ© aprÃ¨s fermeture si configurÃ©
```

## 38.26 RÃ¨gle finale

```text
Lâ€™utilisateur peut sÃ©lectionner plusieurs scÃ©narios.
Le moteur dÃ©cide si ces scÃ©narios peuvent Ãªtre armÃ©s ou actifs simultanÃ©ment
selon une politique de concurrence explicite et modifiable.

Tous les paramÃ¨tres doivent rester modifiables depuis la GUI,
avec validation, versioning, historique et rollback.
```


---

# 39. Couche dâ€™intelligence sÃ©mantique pour analyser les textes de trading complexes

## 39.1 Objectif

Le programme ne doit pas se limiter Ã  rechercher des mots-clÃ©s ou des prix avec des expressions rÃ©guliÃ¨res.

Il doit Ãªtre capable de comprendre un texte de trading rÃ©digÃ© naturellement, mÃªme lorsquâ€™il contient :

- plusieurs scÃ©narios ;
- un ancien plan invalidÃ© ;
- une nouvelle recommandation ;
- une variante prudente ;
- des niveaux techniques informatifs ;
- des rÃ¨gles de no-go ;
- des rÃ¨gles de gestion aprÃ¨s lâ€™entrÃ©e ;
- des phrases qualitatives ;
- des Ã©lÃ©ments fondamentaux non exÃ©cutables ;
- une dÃ©cision finale qui rÃ©sume et remplace certaines informations prÃ©cÃ©dentes.

Le systÃ¨me doit convertir une analyse libre en un **bundle de scÃ©narios structurÃ©s**, contrÃ´lables, Ã©ditables et validables depuis la GUI.

## 39.2 Limite du parseur classique

Un parseur uniquement basÃ© sur des expressions rÃ©guliÃ¨res peut dÃ©tecter :

```text
SL : 18.50
EntrÃ©e : 19.75â€“20.00
ClÃ´ture 15 min au-dessus de 19.70
```

Mais il ne comprend pas correctement :

```text
Lâ€™ancien plan nâ€™est plus valide.
La zone qui Ã©tait support devient rÃ©sistance.
Le scÃ©nario prudent est encore meilleur.
Ne pas acheter automatiquement sur le support suivant.
Conserver le stop, puis le remonter seulement aprÃ¨s maintien.
```

Une couche dâ€™intelligence sÃ©mantique est donc nÃ©cessaire.

## 39.3 Principe dâ€™architecture

Utiliser une architecture hybride :

```text
Parseur dÃ©terministe
+
LLM sÃ©mantique sous JSON Schema strict
+
Validateur mÃ©tier dÃ©terministe
+
GUI de revue et correction
+
Activation explicite par lâ€™utilisateur
```

Le LLM aide Ã  comprendre le texte.

Le LLM ne doit jamais :

- envoyer un ordre Ã  TWS ;
- modifier un stop directement ;
- activer un scÃ©nario ;
- inventer silencieusement une valeur manquante ;
- fusionner des scÃ©narios diffÃ©rents ;
- transformer un niveau informatif en ordre sans preuve textuelle.

## 39.4 Modules Ã  ajouter

```text
app/
  intelligence/
    semantic_analysis_orchestrator.py
    llm_client.py
    llm_prompt_builder.py
    llm_response_parser.py
    semantic_document_analyzer.py
    semantic_block_classifier.py
    scenario_semantic_extractor.py
    contradiction_resolver.py
    scenario_ranker.py
    confidence_engine.py
    provenance_enforcer.py
    rule_compiler.py
    semantic_validation_service.py
    human_review_service.py
    safety_policy.py

  intelligence/
    prompts/
      system_prompt.md
      extraction_prompt.md
      conflict_resolution_prompt.md
      qualitative_rules_prompt.md

  intelligence/
    schemas/
      analysis_bundle.schema.json
      scenario.schema.json
      extracted_field.schema.json
      ambiguity.schema.json
      compiled_rule.schema.json
```

## 39.5 Pipeline complet

```text
Texte libre
  â†“
Normalisation linguistique
  â†“
Segmentation structurelle
  â†“
Classification dÃ©terministe des blocs Ã©vidents
  â†“
Analyse sÃ©mantique LLM
  â†“
Extraction JSON stricte avec provenance
  â†“
RÃ©solution des contradictions
  â†“
Compilation vers rÃ¨gles exÃ©cutables
  â†“
Validation dÃ©terministe
  â†“
Calcul de confiance
  â†“
PrÃ©visualisation GUI
  â†“
Correction utilisateur
  â†“
SÃ©lection des scÃ©narios
  â†“
Armement explicite
```

## 39.6 RÃ´le du LLM

Le LLM doit analyser :

```text
titres
paragraphes
listes
tableaux
phrases qualitatives
relations entre niveaux
chronologie
prioritÃ©s
invalidations
variantes
rÃ©sumÃ© final
```

Le LLM doit identifier :

```text
symbol
document_intent
market_context
historical_plans
invalidated_plans
reference_levels
primary_scenarios
alternative_scenarios
no_go_rules
management_rules
qualitative_conditions
unresolved_ambiguities
final_decision
```

## 39.7 SchÃ©ma de sortie obligatoire du LLM

Le LLM doit rÃ©pondre uniquement avec un JSON conforme au schÃ©ma.

Exemple gÃ©nÃ©rique :

```json
{
  "document_type": "TRADING_ANALYSIS",
  "symbol": "<SYMBOL>",
  "summary": "<SHORT_SUMMARY>",

  "historical_plans": [
    {
      "plan_id": "<PLAN_ID>",
      "status": "INVALIDATED",
      "reason": "<SOURCE_REASON>",
      "entry": {},
      "risk": {},
      "provenance": []
    }
  ],

  "reference_levels": [
    {
      "level_id": "<LEVEL_ID>",
      "price_or_zone": {},
      "role": "<SUPPORT|RESISTANCE|PIVOT|INFORMATIONAL>",
      "is_executable": false,
      "provenance": []
    }
  ],

  "scenarios": [
    {
      "scenario_id": "<SCENARIO_ID>",
      "scenario_role": "<PRIMARY|ALTERNATIVE>",
      "status": "NEEDS_REVIEW",
      "entry_model": {},
      "timing_rules": [],
      "risk": {},
      "no_go_rules": [],
      "management_rules": [],
      "ambiguities": [],
      "provenance": []
    }
  ],

  "final_decision": {
    "preferred_scenario_id": "<SCENARIO_ID_OR_NULL>",
    "immediate_action": "<NO_GO|WAIT|ARM|MANUAL_REVIEW>",
    "provenance": []
  }
}
```

## 39.8 Provenance obligatoire

Chaque champ extrait doit Ãªtre reliÃ© Ã  la phrase source exacte.

Exemple :

```json
{
  "field": "trailing_stop_loss.initial_stop",
  "value": 18.50,
  "source_text": "Stop-loss : $18.50",
  "source_block": "PRIMARY_SETUP",
  "source_line_start": 42,
  "source_line_end": 42,
  "confidence": 0.99
}
```

RÃ¨gle :

```text
Aucune valeur exÃ©cutable sans provenance.
```

Si le systÃ¨me ne peut pas rattacher une valeur Ã  une phrase source :

```text
status = NEEDS_REVIEW
```

## 39.9 Analyse des blocs

Le LLM doit classer chaque bloc :

```text
MARKET_CONTEXT
FUNDAMENTAL_CONTEXT
REFERENCE_LEVELS
HISTORICAL_PLAN
INVALIDATION_NOTICE
PRIMARY_SETUP
ALTERNATIVE_SETUP
NO_GO_RULES
POST_ENTRY_MANAGEMENT
FINAL_DECISION
QUALITATIVE_FILTER
NON_EXECUTABLE_COMMENT
```

Les blocs suivants ne doivent jamais gÃ©nÃ©rer directement un ordre :

```text
MARKET_CONTEXT
FUNDAMENTAL_CONTEXT
REFERENCE_LEVELS
NON_EXECUTABLE_COMMENT
```

## 39.10 DÃ©tection des scÃ©narios

Le systÃ¨me doit comprendre quâ€™un mÃªme texte peut contenir plusieurs scÃ©narios.

Exemple gÃ©nÃ©rique :

```text
ScÃ©nario principal :
entrÃ©e aprÃ¨s reclaim confirmÃ© dâ€™une rÃ©sistance.

Variante prudente :
entrÃ©e aprÃ¨s cassure supÃ©rieure puis retest.
```

RÃ©sultat :

```text
scenario_1 = PRIMARY
scenario_2 = ALTERNATIVE
```

Les deux scÃ©narios doivent Ãªtre visibles dans la GUI.

Lâ€™utilisateur choisit :

```text
sÃ©lectionner
armer
laisser en standby
modifier
archiver
```

## 39.11 RÃ©solution des contradictions

Le systÃ¨me doit dÃ©tecter les conflits.

Exemples :

```text
ancien stop = 19.70
nouveau stop = 18.50
stop variante prudente = 19.40
```

Ces valeurs ne doivent jamais Ãªtre fusionnÃ©es.

RÃ©sultat :

```text
historical_plan.stop = 19.70
primary_scenario.stop = 18.50
alternative_scenario.stop = 19.40
```

Autre exemple :

```text
No-Go immÃ©diat Ã  lâ€™ouverture.
EntrÃ©e uniquement aprÃ¨s reclaim confirmÃ©.
```

InterprÃ©tation correcte :

```text
ne pas placer dâ€™ordre Ã  lâ€™ouverture
attendre les conditions du reclaim
```

## 39.12 RÃ¨gles de prioritÃ©

Le systÃ¨me doit appliquer :

```text
FINAL_DECISION
  > PRIMARY_SETUP
  > ALTERNATIVE_SETUP
  > POST_ENTRY_MANAGEMENT
  > NO_GO_RULES
  > REFERENCE_LEVELS
  > FUNDAMENTAL_CONTEXT
  > MARKET_CONTEXT
  > HISTORICAL_PLAN
```

Un bloc final peut confirmer ou remplacer un paramÃ¨tre prÃ©cÃ©dent.

Toute modification doit conserver :

```text
ancienne valeur
nouvelle valeur
source
raison
niveau de confiance
```

## 39.13 ComprÃ©hension des notions techniques

Le systÃ¨me doit comprendre les concepts suivants :

```text
breakout
retest
reclaim
support hold
resistance flip
higher low
lower high
wick
candle close
volume ratio
momentum
shakeout
fake breakout
trailing stop
break-even
partial exit
no-go
invalidation
standby
```

Exemple :

```text
Une simple mÃ¨che sous le support est tolÃ©rÃ©e,
mais pas une clÃ´ture 15 min nette en dessous.
```

Compilation attendue :

```yaml
support_hold:
  level: <SUPPORT_LEVEL>
  timeframe: "15m"
  wick_below_allowed: true
  close_below_allowed: false
```

## 39.14 Gestion des formulations qualitatives

Expressions courantes :

```text
reprise rapide
volume correct
higher low propre
retest propre
cassure propre
rejet immÃ©diat
grosse mÃ¨che vendeuse
premiÃ¨res heures
forte accÃ©lÃ©ration vendeuse
```

Ces expressions doivent devenir :

```text
AMBIGUITY
```

ou Ãªtre rÃ©solues avec un profil explicite choisi par lâ€™utilisateur.

Exemple :

```json
{
  "ambiguity_id": "AMB_BUYER_VOLUME",
  "source_text": "volume acheteur correct",
  "field": "entry_model.optional_quality_filters.buyer_volume.min_volume_ratio",
  "suggested_profile": "intraday_default",
  "suggested_value": 1.20,
  "requires_user_confirmation": true
}
```

RÃ¨gle :

```text
Ne jamais transformer automatiquement "volume correct" en 1.20 sans validation.
```

## 39.15 Profils qualitatifs configurables

```yaml
qualitative_profiles:
  intraday_default:
    rapid_recovery_minutes: 30
    first_hours_minutes: 120
    immediate_rejection_bars: 1
    large_upper_wick_ratio: 0.50
    clean_breakout_required_closes: 1
    clean_retest_required_closes: 1
    buyer_volume_ratio_min: 1.20
    hold_above_required_bars: 1
    higher_low_timeframe: "15m"

  conservative:
    rapid_recovery_minutes: 45
    first_hours_minutes: 180
    immediate_rejection_bars: 2
    large_upper_wick_ratio: 0.40
    clean_breakout_required_closes: 2
    clean_retest_required_closes: 2
    buyer_volume_ratio_min: 1.50
    hold_above_required_bars: 2
    higher_low_timeframe: "15m"
```

La GUI doit afficher :

```text
profil sÃ©lectionnÃ©
valeurs injectÃ©es
champs modifiÃ©s
confirmation utilisateur
```

## 39.16 Compilation vers rÃ¨gles exÃ©cutables

Le LLM produit un scÃ©nario sÃ©mantique.

Le `rule_compiler.py` transforme ce scÃ©nario en rÃ¨gles dÃ©terministes.

Exemple sÃ©mantique :

```text
attendre deux bougies de 15 minutes
conserver le support
attendre un reclaim
entrer dans une zone
placer un stop
```

Compilation :

```yaml
timing_rules:
  - type: "wait_closed_bars_after_market_open"
    timeframe: "15m"
    bars: 2

support_hold:
  level: <SUPPORT_PRICE>
  timeframe: "15m"
  wick_below_allowed: true
  close_below_allowed: false

entry_model:
  type: "reclaim"
  close_above:
    timeframe: "15m"
    price: <RECLAIM_PRICE>
  hold_zone:
    min: <HOLD_MIN>
    max: <HOLD_MAX>
  entry_zone:
    min: <ENTRY_MIN>
    max: <ENTRY_MAX>

risk:
  risk_model: "TRAILING_STOP_INITIAL_RISK"

trailing_stop_loss:
  enabled: true
  mode: "AUTO_INTELLIGENT"
  never_lower_stop: true
  initial_stop: <STOP_LOSS>
```

## 39.17 Validation dÃ©terministe

Le validateur mÃ©tier doit vÃ©rifier :

```text
symbol prÃ©sent
scÃ©nario clairement identifiÃ©
rÃ´le du scÃ©nario
stop-loss prÃ©sent
stop cohÃ©rent avec la direction
zone dâ€™entrÃ©e cohÃ©rente
trigger cohÃ©rent
ordre STP_LMT complet
montant maximal prÃ©sent
risque maximal prÃ©sent ou Ã  complÃ©ter
rÃ¨gles temporelles valides
no-go sÃ©parÃ©s des rÃ¨gles dâ€™entrÃ©e
ancien plan non activable
variante alternative non activÃ©e automatiquement
aucune valeur exÃ©cutable sans provenance
```

## 39.18 Score de confiance

Chaque scÃ©nario reÃ§oit un score.

Exemple :

```json
{
  "scenario_id": "PRIMARY_RECLAIM",
  "confidence": {
    "overall": 0.87,
    "entry_rules": 0.95,
    "risk": 0.99,
    "timing": 0.93,
    "management": 0.78,
    "no_go_rules": 0.72
  }
}
```

Politique recommandÃ©e :

```text
confidence >= 0.90
â†’ READY_TO_ACTIVATE aprÃ¨s validation

0.70 <= confidence < 0.90
â†’ NEEDS_REVIEW

confidence < 0.70
â†’ MANUAL_REVIEW_REQUIRED
```

Le score ne remplace jamais les validations mÃ©tier.

## 39.19 GUI de revue intelligente

AprÃ¨s conversion, afficher :

```text
RÃ©sumÃ© de lâ€™analyse
Ancien plan invalidÃ©
Niveaux techniques
ScÃ©nario principal
ScÃ©nario prudent
No-go rules
Gestion post-entrÃ©e
AmbiguÃ¯tÃ©s
Warnings
Score de confiance
Provenance
```

Pour chaque champ :

```text
Valeur extraite
Phrase source
Niveau de confiance
Bouton Modifier
Bouton Accepter
Bouton Rejeter
```

Pour chaque scÃ©nario :

```text
SÃ©lectionner
Armer
Modifier
Dupliquer
Mettre en standby
Archiver
```

## 39.20 API recommandÃ©e

```text
POST /api/intelligence/analyze-text
POST /api/intelligence/reanalyze
POST /api/intelligence/compile-scenario
POST /api/intelligence/validate-scenario
POST /api/intelligence/resolve-ambiguity
POST /api/intelligence/apply-profile
GET  /api/intelligence/analysis/{analysis_id}
GET  /api/intelligence/analysis/{analysis_id}/provenance
```

## 39.21 Persistance

Ajouter :

```sql
CREATE TABLE semantic_analyses (
    analysis_id TEXT PRIMARY KEY,
    symbol TEXT,
    raw_text TEXT NOT NULL,
    normalized_text TEXT,
    llm_model TEXT,
    prompt_version TEXT,
    analysis_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Ajouter :

```sql
CREATE TABLE extracted_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id TEXT NOT NULL,
    scenario_id TEXT,
    field_path TEXT NOT NULL,
    extracted_value_json TEXT,
    source_text TEXT NOT NULL,
    source_block_type TEXT NOT NULL,
    confidence REAL,
    user_decision TEXT,
    created_at TEXT NOT NULL
);
```

Ajouter :

```sql
CREATE TABLE ambiguities (
    ambiguity_id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL,
    scenario_id TEXT,
    source_text TEXT NOT NULL,
    field_path TEXT,
    ambiguity_type TEXT NOT NULL,
    suggested_value_json TEXT,
    resolution_json TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
```

## 39.22 Prompt systÃ¨me recommandÃ©

Le fichier :

```text
app/intelligence/prompts/system_prompt.md
```

doit prÃ©ciser :

```text
Tu es un extracteur de scÃ©narios de trading.
Tu ne fournis aucun conseil.
Tu ne places aucun ordre.
Tu extrais uniquement les informations prÃ©sentes dans le texte.
Tu sÃ©pares les anciens plans invalidÃ©s des scÃ©narios actifs.
Tu distingues les niveaux informatifs des rÃ¨gles exÃ©cutables.
Tu retournes uniquement un JSON conforme au schÃ©ma.
Tu marques toute ambiguÃ¯tÃ©.
Tu conserves la provenance de chaque valeur.
Tu nâ€™inventes jamais de seuil absent du texte.
```

## 39.23 Exemple de sortie attendue pour un texte multi-scÃ©narios

Le rÃ©sultat doit ressembler Ã  :

```json
{
  "document_type": "TRADING_ANALYSIS",
  "symbol": "<SYMBOL>",
  "final_decision": {
    "immediate_action": "WAIT",
    "reason": "No-Go immÃ©diat Ã  lâ€™ouverture"
  },
  "scenarios": [
    {
      "scenario_id": "PRIMARY_RECLAIM",
      "scenario_role": "PRIMARY",
      "status": "NEEDS_REVIEW",
      "entry_model": {
        "type": "reclaim",
        "close_above": {
          "timeframe": "15m",
          "price": "<RECLAIM_PRICE>"
        },
        "entry_zone": {
          "min": "<ENTRY_MIN>",
          "max": "<ENTRY_MAX>"
        }
      },
      "risk": {
        "risk_model": "TRAILING_STOP_INITIAL_RISK",
        "max_position_amount_usd": {
          "min": "<MIN_AMOUNT>",
          "max": "<MAX_AMOUNT>"
        }
      },
      "trailing_stop_loss": {
        "enabled": true,
        "mode": "AUTO_INTELLIGENT",
        "never_lower_stop": true,
        "initial_stop": "<STOP_LOSS>"
      }
    },
    {
      "scenario_id": "ALTERNATIVE_CONSERVATIVE",
      "scenario_role": "ALTERNATIVE",
      "status": "STANDBY"
    }
  ],
  "ambiguities": [
    {
      "source_text": "volume acheteur correct",
      "status": "NEEDS_USER_CONFIRMATION"
    }
  ]
}
```

## 39.24 Tests obligatoires

Ajouter des tests unitaires et dâ€™intÃ©gration.

```text
texte contenant un ancien plan invalidÃ©
texte contenant deux scÃ©narios
texte contenant une dÃ©cision finale prioritaire
tableau technique non transformÃ© en ordres
fondamentaux ignorÃ©s par le moteur dâ€™exÃ©cution
reclaim dÃ©tectÃ©
breakout + retest dÃ©tectÃ©
mÃ¨che tolÃ©rÃ©e mais clÃ´ture interdite
no-go immÃ©diat Ã  lâ€™ouverture dÃ©tectÃ©
gestion du stop progressif compilÃ©e
phrase qualitative convertie en ambiguÃ¯tÃ©
provenance prÃ©sente pour chaque champ exÃ©cutable
score de confiance calculÃ©
LLM JSON invalide rejetÃ©
valeur inventÃ©e sans provenance rejetÃ©e
ancien plan jamais activÃ©
variante prudente conservÃ©e en standby
```

## 39.25 SÃ©curitÃ© et responsabilitÃ©

RÃ¨gle absolue :

```text
Le LLM comprend.
Le validateur vÃ©rifie.
La GUI prÃ©sente.
Lâ€™utilisateur sÃ©lectionne.
Le moteur arme.
TWS exÃ©cute uniquement aprÃ¨s validation.
```

Le programme ne doit jamais confondre comprÃ©hension sÃ©mantique et autorisation dâ€™exÃ©cuter un ordre.

## 39.26 Roadmap dâ€™implÃ©mentation

### Phase 1 â€” Extraction sÃ©mantique hors trading

```text
LLM client
JSON Schema
prompts
segmentation
classification
provenance
stockage SQLite
```

### Phase 2 â€” Compilation dÃ©terministe

```text
rule compiler
validation mÃ©tier
ambiguÃ¯tÃ©s
profils qualitatifs
score de confiance
```

### Phase 3 â€” GUI de revue

```text
cartes scÃ©narios
Ã©dition champ par champ
provenance
scores
rÃ©solution ambiguÃ¯tÃ©s
sÃ©lection
armement
```

### Phase 4 â€” IntÃ©gration TWS en simulation

```text
simulation uniquement
logs dÃ©taillÃ©s
aucun ordre rÃ©el
tests multi-scÃ©narios
```

### Phase 5 â€” Paper trading

```text
activation explicite
compte paper trading
tests de reconnexion
tests dâ€™erreurs
tests de concurrence
```

### Phase 6 â€” Live contrÃ´lÃ©

```text
petits montants
confirmation manuelle configurable
journalisation complÃ¨te
rollback de configuration
emergency stop
```

## 39.27 RÃ¨gle finale

```text
Le programme doit comprendre le texte sans exÃ©cuter aveuglÃ©ment.
Lâ€™intelligence artificielle produit un brouillon structurÃ©.
Le validateur dÃ©terministe protÃ¨ge le systÃ¨me.
Lâ€™utilisateur conserve toujours le contrÃ´le final.
```


---

# 40. Normalisation canonique des champs avant validation

## 40.1 ProblÃ¨me Ã  rÃ©soudre

Le convertisseur peut dÃ©tecter correctement une valeur dans le texte, mais la validation peut Ã©chouer si le programme cherche une clÃ© diffÃ©rente.

Exemple canonique valide :

```text
trailing_stop_loss.initial_stop: 101.40
```

Erreur incorrecte Ã  Ã©viter :

```text
Add a stop loss in the setup text
```

Le problÃ¨me ne vient pas du setup. Il vient dâ€™un mapping incohÃ©rent entre :

```text
clÃ© saisie par lâ€™utilisateur
clÃ© extraite par le parseur
clÃ© attendue par le validateur
clÃ© affichÃ©e par la GUI
```

Le programme doit imposer un schÃ©ma canonique unique.

## 40.2 Principe impÃ©ratif

Toutes les variantes acceptÃ©es doivent Ãªtre converties vers une seule clÃ© interne avant toute validation.

Exemple :

```text
SL
stop
stop_loss
stop-loss
initial stop loss
initial_stop_loss
protective_stop
protective stop
```

doivent toutes devenir :

```text
trailing_stop_loss.initial_stop
```

Le validateur ne doit lire que la clÃ© canonique.

## 40.3 Pipeline obligatoire

```text
Texte utilisateur
  â†“
Parsing ligne par ligne
  â†“
Normalisation de la clÃ©
  â†“
RÃ©solution dâ€™alias
  â†“
Conversion de type
  â†“
Construction du modÃ¨le canonique
  â†“
Validation mÃ©tier
  â†“
Affichage GUI
```

La validation ne doit jamais sâ€™exÃ©cuter directement sur les clÃ©s brutes.

## 40.4 Modules Ã  ajouter

Ajouter :

```text
app/
  conversion/
    canonical_field_registry.py
    alias_resolver.py
    canonical_model_builder.py
```

CrÃ©er aussi :

```text
config/
  field_aliases.yaml
```

## 40.5 Registre dâ€™alias

Exemple :

```yaml
trailing_stop_loss.initial_stop:
  - "sl"
  - "stop"
  - "stop_loss"
  - "stop-loss"
  - "initial stop loss"
  - "initial_stop_loss"
  - "protective stop"
  - "protective_stop"
  - "stop protecteur"
  - "stop_loss_initial"

risk.max_position_amount_usd:
  - "max_position_amount_usd"
  - "montant maximal"
  - "position max"
  - "max position amount"
  - "budget maximal"

risk.max_risk_usd:
  - "max_risk_usd"
  - "risque maximal"
  - "max risk"
  - "risk max"

entry.order_type:
  - "entry_order_type"
  - "order type"
  - "type d'ordre"
  - "type ordre"

entry.trigger_price:
  - "entry_trigger"
  - "trigger"
  - "trigger price"

entry.limit_price:
  - "entry_limit_price"
  - "prix limite maximal"
  - "limit price"
  - "maximum limit price"
```

Ce registre doit Ãªtre extensible sans modifier le code Python.

## 40.6 Normalisation des clÃ©s

Exemple Python :

```python
from __future__ import annotations


def normalize_key(raw_key: str) -> str:
    return (
        raw_key.strip()
        .lower()
        .replace("â€™", "'")
        .replace("-", "_")
        .replace(" ", "_")
    )
```

Exemples :

```text
"Stop-loss"            â†’ "stop_loss"
"initial stop loss"    â†’ "initial_stop_loss"
"Prix limite maximal"  â†’ "prix_limite_maximal"
```

## 40.7 RÃ©solution des alias

Exemple Python :

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CanonicalField:
    canonical_path: str
    aliases: tuple[str, ...]


class AliasResolver:
    def __init__(self, fields: list[CanonicalField]) -> None:
        self._index: dict[str, str] = {}

        for field in fields:
            for alias in field.aliases:
                self._index[normalize_key(alias)] = field.canonical_path

    def resolve(self, raw_key: str) -> str | None:
        return self._index.get(normalize_key(raw_key))
```

RÃ©sultat attendu :

```python
resolver.resolve("initial_stop_loss")
# "trailing_stop_loss.initial_stop"

resolver.resolve("SL")
# "trailing_stop_loss.initial_stop"
```

## 40.8 Conversion des valeurs

Les valeurs doivent Ãªtre normalisÃ©es avant stockage.

```python
def parse_number(raw_value: str) -> float:
    cleaned = (
        raw_value.strip()
        .replace("$", "")
        .replace("USD", "")
        .replace(",", ".")
        .strip()
    )
    return float(cleaned)
```

Formats acceptÃ©s :

```text
101.40
101,40
$101.40
101.40 USD
USD 101.40
```

Format interne :

```text
101.40
```

## 40.9 ModÃ¨le canonique

MÃªme si lâ€™utilisateur saisit un format plat :

```text
trailing_stop_loss.initial_stop: 101.40
risk.max_position_amount_usd: 150
risk.max_risk_usd: 8
```

le programme doit construire :

```json
{
  "risk": {
    "max_position_amount_usd": 150,
    "max_risk_usd": 8
  },
  "trailing_stop_loss": {
    "enabled": true,
    "mode": "AUTO_INTELLIGENT",
    "never_lower_stop": true,
    "initial_stop": 101.40
  }
}
```

MÃªme principe pour lâ€™entrÃ©e :

```text
entry_order_type: STP_LMT
entry_trigger: 105.80
entry_limit_price: 106.80
```

devient :

```json
{
  "entry": {
    "order_type": "STP_LMT",
    "trigger_price": 105.80,
    "limit_price": 106.80
  }
}
```

## 40.10 Validation correcte du stop-loss

Le validateur doit lire uniquement :

```text
trailing_stop_loss.initial_stop
```

Exemple :

```python
from __future__ import annotations

from typing import Any


def validate_trailing_stop_required(setup: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    trailing = setup.get("trailing_stop_loss", {})
    stop_loss = trailing.get("initial_stop") if isinstance(trailing, dict) else None

    if stop_loss is None:
        errors.append(
            "Stop-loss initial manquant. "
            "Ajoutez par exemple : trailing_stop_loss.initial_stop: 101.40"
        )
        return errors

    if not isinstance(stop_loss, (int, float)):
        errors.append("Le stop-loss initial doit Ãªtre numÃ©rique.")

    return errors
```

Interdiction :

```python
setup.get("stop_loss")
setup.get("SL")
setup.get("initial_stop_loss")
```

Le validateur ne doit jamais dÃ©pendre des alias.

## 40.11 Sauvegarde et armement

Un setup peut Ãªtre sauvegardÃ© mÃªme sâ€™il nâ€™est pas armable.

Exemple :

```text
status: INVALIDATED_REQUIRES_REVIEW
armed: NO
```

Comportement attendu :

```text
Sauvegarde : autorisÃ©e
Armement : interdit
ExÃ©cution TWS : interdite
```

Le validateur doit produire deux rÃ©sultats distincts :

```json
{
  "save_validation": {
    "allowed": true,
    "errors": []
  },
  "arm_validation": {
    "allowed": false,
    "errors": [
      "Le scÃ©nario est invalidÃ© et nÃ©cessite une revue manuelle."
    ]
  }
}
```

## 40.12 Messages dâ€™erreur prÃ©cis

Message interdit :

```text
Add a stop loss in the setup text
```

si une variante reconnue existe dÃ©jÃ  dans le texte.

Message correct si le champ est rÃ©ellement absent :

```text
Stop-loss initial introuvable.
Variantes acceptÃ©es :
- trailing_stop_loss.initial_stop: 101.40
```

Message correct si le mapping Ã©choue :

```text
Le champ "initial_stop_loss" a Ã©tÃ© dÃ©tectÃ©,
mais n'a pas ete mappe vers "trailing_stop_loss.initial_stop".
VÃ©rifiez le registre dâ€™alias.
```

## 40.13 Logs de dÃ©bogage obligatoires

Ajouter des logs structurÃ©s :

```text
raw_key
normalized_key
resolved_canonical_path
raw_value
parsed_value
validation_result
```

Exemple :

```json
{
  "raw_key": "SL",
  "normalized_key": "sl",
  "resolved_canonical_path": "trailing_stop_loss.initial_stop",
  "raw_value": "101.40",
  "parsed_value": 101.40,
  "validation_result": "OK"
}
```

## 40.14 Tests unitaires obligatoires

```python
import pytest


@pytest.mark.parametrize(
    ("raw_key", "expected_path"),
    [
        ("SL", "trailing_stop_loss.initial_stop"),
        ("stop", "trailing_stop_loss.initial_stop"),
        ("stop_loss", "trailing_stop_loss.initial_stop"),
        ("stop-loss", "trailing_stop_loss.initial_stop"),
        ("initial stop loss", "trailing_stop_loss.initial_stop"),
        ("initial_stop_loss", "trailing_stop_loss.initial_stop"),
        ("protective_stop", "trailing_stop_loss.initial_stop"),
    ],
)
def test_stop_loss_aliases(raw_key, expected_path):
    ...


@pytest.mark.parametrize(
    ("raw_value", "expected_value"),
    [
        ("101.40", 101.40),
        ("101,40", 101.40),
        ("$101.40", 101.40),
        ("101.40 USD", 101.40),
        ("USD 101.40", 101.40),
    ],
)
def test_stop_loss_number_formats(raw_value, expected_value):
    ...


def test_initial_stop_loss_is_mapped_before_validation():
    ...


def test_invalidated_setup_can_be_saved_but_not_armed():
    ...
```

## 40.15 Test dâ€™intÃ©gration obligatoire

EntrÃ©e :

```text
SETUP_TRADING

trailing_stop_loss.initial_stop: 101.40
risk.max_position_amount_usd: 150
risk.max_risk_usd: 8
```

RÃ©sultat attendu :

```json
{
  "risk": {
    "max_position_amount_usd": 150,
    "max_risk_usd": 8
  },
  "trailing_stop_loss": {
    "enabled": true,
    "mode": "AUTO_INTELLIGENT",
    "never_lower_stop": true,
    "initial_stop": 101.40
  }
}
```

Erreur interdite :

```text
Add a stop loss in the setup text
```

## 40.16 Migration du code existant

Le dÃ©veloppeur doit rechercher les validations directes comme :

```python
setup.get("stop_loss")
setup.get("SL")
setup.get("initial_stop_loss")
```

et les remplacer par :

```python
setup["trailing_stop_loss"]["initial_stop"]
```

aprÃ¨s passage obligatoire dans :

```text
canonical_model_builder
```

## 40.17 RÃ¨gle finale

```text
Le parseur accepte plusieurs variantes.
Le registre dâ€™alias les normalise.
Le modÃ¨le canonique les structure.
Le validateur lit uniquement le modÃ¨le canonique.
La GUI affiche des erreurs prÃ©cises.
Un setup sauvegardable nâ€™est pas nÃ©cessairement armable.
```

---

# 41. Extension V2.0 — Application orientee opportunites, scoring, backtest et forecasting

## 41.1 Objectif de l'extension

Le programme ne doit plus etre considere uniquement comme un moteur d'execution d'ordres a partir de setups deja fournis par l'utilisateur.

La cible V2.0 est une application complete capable de :

```text
1. surveiller un univers de symboles ;
2. detecter automatiquement des opportunites techniques ;
3. transformer ces opportunites en candidats de setups ;
4. classer les opportunites par qualite ;
5. enrichir les setups par des previsions multi-modeles ;
6. backtester les regles sur donnees historiques ;
7. afficher les raisons d'acceptation ou de rejet ;
8. permettre a l'utilisateur de selectionner et armer uniquement les meilleurs scenarios ;
9. executer via TWS seulement apres validation stricte ;
10. tracer toutes les decisions.
```

Le nouveau flux cible devient :

```text
Universe / Watchlist
      ↓
Market Data + Historical Data
      ↓
Data Quality Layer
      ↓
Feature Store
      ↓
Opportunity Scanner
      ↓
Opportunity Ranker
      ↓
Scenario Generator / Semantic Intelligence
      ↓
Setup Quality Scoring
      ↓
Forecasting Ensemble
      ↓
Backtest / Replay Feedback
      ↓
GUI Review
      ↓
User Selection / Arming
      ↓
Setup Engine
      ↓
Risk Engine
      ↓
Order Manager
      ↓
TWS Execution
      ↓
Reconciliation + Observability
```

## 41.2 Regle d'architecture fondamentale

Le systeme doit separer strictement :

```text
Analysis        = texte, commentaire ou recherche brute.
Opportunity     = detection marche non encore executable.
Scenario        = hypothese structuree issue d'une analyse ou d'un scanner.
Setup           = scenario valide et armable.
Order Plan      = intention d'ordre calculee et verifiee.
IBKR Order      = ordre reel envoye a TWS.
Position        = exposition reelle detectee chez IBKR.
```

Regle obligatoire :

```text
Aucune opportunite detectee automatiquement ne doit devenir un ordre IBKR sans passer par :
scanner → scenario → validation → scoring → revue GUI → armement → risk engine → order manager.
```

## 41.3 Dossiers a ajouter ou completer

```text
app/
  opportunity_scanner/
  scoring/
  features/
  data_quality/
  research/
  model_lab/
  portfolio/
  observability/
  runtime/
  calendar/
  reports/
  migrations/
```

Ces dossiers ne remplacent pas les modules existants. Ils les entourent.

---

# 42. Opportunity Scanner / Radar d'opportunites

## 42.1 Objectif

Le `Opportunity Scanner` detecte les candidats interessants avant que l'utilisateur ne redige un setup.

Il doit repondre a la question :

```text
Quelles actions meritent mon attention maintenant ?
```

Il ne doit jamais repondre directement :

```text
Acheter maintenant.
```

## 42.2 Responsabilites

Le scanner doit :

- charger un univers de symboles ;
- filtrer les titres non tradables ;
- verifier liquidite et spread ;
- calculer des signaux techniques simples et rapides ;
- detecter des patterns : breakout, retest, reclaim, pullback, range breakout, support rebound ;
- produire des `OpportunityCandidate` non executables ;
- classer les candidats ;
- envoyer les meilleurs a la GUI ;
- permettre de generer un brouillon de scenario depuis une opportunite.

## 42.3 Structure de fichiers

Etat actuel implemente au 2026-06-30 :

```text
app/opportunity_scanner/
  __init__.py
  service.py
  detectors.py
  scoring.py
  schemas.py
  repository.py

app/opportunities/
  scanner.py
  shortlist_service.py
  scenario_generator.py
  opportunity_to_scenario_mapper.py
  opportunity_lifecycle_service.py
  opportunity_expiration_policy.py
  opportunity_explainer.py
```

Structure cible etendue :

```text
app/opportunity_scanner/
  __init__.py
  universe_loader.py
  watchlist_manager.py
  scanner_orchestrator.py
  technical_scanner.py
  momentum_scanner.py
  breakout_scanner.py
  retest_scanner.py
  reclaim_scanner.py
  pullback_scanner.py
  range_breakout_scanner.py
  volume_scanner.py
  gap_scanner.py
  relative_strength_scanner.py
  liquidity_filter.py
  opportunity_ranker.py
  opportunity_repository.py
  opportunity_models.py
```

## 42.4 Modele `OpportunityCandidate`

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OpportunityCandidate:
    opportunity_id: str
    symbol: str
    opportunity_type: str
    timeframe: str
    detected_at: str
    status: str
    source: str
    levels: dict[str, float]
    features: dict[str, Any]
    scores: dict[str, float]
    warnings: list[str]
    blocking_reasons: list[str]
    generated_scenario_id: str | None = None
```

Statuts possibles :

```text
DETECTED
FILTERED_OUT
NEEDS_DATA
CANDIDATE
SHORTLISTED
GENERATED_SCENARIO
IGNORED
ARCHIVED
EXPIRED
```

Statuts payload du scanner Market Context :

```text
OPPORTUNITY_DETECTED
WATCHLIST_OPPORTUNITY
WEAK_OPPORTUNITY
NO_OPPORTUNITY
```

La colonne `status` SQLite reste compatible avec le pipeline existant (`DETECTED`, `WATCHLIST`, `REJECTED`, etc.). Le statut riche est conserve dans `payload.opportunity_status`.

## 42.5 Types d'opportunites supportes V1

```text
INTRADAY_MOMENTUM_ANOMALY
WATCHLIST_ANOMALY
MOMENTUM_BREAKOUT
BREAKOUT_RETEST
RECLAIM
PULLBACK_CONTINUATION
SUPPORT_REBOUND
RANGE_BREAKOUT
HIGH_VOLUME_MOVER
RELATIVE_STRENGTH_LEADER
GAP_AND_HOLD
FAILED_BREAKDOWN_RECLAIM
```

## 42.6 Detection momentum breakout

Conditions minimales configurables :

```yaml
scanner:
  momentum_breakout:
    enabled: true
    timeframes: ["15m", "1h", "1d"]
    resistance_lookback_bars: 20
    min_distance_to_resistance_pct: -1.0
    max_distance_above_resistance_pct: 2.0
    min_volume_ratio: 1.5
    min_relative_strength_score: 60
    require_price_above_vwap: true
    require_spread_pct_below: 0.35
```

Sortie attendue :

```json
{
  "opportunity_type": "MOMENTUM_BREAKOUT",
  "symbol": "<SYMBOL>",
  "timeframe": "15m",
  "levels": {
    "resistance": 105.50,
    "suggested_trigger": 105.60,
    "suggested_limit": 106.00,
    "technical_invalidation": 102.40
  },
  "features": {
    "volume_ratio": 1.72,
    "relative_strength_score": 81,
    "distance_to_resistance_pct": 0.28,
    "spread_pct": 0.08
  },
  "status": "CANDIDATE"
}
```

## 42.7 Detection breakout + retest

Le scanner doit distinguer :

```text
breakout confirme
retest en cours
retest reussi
retest rate
```

Regles :

```yaml
scanner:
  breakout_retest:
    breakout_timeframe: "1d"
    retest_timeframe: "15m"
    breakout_close_above_required: true
    retest_max_days_after_breakout: 5
    retest_zone_tolerance_pct: 1.0
    close_below_retest_zone_invalidates: true
    bullish_confirmation_required: true
```

## 42.8 Detection reclaim

Un reclaim n'est pas une simple cassure. Il implique souvent :

```text
1. perte ou passage sous un niveau ;
2. reprise du niveau ;
3. cloture au-dessus ;
4. maintien ;
5. confirmation par volume ou structure.
```

Structure cible :

```yaml
scanner:
  reclaim:
    timeframe: "15m"
    reclaim_close_above_required: true
    hold_bars_after_reclaim: 1
    max_rejection_bars: 2
    allow_wick_below_before_reclaim: true
    require_volume_ratio_min: 1.2
```

## 42.9 Detection relative strength

Le scanner doit comparer le symbole a un benchmark :

```text
SPY pour le marche global US.
QQQ pour les valeurs tech/growth.
IWM pour small caps.
Sector ETF si disponible.
```

Score simple :

```text
relative_strength_score = percentile_rank(symbol_return - benchmark_return)
```

Timeframes :

```text
15m
1h
1d
5d
20d
```

## 42.10 Filtre liquidite

Une opportunite doit etre rejetee ou degradee si :

```text
spread trop large
volume moyen trop faible
prix trop bas avec spread instable
market data delayed non autorisee
dernier tick trop ancien
bid ou ask absent
```

Configuration :

```yaml
liquidity_filter:
  min_price: 1.00
  min_avg_daily_volume: 500000
  max_spread_pct: 0.35
  max_last_tick_age_seconds: 20
  require_bid_ask: true
```

## 42.11 Cycle de vie d'une opportunite

```text
DETECTED
  ↓ data quality OK
CANDIDATE
  ↓ score suffisant
SHORTLISTED
  ↓ utilisateur clique Generate Scenario
GENERATED_SCENARIO
  ↓ scenario valide
ARMABLE
```

Transitions interdites :

```text
DETECTED → ORDER_PLACED
SHORTLISTED → ORDER_PLACED
GENERATED_SCENARIO → ORDER_PLACED sans setup valide
```

## 42.12 API du scanner

```text
GET  /api/opportunities
GET  /api/opportunities/{opportunity_id}
POST /api/opportunities/scan
POST /api/opportunities/{opportunity_id}/ignore
POST /api/opportunities/{opportunity_id}/archive
POST /api/opportunities/{opportunity_id}/generate-scenario
POST /api/opportunities/{symbol}/create-setup-candidate
GET  /api/opportunities/top
GET  /api/opportunities/by-symbol/{symbol}
```

## 42.13 Table SQLite

```sql
CREATE TABLE opportunities (
    opportunity_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    opportunity_type TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    levels_json TEXT NOT NULL,
    features_json TEXT NOT NULL,
    scores_json TEXT NOT NULL,
    warnings_json TEXT,
    blocking_reasons_json TEXT,
    generated_scenario_id TEXT,
    detected_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE INDEX idx_opportunities_symbol ON opportunities(symbol);
CREATE INDEX idx_opportunities_status ON opportunities(status);
CREATE INDEX idx_opportunities_type ON opportunities(opportunity_type);
CREATE INDEX idx_opportunities_detected_at ON opportunities(detected_at);
```

Table actuelle du depot :

```sql
CREATE TABLE opportunities (
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
```

Les champs recommandes `opportunity_status`, `reasons`, `warnings`, `recommended_next_action`, `source_snapshot` et `can_send_order=false` sont stockes dans `payload_json` afin de rester compatibles avec la table existante.

## 42.14 Tests obligatoires

```text
test_scanner_never_places_order
test_momentum_breakout_candidate_created
test_low_liquidity_candidate_filtered_out
test_stale_market_data_blocks_opportunity
test_breakout_retest_state_detected
test_reclaim_not_confused_with_simple_breakout
test_opportunity_can_generate_scenario_draft
test_generated_scenario_requires_validation_before_arming
test_cast_like_move_detects_opportunity_without_sector_metadata
test_extended_price_keeps_detection_but_recommends_waiting_for_retest
test_known_sector_calculates_relative_strength_leader
```

---

# 43. Feature Store et Market Context Store

## 43.1 Objectif

Le `Feature Store` stocke les indicateurs calcules pour eviter de recalculer tout a chaque tick.

Il doit fournir au scanner, au scoring, au forecasting et au risk engine un contexte coherent.

## 43.2 Features minimales

```text
last_price
bid
ask
mid_price
spread
spread_pct
volume
volume_ratio
relative_volume
atr_15m
atr_1h
atr_1d
ema_20
ema_50
sma_20
sma_50
vwap
price_vs_vwap_pct
distance_to_support_pct
distance_to_resistance_pct
relative_strength_vs_spy
relative_strength_vs_qqq
gap_pct
open_range_high
open_range_low
higher_low_detected
lower_high_detected
breakout_attempt_count
failed_breakout_count
session_phase
market_regime
```

## 43.3 Structure de fichiers

```text
app/features/
  feature_store.py
  feature_models.py
  feature_repository.py
  technical_features.py
  volume_features.py
  volatility_features.py
  trend_features.py
  relative_strength_features.py
  regime_features.py
  feature_snapshot_service.py
```

## 43.4 Table SQLite

```sql
CREATE TABLE feature_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    features_json TEXT NOT NULL,
    data_quality_status TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_feature_snapshot_unique
ON feature_snapshots(symbol, timeframe, timestamp);

CREATE INDEX idx_feature_snapshot_lookup
ON feature_snapshots(symbol, timeframe, created_at);
```

## 43.5 Regles de fraicheur

```yaml
features:
  max_age_seconds:
    tick_features: 20
    candle_1m: 90
    candle_5m: 360
    candle_15m: 1200
    daily: 172800
```

Si une feature critique est obsolete :

```text
scanner: peut afficher warning ou bloquer selon criticite
setup engine: doit bloquer toute nouvelle entree
gestion stop: peut continuer si la position doit rester protegee, mais doit signaler WARNING
```

## 43.6 API

```text
GET /api/features/{symbol}
GET /api/features/{symbol}/{timeframe}
GET /api/market-context/{symbol}
GET /api/market-context/summary
```

## 43.7 Tests obligatoires

```text
test_feature_snapshot_saved
test_feature_snapshot_unique_by_symbol_timeframe_timestamp
test_stale_feature_detected
test_missing_critical_feature_blocks_entry
test_relative_strength_feature_computed
test_spread_pct_computed_from_bid_ask
```

---

# 44. Data Quality Layer

## 44.1 Objectif

La couche `data_quality` protege tout le systeme contre les mauvaises donnees.

Regle obligatoire :

```text
Une mauvaise donnee ne doit jamais declencher un ordre.
```

## 44.2 Problemes a detecter

```text
bad tick
last price absent
bid absent
ask absent
bid > ask
spread anormal
volume absent
volume nul anormal
bougie incomplete
bougie non cloturee utilisee par erreur
trou temporel dans les bougies
market data delayed alors que live requis
donnees obsoletes
timezone incoherente
split non ajuste
reverse split non ajuste
halt trading
symbol non tradable
IBKR pacing limit
session incorrecte
pre-market utilise alors que interdit
after-hours utilise alors que interdit
```

## 44.3 Structure de fichiers

```text
app/data_quality/
  data_quality_service.py
  market_data_validator.py
  tick_validator.py
  candle_integrity_checker.py
  bad_tick_detector.py
  spread_guard.py
  session_guard.py
  stale_data_guard.py
  corporate_action_adjuster.py
  halt_detector.py
  data_quality_models.py
  data_quality_repository.py
```

## 44.4 Niveaux de qualite

```text
OK
WARNING
DEGRADED
BLOCKED
UNKNOWN
```

Interpretation :

```text
OK       → donnees utilisables.
WARNING  → utilisables pour affichage, prudence pour scoring.
DEGRADED → pas de nouvelle entree automatique, analyse possible.
BLOCKED  → aucune decision d'entree.
UNKNOWN  → pas de decision d'entree.
```

## 44.5 Modele de resultat

```json
{
  "symbol": "<SYMBOL>",
  "timeframe": "15m",
  "quality_status": "DEGRADED",
  "trading_allowed": false,
  "analysis_allowed": true,
  "issues": [
    {
      "code": "SPREAD_TOO_WIDE",
      "severity": "BLOCKING",
      "message": "Spread percent 0.82 is above max 0.35"
    }
  ],
  "checked_at": "<TIMESTAMP>"
}
```

## 44.6 Configuration

```yaml
data_quality:
  max_last_tick_age_seconds: 20
  max_spread_pct_default: 0.35
  reject_bid_greater_than_ask: true
  reject_negative_volume: true
  require_closed_candle_for_close_conditions: true
  allow_delayed_data_in_paper: true
  allow_delayed_data_in_live: false
  block_entry_on_unknown_quality: true
```

## 44.7 Table SQLite

```sql
CREATE TABLE data_quality_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT,
    quality_status TEXT NOT NULL,
    issue_code TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_data_quality_symbol ON data_quality_events(symbol);
CREATE INDEX idx_data_quality_created_at ON data_quality_events(created_at);
```

## 44.8 Tests obligatoires

```text
test_bad_tick_rejected
test_bid_greater_than_ask_blocked
test_spread_too_wide_blocks_entry
test_stale_tick_blocks_entry
test_unclosed_candle_cannot_validate_close_condition
test_delayed_data_allowed_in_paper_if_configured
test_delayed_data_blocked_in_live
test_data_quality_event_saved
```

---

# 45. Setup Quality Scoring Engine

## 45.1 Objectif

Le `Setup Quality Scoring Engine` attribue une note explicable a chaque opportunite, scenario ou setup.

Il doit repondre a :

```text
Pourquoi ce setup est meilleur ou moins bon qu'un autre ?
```

## 45.2 Structure de fichiers

```text
app/scoring/
  setup_quality_engine.py
  opportunity_score.py
  technical_score.py
  volume_score.py
  liquidity_score.py
  risk_score.py
  trend_score.py
  market_context_score.py
  forecast_alignment_score.py
  backtest_score.py
  score_explainer.py
  scoring_models.py
  scoring_repository.py
```

## 45.3 Scores individuels

```text
technical_score           : qualite du pattern technique.
volume_score              : volume relatif, volume acheteur, confirmation.
liquidity_score           : spread, volume moyen, tradability.
risk_score                : distance stop, risque/action, quantite possible.
trend_score               : alignement tendance 15m/1h/1d.
market_context_score      : SPY/QQQ, regime, volatilite globale.
forecast_alignment_score  : alignement TimesFM/Chronos/Lag-Llama/baselines.
backtest_score            : historique de performance pour setup_type + symbole + timeframe.
execution_score           : probabilite d'execution sans slippage excessif.
```

## 45.4 Formule par defaut

```text
overall_score =
  0.20 * technical_score
+ 0.15 * volume_score
+ 0.15 * liquidity_score
+ 0.15 * risk_score
+ 0.10 * trend_score
+ 0.10 * market_context_score
+ 0.10 * forecast_alignment_score
+ 0.05 * backtest_score
```

La formule doit etre configurable :

```yaml
scoring:
  weights:
    technical_score: 0.20
    volume_score: 0.15
    liquidity_score: 0.15
    risk_score: 0.15
    trend_score: 0.10
    market_context_score: 0.10
    forecast_alignment_score: 0.10
    backtest_score: 0.05
  thresholds:
    excellent: 85
    good: 70
    weak: 50
    reject_below: 40
```

## 45.5 Decisions

```text
EXCELLENT
GOOD
ACCEPTABLE_BUT_WAIT_CONFIRMATION
WEAK
REJECTED
BLOCKED_BY_RISK
BLOCKED_BY_DATA_QUALITY
BLOCKED_BY_LIQUIDITY
```

## 45.6 Sortie JSON

```json
{
  "score_id": "<SCORE_ID>",
  "target_type": "SCENARIO",
  "target_id": "<SCENARIO_ID>",
  "symbol": "<SYMBOL>",
  "overall_score": 78,
  "decision": "GOOD",
  "components": {
    "technical_score": 82,
    "volume_score": 74,
    "liquidity_score": 91,
    "risk_score": 69,
    "trend_score": 76,
    "market_context_score": 72,
    "forecast_alignment_score": 63,
    "backtest_score": 58
  },
  "explanation": [
    "Breakout proche d'une resistance claire",
    "Volume superieur a la moyenne",
    "Risque par action eleve mais acceptable",
    "Forecast favorable mais consensus faible"
  ],
  "blocking_reasons": [],
  "warnings": ["Backtest insuffisant sur ce symbole"]
}
```

## 45.7 Tables SQLite

```sql
CREATE TABLE setup_scores (
    score_id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    overall_score REAL NOT NULL,
    decision TEXT NOT NULL,
    components_json TEXT NOT NULL,
    explanation_json TEXT,
    blocking_reasons_json TEXT,
    warnings_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_setup_scores_target ON setup_scores(target_type, target_id);
CREATE INDEX idx_setup_scores_symbol ON setup_scores(symbol);
CREATE INDEX idx_setup_scores_created_at ON setup_scores(created_at);
```

## 45.8 Tests obligatoires

```text
test_score_components_are_between_0_and_100
test_overall_score_uses_configured_weights
test_low_liquidity_blocks_even_if_technical_score_high
test_missing_forecast_does_not_block_but_warns
test_data_quality_block_forces_decision_blocked
test_score_explanation_contains_main_reasons
test_scoring_result_saved
```

---

# 46. Forecasting Engine multi-modeles

## 46.1 Objectif

Le moteur `forecasting` enrichit les setups avec une estimation probabiliste ou directionnelle.

Regle absolue :

```text
Le forecasting ne declenche jamais un ordre directement.
Il renforce, degrade ou neutralise uniquement le score d'un setup.
```

## 46.2 Modeles supportes progressivement

```text
TimesFM          : modele principal foundation forecasting.
Chronos          : benchmark direct foundation model.
Lag-Llama        : prevision probabiliste et intervalles.
Moirai / Uni2TS  : modele foundation avance.
NeuralForecast   : NHITS, NBEATS, PatchTST, iTransformer, TFT.
AutoGluon        : baseline AutoML time series.
NaiveBaseline    : last close / random walk.
ATRBaseline      : estimation simple basee sur volatilite.
TrendBaseline    : continuation tendance simple.
```

## 46.3 Structure de fichiers

```text
app/forecasting/
  base_forecaster.py
  forecast_models.py
  forecast_registry.py
  forecast_service.py
  forecast_request_builder.py
  forecast_result_normalizer.py
  forecast_cache.py
  forecast_ensemble.py
  forecast_confidence.py
  forecast_evaluator.py
  adapters/
    timesfm_adapter.py
    chronos_adapter.py
    lag_llama_adapter.py
    moirai_adapter.py
    neuralforecast_adapter.py
    autogluon_adapter.py
    naive_baseline_adapter.py
    atr_baseline_adapter.py
```

## 46.4 Interface commune

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ForecastRequest:
    symbol: str
    timeframe: str
    horizon_bars: int
    context_bars: int
    target: str
    candles: list[dict[str, Any]]
    covariates: dict[str, Any]


@dataclass(frozen=True)
class ForecastResult:
    model_name: str
    symbol: str
    timeframe: str
    horizon_bars: int
    generated_at: str
    point_forecast: list[float] | None
    quantiles: dict[str, list[float]] | None
    direction: str
    direction_confidence: float
    prob_touch_entry: float | None
    prob_touch_stop_before_entry: float | None
    raw_output: dict[str, Any]
    warnings: list[str]


class BaseForecaster(ABC):
    @abstractmethod
    def forecast(self, request: ForecastRequest) -> ForecastResult:
        raise NotImplementedError
```

## 46.5 Horizons standards

```text
15m setup  → horizon 4, 8, 16 bougies
1h setup   → horizon 4, 8, 24 bougies
1d setup   → horizon 3, 5, 10 bougies
```

Configuration :

```yaml
forecasting:
  enabled: true
  default_models: ["timesfm", "chronos", "lag_llama", "naive_baseline", "atr_baseline"]
  horizons:
    "15m": [4, 8, 16]
    "1h": [4, 8, 24]
    "1d": [3, 5, 10]
  cache_ttl_seconds: 300
  block_execution_if_forecast_fails: false
  use_forecast_for_scoring_only: true
```

## 46.6 Ensemble forecast

L'ensemble doit produire :

```text
consensus_direction
consensus_strength
model_agreement_ratio
prob_touch_entry_combined
prob_touch_stop_before_entry_combined
forecast_alignment_score
warnings
```

Sortie :

```json
{
  "symbol": "<SYMBOL>",
  "timeframe": "15m",
  "horizon_bars": 8,
  "consensus_direction": "UP",
  "consensus_strength": 0.62,
  "model_agreement_ratio": 0.60,
  "prob_touch_entry_combined": 0.64,
  "prob_touch_stop_before_entry_combined": 0.22,
  "forecast_alignment_score": 63,
  "models": {
    "timesfm": {"direction": "UP", "confidence": 0.61},
    "chronos": {"direction": "UP", "confidence": 0.57},
    "lag_llama": {"prob_touch_entry": 0.67, "prob_touch_stop_before_entry": 0.19},
    "naive_baseline": {"direction": "FLAT", "confidence": 0.50}
  },
  "decision": "FORECAST_SUPPORTS_SETUP_WEAKLY"
}
```

## 46.7 Interpretation dans le scoring

```text
Forecast favorable + setup technique valide     → score augmente.
Forecast defavorable + setup technique valide   → score degrade, pas forcement rejet.
Forecast incertain                              → warning, pas de blocage.
Modeles en desaccord fort                       → besoin de confirmation.
Forecast indisponible                           → pas de blocage si config scoring-only.
```

## 46.8 Tables SQLite

```sql
CREATE TABLE forecast_runs (
    forecast_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    horizon_bars INTEGER NOT NULL,
    model_name TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    status TEXT NOT NULL,
    generated_at TEXT NOT NULL
);

CREATE TABLE forecast_ensembles (
    ensemble_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    horizon_bars INTEGER NOT NULL,
    model_results_json TEXT NOT NULL,
    consensus_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_forecast_runs_symbol ON forecast_runs(symbol, timeframe, generated_at);
CREATE INDEX idx_forecast_ensembles_symbol ON forecast_ensembles(symbol, timeframe, created_at);
```

## 46.9 Tests obligatoires

```text
test_forecasting_never_places_order
test_forecast_request_built_from_candles
test_model_adapter_returns_normalized_result
test_forecast_cache_used
test_missing_model_returns_warning_not_crash
test_ensemble_computes_agreement_ratio
test_forecast_alignment_score_degrades_on_disagreement
test_forecast_failure_does_not_block_if_scoring_only
```

---

# 47. Model Lab, Backtesting et Replay Engine

## 47.1 Objectif

Le `Model Lab` doit prouver si une regle, un setup ou un modele apporte une valeur statistique.

Il doit repondre a :

```text
Est-ce que ce setup a un edge historique ?
Est-ce que TimesFM bat une baseline naive ?
Est-ce que le scanner produit trop de faux positifs ?
Quelle regle provoque le plus de pertes ?
```

## 47.2 Structure de fichiers

```text
app/research/
  historical_data_loader.py
  replay_engine.py
  walk_forward_backtester.py
  setup_backtester.py
  execution_simulator.py
  slippage_model.py
  commission_model.py
  backtest_metrics.py
  backtest_report_generator.py
  replay_repository.py

app/model_lab/
  dataset_builder.py
  model_benchmark_runner.py
  walk_forward_splitter.py
  baseline_models.py
  model_scorecard.py
  model_drift_detector.py
  model_selection_policy.py
  benchmark_repository.py
```

## 47.3 Regle de separation

```text
Backtest Engine teste des regles de trading.
Model Lab teste des modeles de forecasting.
Les deux peuvent partager les memes donnees historiques, mais pas les memes metriques finales.
```

## 47.4 Donnees historiques requises

Pour chaque symbole :

```text
OHLCV 1m
OHLCV 5m
OHLCV 15m
OHLCV 1h
OHLCV 1d
corporate actions si disponibles
earnings dates si disponibles
session calendar
spread approximatif ou modele de spread
```

## 47.5 Replay Engine

Le replay doit simuler le temps qui passe :

```text
1. charger les bougies historiques ;
2. avancer bougie par bougie ;
3. emettre un evenement CANDLE_CLOSED ;
4. laisser le scanner detecter les opportunites ;
5. laisser le setup engine evaluer les scenarios ;
6. simuler ordres, fills, stops et slippage ;
7. enregistrer tous les evenements ;
8. produire un rapport.
```

## 47.6 Simulation d'execution

Le simulateur doit gerer :

```text
STP_LMT touche trigger mais ne remplit pas si le prix saute au-dessus du limit
LMT remplit si prix touche limit
MKT remplit au prochain prix disponible avec slippage
SELL STP sort au premier prix disponible sous stop
partial fill configurable
commission configurable
spread configurable
```

Configuration :

```yaml
backtest:
  execution:
    commission_per_share: 0.005
    min_commission: 1.0
    default_slippage_bps: 10
    use_intrabar_high_low_for_stop_trigger: true
    conservative_fill_model: true
    stp_lmt_requires_price_within_limit: true
```

## 47.7 Walk-forward validation

Le systeme doit eviter l'overfitting.

Approche :

```text
train window: 6 mois
validation window: 1 mois
test window: 1 mois
roll forward: 1 mois
```

Le developpeur doit pouvoir configurer :

```yaml
model_lab:
  walk_forward:
    train_months: 6
    validation_months: 1
    test_months: 1
    step_months: 1
```

## 47.8 Metriques trading

```text
total_return
win_rate
profit_factor
expectancy
average_r
median_r
max_drawdown
max_consecutive_losses
stop_hit_rate
entry_missed_rate
false_breakout_rate
average_time_in_trade
exposure_time
risk_adjusted_return
```

## 47.9 Metriques forecasting

```text
MAE
RMSE
MAPE si applicable
direction_accuracy
hit_entry_accuracy
stop_before_entry_accuracy
quantile_coverage
calibration_error
brier_score pour evenements binaires
PnL_simulated_when_used_as_filter
```

## 47.10 Baselines obligatoires

Aucun modele avance ne doit etre considere utile s'il ne bat pas :

```text
last_close_baseline
random_walk_baseline
atr_range_baseline
simple_momentum_baseline
simple_trend_filter_baseline
```

## 47.11 Rapports

Le systeme doit generer :

```text
rapport par symbole
rapport par setup_type
rapport par timeframe
rapport par regime marche
rapport par modele forecasting
rapport global scanner
rapport erreurs / faux positifs
```

Format :

```text
HTML pour GUI
JSON pour stockage
CSV pour export
Markdown pour lecture rapide
```

## 47.12 Tables SQLite

```sql
CREATE TABLE backtest_runs (
    backtest_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    summary_json TEXT
);

CREATE TABLE backtest_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    backtest_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    setup_type TEXT NOT NULL,
    entry_time TEXT,
    exit_time TEXT,
    entry_price REAL,
    exit_price REAL,
    quantity INTEGER,
    pnl REAL,
    r_multiple REAL,
    exit_reason TEXT,
    payload_json TEXT
);

CREATE TABLE model_benchmarks (
    benchmark_id TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    horizon_bars INTEGER NOT NULL,
    metrics_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

## 47.13 API

```text
POST /api/backtests/run
GET  /api/backtests
GET  /api/backtests/{backtest_id}
GET  /api/backtests/{backtest_id}/trades
GET  /api/backtests/{backtest_id}/report
POST /api/model-lab/benchmark
GET  /api/model-lab/benchmarks
GET  /api/model-lab/scorecard/{symbol}
```

## 47.14 Tests obligatoires

```text
test_replay_emits_candle_closed_events
test_stp_lmt_trigger_without_fill_if_limit_skipped
test_stop_hit_simulated_conservatively
test_backtest_metrics_computed
test_walk_forward_splits_do_not_overlap
test_model_must_beat_naive_baseline_to_be_selected
test_forecast_filter_improves_or_degrades_pnl_reported
test_backtest_report_generated
```

---

# 48. Portfolio Risk Analytics et Global No-Trade Rules

## 48.1 Objectif

Le risque ne doit pas etre controle seulement trade par trade.

Le systeme doit aussi evaluer :

```text
exposition totale
exposition par secteur
correlation entre positions
concentration sur un theme
risque global du jour
risque de marche
risque earnings/news
```

## 48.2 Structure de fichiers

```text
app/portfolio/
  portfolio_service.py
  exposure_analyzer.py
  correlation_analyzer.py
  sector_exposure.py
  portfolio_risk_engine.py
  buying_power_manager.py
  concentration_limits.py
  portfolio_models.py

app/risk/
  global_no_trade_rules.py
  market_regime_guard.py
  news_event_guard.py
  liquidity_guard.py
```

## 48.3 Regles de risque portefeuille

```yaml
portfolio_risk:
  max_total_exposure_usd: 1000
  max_sector_exposure_pct: 40
  max_theme_exposure_pct: 50
  max_correlated_positions: 3
  max_single_symbol_exposure_pct: 20
  reduce_size_if_high_correlation: true
  block_new_entries_if_daily_loss_pct_reached: true
```

## 48.4 Global No-Trade

Statuts globaux :

```text
TRADING_ALLOWED
PAUSE_NEW_ENTRIES
MANAGE_ONLY
EMERGENCY_STOP
LIVE_DISABLED
```

Interpretation :

```text
TRADING_ALLOWED   → tout fonctionne selon les regles.
PAUSE_NEW_ENTRIES → aucune nouvelle entree, gestion positions autorisee.
MANAGE_ONLY       → seulement stops, annulations et fermetures.
EMERGENCY_STOP    → arret total sauf actions de securite.
LIVE_DISABLED     → paper/simulation uniquement.
```

## 48.5 Exemple de decision portefeuille

```json
{
  "symbol": "<SYMBOL>",
  "trade_allowed": true,
  "recommended_size_multiplier": 0.7,
  "warnings": [
    "High semiconductor exposure",
    "Correlation with existing position above 0.70"
  ],
  "blocking_reasons": []
}
```

## 48.6 API

```text
GET /api/portfolio/exposure
GET /api/portfolio/risk
GET /api/portfolio/correlations
GET /api/risk/global-status
POST /api/risk/global-pause
POST /api/risk/global-resume
```

## 48.7 Tests obligatoires

```text
test_portfolio_exposure_computed
test_sector_limit_warning
test_high_correlation_reduces_position_size
test_global_no_trade_blocks_new_entries
test_manage_only_allows_raise_stop_but_blocks_buy
test_daily_loss_reached_sets_pause_new_entries
```

---

# 49. Event Bus, Runtime et orchestration asynchrone

## 49.1 Objectif

Le systeme doit eviter les boucles globales lourdes.

Il doit utiliser un bus d'evenements interne.

## 49.2 Structure de fichiers

```text
app/runtime/
  event_bus.py
  event_models.py
  task_scheduler.py
  symbol_subscription_manager.py
  rate_limiter.py
  lock_manager.py
  job_queue.py
  runtime_state.py
```

## 49.3 Evenements internes

```text
APP_STARTED
APP_STOPPING
TWS_CONNECTED
TWS_DISCONNECTED
TWS_RECONNECTED
MARKET_TICK_RECEIVED
CANDLE_CLOSED
DATA_QUALITY_CHANGED
FEATURE_SNAPSHOT_UPDATED
OPPORTUNITY_DETECTED
OPPORTUNITY_SHORTLISTED
SCENARIO_CREATED
SCENARIO_ARMED
SCENARIO_BLOCKED
SETUP_STATUS_CHANGED
SIGNAL_VALID
SIGNAL_INVALID
ENTRY_READY
ORDER_SUBMITTED
ORDER_STATUS_CHANGED
EXECUTION_RECEIVED
POSITION_UPDATED
STOP_UPDATED
RECONCILIATION_STARTED
RECONCILIATION_FINISHED
RISK_LIMIT_REACHED
GLOBAL_TRADING_STATUS_CHANGED
ERROR_REQUIRES_MANUAL_REVIEW
```

## 49.4 Contrat d'un event

```python
@dataclass(frozen=True)
class DomainEvent:
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    symbol: str | None
    payload: dict
    created_at: str
    correlation_id: str | None
    causation_id: str | None
```

## 49.5 Regle de performance

```text
Market ticks      → traitement minimal, mise a jour snapshot.
Candle close      → evaluation setups/scanner.
Order callbacks   → traitement immediat.
Reconciliation    → periodique et apres reconnexion.
GUI updates       → websocket depuis events, pas polling lourd.
Forecasting       → cache + jobs asynchrones, jamais bloquer l'ordre critique.
Backtesting       → job separe, jamais bloquer runtime live/paper.
```

## 49.6 Tests obligatoires

```text
test_event_published_and_consumed
test_candle_closed_triggers_setup_evaluation
test_order_event_triggers_reconciliation_if_needed
test_rate_limiter_blocks_excessive_ibkr_requests
test_symbol_lock_prevents_concurrent_entry
test_scenario_lock_prevents_conflicting_actions
```

---

# 50. Observability, Decision Trace et Health Checks

## 50.1 Objectif

L'utilisateur doit pouvoir savoir :

```text
Pourquoi le bot attend ?
Pourquoi le bot refuse ?
Pourquoi le bot a choisi ce setup ?
Quelle donnee a declenche la decision ?
Quelle regle a bloque l'ordre ?
Combien de temps le systeme met a reagir ?
```

## 50.2 Structure de fichiers

```text
app/observability/
  metrics.py
  health_checks.py
  latency_tracker.py
  decision_trace.py
  audit_report.py
  system_status.py
  observability_repository.py
```

## 50.3 Metrics minimales

```text
tws_connection_status
tws_reconnect_count
last_tick_age_seconds
last_candle_close_age_seconds
market_data_events_per_minute
setup_evaluations_per_minute
scanner_runs_per_minute
forecast_runs_per_hour
order_submit_latency_ms
order_status_latency_ms
reconciliation_duration_ms
sqlite_write_latency_ms
websocket_clients_count
error_count_by_module
manual_review_count
blocked_entries_count
```

## 50.4 Decision Trace

Chaque decision critique doit produire une trace :

```json
{
  "trace_id": "<TRACE_ID>",
  "symbol": "<SYMBOL>",
  "setup_id": "<SETUP_ID>",
  "decision_type": "ENTRY_REJECTED",
  "input_snapshot": {},
  "rules_evaluated": [
    {
      "rule_id": "SPREAD_MAX",
      "result": "FAILED",
      "expected": "spread_pct <= 0.35",
      "actual": "0.82"
    }
  ],
  "final_decision": "BLOCKED_BY_LIQUIDITY",
  "created_at": "<TIMESTAMP>"
}
```

## 50.5 API

```text
GET /api/health
GET /api/metrics
GET /api/system/status
GET /api/decision-trace/{trace_id}
GET /api/decision-trace/setup/{setup_id}
GET /api/decision-trace/symbol/{symbol}
GET /api/audit/daily-report
```

## 50.6 Tables SQLite

```sql
CREATE TABLE decision_traces (
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

CREATE INDEX idx_decision_traces_symbol ON decision_traces(symbol);
CREATE INDEX idx_decision_traces_setup ON decision_traces(setup_id);
CREATE INDEX idx_decision_traces_created ON decision_traces(created_at);
```

## 50.7 Tests obligatoires

```text
test_health_endpoint_returns_status
test_decision_trace_created_for_rejected_entry
test_decision_trace_contains_rule_results
test_metrics_increment_on_order_event
test_latency_tracker_records_duration
test_audit_daily_report_generated
```

---

# 51. GUI orientee opportunites

## 51.1 Pages a ajouter

```text
/opportunities
/scanner
/radar
/market-context
/model-lab
/backtests
/forecasting
/decision-trace
/system-health
```

## 51.2 Page `/opportunities`

Colonnes :

```text
Symbol
Opportunity Type
Timeframe
Detected At
Overall Score
Technical Score
Volume Score
Liquidity Score
Risk Score
Forecast Score
Backtest Score
Distance To Trigger
Spread %
Status
Warnings
Actions
```

Actions :

```text
View
Generate Scenario
Add To Watchlist
Ignore
Archive
Run Backtest
Run Forecast
```

## 51.3 Page `/radar`

La page radar doit afficher des cartes :

```text
Top 10 opportunities
Momentum breakouts
Breakout retests
Reclaim candidates
High volume movers
Relative strength leaders
Rejected by liquidity
Rejected by data quality
Forecast-supported setups
Backtest-supported setups
```

## 51.4 Page `/market-context`

Afficher :

```text
SPY trend
QQQ trend
IWM trend
market regime
VIX si disponible
premarket/regular/after-hours status
number of opportunities detected
number blocked by global no-trade
sector exposure
```

## 51.5 Page `/model-lab`

Afficher :

```text
model name
symbol
timeframe
horizon
MAE
RMSE
direction accuracy
entry hit accuracy
stop-before-entry error
PnL when used as filter
beats naive baseline: YES/NO
last benchmark date
```

## 51.6 Page `/decision-trace`

Cette page est prioritaire.

Elle doit permettre de filtrer par :

```text
symbol
setup_id
scenario_id
opportunity_id
decision_type
final_decision
date
```

Elle doit afficher :

```text
inputs
rules evaluated
passed rules
failed rules
warnings
final decision
linked event ids
linked order ids if any
```

## 51.7 WebSocket events GUI

Ajouter :

```text
opportunity_detected
opportunity_score_updated
forecast_completed
backtest_completed
data_quality_changed
global_trading_status_changed
decision_trace_created
system_health_changed
```

---

# 52. API REST globale a ajouter

## 52.1 Opportunities

```text
GET    /api/opportunities
GET    /api/opportunities/top
GET    /api/opportunities/{opportunity_id}
POST   /api/opportunities/scan
POST   /api/opportunities/{opportunity_id}/generate-scenario
POST   /api/opportunities/{opportunity_id}/ignore
POST   /api/opportunities/{opportunity_id}/archive
```

## 52.2 Scanner

```text
GET    /api/scanner/status
POST   /api/scanner/run
POST   /api/scanner/pause
POST   /api/scanner/resume
GET    /api/scanner/config
PUT    /api/scanner/config
```

## 52.3 Scoring

```text
POST   /api/scoring/score-opportunity/{opportunity_id}
POST   /api/scoring/score-scenario/{scenario_id}
POST   /api/scoring/score-setup/{setup_id}
GET    /api/scoring/{score_id}
GET    /api/scoring/symbol/{symbol}
```

## 52.4 Forecasting

```text
POST   /api/forecasting/run
POST   /api/forecasting/run-for-scenario/{scenario_id}
GET    /api/forecasting/{forecast_id}
GET    /api/forecasting/symbol/{symbol}
GET    /api/forecasting/ensemble/{ensemble_id}
GET    /api/forecasting/models
```

## 52.5 Backtesting / Model Lab

```text
POST   /api/backtests/run
GET    /api/backtests
GET    /api/backtests/{backtest_id}
GET    /api/backtests/{backtest_id}/report
POST   /api/model-lab/benchmark
GET    /api/model-lab/benchmarks
GET    /api/model-lab/scorecard/{symbol}
```

## 52.6 Data Quality / Features

```text
GET    /api/data-quality/{symbol}
GET    /api/data-quality/events
GET    /api/features/{symbol}
GET    /api/features/{symbol}/{timeframe}
GET    /api/market-context/{symbol}
GET    /api/market-context/summary
```

## 52.7 Observability

```text
GET    /api/health
GET    /api/metrics
GET    /api/system/status
GET    /api/decision-trace/{trace_id}
GET    /api/decision-trace/setup/{setup_id}
GET    /api/decision-trace/symbol/{symbol}
```

---

# 53. Configuration globale V2.0

Ajouter au `config.yaml` :

```yaml
opportunity_scanner:
  enabled: true
  default_timeframes: ["15m", "1h", "1d"]
  max_candidates_per_scan: 100
  max_shortlisted: 20
  scan_interval_seconds: 60
  run_only_during_regular_market_hours: true
  allow_premarket_scan: false
  allow_after_hours_scan: false

  universe:
    source: "watchlist"
    max_symbols: 200
    default_watchlist_file: "data/watchlists/default.yaml"

  scanners:
    momentum_breakout:
      enabled: true
      min_volume_ratio: 1.5
      min_relative_strength_score: 60
      max_spread_pct: 0.35
    breakout_retest:
      enabled: true
      retest_max_days_after_breakout: 5
    reclaim:
      enabled: true
      hold_bars_after_reclaim: 1
    pullback_continuation:
      enabled: true

features:
  enabled: true
  persist_snapshots: true
  max_age_seconds:
    tick_features: 20
    candle_1m: 90
    candle_5m: 360
    candle_15m: 1200
    daily: 172800

data_quality:
  max_last_tick_age_seconds: 20
  max_spread_pct_default: 0.35
  reject_bid_greater_than_ask: true
  require_closed_candle_for_close_conditions: true
  allow_delayed_data_in_paper: true
  allow_delayed_data_in_live: false
  block_entry_on_unknown_quality: true

scoring:
  enabled: true
  weights:
    technical_score: 0.20
    volume_score: 0.15
    liquidity_score: 0.15
    risk_score: 0.15
    trend_score: 0.10
    market_context_score: 0.10
    forecast_alignment_score: 0.10
    backtest_score: 0.05
  thresholds:
    excellent: 85
    good: 70
    weak: 50
    reject_below: 40

forecasting:
  enabled: true
  use_forecast_for_scoring_only: true
  default_models: ["timesfm", "chronos", "lag_llama", "naive_baseline", "atr_baseline"]
  cache_ttl_seconds: 300
  block_execution_if_forecast_fails: false
  horizons:
    "15m": [4, 8, 16]
    "1h": [4, 8, 24]
    "1d": [3, 5, 10]

backtest:
  enabled: true
  data_folder: "data/historical"
  execution:
    commission_per_share: 0.005
    min_commission: 1.0
    default_slippage_bps: 10
    conservative_fill_model: true
    stp_lmt_requires_price_within_limit: true

model_lab:
  enabled: true
  require_model_to_beat_naive_baseline: true
  walk_forward:
    train_months: 6
    validation_months: 1
    test_months: 1
    step_months: 1

portfolio_risk:
  enabled: true
  max_total_exposure_usd: 1000
  max_sector_exposure_pct: 40
  max_theme_exposure_pct: 50
  max_correlated_positions: 3
  max_single_symbol_exposure_pct: 20
  reduce_size_if_high_correlation: true

observability:
  enabled: true
  persist_decision_traces: true
  expose_metrics_endpoint: true
  audit_report_enabled: true

runtime:
  use_event_bus: true
  max_event_queue_size: 10000
  reconciliation_interval_seconds: 45
  scanner_interval_seconds: 60
  forecast_jobs_max_concurrency: 2
  backtest_jobs_max_concurrency: 1
```

---

# 54. Migrations SQLite V2.0

## 54.1 Tables a ajouter

Le developpeur doit ajouter des migrations pour :

```text
opportunities
feature_snapshots
data_quality_events
setup_scores
forecast_runs
forecast_ensembles
backtest_runs
backtest_trades
model_benchmarks
decision_traces
runtime_events
portfolio_snapshots
```

## 54.2 Table runtime events

```sql
CREATE TABLE runtime_events (
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

CREATE INDEX idx_runtime_events_type ON runtime_events(event_type);
CREATE INDEX idx_runtime_events_symbol ON runtime_events(symbol);
CREATE INDEX idx_runtime_events_created ON runtime_events(created_at);
```

## 54.3 Table portfolio snapshots

```sql
CREATE TABLE portfolio_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    total_exposure_usd REAL NOT NULL,
    open_positions_count INTEGER NOT NULL,
    sector_exposure_json TEXT,
    symbol_exposure_json TEXT,
    correlation_json TEXT,
    risk_status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

## 54.4 Regle de migration

```text
Aucune migration ne doit supprimer des donnees sans backup.
Chaque migration doit etre reversible si possible.
Avant migration majeure, creer une copie de data/trading_state.sqlite.
```

---

# 55. Environnements, secrets, backups et CI locale

## 55.1 Environnements

Le projet doit distinguer :

```text
development
simulation
paper
live
```

Chaque environnement doit avoir :

```text
config dediee
base SQLite dediee
logs dedies
mode TWS dedie
risk limits dediees
feature flags dedies
```

Structure :

```text
config/
  config.development.yaml
  config.simulation.yaml
  config.paper.yaml
  config.live.yaml
  field_aliases.yaml
  qualitative_profiles.yaml
```

## 55.2 Secrets

Ne jamais stocker dans git :

```text
identifiants IBKR
tokens Telegram
mots de passe SMTP
cles API donnees marche
cles API LLM
```

Utiliser :

```text
.env
.env.example
```

## 55.3 Backup

Scripts :

```text
scripts/backup_sqlite.py
scripts/export_daily_report.py
scripts/restore_sqlite_backup.py
```

Regles :

```text
backup automatique au demarrage
backup avant migration
backup quotidien apres cloture marche
conserver au moins 30 backups
```

## 55.4 CI locale ou GitHub Actions

```text
.github/workflows/tests.yml
```

Doit executer :

```text
ruff / lint
mypy si active
pytest unitaires
pytest integration sans TWS reel
schema validation
migration dry-run
```

## 55.5 Tests minimaux avant merge

```text
pytest tests/unit
pytest tests/conversion
pytest tests/scoring
pytest tests/data_quality
pytest tests/research
pytest tests/forecasting
```

---

# 56. Roadmap developpeur mise a jour

## Phase 1 — Stabilisation du coeur existant

Objectif : garantir que le moteur actuel est fiable avant d'ajouter l'intelligence.

Livrables :

```text
canonical model builder stable
validation setup_role stable
state machine stable
risk engine stable
order manager paper stable
reconciliation stable
GUI minimale stable
logs exploitables
```

Definition of Done :

```text
Aucun BUY possible sans stop.
Aucun BUY possible depuis MANAGEMENT_ONLY.
STP_LMT utilise limit_price pour le risque.
Setup sauvegardable distinct de setup armable.
Redemarrage + reconciliation testes.
```

## Phase 2 — Data Quality + Feature Store

Livrables :

```text
data_quality_service
feature_store
feature_snapshots SQLite
spread guard
stale data guard
closed candle guard
market session guard
```

Definition of Done :

```text
Le systeme bloque une entree si bid/ask absent, spread trop large ou tick obsolete.
Les setups ne peuvent pas valider une condition de cloture avec une bougie non cloturee.
Les features principales sont visibles dans la GUI.
```

## Phase 3 — Opportunity Scanner simple

Livrables :

```text
watchlist manager
market_context opportunity scanner
context scoring thresholds
momentum breakout scanner
breakout retest scanner
reclaim scanner
liquidity filter
opportunity table
Market Context badges
/opportunities API + scenario draft
```

Definition of Done :

```text
Le scanner produit des opportunites non executables.
Market Context expose opportunity_status, opportunity_type, opportunity_score, reasons, warnings et recommended_next_action.
Les secteurs inconnus generent un warning mais ne bloquent pas la detection.
Une opportunite peut generer un scenario brouillon.
Aucune opportunite ne peut envoyer d'ordre.
```

## Phase 4 — Setup Quality Scoring

Livrables :

```text
technical score
volume score
liquidity score
risk score
trend score
final score
score explanations
```

Definition of Done :

```text
Chaque opportunite affiche une note et une explication.
Un setup techniquement bon mais illiquide est bloque ou degrade.
La GUI classe les opportunites par score.
```

## Phase 5 — Backtest / Replay minimal

Livrables :

```text
historical loader
replay engine
execution simulator
backtest metrics
backtest report
```

Definition of Done :

```text
Un setup peut etre rejoue sur historique.
Le rapport montre win rate, profit factor, max drawdown, stop hit rate et entry missed rate.
Le simulateur gere STP_LMT de maniere conservatrice.
```

## Phase 6 — Forecasting TimesFM + baselines

Livrables :

```text
timesfm_adapter
naive_baseline_adapter
atr_baseline_adapter
forecast_service
forecast cache
forecast result normalizer
```

Definition of Done :

```text
Le forecast apparait dans la fiche setup.
Le forecast modifie uniquement le score.
Le forecast ne peut pas declencher un ordre.
```

## Phase 7 — Forecasting multi-modeles

Livrables :

```text
chronos_adapter
lag_llama_adapter
forecast_ensemble
model agreement
prob_touch_entry
prob_touch_stop_before_entry
```

Definition of Done :

```text
La GUI affiche le consensus multi-modeles.
Le scoring tient compte de l'accord ou du desaccord entre modeles.
Un modele indisponible produit un warning, pas un crash.
```

## Phase 8 — Model Lab

Livrables :

```text
model benchmark runner
walk-forward splitter
model scorecard
baseline comparison
model selection policy
```

Definition of Done :

```text
Un modele est marque utile seulement s'il bat les baselines.
La performance est mesuree par ticker, timeframe et horizon.
La GUI affiche le scorecard.
```

## Phase 9 — Portfolio Risk + Global No-Trade

Livrables :

```text
portfolio exposure analyzer
correlation analyzer
global no-trade status
market regime guard
manage-only mode
```

Definition of Done :

```text
Le bot peut bloquer toutes les nouvelles entrees tout en continuant la gestion des stops.
Le risque cumule portefeuille est visible.
La taille peut etre reduite si correlation elevee.
```

## Phase 10 — Observability et production readiness

Livrables :

```text
event bus
health endpoint
metrics endpoint
decision trace
audit report
backup scripts
migration scripts
CI tests
```

Definition of Done :

```text
Chaque refus d'entree est explicable.
Chaque ordre est relie a une decision trace.
La latence et l'etat TWS sont visibles.
Un backup est cree avant migration.
```

---

# 57. Definition of Done globale pour le developpeur

Une fonctionnalite est consideree terminee uniquement si :

```text
1. le code est modulaire ;
2. les types sont explicites ;
3. les erreurs sont gerees ;
4. les logs sont utiles ;
5. les donnees sont persistantes si necessaire ;
6. la GUI affiche l'etat ou le resultat ;
7. une API REST existe si la fonctionnalite est interactive ;
8. les tests unitaires couvrent les cas normaux et dangereux ;
9. les tests d'integration couvrent le flux principal ;
10. la fonctionnalite ne peut pas envoyer d'ordre sans passer par le risk engine et l'order manager ;
11. la fonctionnalite ne casse pas MANAGEMENT_ONLY ;
12. la fonctionnalite respecte never_lower_stop ;
13. la fonctionnalite produit une decision trace pour toute decision critique ;
14. la documentation du module est ajoutee ou mise a jour.
```

---

# 58. Regles finales V2.0

## 58.1 Regles de securite

```text
Une opportunite n'est pas un ordre.
Un forecast n'est pas un ordre.
Un score eleve n'est pas un ordre.
Un backtest positif n'est pas un ordre.
Un texte analyse par LLM n'est pas un ordre.
Un scenario selectionne n'est pas encore un ordre.
Seul un setup valide, arme, autorise par le risk engine, synchronise avec IBKR et execute par Order Manager peut produire un ordre.
```

## 58.2 Regles de qualite

```text
Pas de donnees fiables = pas de nouvelle entree.
Pas de stop = pas d'entree.
Pas de provenance = pas de valeur executable.
Pas de validation deterministe = pas d'armement.
Pas de reconciliation coherente = pas d'action automatique.
Pas de modele meilleur qu'une baseline = modele non prioritaire.
Pas de decision critique sans trace.
```

## 58.3 Direction finale du projet

Le projet doit evoluer de :

```text
setup-order = moteur de setups et ordres
```

vers :

```text
setup-order = plateforme locale de detection, analyse, scoring, backtest, forecasting et execution controlee de setups.
```

La logique cible est :

```text
Detecter vite.
Filtrer proprement.
Scorer clairement.
Backtester honnetement.
Prevoir prudemment.
Afficher simplement.
Armer explicitement.
Executer uniquement si securise.
Tracer absolument tout.
```

---

# 59. Extension V2.2 — Historique TimesFM et snapshot de creation des setups

## 59.1 Objectif

Cette extension ajoute deux briques indispensables pour rendre l'application mesurable et auditables :

```text
1. Historiser chaque forecast TimesFM et mesurer apres coup s'il etait correct.
2. Capturer le prix et le contexte marche exacts au moment ou un setup est ajoute.
```

Ces deux donnees permettent de repondre a des questions critiques :

```text
TimesFM est-il fiable sur ce symbole ?
TimesFM est-il fiable sur ce timeframe ?
TimesFM est-il meilleur sur 15m, 1h ou 1d ?
Le setup a-t-il ete cree proche du trigger ou deja trop loin ?
Le setup etait-il cree avec un spread normal ou anormal ?
Le setup a-t-il ete ajoute avant ou apres le mouvement principal ?
```

Regle absolue :

```text
Un historique TimesFM positif ne declenche jamais un ordre.
Il ajuste uniquement le score, la confiance et la priorite d'analyse.
```

---

# 60. TimesFM Forecast Accuracy Ledger

## 60.1 Objectif

Le module `Forecast Accuracy Ledger` doit mesurer objectivement la qualite des previsions TimesFM.

Il ne suffit pas d'afficher :

```text
TimesFM forecast_direction = UP
```

Il faut enregistrer :

```text
ce que TimesFM a prevu ;
quand il l'a prevu ;
pour quel horizon ;
quel etait le prix au moment du forecast ;
quel prix reel a ete observe a la fin de l'horizon ;
si la direction etait correcte ;
si l'entree ou le stop auraient ete touches ;
quelle erreur numerique a ete observee ;
quelle fiabilite historique TimesFM a sur ce contexte.
```

## 60.2 Structure de fichiers

```text
app/forecasting/
  accuracy_ledger.py
  forecast_outcome_evaluator.py
  forecast_accuracy_repository.py
  forecast_accuracy_scorecard.py
  forecast_calibration.py
  forecast_outcome_models.py
```

## 60.3 Evenements suivis

Chaque forecast TimesFM doit produire un enregistrement initial :

```text
FORECAST_CREATED
```

Puis, a expiration de l'horizon :

```text
FORECAST_OUTCOME_READY
FORECAST_EVALUATED
FORECAST_SCORECARD_UPDATED
```

## 60.4 Table SQLite `forecast_outcomes`

```sql
CREATE TABLE forecast_outcomes (
    outcome_id TEXT PRIMARY KEY,
    forecast_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    horizon_bars INTEGER NOT NULL,
    forecast_generated_at TEXT NOT NULL,
    forecast_target_time TEXT NOT NULL,

    price_at_forecast REAL NOT NULL,
    predicted_price REAL,
    predicted_direction TEXT,
    predicted_return_pct REAL,
    direction_confidence REAL,

    actual_price_at_horizon REAL,
    actual_return_pct REAL,
    actual_direction TEXT,

    direction_correct INTEGER,
    absolute_error REAL,
    absolute_percentage_error REAL,
    signed_error REAL,

    entry_price_reference REAL,
    stop_price_reference REAL,
    entry_touched_before_horizon INTEGER,
    stop_touched_before_horizon INTEGER,
    stop_touched_before_entry INTEGER,

    quality_bucket TEXT,
    outcome_status TEXT NOT NULL,
    evaluated_at TEXT,
    payload_json TEXT
);

CREATE INDEX idx_forecast_outcomes_model_symbol
ON forecast_outcomes(model_name, symbol, timeframe, horizon_bars);

CREATE INDEX idx_forecast_outcomes_generated_at
ON forecast_outcomes(forecast_generated_at);
```

## 60.5 Table SQLite `forecast_accuracy_scorecards`

```sql
CREATE TABLE forecast_accuracy_scorecards (
    scorecard_id TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    horizon_bars INTEGER NOT NULL,

    sample_size INTEGER NOT NULL,
    direction_accuracy REAL,
    mean_absolute_error REAL,
    mean_absolute_percentage_error REAL,
    median_absolute_error REAL,
    entry_touch_accuracy REAL,
    stop_before_entry_error_rate REAL,
    calibration_score REAL,

    reliability_grade TEXT NOT NULL,
    enough_data INTEGER NOT NULL,
    min_required_samples INTEGER NOT NULL,
    computed_at TEXT NOT NULL,
    payload_json TEXT
);

CREATE UNIQUE INDEX idx_forecast_scorecard_unique
ON forecast_accuracy_scorecards(model_name, symbol, timeframe, horizon_bars);
```

## 60.6 Reliability grade

```text
A  = excellent historique, sample suffisant, direction et erreurs stables.
B  = bon historique, exploitable dans le scoring.
C  = moyen, utiliser uniquement comme signal secondaire.
D  = faible, degrader le poids du forecast.
F  = mauvais, ne pas utiliser dans le scoring sauf pour analyse.
INSUFFICIENT_DATA = pas assez d'historique.
```

Configuration :

```yaml
forecast_accuracy:
  min_required_samples: 30
  grades:
    A:
      min_direction_accuracy: 0.62
      max_mape: 0.04
    B:
      min_direction_accuracy: 0.57
      max_mape: 0.06
    C:
      min_direction_accuracy: 0.52
      max_mape: 0.08
    D:
      min_direction_accuracy: 0.48
      max_mape: 0.12
```

## 60.7 Impact sur le setup quality score

Le score TimesFM ne doit pas etre fixe.

Il doit dependre de sa fiabilite historique :

```text
TimesFM forecast favorable + reliability A/B  → boost clair.
TimesFM forecast favorable + reliability C    → petit boost.
TimesFM forecast favorable + insufficient data → warning, boost faible ou nul.
TimesFM forecast favorable + reliability D/F  → pas de boost, voire degradation.
TimesFM forecast defavorable + reliability A/B → degradation du setup score.
```

Exemple :

```json
{
  "forecast_score": {
    "model": "TimesFM",
    "direction": "UP",
    "confidence": 0.64,
    "historical_reliability_grade": "B",
    "sample_size": 84,
    "direction_accuracy": 0.59,
    "score_impact": "+6"
  }
}
```

## 60.8 API

```text
GET  /api/forecasting/accuracy/timesfm
GET  /api/forecasting/accuracy/{model_name}
GET  /api/forecasting/accuracy/{model_name}/{symbol}
GET  /api/forecasting/accuracy/{model_name}/{symbol}/{timeframe}
POST /api/forecasting/outcomes/evaluate-due
POST /api/forecasting/scorecards/rebuild
GET  /api/forecasting/scorecards/{model_name}/{symbol}
```

## 60.9 GUI

Dans la fiche setup et la page forecasting, afficher :

```text
TimesFM current forecast
TimesFM historical direction accuracy
TimesFM sample size
TimesFM reliability grade
Last 10 forecasts
Forecast outcome status: PENDING / EVALUATED / EXPIRED / INSUFFICIENT_DATA
Impact sur setup_quality_score
```

## 60.10 Tests obligatoires

```text
test_timesfm_forecast_outcome_created
test_forecast_outcome_evaluated_after_horizon
test_direction_correct_computed
test_absolute_error_computed
test_scorecard_requires_minimum_samples
test_reliability_grade_insufficient_data
test_reliability_grade_A_B_C_D_F
test_timesfm_score_impact_depends_on_reliability
test_forecast_accuracy_never_places_order
```

---

# 61. Setup Creation Market Snapshot

## 61.1 Objectif

Quand l'utilisateur ajoute un nouveau setup, le programme doit capturer le prix et le contexte exacts de ce moment.

Ce prix n'est pas le prix d'entree.

C'est le prix de reference historique au moment ou l'idee a ete creee.

Il permet de savoir plus tard :

```text
Le setup a ete ajoute a quel prix ?
Le setup etait-il proche de l'entree ou deja trop eloigne ?
Le spread etait-il acceptable ?
Le setup a-t-il ete cree avant le breakout ou apres le mouvement ?
Le setup a-t-il ete arme beaucoup plus tard que sa creation ?
```

## 61.2 Champs obligatoires dans le setup

Chaque setup doit conserver :

```json
{
  "creation_market_snapshot": {
    "captured_at": "<TIMESTAMP>",
    "symbol": "<SYMBOL>",
    "last_price": 105.42,
    "bid": 105.40,
    "ask": 105.45,
    "mid_price": 105.425,
    "spread_pct": 0.047,
    "volume": 123456,
    "volume_ratio": 1.32,
    "atr_15m": 1.15,
    "atr_1h": 2.80,
    "vwap": 104.90,
    "distance_to_trigger_pct": 0.36,
    "distance_to_stop_pct": 2.90,
    "data_quality_status": "OK",
    "source": "TWS_LIVE_OR_DELAYED"
  }
}
```

## 61.3 Table SQLite `setup_creation_snapshots`

```sql
CREATE TABLE setup_creation_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    setup_id TEXT NOT NULL,
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
    trailing_initial_stop REAL,
    distance_to_trigger_pct REAL,
    distance_to_limit_pct REAL,
    distance_to_stop_pct REAL,

    data_quality_status TEXT NOT NULL,
    data_quality_issues_json TEXT,
    source TEXT NOT NULL,
    payload_json TEXT
);

CREATE INDEX idx_setup_creation_snapshots_setup
ON setup_creation_snapshots(setup_id);

CREATE INDEX idx_setup_creation_snapshots_symbol
ON setup_creation_snapshots(symbol, captured_at);
```

## 61.4 Regles de capture

```text
Creation depuis formulaire GUI      → capturer immediatement.
Creation depuis texte libre         → capturer apres extraction du symbole.
Creation depuis opportunite scanner → reprendre aussi le snapshot de l'opportunite.
Import YAML/JSON                    → capturer au moment de l'import si le symbole est disponible.
Duplication d'un setup              → creer un nouveau snapshot, conserver reference vers l'ancien.
Modification d'un setup             → ne pas ecraser le snapshot de creation ; creer un modification_snapshot separe si necessaire.
```

## 61.5 GUI

Dans la liste et le detail setup, afficher :

```text
Prix a la creation
Prix courant
Variation depuis creation %
Distance creation → trigger
Distance prix courant → trigger
Distance creation → stop
Data quality a la creation
Timestamp de creation
```

Exemple :

```text
Creation price: 105.42
Current price : 108.10
Move since creation: +2.54%
Entry trigger: 109.00
Distance current to trigger: +0.83%
Data quality at creation: OK
```

## 61.6 API

```text
GET /api/setups/{setup_id}/creation-snapshot
GET /api/setups/{setup_id}/price-drift
POST /api/setups/{setup_id}/capture-creation-snapshot
```

## 61.7 Tests obligatoires

```text
test_setup_creation_snapshot_created_on_new_setup
test_snapshot_contains_current_price
test_snapshot_does_not_replace_entry_price
test_snapshot_not_overwritten_on_setup_edit
test_duplicate_setup_creates_new_snapshot
test_creation_snapshot_visible_in_setup_detail
test_missing_market_data_creates_snapshot_with_warning
test_setup_can_be_saved_even_if_snapshot_quality_warning
```

---

# 62. Extension V2.3 — Stack officielle de forecasting et benchmark

## 62.1 Objectif

Cette section definit la stack officielle a integrer progressivement dans le moteur forecasting et le Model Lab.

Stack cible :

```text
TimesFM comme modele deja valide et modele principal.
Chronos comme concurrent direct.
Lag-Llama pour previsions probabilistes avec intervalles d'incertitude.
Darts comme framework de backtest, comparaison et orchestration d'experiences.
NeuralForecast pour NHITS, NBEATS, PatchTST, iTransformer.
AutoGluon-TimeSeries comme baseline AutoML rapide.
Moirai/Uni2TS en phase 2 pour benchmarker les modeles foundation recents.
```

Regle centrale :

```text
Aucun modele de forecasting ne peut envoyer un ordre.
Tous les modeles alimentent uniquement : score, confiance, ranking, rapports et decision trace.
```

## 62.2 Role de chaque composant

| Composant | Role dans setup-order | Priorite | Utilisation principale |
|---|---|---:|---|
| TimesFM | Modele principal deja valide | P0 | Forecast principal, scoring, historique de precision |
| Chronos | Concurrent direct de TimesFM | P1 | Comparaison foundation model, confirmation ou divergence |
| Lag-Llama | Forecast probabiliste | P1 | Quantiles, intervalles, probabilite toucher entry/stop |
| Darts | Framework de comparaison/backtest | P1 | Backtest, walk-forward, comparaison models/setups |
| NeuralForecast | Modeles deep learning classiques/modernes | P2 | NHITS, NBEATS, PatchTST, iTransformer |
| AutoGluon-TimeSeries | Baseline AutoML rapide | P2 | Baseline automatique, sanity check contre TimesFM |
| Moirai/Uni2TS | Foundation models recents | P3 | Benchmark avance phase 2 |

## 62.3 Phasage recommande

### Phase A — Socle deja commence

```text
TimesFM
Naive baseline
ATR baseline
Forecast cache
Forecast result normalizer
Setup quality integration
Forecast Accuracy Ledger
```

Definition of Done :

```text
TimesFM produit des forecasts normalises.
Chaque forecast est historise.
Chaque forecast est evalue apres son horizon.
Le setup score affiche l'impact TimesFM.
TimesFM ne peut pas declencher un ordre.
```

### Phase B — Comparaison directe foundation models

```text
Chronos
Lag-Llama
Darts compare/backtest wrapper
```

Definition of Done :

```text
Chronos peut produire le meme format de sortie que TimesFM.
Lag-Llama produit au moins quantiles et probabilite de toucher entry/stop.
Darts peut comparer TimesFM vs Chronos vs baselines sur un meme dataset.
La GUI affiche agreement/disagreement entre TimesFM, Chronos et Lag-Llama.
```

### Phase C — Baselines avancees

```text
NeuralForecast
AutoGluon-TimeSeries
```

Definition of Done :

```text
NHITS, NBEATS, PatchTST et iTransformer sont accessibles via adapter NeuralForecast si le package est installe.
AutoGluon produit une baseline rapide sur un dataset donne.
Le Model Lab indique si ces modeles battent les baselines simples et TimesFM.
```

### Phase D — Foundation benchmark phase 2

```text
Moirai / Uni2TS
```

Definition of Done :

```text
Moirai/Uni2TS est optionnel.
Son absence ne doit pas casser l'application.
Il est utilise uniquement en benchmark et scoring experimental jusqu'a validation.
```

---

# 63. Architecture d'integration de la stack forecasting

## 63.1 Structure de fichiers

```text
app/forecasting/
  base_forecaster.py
  forecast_models.py
  forecast_registry.py
  forecast_service.py
  forecast_request_builder.py
  forecast_result_normalizer.py
  forecast_cache.py
  forecast_ensemble.py
  forecast_confidence.py
  forecast_evaluator.py
  forecast_stack_config.py
  forecast_provider_status.py

  adapters/
    timesfm_adapter.py
    chronos_adapter.py
    lag_llama_adapter.py
    darts_adapter.py
    neuralforecast_adapter.py
    autogluon_adapter.py
    moirai_uni2ts_adapter.py
    naive_baseline_adapter.py
    atr_baseline_adapter.py
    trend_baseline_adapter.py

app/model_lab/
  darts_experiment_runner.py
  forecast_stack_benchmark.py
  model_comparison_service.py
  model_scorecard_service.py
  model_selection_policy.py
  model_drift_detector.py
```

## 63.2 Interface commune obligatoire

Tous les adapters doivent retourner le meme format interne.

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ForecastModelCapabilities:
    model_name: str
    supports_point_forecast: bool
    supports_quantiles: bool
    supports_probabilistic_paths: bool
    supports_covariates: bool
    supports_zero_shot: bool
    requires_training: bool
    requires_local_model_path: bool
    installed: bool
    available: bool
    unavailable_reason: str | None


@dataclass(frozen=True)
class NormalizedForecastResult:
    model_name: str
    symbol: str
    timeframe: str
    horizon_bars: int
    generated_at: str
    status: str
    point_forecast: list[float] | None
    quantiles: dict[str, list[float]] | None
    prediction_intervals: dict[str, Any] | None
    direction: str
    direction_confidence: float | None
    expected_return_pct: float | None
    prob_touch_entry: float | None
    prob_touch_stop_before_entry: float | None
    warnings: list[str]
    raw_output_ref: str | None
```

## 63.3 Gestion des dependances optionnelles

Regle :

```text
Aucun package forecasting externe ne doit etre obligatoire pour lancer l'application.
Si un package n'est pas installe, l'adapter retourne MODEL_NOT_INSTALLED avec warning.
Si un model_path est absent, l'adapter retourne MODEL_NOT_CONFIGURED avec warning.
Le runtime trading ne doit jamais crasher a cause d'un modele forecasting indisponible.
```

Statuts providers :

```text
AVAILABLE
MODEL_NOT_INSTALLED
MODEL_NOT_CONFIGURED
MODEL_NOT_LOADED
MODEL_ERROR
DISABLED_BY_CONFIG
EXPERIMENTAL_ONLY
```

## 63.4 Configuration `config.yaml`

```yaml
forecast_stack:
  enabled: true
  execution_mode: "scoring_only"

  primary_model: "timesfm"

  active_models:
    - "timesfm"
    - "naive_baseline"
    - "atr_baseline"

  comparison_models:
    - "chronos"
    - "lag_llama"

  advanced_models:
    - "neuralforecast"
    - "autogluon"

  experimental_models:
    - "moirai_uni2ts"

  providers:
    timesfm:
      enabled: true
      priority: 0
      role: "primary"
      use_for_setup_score: true
      use_for_execution: false

    chronos:
      enabled: false
      auto_enable_when_ready: true
      priority: 1
      role: "direct_competitor"
      use_for_setup_score: true
      use_for_execution: false

    lag_llama:
      enabled: false
      auto_enable_when_ready: true
      priority: 1
      role: "probabilistic"
      use_for_setup_score: true
      use_for_execution: false

    darts:
      enabled: false
      auto_enable_when_ready: true
      priority: 1
      role: "benchmark_framework"
      use_for_runtime_forecast: false
      use_for_model_lab: true

    neuralforecast:
      enabled: false
      auto_enable_when_ready: true
      priority: 2
      role: "deep_learning_models"
      models: ["NHITS", "NBEATS", "PatchTST", "iTransformer"]
      use_for_model_lab: true

    autogluon:
      enabled: false
      auto_enable_when_ready: true
      priority: 2
      role: "automl_baseline"
      use_for_model_lab: true

    moirai_uni2ts:
      enabled: false
      auto_enable_when_ready: true
      priority: 3
      role: "experimental_foundation_benchmark"
      use_for_model_lab: true
      use_for_runtime_forecast: false

  horizons:
    "15m": [4, 8, 16]
    "1h": [4, 8, 24]
    "1d": [3, 5, 10]

  safety:
    block_order_from_forecast: true
    use_forecast_for_scoring_only: true
    require_accuracy_history_before_score_boost: true
    min_accuracy_samples_for_boost: 30
```

---

# 64. Model Lab avec Darts comme comparateur central

## 64.1 Objectif

Darts doit servir de couche de comparaison et d'experimentation, pas de moteur d'ordre.

Il doit repondre a :

```text
Quel modele marche le mieux sur ce ticker ?
Quel modele marche le mieux sur ce timeframe ?
Quel modele bat la baseline naive ?
Quel modele donne de bons signaux directionnels mais mauvais PnL ?
Quel modele est utile pour filtrer les setups ?
```

## 64.2 Workflow Darts

```text
1. Construire un dataset OHLCV propre depuis SQLite.
2. Selectionner symboles, timeframes et horizon.
3. Lancer TimesFM, Chronos, Lag-Llama, baselines et modeles optionnels.
4. Normaliser toutes les sorties.
5. Comparer MAE/RMSE/direction_accuracy/entry_touch/stop_before_entry.
6. Lancer un backtest setup-aware.
7. Produire un scorecard par modele.
8. Mettre a jour model_selection_policy.
```

## 64.3 API Model Lab

```text
POST /api/model-lab/forecast-stack/compare
POST /api/model-lab/forecast-stack/walk-forward
POST /api/model-lab/forecast-stack/run-native
GET  /api/model-lab/forecast-stack/results
GET  /api/model-lab/forecast-stack/scorecard/{symbol}
GET  /api/model-lab/forecast-stack/providers
POST /api/model-lab/darts/run-experiment
GET  /api/model-lab/darts/experiments/{experiment_id}
```

## 64.4 Table SQLite `forecast_stack_experiments`

```sql
CREATE TABLE forecast_stack_experiments (
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

CREATE TABLE forecast_stack_results (
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
```

## 64.5 Metrics obligatoires

Forecast metrics :

```text
MAE
RMSE
MAPE
direction_accuracy
quantile_coverage
calibration_error
brier_score_touch_entry
brier_score_touch_stop
```

Trading-aware metrics :

```text
entry_touch_accuracy
stop_before_entry_error_rate
false_positive_rate
forecast_filter_pnl_delta
forecast_filter_drawdown_delta
setup_score_improvement
missed_good_setup_rate
```

## 64.6 Politique de selection

Un modele ne devient pas prioritaire uniquement parce qu'il est moderne.

Politique :

```text
1. Le modele doit battre naive_baseline.
2. Le modele doit battre ATR baseline sur au moins une metrique utile.
3. Le modele doit avoir assez d'echantillons.
4. Le modele ne doit pas degrader le PnL simule quand utilise comme filtre.
5. La selection est specifique par symbole + timeframe + horizon.
```

---

# 65. Integration specifique des modeles de la stack

## 65.1 TimesFM

Role :

```text
Modele principal deja valide.
Reference runtime pour forecast directionnel.
Premier modele connecte au Forecast Accuracy Ledger.
```

Exigences :

```text
forecast normalise
cache obligatoire
historique obligatoire
scorecard obligatoire
pas de recalcul automatique lourd a l'ouverture d'une page GUI
bouton Recalculer explicite
```

## 65.2 Chronos

Role :

```text
Concurrent direct de TimesFM.
Permet de confirmer ou contredire TimesFM.
```

Interpretation :

```text
TimesFM UP + Chronos UP       → meilleur consensus.
TimesFM UP + Chronos DOWN     → divergence, demander confirmation supplementaire.
TimesFM faible + Chronos fort → signal experimental, pas de decision automatique.
```

## 65.3 Lag-Llama

Role :

```text
Modele probabiliste.
Produit intervalles, chemins probables, quantiles et probabilite de toucher entry/stop.
```

Sortie attendue :

```json
{
  "model_name": "lag_llama",
  "quantiles": {
    "0.10": [100.2, 100.8],
    "0.50": [101.1, 102.0],
    "0.90": [103.5, 104.4]
  },
  "prob_touch_entry": 0.64,
  "prob_touch_stop_before_entry": 0.22,
  "uncertainty_width_pct": 3.1
}
```

## 65.4 Darts

Role :

```text
Framework de backtest, comparaison, walk-forward et orchestration d'experiences.
```

Darts ne doit pas etre appele dans la boucle temps reel critique.

Utilisation :

```text
jobs offline
backtest/replay
comparaison modele contre modele
rapports model lab
```

## 65.5 NeuralForecast

Role :

```text
Ajouter des modeles deep learning benchmarkables : NHITS, NBEATS, PatchTST, iTransformer.
```

Regle :

```text
NeuralForecast est P2.
Il ne doit pas bloquer le MVP.
Il doit etre execute principalement dans Model Lab jusqu'a validation.
```

## 65.6 AutoGluon-TimeSeries

Role :

```text
Baseline AutoML rapide.
Permet de verifier si une approche automatique simple bat TimesFM ou les baselines.
```

Regle :

```text
AutoGluon peut etre lent.
Il doit fonctionner en job separe.
Il ne doit pas bloquer la GUI ni le runtime TWS.
```

## 65.7 Moirai / Uni2TS

Role :

```text
Phase 2 experimental foundation benchmark.
A utiliser apres stabilisation TimesFM + Chronos + Lag-Llama + Darts.
```

Regle :

```text
Moirai/Uni2TS ne doit pas influencer le score live tant qu'il n'a pas une scorecard suffisante.
```

---

# 66. GUI pour la forecasting stack

## 66.1 Page `/forecasting/stack`

Afficher :

```text
Model name
Role
Priority
Status
Installed
Configured
Last run
Last error
Use for scoring
Use for model lab
Reliability grade
Sample size
```

Actions :

```text
Enable / Disable
Run test forecast
Run benchmark
View scorecard
View last forecasts
View errors
```

## 66.2 Page `/model-lab/forecast-stack`

Afficher :

```text
Comparaison TimesFM vs Chronos vs Lag-Llama vs baselines
Classement par symbole
Classement par timeframe
Classement par horizon
Direction accuracy
Error metrics
Trading-aware metrics
Selected model policy
```

## 66.3 Fiche setup

Dans chaque fiche setup, afficher un bloc :

```text
Forecast stack summary
- TimesFM: direction, confidence, reliability grade
- Chronos: direction, confidence, availability
- Lag-Llama: prob_touch_entry, prob_touch_stop_before_entry, interval width
- Consensus: UP / DOWN / MIXED / INSUFFICIENT_DATA
- Score impact: +X / -X
```

---

# 67. Tests obligatoires pour la stack forecasting

## 67.1 Tests adapters

```text
test_timesfm_adapter_available_when_configured
test_chronos_adapter_returns_normalized_result
test_lag_llama_adapter_returns_quantiles
test_darts_adapter_not_used_for_runtime_order
test_neuralforecast_adapter_optional_dependency
test_autogluon_adapter_optional_dependency
test_moirai_uni2ts_marked_experimental
test_missing_optional_package_returns_warning_not_crash
```

## 67.2 Tests ensemble/scoring

```text
test_timesfm_chronos_agreement_boosts_score
test_timesfm_chronos_disagreement_creates_warning
test_lag_llama_high_stop_probability_degrades_score
test_model_without_accuracy_history_cannot_strongly_boost_score
test_forecast_stack_never_places_order
test_forecast_stack_not_called_when_setup_management_only_for_entry
```

## 67.3 Tests Model Lab

```text
test_darts_experiment_created
test_forecast_stack_comparison_saves_results
test_model_must_beat_naive_baseline
test_model_selection_policy_per_symbol_timeframe_horizon
test_walk_forward_no_data_leakage
test_experimental_model_not_selected_without_scorecard
```

---

# 68. Roadmap operationnelle V2.3

## 68.1 Next step immediat

Le prochain developpement doit se faire dans cet ordre :

```text
1. Finaliser Forecast Accuracy Ledger pour TimesFM.
2. Ajouter Setup Creation Market Snapshot.
3. Ajouter provider status pour tous les modeles de la stack.
4. Ajouter Chronos adapter en option.
5. Ajouter Lag-Llama adapter en option.
6. Ajouter Darts experiment runner pour comparer TimesFM vs baselines.
7. Brancher les resultats dans Setup Quality Score.
8. Ajouter GUI `/forecasting/stack` et `/model-lab/forecast-stack`.
```

## 68.2 Ce qu'il ne faut pas faire maintenant

```text
Ne pas brancher tous les modeles dans la boucle live en meme temps.
Ne pas laisser un modele experimental influencer l'execution.
Ne pas recalculer les forecasts lourds a chaque refresh GUI.
Ne pas considerer un modele meilleur sans baseline et scorecard.
Ne pas utiliser Darts comme composant critique temps reel.
```

## 68.3 Definition of Done V2.3

```text
La stack est visible dans la GUI.
Chaque modele a un statut clair : available, missing, disabled, experimental.
TimesFM possede un historique de precision.
Chronos et Lag-Llama peuvent etre ajoutes sans casser le runtime si absents.
Darts peut lancer une experience offline.
Le setup score utilise seulement les modeles ayant assez d'historique.
Aucun modele ne peut envoyer un ordre.
program.md et les fichiers docs sont mis a jour a chaque livraison.
```

---

# 69. Regle de gouvernance documentaire V2.3

A chaque changement de module, le developpeur doit mettre a jour :

```text
program.md
docs/change-log.md
docs/implementation-status.md
docs/module-roadmap.md
docs/known-limitations.md
docs/manual-test-checklist.md
```

Le haut de `program.md` doit toujours indiquer :

```text
ce qui est implemente ;
ce qui est partiel ;
ce qui est prevu ;
les limites connues ;
le prochain module recommande.
```

Regle finale :

```text
Une fonctionnalite qui n'est pas documentee, testee et tracee n'est pas consideree terminee.
```
