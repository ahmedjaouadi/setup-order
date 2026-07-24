# 20 — Audit lecture seule : factorisation de la progression post-fill

Mode lecture seule strict. Aucune modification de code, aucune proposition
d'implémentation. Ce lot répond aux 3 questions posées sur la faisabilité
d'un composant partagé entre `fill_executor.py` (chemin simulé) et
`reconciliation.py` (chemin réel), en s'appuyant sur `audit/19_pre_impl_
reconciliation.md` (déjà établi : `reconciliation.py` ne traite pas
`FILLED` par une écriture de statut, Q1.2/1.3 de l'audit 19) et sur une
relecture fraîche de `fill_executor.py` (134 lignes), `reconciliation.py`
(606 lignes), `order_manager.py` (constructeur), `trading_engine.py`
(câblage), `state_machine.py` (imports), `repositories.py`
(`protection_snapshot_for_setup`).

---

## Q1 — Commun vs spécifique dans `simulate_fill_order` (`fill_executor.py:38-133`)

Découpage ligne par ligne :

### (a) SPÉCIFIQUE SIMULATION

- `:43-45` — garde `order["status"] != OrderStatus.SUBMITTED.value` : lit un
  ordre **local**, généraliste en soi, mais la condition qui suit (b) en
  dépend directement.
- `:47-49` — `if not isinstance(broker, SimulatedBrokerConnector): return
  None`. **La ligne la plus spécifique du fichier** : ce garde-fou existe
  précisément parce que cette fonction ne doit jamais s'exécuter sur le
  chemin réel. Un composant partagé ne peut pas hériter de cette ligne
  telle quelle.
- `:51-58` — application du slippage simulé
  (`simulated_fill_price`/`transaction_cost_settings`, import
  `app.engine.transaction_costs:7`). Commentaire ligne 51-52 : "Paper fills
  must never be perfect (docs/skills.md 24bis.2)". Spécifique au broker
  simulé — un fill réel a déjà un prix réel, il n'y a rien à simuler.
- `:64` — `broker.simulate_fill(broker_order_id, fill_price)` : appel direct
  à une méthode qui n'existe que sur `SimulatedBrokerConnector` (le type
  est déjà filtré ligne 48, donc l'appel est sûr ici mais reste spécifique
  par construction).
- `:65-66` — `if not broker_position: return None` : dépend du retour de
  l'appel spécifique ci-dessus.
- `:117-126` — pose **réactive** du stop après le fill
  (`stop_order_placer.place_stop_order(...)`) : spécifique au fait que le
  broker simulé ne transmet pas de bracket parent+stop en une fois. L'audit
  19 (§3.1) a établi que le chemin réel transmet le stop **avec** l'ordre
  parent (`transmit=False`/`transmit=True`, `order_manager.py:114,163`) —
  le stop existe donc déjà côté broker au moment où un fill réel serait
  détecté par `reconciliation.py`. Cette étape ne se répliquerait pas telle
  quelle sur le chemin réel ; au mieux une **vérification** (le stop
  existe-t-il ?), jamais une **pose**.

### (b) COMMUN aux deux chemins

- `:68` — `repository.update_order_status(order_id, OrderStatus.FILLED.value)`.
  Statut d'**ordre**, indépendant du broker qui a produit le fill.
- `:69-71` — `setup = repository.get_setup(order["setup_id"]); if not setup:
  return None`. Lecture générique.
- `:73-89` — lecture de `trailing_stop_loss.initial_stop` et branche
  d'échec : si absent, `EventLevel.CRITICAL` +
  `SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value` (`:84-88`). Cette
  validation ("le setup a-t-il un stop configuré ?") ne dépend d'aucune
  particularité du broker simulé — un fill réel sans `initial_stop`
  configuré serait tout aussi invalide.
- `:91-102` — construction et `upsert_position(...)` du `PositionRecord`.
  Les champs (`quantity`, `average_price`, `current_stop`,
  `risk_remaining`) sont calculés à partir de `fill_price` et
  `broker_position.quantity` — génériques dans leur formule, mais leur
  **source** (`broker_position` ligne 64, un objet retourné par l'appel
  spécifique (a)) diffère : côté réel, l'équivalent viendrait de
  `broker.positions()` (`reconciliation.py:56`) ou de
  `broker_executions`/`recent_executions()` (`reconciliation.py:89`,
  audit 19 §1.1). La *formule* est commune, la *donnée d'entrée* ne l'est
  pas — nuance importante pour Q1 (voir réponse directe ci-dessous).
- `:103-107` — `update_setup_status(setup_id, ENTRY_FILLED, "Entry order
  filled")`. Écriture de statut pure, aucune dépendance au broker.
- `:108-115` — `event_store.record(EventLevel.TRADE, "entry_filled", ...)`.
  Le `message` ("Entry filled by the internal test broker") est
  spécifique au libellé, mais la structure de l'appel (niveau, event_type,
  data) est générique.
- `:117-118` — `protection = repository.protection_snapshot_for_setup(...)`;
  `if not protection.get("has_active_stop_order")`. La **vérification**
  elle-même (le stop est-il actif ?) est commune ; c'est la branche qui
  suit (poser vs seulement vérifier) qui diverge (a).
- `:128-132` — `update_setup_status(setup_id, IN_POSITION, "Position
  protected and open")`. Écriture de statut pure, générique.

### (c) AMBIGU

- `:64-66` — la ligne `broker_position = await broker.simulate_fill(...)`
  est classée (a) ci-dessus par sa source, mais la **forme** de son
  résultat (`BrokerPosition`-like avec `.symbol`/`.quantity`) est la même
  interface que celle que `reconciliation.py` consomme déjà
  (`BrokerPosition`, import `reconciliation.py:5`, utilisé
  `:52,138-142,158`). Je ne suis pas certain si un composant partagé
  devrait prendre un `BrokerPosition` en paramètre (ce qui rendrait (b)
  directement réutilisable par les deux appelants) ou rester agnostique du
  type — dépend d'une décision de conception hors périmètre de ce lot.
- `:125-126` — `if stop_order.status in {REJECTED, ERROR}: return position`
  (pas d'écriture d'un statut d'erreur explicite à ce point précis, note
  déjà faite audit 19 §2.1 point 7). Ambigu parce que cette branche
  mélange une décision spécifique-simulation (on vient de *poser* le stop,
  donc on peut connaître son statut immédiat) avec un comportement qu'on
  voudrait sans doute commun (que faire si, côté réel, le stop n'est pas
  actif — audit 19 §3.2, obstacle #3). Le *code* de cette branche est
  spécifique, mais le *problème* qu'elle traite (que faire si le stop
  n'est pas garanti) est commun.

### Réponse directe : que resterait-il dans `fill_executor.py` si (b) était extrait ?

En retirant les lignes classées (b) ci-dessus, il resterait exactement :
`:43-49` (les deux gardes, dont celle qui rend la fonction inutilisable
pour un fill réel), `:51-58` (slippage simulé), `:60-66` (appel à
`broker.simulate_fill` et son garde de nullité), et la pose réactive du
stop `:119-124` (dans sa forme "pose", pas "vérifie"). Soit environ
**35 lignes sur 96** (`:38-133`) restant strictement spécifiques à la
simulation — le reste (statuts, position, stop check, événements) est,
par construction ligne à ligne, indépendant de `SimulatedBrokerConnector`.

---

## Q2 — Dépendances disponibles de chaque côté

### `ReconciliationEngine.__init__` (`reconciliation.py:35-45`, cité intégralement)

```python
def __init__(
    self,
    repository: TradingRepository,
    event_store: EventStore,
    broker: BrokerConnector,
    settings: dict[str, Any] | None = None,
) -> None:
    self.repository = repository
    self.event_store = event_store
    self.broker = broker
    self.settings = settings if isinstance(settings, dict) else {}
```

4 dépendances : `repository`, `event_store`, `broker`, `settings`.
**Aucun accès à `state_machine`** (confirmé : grep de `state_machine` dans
`reconciliation.py` entier → 0 occurrence). **Aucun accès direct à
`protection_snapshot_for_setup` en tant que dépendance nommée** — mais
cette méthode vit sur `self.repository` (`repositories.py:761-766`,
`TradingRepository.protection_snapshot_for_setup`), donc
`ReconciliationEngine` **y a accès transitivement** via `self.repository`,
exactement comme `fill_executor.py:117` y accède via le sien. Ce n'est pas
une dépendance manquante.

### `FillExecutor.__init__` (`fill_executor.py:24-36`, cité intégralement)

```python
def __init__(
    self,
    repository: TradingRepository,
    event_store: EventStore,
    broker_provider: Callable[[], BrokerConnector],
    stop_order_placer: StopOrderPlacer,
    settings: dict[str, Any] | None = None,
) -> None:
    self.repository = repository
    self.event_store = event_store
    self.broker_provider = broker_provider
    self.stop_order_placer = stop_order_placer
    self.settings = settings if isinstance(settings, dict) else {}
```

5 dépendances : `repository`, `event_store`, `broker_provider` (un
*callable*, pas l'instance directe — diffère de `ReconciliationEngine.broker`
qui est l'instance elle-même), `stop_order_placer` (protocole
`StopOrderPlacer`, `:13-20`, satisfait par `OrderManager` lui-même —
`order_manager.py:60`, `stop_order_placer=self`), `settings`.

### Comparaison directe

| Dépendance | `ReconciliationEngine` | `FillExecutor` |
|---|---|---|
| `repository` | Oui (instance directe) | Oui (instance directe) |
| `event_store` | Oui | Oui |
| broker | Oui, **instance directe** (`self.broker`) | Oui, **callable** (`self.broker_provider()`) |
| `settings` | Oui (`settings.raw`, dict complet) | Oui (dict, même origine) |
| `stop_order_placer` (= `OrderManager`) | **Non** | Oui |
| `state_machine` | **Non** | **Non** (aucune des deux classes n'y a accès) |
| `protection_snapshot_for_setup` | Transitif via `repository` | Transitif via `repository` |

### Ce qui manquerait à `ReconciliationEngine` pour exécuter (b)

Pour exécuter la partie (b) de Q1 telle quelle, `ReconciliationEngine`
dispose déjà de `repository` (donc `update_order_status`,
`update_setup_status`, `upsert_position`, `protection_snapshot_for_setup`,
`get_setup` — toutes des méthodes utilisées par (b), déjà appelées
ailleurs dans `reconciliation.py` : `update_setup_status` par ex.
`:162-166,178-182,193-197,213-218,249-253,435-439,461-465`) et de
`event_store` (déjà utilisé, ex. `:59-64,75-80,124-129`). **Constat : rien
ne manque structurellement pour la partie (b) seule** — les 2 dépendances
nécessaires (`repository`, `event_store`) sont déjà présentes dans les 2
constructeurs.

Ce qui manquerait **si** on voulait aussi répliquer la branche (a) "pose
réactive du stop" (ce que Q1 exclut déjà comme non pertinent côté réel,
le stop étant déjà transmis en bracket) serait `stop_order_placer` — mais
l'audit 19 (§3.1) établit que ce besoin ne se pose pas de la même façon
côté réel. Constat, pas proposition : si une future implémentation voulait
quand même une capacité "vérifier et, si absent, reposer un stop" côté
réel, `ReconciliationEngine` n'a aujourd'hui aucune référence à
`OrderManager`/`StopOrderPlacer` et il faudrait la lui injecter.

### Qui construit ces deux objets, et où

`trading_engine.py`, dans `TradingEngine.__init__` (méthode non entièrement
numérotée dans cette lecture, plage vue `:130-229`) :

- `OrderManager` construit ligne `:153-163`, avec `repository`,
  `self.event_store`, `self.broker`, et `settings.raw` (partiel, clés
  `orders.*`/`setup_defaults.entry.limit_offset`). `OrderManager.__init__`
  construit lui-même son `FillExecutor` interne (`order_manager.py:56-62`,
  `stop_order_placer=self`) — **`FillExecutor` n'est donc jamais construit
  directement par `trading_engine.py`**, il est un objet privé
  d'`OrderManager`.
- `ReconciliationEngine` construit ligne `:170-175`, avec `repository`,
  `self.event_store`, `self.broker`, `settings.raw` (le dict complet, pas
  un sous-ensemble). Construit **après** `OrderManager` (ligne 170 > 153)
  mais rien dans le code lu n'indique une dépendance d'ordre entre les
  deux — ils partagent seulement `repository`/`event_store`/`broker`.
- `self.state_machine = StateMachine()` construit ligne `:177`, **après**
  les deux** — et n'est passé ni à `OrderManager` ni à
  `ReconciliationEngine` (seul `SetupLifecycleService`, ligne `:178-184`,
  le reçoit).

---

## Q3 — Où pourrait vivre un composant partagé

### Graphe d'imports réel (imports `app.*` uniquement, relevé par grep sur chaque fichier)

```
reconciliation.py   → app.broker.ib_models, app.broker.tws_connector,
                       app.engine.broker_reality, app.models,
                       app.setups.setup_roles, app.storage.event_store,
                       app.storage.repositories
fill_executor.py    → app.broker.tws_connector, app.engine.transaction_costs,
                       app.models, app.storage.event_store,
                       app.storage.repositories
order_manager.py    → app.broker.order_mapper, app.broker.tws_connector,
                       app.engine.fill_executor, app.models,
                       app.setups.setup_roles, app.storage.event_store,
                       app.storage.repositories, app.utils.id_generator
state_machine.py    → app.models (uniquement)
repositories.py     → app.models, app.storage.database
                       (aucun import de app.engine.* — confirmé, grep dédié)
models.py           → aucun import app.* (confirmé, grep dédié — module
                       racine, dataclasses/enums purs)
```

**Aucun cycle existant entre ces 6 fichiers.** Direction unique observée :
`order_manager.py → fill_executor.py` (le seul lien direct entre les deux
fichiers visés par ce lot, et il est à sens unique : `fill_executor.py`
n'importe pas `order_manager.py`, confirmé par son bloc d'imports
`:1-10`). `reconciliation.py` et `fill_executor.py` **ne s'importent pas
mutuellement aujourd'hui** — aucun cycle à craindre entre eux directement.

### Un module de bas niveau existe-t-il déjà ?

`app/models.py` et `app/storage/repositories.py` sont les deux seuls
modules de ce graphe qui n'importent **aucun** module de `app/engine/*`
(confirmé ci-dessus). Ce sont donc, par construction du graphe actuel, les
seuls points d'appui "sans risque de cycle" pour un nouveau module
importable par `reconciliation.py` ET `fill_executor.py` : un nouveau
fichier placé, par exemple, au même niveau que `fill_executor.py`
lui-même (dans `app/engine/`) mais qui n'importe que `app.models` et
`app.storage.repositories`/`app.storage.event_store` — comme le fait déjà
`fill_executor.py` lui-même (ses propres imports, `:1-10`, ne remontent
vers aucun autre module de `app/engine/`) — serait, par construction du
graphe actuel, importable des deux côtés sans cycle. Ceci est un constat
sur la topologie existante, pas une proposition d'emplacement : le point
exact (nouveau fichier vs module existant) est une décision de conception
hors périmètre.

### Un composant équivalent existe-t-il déjà ailleurs dans le dépôt ?

Grep exhaustif de `ENTRY_FILLED`, `IN_POSITION`, et
`protection_snapshot_for_setup` sur tout `app/` :

- `ENTRY_FILLED` / `IN_POSITION` : présents dans `app/models.py:55,59`
  (définition de l'enum `SetupStatus`), `app/engine/fill_executor.py:105,130`
  (les 2 seules écritures rencontrées dans ce lot), `app/engine/
  reconciliation.py:251` (**une seule occurrence**, dans la boucle
  `MANAGEMENT_ONLY`/`adopt_existing_ibkr_position` déjà documentée en
  audit 19 §5.1 — un chemin d'adoption de position existante, **pas** une
  progression post-fill), `app/engine/state_machine.py` (table de
  transitions, déjà couverte audit 19 §2.3), `app/engine/broker_reality.py:19,22`
  (constantes de classification pour le rapport de cohérence, lecture
  seule), `app/engine/setup_condition_tracker.py:29,32`,
  `app/engine/setup_status_reporter.py:138,142,276`,
  `app/engine/setup_diagnostics.py:533`, `app/setups/position_management.py:48`,
  `app/setups/trailing_runner.py:19`, `app/storage/repositories.py:201`,
  `app/background_jobs.py:26` — tous des **lecteurs** de statut (filtrage,
  affichage, garde de fonctionnalité), aucun n'écrit `ENTRY_FILLED` ou
  `IN_POSITION` par une logique de "fill → vérif stop → position protégée".
- `protection_snapshot_for_setup` : 5 occurrences —
  `fill_executor.py:117` (chemin simulé, déjà connu),
  `order_manager.py:73` (utilisé avant la pose d'un nouveau stop, contexte
  différent : `place_stop_order`, pas une progression post-fill),
  `repositories.py:761` (définition), `setup_lifecycle_service.py:476`
  (contexte de revalidation de lifecycle, pas de fill), `signal_engine.py:216`
  (contexte de scan/signal, pas de fill).

**Conclusion Q3 (constat) : aucune 2e implémentation de la progression
"fill → vérif stop → `IN_POSITION`" n'existe ailleurs dans `app/`.**
`reconciliation.py:251` est un mécanisme distinct (adoption d'une position
IBKR préexistante à l'armement d'un setup `MANAGEMENT_ONLY`, pas une
réaction à un fill d'entrée) et ne doit pas être confondu avec le besoin
posé par ce lot — un futur correctif écrirait bien une 2e implémentation
du même besoin, pas une 3e.

---

## INCERTITUDES RÉSIDUELLES

1. Le classement (c) de Q1 (`:64-66`, `:125-126`) dépend d'une décision de
   conception (forme d'interface du composant partagé, gestion du cas
   "stop non garanti actif") hors périmètre de ce lot — déjà signalé comme
   obstacle #3 dans `audit/19_pre_impl_reconciliation.md` (section
   "OBSTACLES À LA CIBLE").
2. Ce lot n'a pas vérifié si `settings.raw` (passé en entier à
   `ReconciliationEngine`) et `settings` (sous-ensemble passé à
   `OrderManager`/`FillExecutor` via `order_manager.py:162`) contiennent
   des clés incompatibles pour un usage commun par un composant partagé —
   les deux valent le dict complet `settings.raw` d'après la lecture de
   `trading_engine.py:162,174`, donc probablement sans écart, mais non
   vérifié champ par champ.
3. Le format exact que prendrait la donnée d'entrée côté réel (un
   `BrokerPosition` de `broker.positions()`, une `BrokerExecution` de
   `recent_executions()`, ou une combinaison des deux — cf. audit 19 §1.1)
   n'est pas tranché par ce lot ; cela conditionne directement si (b) est
   réutilisable telle quelle ou doit être adaptée en signature.
4. Aucune vérification par exécution (tests) n'a été effectuée dans ce
   lot — uniquement lecture de code, conformément au mode audit.

---

## COÛT DE LA FACTORISATION (constat, pas conception)

**Fichiers qui seraient touchés** par l'extraction de (b) hors de
`fill_executor.py` :
- `app/engine/fill_executor.py` (`:38-133`) — la fonction perdrait ses
  lignes (b), garderait (a), et appellerait le composant partagé.
- Un nouveau fichier ou un module existant sans import `app.engine.*`
  (`app/models.py`/`app/storage/repositories.py` exclus car ce sont des
  définitions de données/persistance, pas un lieu naturel pour de la
  logique métier ; Q3 ne tranche pas le fichier exact) pour héberger (b).
- `app/engine/reconciliation.py` — un nouveau site d'appel (probablement
  dans `_update_setup_after_reconciled_order`, `:412-465`, le point déjà
  identifié comme le plus naturel par `audit/19` §"OBSTACLES", obstacle #2)
  devrait importer et appeler ce composant.
- `app/engine/order_manager.py` (`:56-62`) — si la signature de
  `FillExecutor.__init__` change pour déléguer (b), le site de
  construction devrait suivre.

**Tests existants couvrant `fill_executor.py`** (risque de régression
direct) :
- `tests/test_fill_executor.py` — 3 tests :
  `test_simulated_fill_creates_position_and_protective_stop` (:83),
  `test_stop_rejection_keeps_setup_in_manual_review` (:101),
  `test_missing_or_non_submitted_order_is_ignored` (:116).
- `tests/test_order_manager.py` — au moins 1 site d'appel identifié,
  `simulate_fill_order` (`:113`), donc au moins 1 test de ce fichier
  exerce `FillExecutor` indirectement via `OrderManager`.

**Tests existants couvrant `reconciliation.py`** (risque de régression sur
le chemin réel si le point d'appel change dans `_update_setup_after_
reconciled_order`) :
- `tests/test_reconciliation.py` — 3 tests :
  `test_open_orders_query_error_is_not_empty_ok` (:38),
  `test_positions_query_error_is_not_empty_ok` (:51),
  `test_positions_query_error_does_not_wrongly_cancel_local_orders` (:64).
- `tests/test_setup_roles.py` — référence `ReconciliationEngine`
  (contexte du rôle `MANAGEMENT_ONLY`, périmètre différent de la
  progression post-fill mais partage le même fichier source).

**Risque de régression, tel qu'observable par ce lot** : la couverture
directe de `fill_executor.py` est **faible en nombre** (3 tests dédiés +
1 test indirect via `order_manager`) mais couvre les 3 branches
principales (succès complet, rejet de stop, garde d'entrée). Un
changement qui déplace (b) hors du fichier devrait, au minimum, continuer
à faire passer ces 3+1 tests sans modification de leurs assertions (ils
testent le comportement observable de `simulate_fill_order`, pas son
implémentation interne) — un signal que la factorisation est,
structurellement, testable sans réécrire les tests existants, si
l'interface externe de `simulate_fill_order` reste stable. Le risque
inverse (couverture insuffisante pour capter une régression fine dans (b)
une fois extrait) n'est pas mesurable par lecture de code seule.
