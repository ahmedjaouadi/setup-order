# Audit 16 — Pré-implémentation du gate current_status (rang 1)

Mode : audit lecture seule. Aucune conception, aucune correction. Chaque
affirmation cite fichier:ligne, vérifié directement dans le code au moment
de l'audit (2026-07-19).

## Q1 — Le point d'ancrage principal (`_handle_signal`)

**Signature exacte**, `app/engine/trading_engine.py:2463-2465` :

```python
async def _handle_signal(
    self, setup: dict[str, Any], current_status: SetupStatus, signal: Any
) -> None:
```

- Nom : `_handle_signal`
- Paramètres : `self`, `setup: dict[str, Any]`, `current_status: SetupStatus`, `signal: Any`
- Retour : `None`
- Ligne de définition confirmée aujourd'hui : **2463** (et non 2463-2470 comme
  simple hypothèse — 2463 est bien la ligne `async def`, le corps va de 2466
  à 2470).

**Corps exact**, `app/engine/trading_engine.py:2466-2470` :

```python
if self.action_executor.execute_simple_action(setup, current_status, signal):
    return
if self.position_action_executor.execute_raise_stop_signal(setup, current_status, signal):
    return
await self.entry_order_executor.execute_entry_ready(setup, signal)
```

Remarque factuelle : le 3e appel ne passe PAS `current_status` en argument —
seul `setup` et `signal` sont transmis (`app/engine/trading_engine.py:2470`).
`execute_entry_ready` ne reçoit donc pas `current_status` depuis ce site
d'appel.

**`current_status` — type et provenance**

- Type déclaré au paramètre : `SetupStatus` (`app/engine/trading_engine.py:2464`).
- `SetupStatus` est importé dans `trading_engine.py` en tant que membre du
  bloc d'import `from app.models import (...)` — présence confirmée par usage
  direct (`SetupStatus.CLOSED.value` etc.) à `app/engine/trading_engine.py:495-500`,
  `716-721`, `1596-1610`, `2226`.
- Provenance runtime : `_handle_signal` est enregistré comme callback
  `signal_handler` à `app/engine/trading_engine.py:293`
  (`signal_handler=self._handle_signal`). Il est invoqué à
  `app/engine/stock_market_monitor.py:293-297` :
  ```python
  await self.signal_handler(
      evaluation.setup,
      evaluation.current_status,
      evaluation.signal,
  )
  ```
  `evaluation` est une instance de `SignalEvaluation`
  (`app/engine/signal_engine.py:42-47`), dont le champ
  `current_status: SetupStatus` (`app/engine/signal_engine.py:45`) est
  construit à `app/engine/signal_engine.py:74`
  (`current_status = SetupStatus(setup["status"])`), avec un cas particulier
  ligne 77-78 : si `current_status == SetupStatus.DISABLED`, il est remplacé
  par `strategy.initial_status()` avant d'être propagé.
- Type exact confirmé au runtime : `SetupStatus` (enum `StrEnum`, voir Q2),
  pas une simple `str` brute — la conversion `SetupStatus(setup["status"])`
  a lieu explicitement à `app/engine/signal_engine.py:74`.

**`signal` (3e argument) — type et lecture de l'action**

- Type déclaré dans la signature de `_handle_signal` : `Any`
  (`app/engine/trading_engine.py:2464`), mais le type réel transporté au
  runtime est `SetupSignal` : `evaluation.signal` provient du champ
  `signal: SetupSignal` de `SignalEvaluation` (`app/engine/signal_engine.py:46`).
- Définition du type `SetupSignal` : `app/models.py:230-238` :
  ```python
  @dataclass(slots=True)
  class SetupSignal:
      action: SignalAction
      reason: str
      target_status: SetupStatus | None = None
      entry_price: float | None = None
      stop_loss: float | None = None
      new_stop: float | None = None
      metadata: dict[str, Any] = field(default_factory=dict)
  ```
- L'action portée par le signal se lit via l'attribut `.action`
  (`signal.action`), de type `SignalAction` (`app/models.py:232`).
  Confirmé par l'usage effectif dans les trois exécuteurs, ex.
  `app/engine/action_executor.py:30` (`if signal.action == SignalAction.HOLD`),
  `app/engine/position_action_executor.py:33`
  (`if signal.action != SignalAction.RAISE_STOP ...`),
  `app/engine/entry_order_executor.py:55`
  (`if signal.action != SignalAction.ENTRY_READY`).
- Définition de `SignalAction` : `app/models.py:88-93` :
  ```python
  class SignalAction(SerializableEnum):
      HOLD = "HOLD"
      STATUS_CHANGE = "STATUS_CHANGE"
      ENTRY_READY = "ENTRY_READY"
      INVALIDATE = "INVALIDATE"
      RAISE_STOP = "RAISE_STOP"
  ```

## Q2 — L'enum `SetupStatus` — noms exacts des statuts

**Localisation** : `app/models.py:36-68`. L'enum hérite de `SerializableEnum`
(`app/models.py:13`, elle-même `class SerializableEnum(StrEnum): pass`) —
donc chaque membre est à la fois un nom Python et une `str` dont `.value`
égale le nom (aucun membre n'a une `.value` différente de son nom dans ce
fichier).

**Liste exhaustive des 31 membres** (`app/models.py:37-68`), nom = `.value`
pour chacun :

```
DRAFT, LOADED, VALIDATED, DISABLED, WAITING_ACTIVATION, WAITING_BREAKOUT,
MISSED_BREAKOUT, MISSED_BREAKOUT_WAIT_RETEST, STALE_SETUP, BLOCKED,
WAITING_RETEST, WAITING_REBOUND, WAITING_CONFIRMATION, REARMED_ON_NEW_BASE,
WAITING_ENTRY_SIGNAL, ENTRY_READY, ENTRY_ORDER_PLACED,
ENTRY_PARTIALLY_FILLED, ENTRY_FILLED, STOP_ORDER_PLACED, STOP_PLACED,
RECONCILING_EXISTING_POSITION, IN_POSITION, MANAGING_POSITION, PARTIAL_EXIT,
CLOSED, EXPIRED, INVALIDATED, CANCELLED, MANUAL_REVIEW_REQUIRED,
ERROR_REQUIRES_MANUAL_REVIEW, ERROR
```

Ligne exacte de chaque membre : `DRAFT` 37, `LOADED` 38, `VALIDATED` 39,
`DISABLED` 40, `WAITING_ACTIVATION` 41, `WAITING_BREAKOUT` 42,
`MISSED_BREAKOUT` 43, `MISSED_BREAKOUT_WAIT_RETEST` 44, `STALE_SETUP` 45,
`BLOCKED` 46, `WAITING_RETEST` 47, `WAITING_REBOUND` 48,
`WAITING_CONFIRMATION` 49, `REARMED_ON_NEW_BASE` 50,
`WAITING_ENTRY_SIGNAL` 51, `ENTRY_READY` 52, `ENTRY_ORDER_PLACED` 53,
`ENTRY_PARTIALLY_FILLED` 54, `ENTRY_FILLED` 55, `STOP_ORDER_PLACED` 56,
`STOP_PLACED` 57, `RECONCILING_EXISTING_POSITION` 58, `IN_POSITION` 59,
`MANAGING_POSITION` 60, `PARTIAL_EXIT` 61, `CLOSED` 62, `EXPIRED` 63,
`INVALIDATED` 64, `CANCELLED` 65, `MANUAL_REVIEW_REQUIRED` 66,
`ERROR_REQUIRES_MANUAL_REVIEW` 67, `ERROR` 68.

**Statuts "post-entrée / en position" — vérification des 9 noms proposés**
(la consigne en annonce 10 mais n'en liste que 9 — écart signalé en fin de
section) :

| Nom proposé | Existe tel quel ? | Ligne |
|---|---|---|
| `ENTRY_ORDER_PLACED` | Oui | `app/models.py:53` |
| `ENTRY_PARTIALLY_FILLED` | Oui | `app/models.py:54` |
| `ENTRY_FILLED` | Oui | `app/models.py:55` |
| `STOP_ORDER_PLACED` | Oui | `app/models.py:56` |
| `STOP_PLACED` | Oui | `app/models.py:57` |
| `IN_POSITION` | Oui | `app/models.py:59` |
| `MANAGING_POSITION` | Oui | `app/models.py:60` |
| `PARTIAL_EXIT` | Oui | `app/models.py:61` |
| `RECONCILING_EXISTING_POSITION` | Oui | `app/models.py:58` |

Aucun écart : les 9 noms existent exactement tels que supposés, aucune
divergence de casse ou d'orthographe.

Vérification de complétude (recherche d'un 10e statut "post-entrée" oublié) :
en parcourant les 31 membres listés ci-dessus, aucun autre nom ne désigne un
état "déjà entré / en position" au sens du gate. Les statuts restants
(`DRAFT`, `LOADED`, `WAITING_BREAKOUT`, `STALE_SETUP`, `BLOCKED`,
`WAITING_REBOUND`, `CLOSED`, `EXPIRED`, `INVALIDATED`, `CANCELLED`,
`MANUAL_REVIEW_REQUIRED`, `ERROR_REQUIRES_MANUAL_REVIEW`, `ERROR`,
`DISABLED`) sont soit pré-entrée, soit terminaux/erreur — aucun ne
correspond à "en position". Donc : liste de 9 confirmée comme exhaustive
pour la sémantique "post-entrée / en position" ; le "10" annoncé dans la
consigne ne correspond à aucun statut réel supplémentaire trouvé.

**Statuts "pré-entrée / éligibles à une entrée" — vérification des 8 noms
proposés** :

| Nom proposé | Existe tel quel ? | Ligne |
|---|---|---|
| `WAITING_ACTIVATION` | Oui | `app/models.py:41` |
| `WAITING_ENTRY_SIGNAL` | Oui | `app/models.py:51` |
| `ENTRY_READY` | Oui | `app/models.py:52` |
| `WAITING_RETEST` | Oui | `app/models.py:47` |
| `WAITING_CONFIRMATION` | Oui | `app/models.py:49` |
| `VALIDATED` | Oui | `app/models.py:39` |
| `REARMED_ON_NEW_BASE` | Oui | `app/models.py:50` |
| `MISSED_BREAKOUT` | Oui | `app/models.py:43` |

Aucun écart : les 8 noms existent exactement tels que supposés.

Écart à signaler (hors périmètre strict de la question, mais pertinent pour
la calibration du futur gate) : deux statuts existants ont une sémantique
proche de "pré-entrée" et ne figurent dans AUCUNE des deux listes fournies —
`WAITING_BREAKOUT` (`app/models.py:42`) et `WAITING_REBOUND`
(`app/models.py:48`). De même, `MISSED_BREAKOUT_WAIT_RETEST`
(`app/models.py:44`) est distinct de `MISSED_BREAKOUT` et n'est cité dans
aucune des deux listes — il est traité comme statut "terminal" côté moteur
de signal (voir `TERMINAL_SIGNAL_STATUSES`, `app/engine/signal_engine.py:36`),
donc `_handle_signal` n'est jamais appelé pour ce statut (le setup est
filtré en amont, `app/engine/signal_engine.py:79-80`). Idem pour `DISABLED`
(`app/engine/signal_engine.py:29`) qui est remplacé avant propagation
(`app/engine/signal_engine.py:77-78`) et `BLOCKED`/`STALE_SETUP`
(`app/engine/signal_engine.py:34-35`).

## Q3 — `execute_entry_ready` : signature et contrat de retour

**Signature exacte aujourd'hui**, `app/engine/entry_order_executor.py:50-54` :

```python
async def execute_entry_ready(
    self,
    setup: dict[str, Any],
    signal: Any,
) -> bool:
```

Confirmation : la méthode ne reçoit PAS `current_status` — seuls `setup` et
`signal` sont des paramètres (en plus de `self`). L'audit 08 est confirmé
exact sur ce point (`app/engine/entry_order_executor.py:50-53`).

**Retour et signification** : le type de retour déclaré est `bool` (ligne 54,
pas `None` — à corriger si un audit antérieur affirmait un retour `None`).
Parcours de tous les `return` du corps :
- `return False` uniquement à la ligne 56, quand
  `signal.action != SignalAction.ENTRY_READY` (`app/engine/entry_order_executor.py:55-56`)
  — c'est-à-dire "ce n'était pas mon signal, je n'ai rien fait".
- Tous les autres chemins retournent `True` : blocage session policy (78),
  blocage fenêtre d'exécution (93), blocage trade guards (110), blocage
  MANAGEMENT_ONLY (126), auto-exécution désactivée (146), blocage lifecycle
  (150), stop trailing manquant/non prêt (169, 186), risque non approuvé
  (210), coût NO_GO (231), blocage broker reality (260), et enfin la
  transmission effective de l'ordre — succès ou exception métier
  (`BrokerModeMismatchError`, `ManagementOnlyEntryError`,
  `DuplicateOrderError`, `UnprotectedActiveOrderError`) — toutes convergent
  vers `return True` en ligne 305.
- Donc le contrat est : `True` = "signal ENTRY_READY traité (transmis ou
  bloqué avec raison)", `False` = "signal non pertinent pour cette méthode".
  C'est strictement le même contrat que les deux autres exécuteurs (voir
  ci-dessous).

**Usage du retour dans `_handle_signal`** — code exact,
`app/engine/trading_engine.py:2466-2470` :

```python
if self.action_executor.execute_simple_action(setup, current_status, signal):
    return
if self.position_action_executor.execute_raise_stop_signal(setup, current_status, signal):
    return
await self.entry_order_executor.execute_entry_ready(setup, signal)
```

- Le 1er appel (`execute_simple_action`) est bien dans un `if ...: return`
  (ligne 2466-2467) : `True` = "géré, on s'arrête".
- `execute_simple_action` retourne `bool` — signature
  `app/engine/action_executor.py:25-29`, corps
  `app/engine/action_executor.py:30-38` : `return True` pour HOLD (31),
  INVALIDATE avec target_status (33-35), STATUS_CHANGE avec target_status
  (36-38) ; `return False` sinon (38).
- Le 2e appel (`execute_raise_stop_signal`) est bien dans un `if ...: return`
  (ligne 2468-2469) : même sémantique. Signature
  `app/engine/position_action_executor.py:28-33`, corps
  `app/engine/position_action_executor.py:34-38` : `return False` si
  `signal.action != SignalAction.RAISE_STOP or signal.new_stop is None`
  (34-35), sinon `return True` (38) après avoir potentiellement transitionné
  le statut (36-37).
- Le 3e appel (`execute_entry_ready`, ligne 2470) est **awaité sans tester
  son retour** : pas de `if`, pas d'affectation, la valeur `bool` retournée
  est silencieusement jetée. Confirmé littéralement par le texte de la
  ligne 2470 : `await self.entry_order_executor.execute_entry_ready(setup, signal)`.

**Conséquence pour un futur gate** : comme le retour du 3e appel n'est
actuellement lu par personne dans `_handle_signal`, un gate ajouté à
l'intérieur d'`execute_entry_ready` qui "ne fait rien et s'arrête" n'a
besoin d'aucune forme de retour particulière pour être fonctionnellement
correct côté `_handle_signal` — un simple `return True` (ou `return` tout
court si la signature passait à `None`) suffirait à ce site d'appel
spécifique. En revanche, pour rester COHÉRENT avec le contrat existant
`bool` (True = "signal traité") déjà vérifié par les tests unitaires — voir
`tests/test_entry_order_executor.py:75-79` :
```python
handled = await self.executor.execute_entry_ready(setup, signal)
self.assertTrue(handled)
```
— tout ajout de branche de blocage dans cette méthode devra suivre le même
motif `return True` que les branches de blocage existantes (lignes 78, 93,
110, 126, 146, 150, 169, 186, 210, 231, 260, 305), sous peine de casser ce
contrat testé pour les cas déjà couverts par la suite de tests.

## Q4 — Où placer la constante `ENTRY_ELIGIBLE_STATUSES`

**`TERMINAL_SIGNAL_STATUSES`** : reconfirmé à `app/engine/signal_engine.py:24-37` :

```python
TERMINAL_SIGNAL_STATUSES = {
    SetupStatus.CLOSED,
    SetupStatus.CANCELLED,
    SetupStatus.EXPIRED,
    SetupStatus.INVALIDATED,
    SetupStatus.DISABLED,
    SetupStatus.ERROR,
    SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW,
    SetupStatus.BLOCKED,
    SetupStatus.STALE_SETUP,
    SetupStatus.MISSED_BREAKOUT_WAIT_RETEST,
}
```
Ligne d'ouverture 24, ligne de fermeture 37 — identique à ce que les audits
précédents indiquaient.

**Modules important déjà `SetupStatus` et graphe d'imports réel** :

- `app/engine/trading_engine.py` : importe `SetupStatus` via le bloc
  `from app.models import (...)` (usages confirmés lignes 495-500, 716-721,
  1596-1610, 2226, 2464). Imports internes du fichier (`app/engine/trading_engine.py:12-62`)
  incluent notamment `app.engine.action_executor`, `app.engine.entry_order_executor`,
  `app.engine.position_action_executor`, `app.engine.signal_engine`,
  `app.engine.state_machine`, `app.engine.stock_market_monitor`, `app.models`.
- `app/engine/entry_order_executor.py` : `from app.models import EventLevel, SetupStatus, SignalAction`
  (`app/engine/entry_order_executor.py:23`). Imports internes
  (`app/engine/entry_order_executor.py:8-26`) : `app.engine.broker_reality`,
  `app.engine.order_manager`, `app.engine.risk_engine`,
  `app.engine.session_policy`, `app.engine.trade_guards`,
  `app.engine.transaction_costs`, `app.models`, `app.setups.setup_roles`,
  `app.storage.event_store`, `app.storage.repositories`. **Aucun import de
  `app.engine.signal_engine` ni de `app.engine.trading_engine`.**
- `app/engine/signal_engine.py` : `from app.models import MarketSnapshot, SetupSignal, SetupStatus, SignalAction, to_jsonable`
  (`app/engine/signal_engine.py:19`). Imports internes
  (`app/engine/signal_engine.py:8-22`) : `app.engine.entry_decision`,
  `app.engine.session_policy`, `app.engine.setup_lifecycle_service`,
  `app.engine.trade_guards`, `app.engine.transaction_costs`, `app.models`,
  `app.settings`, `app.setups.setup_factory`, `app.storage.repositories`.
  **Aucun import de `app.engine.entry_order_executor` ni de
  `app.engine.trading_engine`.**
- `app/engine/action_executor.py` : `from app.models import EventLevel, SetupStatus, SignalAction`
  (`app/engine/action_executor.py:7`).
- Recherche exhaustive (`grep -rn "entry_order_executor"`) : le SEUL fichier
  qui importe `EntryOrderExecutor` est `app/engine/trading_engine.py:23`.
  Donc `entry_order_executor.py` pourrait importer depuis `signal_engine.py`
  sans créer de cycle (rien dans la chaîne de dépendances de
  `signal_engine.py` ne remonte vers `entry_order_executor.py`) — vérifié en
  inspectant les imports de `app/setups/setup_factory.py:5-12` (uniquement
  des modules `app.setups.*`) et `app/engine/entry_decision.py:5-6`
  (uniquement `app.models` et `app.setups.setup_roles`), aucun ne référence
  `entry_order_executor`.

**Candidat "neutre"** : `app/models.py` n'a AUCUN import interne au projet
(`grep -n "^from app\|^import app" app/models.py` ne retourne rien — le
fichier n'importe que `dataclasses`, `datetime`, `enum`, `typing` de la
stdlib). C'est déjà le module le plus bas du graphe de dépendances, et les
quatre fichiers concernés (`trading_engine.py`, `entry_order_executor.py`,
`signal_engine.py`, `action_executor.py`) importent TOUS directement
`SetupStatus` depuis `app.models` (lignes citées ci-dessus). Une constante
`ENTRY_ELIGIBLE_STATUSES` (ou équivalent) définie dans `app/models.py` serait
donc importable par `_handle_signal` (trading_engine.py) et par
`execute_entry_ready` (entry_order_executor.py) sans aucun risque de cycle,
puisque `models.py` ne dépend de rien dans `app.*`. Alternative possible :
`app/engine/signal_engine.py` (où vit déjà `TERMINAL_SIGNAL_STATUSES`),
également sans cycle d'après le graphe tracé ci-dessus, mais moins "neutre"
que `models.py` puisque `signal_engine.py` porte déjà de la logique métier
(imports de `trade_guards`, `session_policy`, etc.) plutôt que de purs
constants/types.

## Q5 — Les autres chemins d'émission d'entrée (couverture du gate)

**Grep exhaustif de `execute_entry_ready`** (`grep -rn "execute_entry_ready" --include=*.py app/ tests/`) :

- `app/engine/entry_order_executor.py:50` — la définition elle-même.
- `app/engine/trading_engine.py:2470` — le seul appelant en code applicatif
  (`await self.entry_order_executor.execute_entry_ready(setup, signal)`),
  c'est-à-dire uniquement depuis `_handle_signal`.
- `tests/test_entry_order_executor.py:75, 101, 119, 142, 167, 189, 220, 247`
  — huit appels, tous en tests unitaires directs de la méthode (hors
  périmètre applicatif).

**Conclusion partielle** : dans le code applicatif (hors tests),
`_handle_signal` est bien le SEUL chemin par lequel `execute_entry_ready`
est appelé.

**Recherche d'un autre point de déclenchement d'entrée côté moteur
d'exécution (hors `app/setups/`)** — grep de `ENTRY_READY` /
`place_entry_order` dans `app/engine/` :

Tous les usages d'`ENTRY_READY` dans `app/engine/` sont soit des LECTURES de
diagnostic/affichage (`setup_diagnostics.py`, `setup_status_reporter.py`,
`opportunity_alert_service.py`), soit des vérifications de gate en amont
(`signal_engine.py:145` dans `_apply_trade_guard_gates`, qui ne fait que
retourner un signal modifié, sans jamais appeler `order_manager` ni
`execute_entry_ready`), soit des définitions de graphe d'états
(`state_machine.py:99,114,121,127,229`), soit `entry_decision.py` qui ne
fait qu'attacher des métadonnées de décision (aucun appel à
`order_manager`).

En revanche, **un troisième chemin d'émission d'ordre d'entrée existe bel et
bien**, indépendant de `_handle_signal` ET de `execute_entry_ready` :

`grep -rn "place_entry_order"` (la méthode réelle de soumission broker,
`app/engine/order_manager.py:64`) donne exactement deux appelants :
1. `app/engine/entry_order_executor.py:263` (le chemin automatique déjà
   analysé, via `execute_entry_ready`).
2. **`app/engine/manual_order_service.py:293`**, dans la méthode
   `_submit_buy` (`app/engine/manual_order_service.py:262-310`) :
   ```python
   order = await self.order_manager.place_entry_order(setup, decision)
   ```
   Ce chemin est déclenché par `submit()` (`app/engine/manual_order_service.py:82`)
   — la soumission manuelle d'ordre depuis l'UI/API — via `_assess_buy`
   (`app/engine/manual_order_service.py:146-231`) puis `_submit_buy`. **Ce
   chemin ne passe ni par `_handle_signal`, ni par `execute_entry_ready`.**
   Vérification faite : `_assess_buy`
   (`app/engine/manual_order_service.py:146-231`) contrôle stop-loss requis
   (155-163), prix de référence marché (165-176), fenêtre d'exécution
   (190-201), trade guards (203-212), limites de risque (214-217), coûts de
   transaction (219-231) — **mais ne contient aucune vérification du
   `current_status` du setup ciblé**. Aucune ligne de `_assess_buy` ne lit
   `setup["status"]` ou équivalent.

Filet de sécurité existant à un niveau différent (pour mémoire, ne relève
pas de `current_status`) : `order_manager.place_entry_order`
(`app/engine/order_manager.py:64-83`) applique, pour LES DEUX appelants
(`entry_order_executor.py:263` et `manual_order_service.py:293`), un
contrôle basé sur `protection_snapshot_for_setup` — `UnprotectedActiveOrderError`
si une position ouverte n'a pas de stop actif (`app/engine/order_manager.py:74-77`),
`UnprotectedActiveOrderError` si un ordre d'entrée actif n'a pas de stop
attaché (`app/engine/order_manager.py:78-81`), `DuplicateOrderError` si un
ordre protégé actif existe déjà (`app/engine/order_manager.py:83`). Ce
contrôle est fondé sur l'état des ORDRES/POSITIONS au broker
(`protection_snapshot_for_setup`), pas sur le champ `current_status` du
setup — il ne peut donc pas se substituer au gate `SetupStatus` demandé (un
setup en `WAITING_ACTIVATION` ré-entré manuellement sur un setup par
ailleurs `IN_POSITION` via un `setup_id` différent, par exemple, ne serait
pas nécessairement intercepté par ce contrôle si les ordres/positions
associés à CE `setup_id` précis sont fermés au niveau broker).

**Conclusion factuelle** : le gate à deux niveaux envisagé
(`_handle_signal` + `execute_entry_ready`) NE couvre PAS tous les chemins.
Il existe un troisième chemin non couvert : la soumission manuelle via
`ManualOrderService._submit_buy` → `order_manager.place_entry_order`
(`app/engine/manual_order_service.py:293`), qui ne transite ni par
`_handle_signal` ni par `execute_entry_ready` et qui n'effectue aujourd'hui
aucune vérification de `current_status`.

## INCERTITUDES RÉSIDUELLES

1. **Portée voulue du gate rang 1** : la consigne ne précise pas si le gate
   `current_status` doit aussi couvrir le chemin manuel
   (`manual_order_service.py`) ou seulement le chemin automatique
   (`_handle_signal` / `execute_entry_ready`). C'est une question de
   périmètre, pas de fait — non tranchée ici volontairement (hors mandat de
   cet audit lecture seule).
2. **Sémantique exacte attendue de `assess_buy`** : je n'ai pas exploré
   `_assess_buy`/`_synthetic_setup` en détail pour savoir si `assessment["setup_id"]`
   correspond toujours à un setup existant en base avec un `status` réel, ou
   peut être un setup synthétique sans statut persistant
   (`_synthetic_setup`, `app/engine/manual_order_service.py:435`, non lue en
   détail). Cela conditionne si un gate `current_status` y aurait même un
   sens pour tous les cas d'usage manuels.
3. **`opportunity_audit/replay.py`** et **`market_context/service.py`**
   contiennent des occurrences de `ENTRY_READY` (listés par le grep du Q5)
   que je n'ai pas ouvertes en détail — je soupçonne qu'il s'agit de code de
   replay/audit hors chemin d'exécution live, mais je ne l'ai pas confirmé
   ligne par ligne.
4. Le graphe d'imports tracé au Q4 est basé sur les imports de haut niveau
   (`from app... import`) des fichiers cités ; je n'ai pas vérifié
   l'absence d'imports différés (imports locaux à l'intérieur de fonctions)
   qui pourraient introduire un cycle non visible dans les imports de
   module. Aucun indice trouvé en ce sens dans les fichiers lus, mais je ne
   l'ai pas exclu par une recherche dédiée (`grep -n "    from app"` /
   `grep -n "    import app"` non exécutée).
5. Point non demandé mais potentiellement pertinent pour calibrer le rang 1 :
   `app/engine/setup_lifecycle_service.py:67,82` référence aussi
   `SetupStatus.ENTRY_READY.value` — je n'ai pas exploré ce module en
   détail pour savoir s'il a un rôle dans la boucle d'entrée (il semble
   plutôt lié à la revalidation de lifecycle, déjà croisé indirectement via
   `_lifecycle_allows_transmission`, `app/engine/entry_order_executor.py:307-350`).
