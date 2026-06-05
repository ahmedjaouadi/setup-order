# program.md — Spécification détaillée du programme d’automatisation de setups de trading avec Interactive Brokers TWS

Version : 1.7  
Statut : Document de conception générique, corrigé et aligné  
Objectif : Définir une architecture modulaire, évolutive, performante et maintenable pour automatiser différents types de setups de trading via Interactive Brokers TWS, avec interface GUI HTML, stockage sur fichier et suivi complet des setups actifs.

## Historique des corrections — versions 1.1 à 1.7

Ce document intègre les corrections structurantes suivantes :

- séparation explicite entre un setup de **nouvelle entrée** et un setup de **gestion d’une position déjà ouverte** ;
- ajout de `setup_role` avec les rôles `ENTRY_AND_MANAGEMENT`, `ENTRY_ONLY` et `MANAGEMENT_ONLY` ;
- ajout du mécanisme `adopt_existing_ibkr_position` ;
- interdiction de placer un nouvel ordre d’achat depuis un setup `MANAGEMENT_ONLY` ;
- ajout des états `RECONCILING_EXISTING_POSITION`, `MANUAL_REVIEW_REQUIRED` et `ERROR_REQUIRES_MANUAL_REVIEW` ;
- calcul de quantité corrigé pour les ordres `STP_LMT` : utilisation du **prix limite maximal** et non du trigger ;
- affichage GUI corrigé : séparation entre trigger, limite maximale, stop, rôle du setup, quantité maximale et risque maximal ;
- règles de sécurité ajoutées lorsqu’une position existe chez IBKR mais que l’état local est absent ou incohérent ;
- ajout d’exemples complets pour la gestion d’une position existante et pour une nouvelle entrée momentum breakout ;
- suppression des exemples liés à un ticker réel ;
- ajout d’un moteur générique d’ajustements déclaratifs : aucune règle Python ne doit contenir un symbole ou un niveau de prix spécifique ;
- remplacement des exemples opérationnels par des paramètres `<SYMBOL>`, `<PRICE_LEVEL>` et `<RISK_LIMIT>`.

---

# 1. Objectif général du programme

Le programme doit permettre de transformer des setups de trading décrits sous forme de règles en un système automatisé capable de :

- se connecter à Interactive Brokers TWS ou IB Gateway ;
- surveiller plusieurs actions simultanément ;
- gérer plusieurs types de setups ;
- distinguer les setups destinés à ouvrir une nouvelle position des setups destinés uniquement à gérer une position déjà ouverte ;
- détecter les conditions d’entrée ;
- placer automatiquement des ordres uniquement lorsque le rôle du setup l’autorise ;
- associer un stop-loss de protection ;
- adopter et suivre une position existante chez IBKR sans déclencher un nouvel achat ;
- remonter le stop-loss selon des règles définies ;
- enregistrer toutes les données importantes dans des fichiers ;
- afficher l’état du système dans une interface GUI HTML ;
- permettre à l’utilisateur de savoir quel setup est actif, en attente, exécuté, invalidé ou terminé ;
- être extensible pour ajouter plus tard de nouveaux types de setups, indicateurs, stratégies, alertes ou modules IA.

Le programme ne doit pas être un simple script d’achat automatique. Il doit être conçu comme une plateforme modulaire de gestion de setups.

---

# 2. Principes fondamentaux

## 2.1 Priorité à la sécurité

Le programme doit toujours protéger le capital avant de chercher la performance.

Règles obligatoires :

- aucun ordre d’entrée ne doit être envoyé sans règle de stop-loss définie ;
- aucun setup `MANAGEMENT_ONLY` ne doit envoyer un ordre d’entrée ;
- après exécution d’une entrée, un stop-loss réel doit être placé chez IBKR ;
- lors de l’adoption d’une position existante, le programme doit vérifier la quantité réelle, le stop existant et l’état de marché avant toute action ;
- le programme ne doit jamais baisser un stop-loss déjà remonté ;
- le programme doit empêcher les ordres dupliqués ;
- le programme doit vérifier les positions réelles chez IBKR avant toute décision importante ;
- le programme doit bloquer le trading si les données de marché sont trop anciennes ;
- le programme doit bloquer le trading si la connexion TWS est instable ;
- le programme doit respecter une limite de perte maximale journalière ;
- le programme doit permettre un mode `paper trading` obligatoire pendant les tests.

## 2.2 Modularité

Chaque responsabilité doit être isolée dans un module dédié.

Le programme ne doit pas mélanger :

- la connexion TWS ;
- la logique de setup ;
- la gestion des ordres ;
- la gestion du risque ;
- l’interface GUI ;
- le stockage sur fichier ;
- les logs ;
- les alertes.

Chaque module doit pouvoir évoluer indépendamment.

## 2.3 Évolutivité

Le programme doit pouvoir commencer avec une seule action et évoluer vers :

- plusieurs actions ;
- plusieurs setups par action ;
- plusieurs timeframes ;
- plusieurs types d’ordres ;
- plusieurs règles de sortie ;
- plusieurs comptes IBKR ;
- plusieurs sources de données ;
- un futur moteur IA ou scanner automatique.

## 2.4 Traçabilité complète

Chaque décision du programme doit être enregistrée.

Exemples :

- setup chargé ;
- condition détectée ;
- condition refusée ;
- ordre envoyé ;
- ordre accepté ;
- ordre rejeté ;
- entrée exécutée ;
- stop-loss placé ;
- stop-loss modifié ;
- position fermée ;
- erreur TWS ;
- reconnexion ;
- synchronisation avec IBKR ;
- changement manuel via GUI.

---

# 3. Architecture globale recommandée

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

# 4. Technologies recommandées

## 4.1 Langage principal

Langage recommandé :

```text
Python 3.11 ou supérieur
```

Raisons :

- très bon écosystème trading ;
- compatible avec l’API Interactive Brokers ;
- facile à connecter à une interface web ;
- adapté au prototypage rapide ;
- extensible vers IA, backtesting, analyse technique et data science.

## 4.2 Connexion Interactive Brokers

Options possibles :

| Option | Avantage | Inconvénient |
|---|---|---|
| API officielle IBKR | Plus robuste, support officiel | Plus verbeuse |
| ib_async | Moderne, proche de ib_insync | Librairie tierce |
| ib_insync | Simple et connue | Projet original moins maintenu |

Recommandation :

- V1 : `ib_async` ou `ib_insync` si l’utilisateur maîtrise déjà cette bibliothèque ;
- pour une version de production sérieuse : prévoir une couche d’abstraction pour pouvoir migrer vers l’API officielle sans réécrire toute l’application.

Important : le code métier ne doit jamais dépendre directement de `ib_insync` à travers toute l’application. Créer un module `broker_connector`.

## 4.3 Backend web

Recommandation :

```text
FastAPI
```

Raisons :

- rapide ;
- moderne ;
- compatible WebSocket ;
- très bon pour une interface en temps réel ;
- documentation automatique ;
- séparation claire API / moteur de trading.

## 4.4 Interface GUI HTML

Options :

| Option | Usage |
|---|---|
| HTML + CSS + JavaScript simple | V1 rapide |
| React | V2 plus avancée |
| Bootstrap / Tailwind | Interface propre rapidement |
| WebSocket | Mise à jour temps réel |

Recommandation V1 :

```text
FastAPI + HTML + JavaScript + WebSocket
```

Recommandation V2 :

```text
FastAPI + React + WebSocket
```

## 4.5 Stockage des données

L’utilisateur souhaite un stockage dans un fichier.

Deux options possibles :

### Option simple

```text
Fichiers JSON / CSV
```

Avantage :

- simple ;
- lisible ;
- facile à sauvegarder ;
- suffisant pour V1.

Inconvénient :

- moins robuste ;
- risque de corruption si plusieurs écritures simultanées ;
- requêtes moins pratiques.

### Option recommandée

```text
SQLite
```

SQLite reste un fichier local, mais offre :

- transactions ;
- tables structurées ;
- requêtes fiables ;
- meilleure robustesse ;
- meilleur suivi historique ;
- facile à sauvegarder.

Recommandation finale :

```text
Utiliser SQLite comme fichier principal de stockage.
Utiliser JSON/YAML pour les fichiers de configuration des setups.
Utiliser CSV uniquement pour les exports.
```

Structure recommandée :

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

# 5. Structure recommandée du projet

```text
trading-bot/
│
├── program.md
├── README.md
├── requirements.txt
├── config.yaml
│
├── app/
│   ├── main.py
│   ├── settings.py
│   │
│   ├── broker/
│   │   ├── tws_connector.py
│   │   ├── ib_models.py
│   │   ├── order_mapper.py
│   │   └── broker_errors.py
│   │
│   ├── engine/
│   │   ├── trading_engine.py
│   │   ├── setup_engine.py
│   │   ├── state_machine.py
│   │   ├── rule_engine.py
│   │   ├── action_executor.py
│   │   ├── signal_engine.py
│   │   ├── risk_engine.py
│   │   ├── position_manager.py
│   │   ├── order_manager.py
│   │   ├── reconciliation.py
│   │   └── adoption_service.py
│   │
│   ├── setups/
│   │   ├── base_setup.py
│   │   ├── setup_roles.py
│   │   ├── aggressive_rebound.py
│   │   ├── breakout_retest.py
│   │   ├── momentum_breakout.py
│   │   ├── pullback_continuation.py
│   │   ├── position_management.py
│   │   ├── trailing_runner.py
│   │   └── setup_factory.py
│   │
│   ├── market_data/
│   │   ├── market_data_service.py
│   │   ├── candle_builder.py
│   │   ├── indicators.py
│   │   └── timeframe_manager.py
│   │
│   ├── storage/
│   │   ├── database.py
│   │   ├── repositories.py
│   │   ├── event_store.py
│   │   └── file_exporter.py
│   │
│   ├── api/
│   │   ├── routes_dashboard.py
│   │   ├── routes_setups.py
│   │   ├── routes_orders.py
│   │   ├── routes_positions.py
│   │   └── websocket.py
│   │
│   ├── gui/
│   │   ├── templates/
│   │   │   ├── index.html
│   │   │   ├── setups.html
│   │   │   ├── positions.html
│   │   │   ├── orders.html
│   │   │   └── logs.html
│   │   │
│   │   └── static/
│   │       ├── css/
│   │       ├── js/
│   │       └── img/
│   │
│   ├── alerts/
│   │   ├── alert_manager.py
│   │   ├── telegram_alert.py
│   │   └── email_alert.py
│   │
│   └── utils/
│       ├── logger.py
│       ├── clock.py
│       ├── validators.py
│       └── id_generator.py
│
├── data/
│   ├── trading_state.sqlite
│   ├── setups/
│   ├── exports/
│   └── logs/
│
└── tests/
    ├── test_setups.py
    ├── test_risk_engine.py
    ├── test_order_manager.py
    └── test_state_machine.py
```

---

# 6. Concepts principaux du programme

## 6.1 Setup

Un setup représente une règle de trading complète, mais il ne signifie pas forcément « acheter ».

Un setup peut avoir l’un des rôles suivants :

```text
ENTRY_AND_MANAGEMENT
ENTRY_ONLY
MANAGEMENT_ONLY
```

Le rôle doit être stocké dans :

```yaml
setup_role: "ENTRY_AND_MANAGEMENT"
```

### `ENTRY_AND_MANAGEMENT`

Utilisation :

```text
Détecter une opportunité
→ placer une entrée
→ placer le stop initial
→ gérer la position jusqu’à la clôture
```

### `ENTRY_ONLY`

Utilisation :

```text
Détecter une opportunité
→ placer une entrée protégée
→ transférer ensuite la position vers un autre module de gestion
```

Ce mode est facultatif en V1.

### `MANAGEMENT_ONLY`

Utilisation :

```text
Adopter une position déjà existante chez IBKR
→ vérifier la quantité réelle
→ vérifier le stop réel
→ gérer le stop et les objectifs
→ ne jamais envoyer de nouvel ordre d’achat
```

Ce mode est indispensable pour gérer correctement une position déjà ouverte avant le démarrage du programme ou ouverte manuellement dans TWS.

## 6.2 Contenu obligatoire d’un setup

Un setup doit contenir :

- identifiant unique ;
- symbole ;
- type de setup ;
- rôle du setup ;
- direction ;
- mode `simulation`, `paper` ou `live` ;
- conditions d’activation ;
- conditions d’entrée si le rôle autorise une entrée ;
- conditions d’invalidation ;
- stop-loss initial ou stop protecteur ;
- règles de gestion du stop ;
- objectifs informatifs ou take-profit ;
- taille maximale ;
- risque maximal ;
- statut ;
- historique des événements.

Exemple générique de setup d’entrée :

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
  initial_stop_loss: <INITIAL_STOP_LOSS>

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

## 6.3 Setup de gestion d’une position existante

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

Règle obligatoire :

```text
MANAGEMENT_ONLY → aucun BUY autorisé
```

Le programme doit refuser le chargement si :

```text
setup_role = MANAGEMENT_ONLY
et
entry.enabled = true
```

## 6.4 Setup actif

Un setup est considéré comme actif lorsqu’il est chargé, validé, activé et suivi par le moteur.

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

Une position est la détention réelle d’un actif chez IBKR.

La position réelle doit toujours être synchronisée avec IBKR.

Le programme ne doit jamais se baser uniquement sur son fichier local pour supposer qu’une position existe.

Lorsqu’une position est adoptée, le programme doit enregistrer :

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

Un ordre correspond à une instruction envoyée à IBKR.

Types d’ordres à gérer en V1 :

```text
BUY MKT
BUY LMT
BUY STP
BUY STP LMT
SELL STP
SELL LMT
SELL TRAIL
```

Types à gérer en V2 :

```text
BRACKET
OCA
PARTIAL TAKE PROFIT
TRAILING CUSTOM
```

---

# 7. Moteur de setups

## 7.1 Rôle du Setup Engine

Le `Setup Engine` doit :

- charger les setups ;
- vérifier la validité de leur configuration ;
- créer le bon type de stratégie ;
- appliquer le rôle du setup avant toute décision ;
- adopter une position IBKR existante pour les setups `MANAGEMENT_ONLY` ;
- suivre l’état de chaque setup ;
- déclencher les transitions d’état ;
- demander au module `Order Manager` de placer les ordres ;
- demander au module `Risk Engine` de calculer la quantité ;
- enregistrer chaque événement.

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
- séparation claire entre les stratégies ;
- code plus maintenable ;
- meilleure testabilité.

---

# 8. Types de setups à prévoir

Le programme doit être conçu pour gérer plusieurs types de setups.

## 8.1 Setup agressif sur rebond de support

Objectif :

Acheter proche d’une zone de support lorsque le prix montre un rebond confirmé.

Exemple humain :

```text
Entrée agressive seulement si le prix tient 12.50–13.00 $ et montre un rebond clair.
```

Règles automatisables :

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

États :

```text
WAITING_PRICE_IN_ZONE
WAITING_REBOUND_CONFIRMATION
ENTRY_ORDER_PLACED
IN_POSITION
INVALIDATED
```

## 8.2 Setup breakout + retest

Objectif :

Acheter après cassure d’une résistance puis retour contrôlé sur l’ancienne résistance devenue support.

Exemple humain :

```text
Attendre une clôture au-dessus de 14.10–14.50 $, puis idéalement un retest réussi.
```

Règles automatisables :

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

États :

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

Acheter une action déjà haussière lors d’un retour vers une moyenne mobile, VWAP ou zone de prix.

Règles possibles :

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

Ce type de setup sert à une **nouvelle entrée**. Il ne doit pas être utilisé pour gérer une position déjà ouverte.

Règles possibles :

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
  initial_stop_loss: <INITIAL_STOP_LOSS>
  max_position_amount_usd: <MAX_POSITION_AMOUNT_USD>
  max_risk_usd: <MAX_RISK_USD>
```

Formules génériques :

```text
trigger_price = resistance + trigger_offset
limit_price   = trigger_price + limit_offset
```

## 8.5 Setup range breakout

Objectif :

Acheter la sortie d’une boîte de consolidation.

Règles possibles :

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

Le runner est une **politique de gestion**, pas nécessairement une stratégie d’entrée autonome.

Règles possibles :

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

## 8.7 Setup de gestion d’une position existante

Objectif :

Adopter une position déjà présente chez IBKR et la gérer sans créer une nouvelle entrée.

Règles possibles :

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

risk:
  protective_stop: <INITIAL_PROTECTIVE_STOP>
  emergency_exit_if_stop_fails: true
  if_market_price_below_stop: "MANUAL_REVIEW_REQUIRED"
```

---

# 9. Machine à états

Chaque setup doit fonctionner avec une machine à états.

## 9.1 Pourquoi utiliser une machine à états

Une machine à états permet de savoir exactement où se trouve chaque setup.

Exemple :

```text
Le prix n’a pas encore cassé la résistance.
Le breakout est confirmé.
Le retest est en cours.
Le signal d’entrée est validé.
L’ordre d’entrée est placé.
L’entrée est exécutée.
Le stop est placé.
La position est en gestion.
La position est fermée.
```

Sans machine à états, le programme risque :

- d’acheter deux fois ;
- d’oublier un stop ;
- de mal interpréter un retest ;
- de mélanger plusieurs setups ;
- de perdre le suivi après redémarrage.

## 9.2 États globaux recommandés

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
  ↓
VALIDATED
  ↓
WAITING_BREAKOUT
  ↓ clôture journalière > niveau de cassure
WAITING_RETEST
  ↓ price returns to retest zone
WAITING_CONFIRMATION
  ↓ bullish candle confirmed
ENTRY_READY
  ↓ risk approved
ENTRY_ORDER_PLACED
  ↓ order filled
ENTRY_FILLED
  ↓ stop accepted by IBKR
STOP_ORDER_PLACED
  ↓
IN_POSITION
  ↓
MANAGING_POSITION
  ↓ stop hit or manual exit
CLOSED
```

## 9.4 Transitions interdites

Le programme doit interdire certaines transitions dangereuses :

```text
WAITING_BREAKOUT → IN_POSITION
ENTRY_FILLED → CLOSED sans STOP_ORDER_PLACED
IN_POSITION → ENTRY_ORDER_PLACED
CLOSED → IN_POSITION
INVALIDATED → ENTRY_ORDER_PLACED
MANAGEMENT_ONLY → ENTRY_ORDER_PLACED
MANAGEMENT_ONLY → BUY_ORDER_PLACED
```

## 9.5 Machine à états pour une position existante

```text
LOADED
  ↓
VALIDATED
  ↓
RECONCILING_EXISTING_POSITION
  ↓ position trouvée chez IBKR et cohérente
IN_POSITION
  ↓
MANAGING_POSITION
  ↓ stop hit ou sortie manuelle
CLOSED
```

Cas d’erreur :

```text
RECONCILING_EXISTING_POSITION
  ↓ position introuvable, stop absent, quantité incohérente ou prix déjà sous le stop demandé
MANUAL_REVIEW_REQUIRED
```

Le setup ne doit pas acheter pour « corriger » une position introuvable.

---

# 10. Market Data

## 10.1 Rôle du module Market Data

Le module `Market Data` doit :

- récupérer les prix temps réel ou différés ;
- construire les bougies ;
- gérer plusieurs timeframes ;
- détecter les données obsolètes ;
- sauvegarder les prix utiles ;
- fournir les données au moteur de signaux.

## 10.2 Timeframes à gérer

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

## 10.3 Données minimales d’une bougie

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

## 10.4 Règle importante : ne pas utiliser une bougie non clôturée pour une condition de clôture

Exemple :

```text
Condition : clôture journalière > 14.50
```

Le programme doit attendre la clôture réelle de la bougie journalière.

Il ne doit pas valider la condition parce que le prix intrajournalier est temporairement au-dessus de 14.50.

## 10.5 Données obsolètes

Le programme doit bloquer les décisions si :

```text
dernier prix reçu > 20 secondes
```

ou, pour les bougies :

```text
dernière bougie attendue non reçue
```

Règle :

```text
Pas de données fiables = pas de nouvel ordre.
```

---

# 11. Signal Engine

## 11.1 Rôle

Le `Signal Engine` transforme les données de marché en signaux exploitables.

Il ne doit pas envoyer d’ordres directement.

Il retourne uniquement :

```text
SIGNAL_VALID
SIGNAL_INVALID
SIGNAL_PENDING
SIGNAL_ERROR
```

## 11.2 Exemple de détection d’un rebond

Un rebond peut être défini par :

```text
1. le prix touche la zone de support ;
2. aucune clôture 15m ne passe sous la borne basse ;
3. une bougie haussière se forme ;
4. la clôture passe au-dessus du plus haut de la bougie précédente ;
5. le volume n’est pas anormalement faible.
```

## 11.3 Exemple de détection d’un retest réussi

Un retest réussi peut être défini par :

```text
1. breakout validé précédemment ;
2. le prix revient dans la zone de retest ;
3. le prix ne clôture pas sous la zone ;
4. une bougie de réaction haussière apparaît ;
5. l’entrée est placée au-dessus du plus haut de la bougie de confirmation.
```

## 11.4 Signaux interdits

Le moteur doit refuser un signal si :

- le marché est fermé ;
- les données sont obsolètes ;
- le setup est désactivé ;
- le setup est déjà en position ;
- le risque dépasse la limite ;
- un ordre d’entrée existe déjà ;
- le stop-loss initial n’est pas défini ;
- la liquidité est insuffisante ;
- le spread est trop large ;
- la position maximale sur ce symbole est déjà atteinte.

---

# 12. Risk Engine

## 12.1 Rôle

Le `Risk Engine` calcule la quantité autorisée et décide si un trade peut être pris.

Il doit appliquer à la fois :

- limite par budget ;
- limite par risque ;
- limite par nombre de positions ;
- limite de perte journalière ;
- limite d’exposition totale ;
- taille minimale et maximale.

## 12.2 Calcul de quantité pour une nouvelle entrée

Le calcul doit utiliser le **pire prix d’exécution autorisé**.

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
quantité selon budget = budget_max / worst_case_entry_price

risque par action = worst_case_entry_price - stop_loss

quantité selon risque = risque_max / risque_par_action

quantité finale = floor(min(quantité selon budget, quantité selon risque))
```

Le trigger d’un ordre `STP_LMT` ne doit jamais être utilisé comme prix de risque si le prix limite maximal est supérieur.

## 12.3 Calcul générique corrigé avec STP_LMT

```text
trigger_price           = resistance + trigger_offset
entry_limit_price       = trigger_price + limit_offset
worst_case_entry_price  = entry_limit_price

risk_per_share          = worst_case_entry_price - initial_stop_loss

quantity_by_budget      = floor(
                            max_position_amount_usd /
                            worst_case_entry_price
                          )

quantity_by_risk        = floor(
                            max_risk_usd /
                            risk_per_share
                          )

maximum_quantity        = min(quantity_by_budget, quantity_by_risk)
maximum_risk            = maximum_quantity × risk_per_share
```

Le programme doit refuser toute quantité qui dépasse `max_risk_usd`.

## 12.4 Calcul de risque pour une position existante

Pour un setup `MANAGEMENT_ONLY`, le programme ne calcule pas une nouvelle quantité d’achat.

Il mesure l’exposition existante :

```text
quantity = quantité réelle récupérée depuis IBKR
average_cost = coût moyen récupéré depuis IBKR
protective_stop = stop réel ou stop demandé
open_risk = max(0, average_cost - protective_stop) × quantity
remaining_market_risk = max(0, current_market_price - protective_stop) × quantity
```

Si le prix courant est inférieur ou égal au stop demandé :

```text
current_market_price <= protective_stop
```

le programme doit passer en :

```text
MANUAL_REVIEW_REQUIRED
```

Il ne doit pas créer automatiquement un nouvel achat et ne doit pas supposer qu’un ordre stop peut être placé sans contrôle.

## 12.5 Conditions de refus

Le trade doit être refusé si :

```text
quantité finale < 1
risque_par_action <= 0
stop_loss >= worst_case_entry_price
budget insuffisant
max_daily_loss atteint
max_open_positions atteint
données de marché invalides
spread trop grand
setup_role = MANAGEMENT_ONLY
ordre d’entrée déjà existant
position existante incompatible avec une nouvelle entrée
```

---

# 13. Order Manager

## 13.1 Rôle

Le module `Order Manager` est le seul module autorisé à envoyer, modifier ou annuler des ordres.

Aucun setup ne doit appeler directement l’API TWS.

## 13.2 Responsabilités

Le module `Order Manager` doit :

- créer les ordres IBKR ;
- mapper les ordres internes vers les ordres TWS ;
- ajouter `orderRef` ;
- enregistrer `orderId`, `permId`, `parentId`, `parentPermId` ;
- suivre les exécutions partielles ;
- gérer les rejets ;
- modifier les stops ;
- annuler les ordres expirés ;
- éviter les doublons ;
- valider que l’ordre correspond toujours au setup.

## 13.3 orderRef

Chaque ordre doit avoir un `orderRef` clair.

Format recommandé :

```text
BOT_<SYMBOL>_<SETUP_TYPE>_<SETUP_ID>_<ACTION>
```

Exemple :

```text
BOT_<SYMBOL>_<SETUP_TYPE>_<SETUP_ID>_ENTRY
BOT_<SYMBOL>_<SETUP_TYPE>_<SETUP_ID>_STOP
```

## 13.4 Ordre d’entrée recommandé

Pour une entrée automatique :

```text
BUY STP LMT
```

Exemple :

```text
Trigger : 14.58
Limit   : 14.63
```

Avantage :

- évite d’acheter trop haut en cas de spike ;
- force une confirmation au-dessus du niveau.

Inconvénient :

- peut ne pas être exécuté si le prix part trop vite.

## 13.5 Stop-loss recommandé

Pour la protection :

```text
SELL STP
```

Avantage :

- favorise la sortie ;
- plus sûr qu’un stop-limit dans un mouvement violent.

Inconvénient :

- prix d’exécution non garanti.

## 13.6 Annulation automatique des ordres non exécutés

Chaque setup doit pouvoir définir :

```yaml
entry_order:
  cancel_if_not_filled_after_minutes: 30
```

Si l’ordre n’est pas exécuté après ce délai :

```text
ENTRY_ORDER_PLACED → CANCELLED
```

ou :

```text
ENTRY_ORDER_PLACED → WAITING_CONFIRMATION
```

selon la configuration.

## 13.7 Vérification obligatoire du rôle avant placement d’ordre

Avant tout ordre d’entrée, le module `Order Manager` doit vérifier :

```text
setup_role in ["ENTRY_AND_MANAGEMENT", "ENTRY_ONLY"]
entry.enabled = true
setup status = ENTRY_READY
aucune position conflictuelle
aucun ordre d’entrée dupliqué
```

Interdiction absolue :

```text
setup_role = MANAGEMENT_ONLY
→ BUY interdit
```

## 13.8 Champs calculés pour un ordre STP_LMT

Pour éviter un affichage ambigu, stocker séparément :

```text
entry_trigger_price
entry_limit_price
worst_case_entry_price
initial_stop_loss
maximum_quantity
maximum_risk_usd
```

---

# 14. Gestion du stop-loss

## 14.1 Stop initial

Le stop initial doit être placé immédiatement après l’exécution de l’entrée.

Règle obligatoire :

```text
Aucune position ouverte ne doit rester sans stop réel chez IBKR.
```

## 14.2 Modification du stop

Le programme peut modifier le stop si :

- la position existe réellement ;
- l’ordre stop existe réellement ;
- le nouveau stop est supérieur à l’ancien pour une position longue ;
- la quantité du stop ne dépasse pas la quantité détenue ;
- la modification est acceptée par IBKR.

## 14.3 Interdiction de baisser le stop

Pour une position longue :

```text
new_stop > current_stop
```

Sinon, la modification doit être refusée.

Exception possible :

```yaml
allow_stop_widening: false
```

La valeur par défaut doit toujours être `false`.

## 14.4 Modes de gestion du stop

Le programme doit prévoir plusieurs modes.

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

## 14.5 Recommandation pour le style de l’utilisateur

Comme l’utilisateur souhaite souvent laisser courir sans take-profit fixe, le mode recommandé est :

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

## 14.6 Stop d’une position existante

Pour un setup `MANAGEMENT_ONLY`, le programme doit :

```text
1. récupérer la position réelle chez IBKR ;
2. récupérer les ordres ouverts liés au symbole ;
3. identifier le stop protecteur existant ;
4. vérifier la quantité du stop ;
5. comparer le stop réel au stop demandé ;
6. modifier ou créer le stop uniquement si la situation est cohérente ;
7. passer en MANUAL_REVIEW_REQUIRED si le prix courant est déjà inférieur ou égal au stop demandé.
```

Règle :

```text
Ne jamais placer aveuglément un SELL STP au-dessus ou au niveau du prix courant.
```

---

# 15. Gestion des objectifs

Les objectifs peuvent être utilisés de plusieurs manières.

## 15.1 Objectifs informatifs

Le programme envoie une alerte, mais ne vend rien.

```yaml
targets:
  - name: "objectif_1"
    zone_min: 14.80
    zone_max: 15.30
    action: "notify_only"
```

## 15.2 Objectifs avec remontée du stop

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

À gérer prudemment.

Après une sortie partielle :

```text
nouvelle quantité position = quantité initiale - quantité vendue
nouvelle quantité stop = nouvelle quantité position
```

Le programme doit toujours synchroniser le stop avec la position restante.

---

# 16. Reconciliation Engine

## 16.1 Rôle

Le `Reconciliation Engine` est indispensable.

Il compare :

```text
données locales
positions IBKR
ordres ouverts IBKR
exécutions IBKR
```

## 16.2 Quand lancer la réconciliation

La réconciliation doit être lancée :

- au démarrage ;
- après reconnexion TWS ;
- après erreur d’ordre ;
- après modification manuelle dans TWS ;
- périodiquement toutes les 30 à 60 secondes ;
- avant toute nouvelle entrée ;
- avant toute modification de stop.

## 16.3 Cas à détecter

Le moteur doit détecter :

```text
position locale absente mais position IBKR existante
position locale existante mais position IBKR absente
stop local absent mais stop IBKR présent
stop local présent mais stop IBKR absent
quantité stop différente de quantité position
ordre local non retrouvé chez IBKR
ordre IBKR inconnu du programme
ordre modifié manuellement dans TWS
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

Pour la V1, les cas ambigus doivent mettre le setup en état :

```text
ERROR_REQUIRES_MANUAL_REVIEW
```

## 16.5 Adoption d’une position existante

Lorsqu’un setup utilise :

```yaml
position_source:
  mode: "adopt_existing_ibkr_position"
```

la réconciliation doit :

```text
1. chercher la position réelle chez IBKR par compte et symbole ;
2. récupérer la quantité et le coût moyen ;
3. vérifier qu’une seule position compatible existe ;
4. chercher un stop ouvert existant ;
5. comparer la quantité du stop à la quantité détenue ;
6. enregistrer la position adoptée dans SQLite ;
7. rattacher la position au setup ;
8. passer à IN_POSITION ou MANUAL_REVIEW_REQUIRED.
```

Aucune entrée ne doit être placée pendant cette procédure.

---

# 17. Stockage sur fichier

## 17.1 Format recommandé

Même si l’utilisateur demande un fichier, il est préférable d’utiliser :

```text
SQLite = fichier structuré
```

Fichier principal :

```text
data/trading_state.sqlite
```

## 17.2 Tables recommandées

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

Format recommandé :

```text
timestamp | level | module | setup_id | symbol | message
```

---

# 18. Interface GUI HTML

## 18.1 Objectif de la GUI

La GUI doit permettre à l’utilisateur de :

- voir l’état global du bot ;
- voir si TWS est connecté ;
- voir les setups actifs ;
- ajouter un setup ;
- modifier un setup ;
- activer ou désactiver un setup ;
- suivre les positions ;
- suivre les ordres ;
- voir les logs ;
- voir les erreurs ;
- forcer une synchronisation ;
- mettre le bot en pause ;
- fermer une position manuellement ;
- annuler un ordre ;
- remonter un stop manuellement.

## 18.2 Pages recommandées

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
Perte journalière autorisée restante
Derniers événements
Dernières erreurs
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

Colonnes recommandées :

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

## 18.5 Page détail d’un setup

Afficher :

```text
Configuration complète
Rôle du setup
État actuel
Historique des transitions
Ordres liés
Position liée
Origine de la position : bot, manuelle ou adoptée depuis IBKR
Trigger d’entrée
Prix limite maximal
Prix utilisé pour le calcul du risque
Quantité maximale
Stop actuel
Risque maximal
Risque restant
État de réconciliation
Objectifs atteints
Logs spécifiques
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

La GUI doit recevoir les mises à jour en temps réel.

Événements WebSocket :

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

Avant d’activer un setup, le programme doit vérifier :

```text
symbol non vide
setup_type supporté
setup_role supporté
mode défini
timeframe supporté
prix cohérents
budget max défini si nouvelle entrée
risque max défini
règles d’invalidation présentes
aucun setup actif contradictoire sur le même symbole
```

## 20.1 Validation d’un setup d’entrée

Pour :

```text
ENTRY_AND_MANAGEMENT
ENTRY_ONLY
```

vérifier :

```text
entry.enabled = true
ordre d’entrée supporté
stop-loss initial défini
stop-loss inférieur au pire prix d’exécution autorisé pour une position longue
worst_case_entry_price calculable
quantité calculable
```

Pour `STP_LMT` :

```text
trigger_price = resistance + trigger_offset
limit_price = trigger_price + limit_offset
worst_case_entry_price = limit_price
```

## 20.2 Validation d’un setup de gestion seule

Pour :

```text
MANAGEMENT_ONLY
```

vérifier :

```text
entry.enabled = false
position_source.mode = adopt_existing_ibkr_position
position_source.reconcile_on_load = true
protective_stop défini
aucun BUY autorisé
```

Exemple de setup invalide :

```yaml
setup_role: "MANAGEMENT_ONLY"

entry:
  enabled: true
```

Raison :

```text
Un setup de gestion seule ne doit jamais créer une nouvelle entrée.
```

## 20.3 Cas nécessitant une revue manuelle

Passer en :

```text
MANUAL_REVIEW_REQUIRED
```

si :

```text
position IBKR introuvable
plusieurs positions incompatibles
stop IBKR absent et création non sûre
quantité stop différente de la quantité détenue
prix courant inférieur ou égal au stop demandé
ordre IBKR inconnu ou incohérent
```

---

# 21. Règles de comportement du bot

## 21.1 Au démarrage

Le bot doit :

```text
1. charger config.yaml
2. initialiser les logs
3. ouvrir la base SQLite
4. charger les setups YAML
5. valider chaque setup, y compris son rôle
6. se connecter à TWS
7. récupérer positions IBKR
8. récupérer ordres ouverts IBKR
9. lancer la réconciliation
10. adopter les positions IBKR demandées par les setups MANAGEMENT_ONLY
11. vérifier les stops existants
12. placer les setups incohérents en MANUAL_REVIEW_REQUIRED
13. démarrer le backend GUI
14. démarrer le moteur de trading
```

## 21.2 Si TWS est déconnecté

Le bot doit :

```text
1. marquer TWS comme DISCONNECTED
2. arrêter l’envoi de nouveaux ordres
3. continuer à afficher la GUI
4. tenter une reconnexion
5. après reconnexion, lancer une réconciliation
6. reprendre seulement si l’état est cohérent
```

## 21.3 Si un ordre est rejeté

Le bot doit :

```text
1. enregistrer l’erreur
2. afficher l’erreur dans la GUI
3. mettre le setup en ERROR
4. bloquer toute nouvelle action automatique sur ce setup
5. demander une revue manuelle
```

## 21.4 Si une entrée est exécutée mais le stop échoue

Cas critique.

Action obligatoire :

```text
1. tenter de replacer immédiatement le stop
2. si échec, envoyer une alerte critique
3. mettre le bot en pause pour ce symbole
4. afficher le risque dans la GUI
5. demander intervention manuelle
```

Option stricte :

```text
Si le stop ne peut pas être placé après N tentatives, vendre immédiatement la position au marché.
```

Cette option doit être configurable :

```yaml
safety:
  emergency_exit_if_stop_fails: true
  max_stop_submit_retries: 3
```

## 21.5 Si l’utilisateur modifie un ordre manuellement dans TWS

Le bot doit le détecter pendant la réconciliation.

Actions possibles :

```text
1. accepter la modification et synchroniser localement ;
2. remettre l’ordre à la valeur attendue ;
3. mettre le setup en pause ;
4. demander confirmation dans la GUI.
```

Pour la V1, recommandation :

```text
Modification manuelle détectée → setup en MANUAL_REVIEW_REQUIRED
```

## 21.6 Chargement d’un setup MANAGEMENT_ONLY

Le programme doit exécuter :

```text
1. vérifier que entry.enabled = false ;
2. passer à RECONCILING_EXISTING_POSITION ;
3. chercher la position réelle chez IBKR ;
4. si absente, passer à MANUAL_REVIEW_REQUIRED ;
5. si présente, récupérer quantité et coût moyen ;
6. identifier le stop existant ;
7. vérifier le prix courant ;
8. si prix courant <= stop demandé, passer à MANUAL_REVIEW_REQUIRED ;
9. sinon créer ou mettre à jour le stop si nécessaire ;
10. passer à IN_POSITION.
```

Interdiction :

```text
Ne jamais transformer automatiquement MANAGEMENT_ONLY en momentum_breakout.
Ne jamais acheter pour compenser une position introuvable.
```

---

# 22. Performance et évolutivité

## 22.1 Éviter les boucles inutiles

Ne pas faire :

```python
while True:
    check_everything()
    sleep(1)
```

Préférer :

- événements de marché ;
- callbacks d’ordres ;
- WebSocket ;
- tâches planifiées légères ;
- vérifications ciblées.

## 22.2 Séparer les fréquences

Exemple :

```text
Market data temps réel        : événementiel
Vérification setups           : à chaque nouvelle bougie
Réconciliation IBKR           : toutes les 30-60 secondes
Actualisation GUI             : événementielle ou 1-2 secondes
Export CSV                    : à la demande
```

## 22.3 Index SQLite

Créer des index sur :

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

- éviter le blocage de la GUI ;
- éviter le blocage du moteur de trading ;
- gérer plusieurs symboles simultanément ;
- améliorer la réactivité.

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

- zéro setup actif ;
- un setup actif ;
- plusieurs setups en attente ;
- une position ouverte ;
- plusieurs ordres historiques.

Règle recommandée V1 :

```text
Une seule position ouverte par symbole.
```

Règle V2 possible :

```text
Plusieurs setups par symbole autorisés, mais un seul peut être en position.
```

---

# 24. Gestion des conflits

## 24.1 Conflit entre setups

Exemple :

```text
<SYMBOL> setup breakout actif
<SYMBOL> setup pullback actif
```

Si les deux déclenchent une entrée, risque d’acheter deux fois.

Solution V1 :

```text
Interdire plusieurs setups actifs avec entrée automatique sur le même symbole.
```

Solution V2 :

```text
Autoriser plusieurs setups mais utiliser un Symbol Lock.
```

## 24.2 Symbol Lock

```text
symbol_lock["<SYMBOL>"] = True
```

Quand une entrée est placée sur `<SYMBOL>` :

```text
aucun autre setup associé à <SYMBOL> ne peut placer un ordre d’entrée
```

---

# 25. Modes de fonctionnement

## 25.1 Mode simulation interne

Aucune connexion TWS.

Utilité :

- tester la logique ;
- rejouer des données historiques ;
- valider les transitions d’état.

## 25.2 Mode paper

Connexion au compte paper trading de TWS.

Utilité :

- tester les ordres réels ;
- valider les comportements IBKR ;
- tester les erreurs ;
- valider la GUI.

## 25.3 Mode live

Trading réel.

Conditions obligatoires avant activation :

```text
tests unitaires OK
tests paper OK
logs OK
reconciliation OK
contrôles de risque OK
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
1. arrêter toute nouvelle entrée ;
2. annuler tous les ordres d’entrée non exécutés ;
3. conserver les stops existants ;
4. ne pas fermer automatiquement les positions, sauf option activée.
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

Le système d’alertes doit envoyer des notifications pour :

```text
TWS connecté
TWS déconnecté
setup activé
setup invalidé
signal détecté
ordre envoyé
ordre exécuté
stop placé
stop modifié
position fermée
erreur critique
perte journalière atteinte
modification manuelle détectée
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
calcul de quantité avec worst_case_entry_price
validation setup selon setup_role
interdiction de BUY avec MANAGEMENT_ONLY
adoption d’une position IBKR existante
transitions d’état
détection breakout
détection retest
détection rebond
refus du risque
interdiction de baisser le stop
mapping ordre interne → IBKR
```

## 28.2 Tests d’intégration

Tester :

```text
connexion TWS
récupération position
récupération ordre
placement ordre paper
annulation ordre paper
modification stop paper
reconnexion TWS
réconciliation
```

## 28.3 Tests de scénarios

Scénarios :

```text
entrée exécutée normalement
entrée non exécutée puis annulée
entrée partiellement exécutée
stop placé correctement
stop rejeté
TWS déconnecté après entrée
TWS reconnecté avec position ouverte
position existante adoptée correctement
position demandée introuvable chez IBKR
prix courant déjà inférieur au protective_stop
ordre stop absent ou quantité incohérente
ordre modifié manuellement
setup invalidé avant entrée
perte journalière maximale atteinte
```

---

# 29. Roadmap de développement recommandée

## Phase 1 — Base technique

Objectif :

Créer la structure du projet.

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

## Phase 2 — Setup Engine minimal

Objectif :

Gérer un setup simple.

Livrables :

```text
chargement YAML
validation setup selon setup_role
machine à états
adoption d’une position IBKR existante
stockage événements
affichage setup dans GUI
```

## Phase 3 — Market Data

Objectif :

Construire les bougies et alimenter les setups.

Livrables :

```text
récupération prix
construction bougies 1m/5m/15m
détection bougie clôturée
stockage candles
```

## Phase 4 — Ordres en paper trading

Objectif :

Placer un ordre d’entrée et un stop.

Livrables :

```text
BUY STP LMT
SELL STP
suivi orderStatus
suivi execDetails
sauvegarde orderId/permId
```

## Phase 5 — Gestion de position

Objectif :

Suivre une position ouverte.

Livrables :

```text
position manager
adoption service pour position existante
stop manager
remontée stop par paliers
synchronisation quantité stop
gestion MANUAL_REVIEW_REQUIRED
```

## Phase 6 — GUI complète

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

## Phase 7 — Réconciliation

Objectif :

Robustesse après erreur ou redémarrage.

Livrables :

```text
sync positions IBKR
sync ordres ouverts
détection incohérences
manual review
```

## Phase 8 — Multi-setups

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

## Phase 9 — Tests complets en paper trading

Objectif :

Valider avant réel.

Livrables :

```text
journal de tests
scénarios d’erreur
rapport stabilité
```

## Phase 10 — Passage contrôlé en live

Objectif :

Trading réel limité.

Conditions :

```text
montants faibles
1 ou 2 symboles max
risque très réduit
surveillance manuelle
logs renforcés
```

---

# 30. Modèle générique de fichier setup complet

Le fichier suivant est un **template**, pas un setup lié à une action particulière. Les champs entre chevrons doivent être fournis par l’utilisateur ou par la GUI.

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
  initial_stop_loss: <INITIAL_STOP_LOSS>
  emergency_exit_if_stop_fails: true

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
1. Le setup `<SYMBOL>` est chargé.
2. Le setup passe à VALIDATED.
3. Le bot attend la condition `<BREAKOUT_CONFIRMATION_RULE>`.
4. La condition valide le breakout.
5. Le setup passe à WAITING_RETEST.
6. Le prix revient dans `<RETEST_ZONE>`.
7. Le prix ne clôture pas sous `<RETEST_INVALIDATION_PRICE>`.
8. Une bougie 15m haussière confirme le retest.
9. Le setup passe à ENTRY_READY.
10. Le module `Risk Engine` calcule la quantité.
11. Le module `Order Manager` place BUY STP LMT.
12. L’ordre est exécuté.
13. Le module `Order Manager` place SELL STP.
14. Le setup passe à IN_POSITION.
15. Le prix atteint `<STEP_1_TRIGGER_PRICE>`.
16. Le bot remonte le stop à `<STEP_1_NEW_STOP>`.
17. Le prix atteint `<STEP_2_TRIGGER_PRICE>`.
18. Le bot remonte le stop à `<STEP_2_NEW_STOP>`.
19. Le stop est touché.
20. La position est fermée.
21. Le setup passe à CLOSED.
22. Tous les événements sont enregistrés.
```

## Cas invalidation avant entrée

```text
1. Le setup attend le retest.
2. Le prix clôture sous `<INVALIDATION_PRICE>`.
3. Le setup est invalidé.
4. Aucun ordre n’est envoyé.
5. Le statut devient INVALIDATED.
```

## Cas TWS déconnecté

```text
1. Le bot détecte la perte de connexion.
2. Les nouvelles entrées sont bloquées.
3. La GUI affiche TWS DISCONNECTED.
4. Le bot tente une reconnexion.
5. Après reconnexion, le moteur `Reconciliation Engine` compare IBKR et SQLite.
6. Le bot reprend uniquement si l’état est cohérent.
```

---

# 32. Règles de qualité de code

Le projet doit respecter :

```text
type hints Python
classes simples
fonctions courtes
logs clairs
tests unitaires
séparation responsabilités
pas de logique trading dans la GUI
pas d’appel direct TWS depuis les setups
pas de valeur magique dans le code
configuration externe YAML
```

## 32.1 Exemple mauvais design

```python
if price > hardcoded_price_level:
    ib.placeOrder(...)
```

Problème :

- pas de validation ;
- pas de risque ;
- pas de machine à états ;
- pas de stop ;
- pas de trace ;
- pas de modularité.

## 32.2 Exemple bon design

```python
signal = signal_engine.evaluate(setup, market_data)

if signal.is_valid:
    risk_decision = risk_engine.evaluate(setup, signal)

    if risk_decision.approved:
        order_manager.place_entry_order(setup, risk_decision)
```

---

# 33. Règles minimales avant trading réel

Avant d’activer le mode live :

```text
1. au moins 2 semaines de paper trading sans bug critique ;
2. tous les ordres doivent avoir un stop associé ;
3. aucun ordre dupliqué observé ;
4. reconnexion TWS testée ;
5. redémarrage programme testé ;
6. modification manuelle TWS détectée ;
7. perte journalière maximale testée ;
8. emergency stop testé ;
9. logs vérifiés ;
10. export des trades vérifié.
```

---

# 34. Ajustements génériques et templates réutilisables

## 34.1 Principe impératif

Les ajustements ne doivent jamais être codés pour une action spécifique.

Interdiction :

```python
if symbol == "ABC" and price > 15.80:
    move_stop(15.20)
```

Implémentation correcte :

```python
for rule in setup.management.stop_management.rules:
    if rule_engine.evaluate(rule.when, market_context):
        action_executor.execute(rule.action, setup_context)
```

Le code Python reste générique. Les niveaux de prix, le symbole, le timeframe, les confirmations et les actions sont fournis dans le fichier de configuration du setup.

## 34.2 Modèle générique d’une règle d’ajustement

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

## 34.3 Conditions génériques supportées

Le `Rule Engine` doit prendre en charge des conditions combinables.

### Métriques de prix

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

### Métriques de volume et momentum

```text
volume
volume_ratio
relative_strength
atr
ema
sma
vwap
```

### Opérateurs

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

## 34.4 Actions génériques supportées

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

Chaque action doit être exécutée par un composant générique :

```text
Action Executor
```

Aucune stratégie ne doit appeler directement l’API TWS.

## 34.5 Template générique : gestion d’une position déjà ouverte

Ce template doit être utilisable pour n’importe quel symbole.

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
    "protective_stop": "<INITIAL_PROTECTIVE_STOP>",
    "emergency_exit_if_stop_fails": true,
    "if_market_price_below_stop": "MANUAL_REVIEW_REQUIRED"
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
→ récupérer la position réelle associée à <SYMBOL>
→ vérifier quantité et stop
→ IN_POSITION si cohérent
→ MANUAL_REVIEW_REQUIRED sinon
```

## 34.6 Template générique : nouvelle entrée momentum breakout

Ce template doit être utilisable pour n’importe quel symbole.

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
    "initial_stop_loss": "<INITIAL_STOP_LOSS>",
    "max_position_amount_usd": "<MAX_POSITION_AMOUNT_USD>",
    "max_risk_usd": "<MAX_RISK_USD>",
    "emergency_exit_if_stop_fails": true
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

Calcul générique attendu :

```text
trigger_price           = resistance + trigger_offset
entry_limit_price       = trigger_price + limit_offset
worst_case_entry_price  = entry_limit_price
risk_per_share          = worst_case_entry_price - initial_stop_loss
maximum_quantity        = floor(
                            min(
                              max_position_amount_usd / worst_case_entry_price,
                              max_risk_usd / risk_per_share
                            )
                          )
maximum_risk            = maximum_quantity × risk_per_share
```

## 34.7 Affichage GUI générique attendu

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

## 34.8 Stockage de l’état d’exécution des règles

Pour éviter qu’une même règle soit exécutée plusieurs fois, enregistrer :

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

Avant l’activation d’un setup, vérifier :

```text
rule_id unique dans le setup
action supportée
métrique supportée
opérateur supporté
timeframe supporté
valeur requise présente
nouveau stop cohérent avec la direction
règles triées par priorité
aucune règle ne baisse le stop si never_lower_stop = true
```

---

# 35. Recommandation finale

Le programme doit être développé comme une plateforme de trading automatisé pilotée par setups, et non comme un script unique.

Règle d’architecture impérative :

```text
Aucun symbole, niveau de prix ou ajustement propre à une action ne doit être codé dans la logique Python.
Toutes les variations entre setups doivent provenir de la configuration JSON/YAML et être interprétées par le `Rule Engine`.
```

La bonne logique est :

```text
Configuration YAML
      ↓
Setup Engine
      ↓
Signal Engine
      ↓
Risk Engine
      ↓
Order Manager
      ↓
TWS
      ↓
Reconciliation Engine
      ↓
GUI + Logs + Storage
```

Priorité de développement :

```text
1. sécurité
2. traçabilité
3. stabilité
4. modularité
5. performance
6. extension multi-setups
7. automatisation avancée
8. IA ou scanner automatique
```

Le programme doit toujours être capable de répondre à ces questions :

```text
Quel setup est actif ?
Quel est son rôle : entrée, entrée + gestion ou gestion seule ?
Pourquoi le bot attend ?
Pourquoi le bot a refusé une entrée ?
Pourquoi un setup est en MANUAL_REVIEW_REQUIRED ?
Quel ordre est lié à quel setup ?
Quel stop protège quelle position ?
La position a-t-elle été créée par le bot ou adoptée depuis IBKR ?
Quel prix a été utilisé pour calculer le risque ?
Quelle est la quantité maximale autorisée ?
Quel est le risque actuel ?
Quelle action a été faite automatiquement ?
Quelle action a été faite manuellement ?
L’état local correspond-il à IBKR ?
```

Si le programme peut répondre clairement à ces questions, il sera robuste, contrôlable et évolutif.


---

# 36. Convertisseur générique de texte libre vers setup structuré

## 36.1 Problème à résoudre

La GUI accepte un setup saisi en langage naturel. Le convertisseur doit reconnaître les formulations usuelles en français et en anglais. Il ne doit pas exiger une phrase exacte.

Exemple obligatoire à reconnaître :

```text
SL : $19.70
```

Résultat attendu :

```json
{
  "risk": {
    "initial_stop_loss": 19.70
  }
}
```

Le message `Add a stop loss in the setup text` ne doit apparaître que si aucun stop-loss n’a réellement été détecté.

## 36.2 Pipeline obligatoire

```text
Texte brut
  ↓
Normalisation
  ↓
Extraction structurée
  ↓
Validation métier
  ↓
Résolution des ambiguïtés
  ↓
Prévisualisation GUI éditable
  ↓
Sauvegarde explicite
```

## 36.3 Modules à ajouter

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

Le registre doit être chargé depuis YAML ou JSON afin d’éviter les règles codées en dur.

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
  - "entrée"
  - "entry"
  - "acheter"
  - "buy"
  - "rentrer"
  - "entrer"

confirmation:
  - "confirmation"
  - "clôture"
  - "close"
  - "bougie clôturée"
  - "candle close"
```

## 36.5 Formats monétaires acceptés

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
"19,70 $" → 19.70
```

## 36.6 Motifs minimaux pour le stop-loss

```python
STOP_PATTERNS = [
    r"\bsl\b\s*[:=]?\s*\$?\s*(?P<price>\d+(?:[.,]\d+)?)",
    r"\bstop(?:[-\s]?loss)?\b\s*(?:[:=]|sous|à|a)?\s*\$?\s*(?P<price>\d+(?:[.,]\d+)?)",
    r"\binvalidation\b\s*(?:[:=]|sous|à|a)?\s*\$?\s*(?P<price>\d+(?:[.,]\d+)?)",
]
```

Ces motifs constituent le premier niveau. Un extracteur sémantique peut compléter l’analyse pour les formulations plus complexes.

## 36.7 Résultat de conversion

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

## 36.8 Différence entre erreur, ambiguïté et warning

### Erreur bloquante

```text
symbole absent
aucun stop-loss détecté
stop-loss incohérent avec l’entrée
ordre d’entrée impossible à déterminer
```

### Ambiguïté à confirmer

```text
Entrée : 21.55–21.70
SL : 19.70–19.90
volume correct
rebond clair
ne retombe pas immédiatement
marché général faible
```

### Warning non bloquant

```text
aucun objectif défini
aucune règle de remontée de stop
relative strength non renseignée
```

Le convertisseur ne doit jamais inventer un seuil numérique absent du texte.

## 36.9 Prévisualisation GUI obligatoire

Après clic sur `Convertir`, afficher une fiche éditable :

```text
Symbole
Type de setup
Rôle du setup
Condition d’entrée
Timeframe de confirmation
Trigger
Prix limite maximal
Stop-loss
Budget maximal
Risque maximal
Conditions de no-go
Ambiguïtés
Warnings
```

Boutons :

```text
Modifier
Valider le brouillon
Sauvegarder
Annuler
```

Le bouton `Sauvegarder` doit rester désactivé uniquement en présence d’une erreur bloquante.

## 36.10 Représentation générique d’une entrée confirmée

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
attendre la clôture d’une bougie au-dessus du seuil
```

## 36.11 Conditions de no-go génériques

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

## 36.13 Règle d’architecture

```text
Extraire automatiquement ce qui est certain.
Demander confirmation pour ce qui est ambigu.
Refuser uniquement ce qui est réellement invalide.
Ne jamais inventer une règle quantitative absente du texte.
Ne jamais lier le convertisseur à un symbole particulier.
```


---

# 37. Conversion des analyses longues contenant plusieurs scénarios

## 37.1 Problème à résoudre

Une analyse peut contenir plusieurs informations de nature différente :

- un ancien plan explicitement invalidé ;
- du contexte de marché ou des fondamentaux ;
- un tableau de niveaux techniques ;
- un scénario recommandé ;
- une variante plus prudente ;
- des règles de no-go ;
- des règles de gestion post-entrée ;
- une décision finale résumant le scénario privilégié.

Le convertisseur ne doit jamais extraire tous les prix dans un objet unique. Il doit comprendre le rôle de chaque bloc.

Exemple :

```text
Ancien plan : entrée à 21.55 ; stop-loss à 19.70.
Ce plan n’est plus valide.
```

Résultat interdit :

```json
{
  "entry_trigger": 21.55,
  "initial_stop_loss": 19.70,
  "status": "ACTIVE"
}
```

Résultat correct :

```json
{
  "historical_plan": {
    "entry_trigger": 21.55,
    "initial_stop_loss": 19.70,
    "status": "INVALIDATED"
  }
}
```

## 37.2 Architecture à ajouter

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
  ↓
Normalisation
  ↓
Segmentation par titres, listes, tableaux et paragraphes
  ↓
Classification du rôle de chaque bloc
  ↓
Extraction avec provenance
  ↓
Résolution des priorités et invalidations
  ↓
Construction d’un bundle de scénarios
  ↓
Validation métier déterministe
  ↓
Prévisualisation GUI
  ↓
Sélection explicite du scénario à activer
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
"Ton ancien plan était"
→ HISTORICAL_PLAN

"Ce plan n’est plus valide"
→ INVALIDATION_NOTICE

"Mon plan recommandé"
→ PRIMARY_SETUP

"Variante plus prudente"
→ ALTERNATIVE_SETUP

"Cas No-Go"
→ NO_GO_RULES

"Gestion après l’entrée"
→ POST_ENTRY_MANAGEMENT

"Décision finale"
→ FINAL_DECISION
```

## 37.4 Résolution des priorités

Priorité sémantique :

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

Règles obligatoires :

```text
Un HISTORICAL_PLAN n’active jamais un ordre.
Un bloc INVALIDATED n’est jamais transformé en setup actif.
Un niveau REFERENCE_LEVELS n’est pas automatiquement une entrée ou un stop.
Une variante reste STANDBY tant que l’utilisateur ne la sélectionne pas.
La décision finale peut confirmer ou remplacer les paramètres extraits avant elle.
```

## 37.5 Bundle de scénarios

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

Chaque scénario :

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
  "field": "risk.initial_stop_loss",
  "value": 18.50,
  "source_text": "Stop-loss : $18.50",
  "source_block_type": "PRIMARY_SETUP",
  "confidence": 0.99
}
```

Le système peut ainsi afficher pourquoi une valeur a été retenue et éviter de confondre un niveau informatif avec un ordre.

## 37.7 Résolution des conflits

Exemple :

```text
ancien stop-loss : 19.70
nouveau stop-loss recommandé : 18.50
variante prudente : stop-loss 19.40
```

Résultat :

```text
historical_plan.stop_loss = 19.70
primary_scenario.stop_loss = 18.50
alternative_scenario.stop_loss = 19.40
```

Les valeurs ne doivent jamais être fusionnées.

## 37.8 Règles temporelles

Schéma générique :

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
attendre 30 minutes après l’ouverture
laisser passer les deux premières bougies de 15 minutes
clôture 15 minutes au-dessus d’un niveau
maintien au-dessus d’une zone
reprise rapide après ouverture
```

## 37.9 Reclaim

Un reclaim est différent d’une simple cassure.

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

Les filtres qualitatifs non chiffrés deviennent des ambiguïtés ou des filtres facultatifs à confirmer.

## 37.10 Mèches tolérées et clôtures interdites

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

Le moteur ne doit pas confondre une mèche avec une clôture.

## 37.11 Formulations qualitatives

À transformer en paramètres ou ambiguïtés :

```text
reprise rapide
volume acheteur correct
higher low
retest propre
cassure propre
rejet immédiat
grosse mèche vendeuse
premières heures
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

## 37.12 Profils réutilisables

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

Un profil ne doit jamais être appliqué silencieusement. La GUI doit indiquer le profil choisi.

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

## 37.14 Gestion post-entrée

Actions supportées :

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
Ancien plan invalidé
Niveaux informatifs
Scénario principal
Variante prudente
No-go rules
Gestion post-entrée
Ambiguïtés à résoudre
```

Actions disponibles :

```text
Activer
Modifier
Mettre en standby
Archiver
```

Règle :

```text
Un seul scénario d’entrée peut être ACTIVE par symbole.
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

Pour les analyses longues, un extracteur LLM peut compléter les expressions régulières.

```text
Regex + parseur de structure
  ↓
Extracteur LLM sous JSON Schema strict
  ↓
Validateur déterministe
  ↓
Prévisualisation utilisateur
  ↓
Activation explicite
```

Règles :

```text
Le LLM produit uniquement un brouillon.
Le LLM ne place jamais un ordre.
Le validateur déterministe vérifie chaque valeur.
Les ambiguïtés restent visibles.
L’utilisateur sélectionne explicitement le scénario actif.
```

## 37.18 Tests obligatoires

```text
ancien plan invalidé non activé
ancien stop non fusionné avec nouveau stop
variante prudente conservée en STANDBY
tableau technique classé REFERENCE_LEVELS
attente de 30 minutes extraite
deux bougies de 15 minutes extraites
mèche tolérée mais clôture interdite
no-go rules séparées des entry rules
gestion post-entrée séparée de l’entrée
higher low qualitatif marqué NEEDS_REVIEW sans profil choisi
volume correct marqué NEEDS_REVIEW sans seuil choisi
décision finale prioritaire
```

## 37.19 Règle de sécurité

```text
Un texte d’analyse n’est jamais directement exécutable.
Il devient d’abord un bundle de scénarios.
Chaque scénario est prévisualisé, validé et explicitement activé.
Un ancien plan invalidé ne doit jamais redevenir actif.
```


---

# 38. Sélection, activation et modification complète des scénarios depuis la GUI

## 38.1 Objectif

Après conversion d’une analyse, le programme doit permettre à l’utilisateur de :

- visualiser tous les scénarios détectés ;
- sélectionner un ou plusieurs scénarios ;
- modifier tous les paramètres exposés ;
- ajouter ou supprimer des règles ;
- activer immédiatement un scénario ;
- armer un scénario pour activation automatique future ;
- mettre un scénario en standby ;
- désactiver temporairement un scénario ;
- archiver un scénario ;
- dupliquer un scénario afin de tester une variante ;
- comparer plusieurs scénarios avant activation ;
- enregistrer les modifications sans perdre la provenance du texte initial.

La GUI ne doit pas imposer un scénario unique. Elle doit laisser l’utilisateur choisir le ou les scénarios adaptés à sa stratégie.

## 38.2 Différence entre sélection et activation

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

Définition :

| Statut | Signification |
|---|---|
| `SELECTED` | Le scénario est choisi dans la GUI pour revue ou édition |
| `ARMED` | Le scénario est prêt à surveiller le marché et peut devenir actif si ses conditions sont remplies |
| `ACTIVE` | Le scénario contrôle actuellement un ordre, une entrée ou une position |
| `STANDBY` | Le scénario est conservé mais ne peut pas envoyer d’ordre |
| `PAUSED` | Le scénario est temporairement désactivé par l’utilisateur ou par une règle |
| `BLOCKED_BY_CONFLICT` | Le scénario est valide mais bloqué par une règle de concurrence |
| `INVALIDATED` | Le scénario n’est plus exploitable selon ses règles |
| `ARCHIVED` | Le scénario est conservé uniquement pour historique |

Un scénario peut être `SELECTED` sans être `ARMED`.

Un scénario peut être `ARMED` sans être `ACTIVE`.

## 38.3 Sélection multiple

Le programme doit permettre de sélectionner plusieurs scénarios simultanément.

Exemples :

```text
Scénario principal reclaim
+
Scénario prudent breakout + retest
```

ou :

```text
Scénario support rebound
+
Scénario momentum breakout
+
Scénario de gestion de position existante
```

Cependant, la sélection multiple ne signifie pas que tous les scénarios peuvent envoyer un ordre au même moment.

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

Un seul scénario d’entrée peut être actif pour un symbole.

Les autres scénarios restent :

```text
ARMED
```

ou :

```text
BLOCKED_BY_CONFLICT
```

### `FIRST_TRIGGER_WINS`

Plusieurs scénarios sont armés.

Le premier scénario validé obtient le verrou du symbole.

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

Chaque scénario reçoit une priorité.

Exemple :

```text
Scénario prudent       : priorité 100
Scénario spéculatif    : priorité 50
```

Si deux scénarios deviennent valides simultanément, le scénario avec la priorité la plus élevée prend le verrou.

### `MANUAL_CONFIRMATION_BEFORE_ENTRY`

Le programme détecte le signal mais demande une validation dans la GUI avant de placer l’ordre.

### `ALLOW_MULTIPLE_ENTRIES`

À réserver à une version avancée.

Le programme peut exécuter plusieurs scénarios sur le même symbole uniquement si :

- le cumul de risque reste sous la limite globale ;
- les tailles de position sont recalculées ;
- les ordres restent traçables séparément ;
- la gestion de stop est compatible ;
- l’utilisateur a explicitement activé cette option.

Valeur par défaut recommandée :

```text
SINGLE_ACTIVE_ENTRY_PER_SYMBOL
```

## 38.5 Symbol Lock et Scenario Lock

Le moteur doit gérer deux verrous.

### Symbol Lock

```text
symbol_lock["<SYMBOL>"]
```

Empêche deux entrées incompatibles sur un même symbole.

### Scenario Lock

```text
scenario_lock["<SCENARIO_ID>"]
```

Empêche l’exécution concurrente de deux actions contradictoires dans un même scénario.

Exemple :

```text
raise_stop
```

et :

```text
close_position
```

ne doivent pas être envoyés simultanément.

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

## 38.7 Modèle JSON enrichi d’un scénario

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

## 38.8 Interface GUI : écran de sélection des scénarios

Créer une page :

```text
/scenarios
```

et une page de détail :

```text
/scenarios/{scenario_id}
```

### Liste des scénarios

Colonnes :

```text
Checkbox de sélection
Symbole
Nom du scénario
Type
Rôle
Statut
Priorité
Politique de conflit
Trigger principal
Stop initial
Budget maximal
Risque maximal
Dernière modification
Actions
```

Actions :

```text
Sélectionner
Désélectionner
Armer
Désarmer
Activer
Mettre en standby
Mettre en pause
Modifier
Dupliquer
Archiver
Supprimer
Comparer
```

## 38.9 Interface GUI : cartes après conversion

Après clic sur `Convertir`, afficher des cartes séparées :

```text
Scénario principal
Variante prudente
Autres scénarios détectés
Ancien plan invalidé
Niveaux informatifs
No-go rules
Gestion après entrée
Ambiguïtés à résoudre
Warnings
```

Chaque carte doit contenir :

```text
Checkbox : sélectionner ce scénario
Badge : PRIMARY / ALTERNATIVE / HISTORICAL
Badge : READY / NEEDS_REVIEW / INVALIDATED
Bouton : Modifier
Bouton : Armer
Bouton : Mettre en standby
Bouton : Archiver
```

Un ancien plan invalidé doit être visible mais non activable.

## 38.10 Modification complète des paramètres

La GUI doit permettre de modifier tous les paramètres configurables.

### Identité du scénario

```text
Nom
Description
Symbole
Direction long / short
Type de setup
Rôle du scénario
Priorité
Tags
```

### Activation

```text
Mode simulation / paper / live
Activation automatique ou manuelle
Date de début
Date d’expiration
Regular trading hours uniquement
Premarket autorisé
After-hours autorisé
Politique de concurrence
```

### Entrée

```text
Type d’entrée
Type d’ordre
Trigger
Prix limite maximal
Offsets
Zone d’entrée min / max
Confirmation requise
Timeframe
Nombre de bougies
Attente après ouverture
Retest obligatoire
Reclaim obligatoire
Higher low obligatoire ou facultatif
Volume ratio minimal
Spread maximal
Slippage maximal
Expiration de l’ordre
```

### Risque

```text
Stop initial
Type de stop
Risque maximal en USD
Budget maximal
Quantité maximale
Exposition maximale
Perte journalière maximale
Emergency exit si le stop échoue
Tolérance de slippage
```

### No-go rules

```text
Ajouter une règle
Modifier une règle
Désactiver une règle
Supprimer une règle
Réordonner les priorités
```

### Gestion post-entrée

```text
Take-profit activé ou non
Objectifs informatifs
Sorties partielles
Remontée du stop fixe
Remontée du stop par paliers
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
Niveau d’alerte
Notification avant entrée
Notification après exécution
Notification après changement du stop
Notification en cas de blocage
```

## 38.11 Éditeur de règles générique

Créer un composant GUI :

```text
Rule Builder
```

Le Rule Builder doit permettre :

```text
Ajouter une condition
Ajouter un groupe ALL
Ajouter un groupe ANY
Ajouter une négation NOT
Choisir une métrique
Choisir un opérateur
Saisir une valeur
Choisir un timeframe
Ajouter une action
Ajouter une confirmation
Définir une priorité
Activer ou désactiver la règle
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

## 38.12 Champs avancés du Rule Builder

Métriques :

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

Opérateurs :

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

## 38.13 Édition en deux modes

La GUI doit proposer deux modes.

### Mode simple

Pour l’utilisateur qui veut modifier uniquement :

```text
Entrée
Stop-loss
Budget
Risque
Confirmation
No-go
Paliers de stop
```

### Mode avancé

Pour afficher :

```text
toutes les règles
tous les paramètres
JSON brut
YAML brut
priorités
profils qualitatifs
verrous
politiques de concurrence
```

Le mode avancé doit afficher une validation en temps réel.

## 38.14 Édition JSON / YAML

Ajouter deux onglets :

```text
Formulaire
JSON
YAML
```

Le programme doit :

- synchroniser les trois vues ;
- valider le schéma avant sauvegarde ;
- signaler précisément les champs invalides ;
- proposer un diff avant application ;
- conserver la version précédente ;
- permettre un rollback.

## 38.15 Versioning des scénarios

Chaque modification doit créer une nouvelle version.

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
Voir l’historique
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

La sauvegarde doit être refusée si :

```text
stop initial absent pour un scénario d’entrée
stop incohérent avec la direction
risque maximal absent
budget maximal absent si requis
ordre STP_LMT sans prix limite maximal
règle inconnue
timeframe inconnu
métrique inconnue
action inconnue
plusieurs scénarios ACTIVE incompatibles
```

La sauvegarde peut être autorisée avec warning si :

```text
aucun objectif défini
aucune sortie partielle
aucune règle de remontée du stop
relative strength désactivée
profil qualitatif incomplet
```

## 38.18 Validation avant activation

La validation avant activation est plus stricte que la sauvegarde.

Un scénario peut être :

```text
SAVED_AS_DRAFT
```

mais non :

```text
ARMED
```

si des ambiguïtés restent ouvertes.

Pour armer un scénario :

```text
aucune erreur bloquante
aucune ambiguïté obligatoire non résolue
risk engine validé
données de marché disponibles
conflits vérifiés
```

## 38.19 API REST recommandée

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

Événements :

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

## 38.22 Comparaison de scénarios

La GUI doit permettre de comparer plusieurs scénarios.

Colonnes recommandées :

```text
Nom
Type
Trigger
Zone d’entrée
Stop
Risque par action
Budget
Quantité maximale
Confirmation
No-go rules
Gestion post-entrée
Priorité
Statut
```

## 38.23 Flux utilisateur recommandé

```text
1. Coller une analyse.
2. Cliquer sur Convertir.
3. Voir les scénarios détectés.
4. Corriger les ambiguïtés.
5. Modifier les paramètres souhaités.
6. Sélectionner un ou plusieurs scénarios.
7. Choisir la politique de concurrence.
8. Enregistrer comme brouillon.
9. Armer les scénarios retenus.
10. Laisser le moteur surveiller les conditions.
11. Activer automatiquement ou manuellement selon la configuration.
```

## 38.24 Sécurité

Règles obligatoires :

```text
La sélection multiple est autorisée.
L’activation concurrente est contrôlée.
Un seul ordre d’entrée incompatible par symbole est autorisé par défaut.
Toute modification après armement entraîne une nouvelle validation.
Toute modification d’un scénario ACTIVE doit être journalisée.
Toute modification du stop doit respecter never_lower_stop.
Toute activation live peut demander une confirmation manuelle configurable.
```

## 38.25 Tests obligatoires

Ajouter :

```text
sélection multiple autorisée
armement multiple autorisé
un seul scénario actif selon SINGLE_ACTIVE_ENTRY_PER_SYMBOL
premier signal gagnant selon FIRST_TRIGGER_WINS
priorité respectée selon PRIORITY_BASED
activation concurrente bloquée si incompatible
modification d’un scénario crée une nouvelle version
rollback restaure la configuration précédente
édition formulaire synchronisée avec JSON et YAML
validation refuse un stop incohérent
validation refuse une ambiguïté obligatoire avant armement
modification d’un scénario actif journalisée
verrou symbole libéré après fermeture si configuré
```

## 38.26 Règle finale

```text
L’utilisateur peut sélectionner plusieurs scénarios.
Le moteur décide si ces scénarios peuvent être armés ou actifs simultanément
selon une politique de concurrence explicite et modifiable.

Tous les paramètres doivent rester modifiables depuis la GUI,
avec validation, versioning, historique et rollback.
```


---

# 39. Couche d’intelligence sémantique pour analyser les textes de trading complexes

## 39.1 Objectif

Le programme ne doit pas se limiter à rechercher des mots-clés ou des prix avec des expressions régulières.

Il doit être capable de comprendre un texte de trading rédigé naturellement, même lorsqu’il contient :

- plusieurs scénarios ;
- un ancien plan invalidé ;
- une nouvelle recommandation ;
- une variante prudente ;
- des niveaux techniques informatifs ;
- des règles de no-go ;
- des règles de gestion après l’entrée ;
- des phrases qualitatives ;
- des éléments fondamentaux non exécutables ;
- une décision finale qui résume et remplace certaines informations précédentes.

Le système doit convertir une analyse libre en un **bundle de scénarios structurés**, contrôlables, éditables et validables depuis la GUI.

## 39.2 Limite du parseur classique

Un parseur uniquement basé sur des expressions régulières peut détecter :

```text
SL : 18.50
Entrée : 19.75–20.00
Clôture 15 min au-dessus de 19.70
```

Mais il ne comprend pas correctement :

```text
L’ancien plan n’est plus valide.
La zone qui était support devient résistance.
Le scénario prudent est encore meilleur.
Ne pas acheter automatiquement sur le support suivant.
Conserver le stop, puis le remonter seulement après maintien.
```

Une couche d’intelligence sémantique est donc nécessaire.

## 39.3 Principe d’architecture

Utiliser une architecture hybride :

```text
Parseur déterministe
+
LLM sémantique sous JSON Schema strict
+
Validateur métier déterministe
+
GUI de revue et correction
+
Activation explicite par l’utilisateur
```

Le LLM aide à comprendre le texte.

Le LLM ne doit jamais :

- envoyer un ordre à TWS ;
- modifier un stop directement ;
- activer un scénario ;
- inventer silencieusement une valeur manquante ;
- fusionner des scénarios différents ;
- transformer un niveau informatif en ordre sans preuve textuelle.

## 39.4 Modules à ajouter

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
  ↓
Normalisation linguistique
  ↓
Segmentation structurelle
  ↓
Classification déterministe des blocs évidents
  ↓
Analyse sémantique LLM
  ↓
Extraction JSON stricte avec provenance
  ↓
Résolution des contradictions
  ↓
Compilation vers règles exécutables
  ↓
Validation déterministe
  ↓
Calcul de confiance
  ↓
Prévisualisation GUI
  ↓
Correction utilisateur
  ↓
Sélection des scénarios
  ↓
Armement explicite
```

## 39.6 Rôle du LLM

Le LLM doit analyser :

```text
titres
paragraphes
listes
tableaux
phrases qualitatives
relations entre niveaux
chronologie
priorités
invalidations
variantes
résumé final
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

## 39.7 Schéma de sortie obligatoire du LLM

Le LLM doit répondre uniquement avec un JSON conforme au schéma.

Exemple générique :

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

Chaque champ extrait doit être relié à la phrase source exacte.

Exemple :

```json
{
  "field": "risk.initial_stop_loss",
  "value": 18.50,
  "source_text": "Stop-loss : $18.50",
  "source_block": "PRIMARY_SETUP",
  "source_line_start": 42,
  "source_line_end": 42,
  "confidence": 0.99
}
```

Règle :

```text
Aucune valeur exécutable sans provenance.
```

Si le système ne peut pas rattacher une valeur à une phrase source :

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

Les blocs suivants ne doivent jamais générer directement un ordre :

```text
MARKET_CONTEXT
FUNDAMENTAL_CONTEXT
REFERENCE_LEVELS
NON_EXECUTABLE_COMMENT
```

## 39.10 Détection des scénarios

Le système doit comprendre qu’un même texte peut contenir plusieurs scénarios.

Exemple générique :

```text
Scénario principal :
entrée après reclaim confirmé d’une résistance.

Variante prudente :
entrée après cassure supérieure puis retest.
```

Résultat :

```text
scenario_1 = PRIMARY
scenario_2 = ALTERNATIVE
```

Les deux scénarios doivent être visibles dans la GUI.

L’utilisateur choisit :

```text
sélectionner
armer
laisser en standby
modifier
archiver
```

## 39.11 Résolution des contradictions

Le système doit détecter les conflits.

Exemples :

```text
ancien stop = 19.70
nouveau stop = 18.50
stop variante prudente = 19.40
```

Ces valeurs ne doivent jamais être fusionnées.

Résultat :

```text
historical_plan.stop = 19.70
primary_scenario.stop = 18.50
alternative_scenario.stop = 19.40
```

Autre exemple :

```text
No-Go immédiat à l’ouverture.
Entrée uniquement après reclaim confirmé.
```

Interprétation correcte :

```text
ne pas placer d’ordre à l’ouverture
attendre les conditions du reclaim
```

## 39.12 Règles de priorité

Le système doit appliquer :

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

Un bloc final peut confirmer ou remplacer un paramètre précédent.

Toute modification doit conserver :

```text
ancienne valeur
nouvelle valeur
source
raison
niveau de confiance
```

## 39.13 Compréhension des notions techniques

Le système doit comprendre les concepts suivants :

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
Une simple mèche sous le support est tolérée,
mais pas une clôture 15 min nette en dessous.
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
rejet immédiat
grosse mèche vendeuse
premières heures
forte accélération vendeuse
```

Ces expressions doivent devenir :

```text
AMBIGUITY
```

ou être résolues avec un profil explicite choisi par l’utilisateur.

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

Règle :

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
profil sélectionné
valeurs injectées
champs modifiés
confirmation utilisateur
```

## 39.16 Compilation vers règles exécutables

Le LLM produit un scénario sémantique.

Le `rule_compiler.py` transforme ce scénario en règles déterministes.

Exemple sémantique :

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
  initial_stop_loss: <STOP_LOSS>
```

## 39.17 Validation déterministe

Le validateur métier doit vérifier :

```text
symbol présent
scénario clairement identifié
rôle du scénario
stop-loss présent
stop cohérent avec la direction
zone d’entrée cohérente
trigger cohérent
ordre STP_LMT complet
montant maximal présent
risque maximal présent ou à compléter
règles temporelles valides
no-go séparés des règles d’entrée
ancien plan non activable
variante alternative non activée automatiquement
aucune valeur exécutable sans provenance
```

## 39.18 Score de confiance

Chaque scénario reçoit un score.

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

Politique recommandée :

```text
confidence >= 0.90
→ READY_TO_ACTIVATE après validation

0.70 <= confidence < 0.90
→ NEEDS_REVIEW

confidence < 0.70
→ MANUAL_REVIEW_REQUIRED
```

Le score ne remplace jamais les validations métier.

## 39.19 GUI de revue intelligente

Après conversion, afficher :

```text
Résumé de l’analyse
Ancien plan invalidé
Niveaux techniques
Scénario principal
Scénario prudent
No-go rules
Gestion post-entrée
Ambiguïtés
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

Pour chaque scénario :

```text
Sélectionner
Armer
Modifier
Dupliquer
Mettre en standby
Archiver
```

## 39.20 API recommandée

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

## 39.22 Prompt système recommandé

Le fichier :

```text
app/intelligence/prompts/system_prompt.md
```

doit préciser :

```text
Tu es un extracteur de scénarios de trading.
Tu ne fournis aucun conseil.
Tu ne places aucun ordre.
Tu extrais uniquement les informations présentes dans le texte.
Tu sépares les anciens plans invalidés des scénarios actifs.
Tu distingues les niveaux informatifs des règles exécutables.
Tu retournes uniquement un JSON conforme au schéma.
Tu marques toute ambiguïté.
Tu conserves la provenance de chaque valeur.
Tu n’inventes jamais de seuil absent du texte.
```

## 39.23 Exemple de sortie attendue pour un texte multi-scénarios

Le résultat doit ressembler à :

```json
{
  "document_type": "TRADING_ANALYSIS",
  "symbol": "<SYMBOL>",
  "final_decision": {
    "immediate_action": "WAIT",
    "reason": "No-Go immédiat à l’ouverture"
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
        "initial_stop_loss": "<STOP_LOSS>",
        "max_position_amount_usd": {
          "min": "<MIN_AMOUNT>",
          "max": "<MAX_AMOUNT>"
        }
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

Ajouter des tests unitaires et d’intégration.

```text
texte contenant un ancien plan invalidé
texte contenant deux scénarios
texte contenant une décision finale prioritaire
tableau technique non transformé en ordres
fondamentaux ignorés par le moteur d’exécution
reclaim détecté
breakout + retest détecté
mèche tolérée mais clôture interdite
no-go immédiat à l’ouverture détecté
gestion du stop progressif compilée
phrase qualitative convertie en ambiguïté
provenance présente pour chaque champ exécutable
score de confiance calculé
LLM JSON invalide rejeté
valeur inventée sans provenance rejetée
ancien plan jamais activé
variante prudente conservée en standby
```

## 39.25 Sécurité et responsabilité

Règle absolue :

```text
Le LLM comprend.
Le validateur vérifie.
La GUI présente.
L’utilisateur sélectionne.
Le moteur arme.
TWS exécute uniquement après validation.
```

Le programme ne doit jamais confondre compréhension sémantique et autorisation d’exécuter un ordre.

## 39.26 Roadmap d’implémentation

### Phase 1 — Extraction sémantique hors trading

```text
LLM client
JSON Schema
prompts
segmentation
classification
provenance
stockage SQLite
```

### Phase 2 — Compilation déterministe

```text
rule compiler
validation métier
ambiguïtés
profils qualitatifs
score de confiance
```

### Phase 3 — GUI de revue

```text
cartes scénarios
édition champ par champ
provenance
scores
résolution ambiguïtés
sélection
armement
```

### Phase 4 — Intégration TWS en simulation

```text
simulation uniquement
logs détaillés
aucun ordre réel
tests multi-scénarios
```

### Phase 5 — Paper trading

```text
activation explicite
compte paper trading
tests de reconnexion
tests d’erreurs
tests de concurrence
```

### Phase 6 — Live contrôlé

```text
petits montants
confirmation manuelle configurable
journalisation complète
rollback de configuration
emergency stop
```

## 39.27 Règle finale

```text
Le programme doit comprendre le texte sans exécuter aveuglément.
L’intelligence artificielle produit un brouillon structuré.
Le validateur déterministe protège le système.
L’utilisateur conserve toujours le contrôle final.
```


---

# 40. Normalisation canonique des champs avant validation

## 40.1 Problème à résoudre

Le convertisseur peut détecter correctement une valeur dans le texte, mais la validation peut échouer si le programme cherche une clé différente.

Exemple valide :

```text
initial_stop_loss: 101.40
```

Erreur incorrecte à éviter :

```text
Add a stop loss in the setup text
```

Le problème ne vient pas du setup. Il vient d’un mapping incohérent entre :

```text
clé saisie par l’utilisateur
clé extraite par le parseur
clé attendue par le validateur
clé affichée par la GUI
```

Le programme doit imposer un schéma canonique unique.

## 40.2 Principe impératif

Toutes les variantes acceptées doivent être converties vers une seule clé interne avant toute validation.

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
risk.initial_stop_loss
```

Le validateur ne doit lire que la clé canonique.

## 40.3 Pipeline obligatoire

```text
Texte utilisateur
  ↓
Parsing ligne par ligne
  ↓
Normalisation de la clé
  ↓
Résolution d’alias
  ↓
Conversion de type
  ↓
Construction du modèle canonique
  ↓
Validation métier
  ↓
Affichage GUI
```

La validation ne doit jamais s’exécuter directement sur les clés brutes.

## 40.4 Modules à ajouter

Ajouter :

```text
app/
  conversion/
    canonical_field_registry.py
    alias_resolver.py
    canonical_model_builder.py
```

Créer aussi :

```text
config/
  field_aliases.yaml
```

## 40.5 Registre d’alias

Exemple :

```yaml
risk.initial_stop_loss:
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

Ce registre doit être extensible sans modifier le code Python.

## 40.6 Normalisation des clés

Exemple Python :

```python
from __future__ import annotations


def normalize_key(raw_key: str) -> str:
    return (
        raw_key.strip()
        .lower()
        .replace("’", "'")
        .replace("-", "_")
        .replace(" ", "_")
    )
```

Exemples :

```text
"Stop-loss"            → "stop_loss"
"initial stop loss"    → "initial_stop_loss"
"Prix limite maximal"  → "prix_limite_maximal"
```

## 40.7 Résolution des alias

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

Résultat attendu :

```python
resolver.resolve("initial_stop_loss")
# "risk.initial_stop_loss"

resolver.resolve("SL")
# "risk.initial_stop_loss"
```

## 40.8 Conversion des valeurs

Les valeurs doivent être normalisées avant stockage.

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

Formats acceptés :

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

## 40.9 Modèle canonique

Même si l’utilisateur saisit un format plat :

```text
initial_stop_loss: 101.40
max_position_amount_usd: 150
max_risk_usd: 8
```

le programme doit construire :

```json
{
  "risk": {
    "initial_stop_loss": 101.40,
    "max_position_amount_usd": 150,
    "max_risk_usd": 8
  }
}
```

Même principe pour l’entrée :

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
risk.initial_stop_loss
```

Exemple :

```python
from __future__ import annotations

from typing import Any


def validate_initial_stop_loss(setup: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    stop_loss = (
        setup
        .get("risk", {})
        .get("initial_stop_loss")
    )

    if stop_loss is None:
        errors.append(
            "Stop-loss initial manquant. "
            "Ajoutez par exemple : initial_stop_loss: 101.40"
        )
        return errors

    if not isinstance(stop_loss, (int, float)):
        errors.append("Le stop-loss initial doit être numérique.")

    return errors
```

Interdiction :

```python
setup.get("stop_loss")
setup.get("SL")
setup.get("initial_stop_loss")
```

Le validateur ne doit jamais dépendre des alias.

## 40.11 Sauvegarde et armement

Un setup peut être sauvegardé même s’il n’est pas armable.

Exemple :

```text
status: INVALIDATED_REQUIRES_REVIEW
armed: NO
```

Comportement attendu :

```text
Sauvegarde : autorisée
Armement : interdit
Exécution TWS : interdite
```

Le validateur doit produire deux résultats distincts :

```json
{
  "save_validation": {
    "allowed": true,
    "errors": []
  },
  "arm_validation": {
    "allowed": false,
    "errors": [
      "Le scénario est invalidé et nécessite une revue manuelle."
    ]
  }
}
```

## 40.12 Messages d’erreur précis

Message interdit :

```text
Add a stop loss in the setup text
```

si une variante reconnue existe déjà dans le texte.

Message correct si le champ est réellement absent :

```text
Stop-loss initial introuvable.
Variantes acceptées :
- initial_stop_loss: 101.40
- stop_loss: 101.40
- SL: 101.40
- protective_stop: 101.40
```

Message correct si le mapping échoue :

```text
Le champ "initial_stop_loss" a été détecté,
mais n’a pas été mappé vers "risk.initial_stop_loss".
Vérifiez le registre d’alias.
```

## 40.13 Logs de débogage obligatoires

Ajouter des logs structurés :

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
  "raw_key": "initial_stop_loss",
  "normalized_key": "initial_stop_loss",
  "resolved_canonical_path": "risk.initial_stop_loss",
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
        ("SL", "risk.initial_stop_loss"),
        ("stop", "risk.initial_stop_loss"),
        ("stop_loss", "risk.initial_stop_loss"),
        ("stop-loss", "risk.initial_stop_loss"),
        ("initial stop loss", "risk.initial_stop_loss"),
        ("initial_stop_loss", "risk.initial_stop_loss"),
        ("protective_stop", "risk.initial_stop_loss"),
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

## 40.15 Test d’intégration obligatoire

Entrée :

```text
SETUP_TRADING

initial_stop_loss: 101.40
max_position_amount_usd: 150
max_risk_usd: 8
```

Résultat attendu :

```json
{
  "risk": {
    "initial_stop_loss": 101.40,
    "max_position_amount_usd": 150,
    "max_risk_usd": 8
  }
}
```

Erreur interdite :

```text
Add a stop loss in the setup text
```

## 40.16 Migration du code existant

Le développeur doit rechercher les validations directes comme :

```python
setup.get("stop_loss")
setup.get("SL")
setup.get("initial_stop_loss")
```

et les remplacer par :

```python
setup["risk"]["initial_stop_loss"]
```

après passage obligatoire dans :

```text
canonical_model_builder
```

## 40.17 Règle finale

```text
Le parseur accepte plusieurs variantes.
Le registre d’alias les normalise.
Le modèle canonique les structure.
Le validateur lit uniquement le modèle canonique.
La GUI affiche des erreurs précises.
Un setup sauvegardable n’est pas nécessairement armable.
```
