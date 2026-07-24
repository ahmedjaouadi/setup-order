# Audit 26 — Clôture de la phase sécurité (lecture seule)

Mode : audit lecture seule strict. Aucune modification de code. Commandes
exécutées en dehors de lectures de fichiers et de `git status`/`git log`/`git
diff`/`git merge-base` : `sqlite3` en `mode=ro` (via `sqlite3.connect(...,
uri=True)`) sur `data/trading_state.sqlite`, et `python -m pytest` (suites
ciblées puis suite complète, aucune écriture de code entre les deux).
Date d'audit : 2026-07-23, sur la branche `fix/03b2-filled-branch`.

Objectif : ne pas déclarer la phase sécurité close sur des affirmations.
Chaque problème (A1-A6) est prouvé fermé, ou déclaré ouvert.

---

## Q1 — Inventaire exhaustif des chemins d'ordre d'entrée

### Méthode

Point d'ancrage : `OrderManager.place_entry_order` (`app/engine/order_manager.py:64`),
la seule méthode qui construit un `OrderRecord` BUY et appelle
`self.broker.submit_order(...)` pour une entrée réelle (`order_manager.py:113-115`).
Recherche exhaustive de ses appelants (`Grep` sur `place_entry_order`,
`execute_entry_ready`, `manual_order_service\.`, glob `*.py`, tout le dépôt) :
**9 fichiers seulement** contiennent l'une de ces trois chaînes :
`tests/test_entry_order_executor.py`, `tests/test_entry_gate_current_status.py`,
`tests/test_fill_executor.py`, `tests/test_order_manager.py` (tests, hors
périmètre), et **`app/engine/trading_engine.py`, `app/engine/entry_order_executor.py`,
`app/engine/manual_order_service.py`, `app/api/routes_orders.py`,
`app/engine/order_manager.py`** — soit exactement les fichiers déjà identifiés
ci-dessous. Aucun script, CLI, scheduler ou tâche cron n'a été trouvé en dehors
de `app/` et `tests/` (même recherche, résultat vide en dehors des 9 fichiers
listés).

**Conclusion de la recherche : il n'existe que DEUX appelants réels de
`place_entry_order`** dans le code applicatif :
1. `app/engine/entry_order_executor.py:267` (chemin automatique).
2. `app/engine/manual_order_service.py:293` (chemin manuel).

Aucun troisième chemin non gaté n'a été trouvé au-delà du chemin manuel déjà
connu (audit 16). C'est un résultat négatif documenté, pas une absence de
recherche.

### Chemin A — Automatique (piloté par signal)

```
app/engine/stock_market_monitor.py:293-297
  await self.signal_handler(evaluation.setup, evaluation.current_status, evaluation.signal)
    ↓ (signal_handler=self._handle_signal, trading_engine.py:293)
app/engine/trading_engine.py:2463  async def _handle_signal(self, setup, current_status, signal)
  trading_engine.py:2472-2486  <-- GATE 1
    if signal.action == SignalAction.ENTRY_READY and current_status not in ENTRY_ELIGIBLE_STATUSES:
        event_store.record(..., "entry_gate_blocked", ...)
        return
  trading_engine.py:2487
    await self.entry_order_executor.execute_entry_ready(setup, signal, current_status)
      ↓
app/engine/entry_order_executor.py:50-57  <-- GATE 2 (défense en profondeur)
    async def execute_entry_ready(self, setup, signal, current_status):
        if current_status not in ENTRY_ELIGIBLE_STATUSES:
            return True
      ↓ (si passé les deux gates + tous les autres garde-fous métier, ligne 267)
entry_order_executor.py:267
    await self.order_manager.place_entry_order(effective_setup, decision)
```

`ENTRY_ELIGIBLE_STATUSES` : `frozenset` défini `app/models.py` (commit
`0aaf12a`, 2026-07-23), 9 membres (`WAITING_ACTIVATION`, `WAITING_ENTRY_SIGNAL`,
`ENTRY_READY`, `WAITING_RETEST`, `WAITING_CONFIRMATION`, `WAITING_REBOUND`,
`REARMED_ON_NEW_BASE`, `VALIDATED`, `MISSED_BREAKOUT`) — liste blanche, pas
liste noire (confirmé par `test_whitelist_blocks_hypothetical_status_by_default`,
`tests/test_entry_gate_current_status.py:206-221`).

**GATÉ : OUI, doublement.** Le gate est vérifié à deux points indépendants
(`trading_engine.py:2472` avant même d'invoquer l'exécuteur, et
`entry_order_executor.py:56` à l'intérieur de l'exécuteur si jamais il était
appelé par un autre chemin futur) — commit `0aaf12a` "fix(safety): gate
ENTRY_READY on current_status before broker submission", 2026-07-23.

### Chemin B — Manuel (piloté par l'API)

```
POST /api/orders/manual  (app/api/routes_orders.py:59-61)
    result = await request.app.state.engine.manual_order_service.submit(payload.model_dump())
      ↓
app/engine/manual_order_service.py:82-106  async def submit(self, payload)
    assessment = await self._assess(payload)          # :109-144
      ↓ side == "BUY"
    manual_order_service.py:141  await self._assess_buy(assessment, allow_unprotected, now)
      # :146-231 — vérifie : stop_loss requis (155-163), prix de référence marché
      # (165-176), fenêtre de session (190-201), trade_guards (203-212), limites
      # de risque (214-217), cost gate (219-231).
      # AUCUNE de ces vérifications ne lit un current_status de setup.
    manual_order_service.py:100  orders = await self._submit_buy(assessment)   # :262-310
      ↓
manual_order_service.py:293
    order = await self.order_manager.place_entry_order(setup, decision)
    # setup = self._synthetic_setup(assessment), construit ligne 282,
    # à partir d'un setup_id ENTIÈREMENT NEUF : assessment["setup_id"] =
    # new_id("man") (manual_order_service.py:121, à l'intérieur de _assess).
```

**GATÉ : NON.** Aucune ligne de `_assess_buy` (`manual_order_service.py:146-231`)
ni de `_submit_buy` (`:262-310`) ne lit `current_status`. Confirmé inchangé
par le commit de gate lui-même : `git log 9544cab..HEAD --
app/engine/manual_order_service.py` → aucun résultat, ce fichier n'a reçu
aucun commit depuis avant le début de `feat/setup-conditions`.

**Nuance factuelle importante, pas une excuse** : parce que
`assessment["setup_id"]` est généré neuf à chaque appel (`new_id("man")`),
ce chemin n'a structurellement pas de `current_status` de setup existant sur
lequel s'appuyer — ce n'est pas qu'un gate a été oublié sur un champ présent,
c'est que le concept même de "statut du setup" n'a pas de porteur pour cette
requête. Deux conséquences :
- Le mécanisme exact de l'incident du 29 juin (un `setup_id` existant qui
  repasse par le pipeline d'entrée alors qu'il est déjà `ENTRY_ORDER_PLACED`/
  `IN_POSITION`) **ne peut pas se reproduire par ce chemin**, puisqu'aucun
  `setup_id` réel n'est jamais réutilisé.
- Mais le filet de sécurité de `order_manager.place_entry_order` lui-même
  (`protection_snapshot_for_setup`, `order_manager.py:73-83`,
  `DuplicateOrderError`/`UnprotectedActiveOrderError`) est **également**
  neutralisé pour la même raison : il est indexé par `setup_id`, qui est
  neuf à chaque soumission manuelle — donc deux achats manuels successifs du
  même symbole ne seront jamais vus comme "doublon" par ce garde-fou non plus.
  Le seul filet restant est au niveau de l'exposition totale
  (`risk_limits.max_total_exposure_usd`, `manual_order_service.py:423-432`)
  et le jugement humain — pas un gate `current_status`.

Ce chemin était déjà identifié dans l'audit 16 (§Q5, alors sur les numéros de
ligne `manual_order_service.py:394-401`) comme non couvert. Il **reste non
couvert aujourd'hui**, inchangé par les 6 commits de correctifs de sécurité
qui ont suivi.

### Tableau de synthèse Q1

| Chemin | Point d'entrée | Gaté sur `current_status` ? | Preuve (fichier:ligne) |
|---|---|---|---|
| A — Automatique (signal) | `stock_market_monitor.py:293` → `_handle_signal` | **OUI** (double) | `trading_engine.py:2472-2486` (gate 1) ; `entry_order_executor.py:56-57` (gate 2) ; `app/models.py` `ENTRY_ELIGIBLE_STATUSES` (commit `0aaf12a`) |
| B — Manuel (API) | `POST /api/orders/manual` → `manual_order_service.submit` | **NON** | `manual_order_service.py:146-231` (`_assess_buy`, aucune lecture de `current_status`) ; `manual_order_service.py:293` (appel direct à `place_entry_order`) ; confirmé inchangé par `git log 9544cab..HEAD -- app/engine/manual_order_service.py` (vide) |

**Résultat le plus important demandé par cet audit** : aucun TROISIÈME chemin
non gaté n'a été trouvé au-delà du chemin manuel déjà connu. Le chemin manuel
reste ouvert, sans changement depuis l'audit 16.

---

## Q2 — Le gate est-il prouvé pour chaque `setup_type` ?

### Le test nominal EST paramétré par `setup_type` ; le test de blocage NE L'EST PAS

`tests/test_entry_gate_current_status.py:45-51` :

```python
ENTRY_CAPABLE_SETUP_TYPES = [
    SetupType.AGGRESSIVE_REBOUND,
    SetupType.BREAKOUT_RETEST,
    SetupType.PULLBACK_CONTINUATION,
    SetupType.MOMENTUM_BREAKOUT,
    SetupType.RANGE_BREAKOUT,
]
```

Le test nominal boucle bien dessus (`tests/test_entry_gate_current_status.py:159-186`) :

```python
async def test_nominal_entry_transmitted_for_each_setup_type(self) -> None:
    """Proof 1: the gate is transparent for the normal flow, for all 5 entry-capable types."""
    for setup_type in ENTRY_CAPABLE_SETUP_TYPES:
        with self.subTest(setup_type=setup_type.value):
            ...
```

Le test de blocage, en revanche (`tests/test_entry_gate_current_status.py:188-204`, cité
intégralement) :

```python
async def test_entry_ready_blocked_for_each_post_entry_status(self) -> None:
    """Proof 2: ENTRY_READY on any post-entry status places no order and emits entry_gate_blocked."""
    for status in POST_ENTRY_STATUSES:
        with self.subTest(status=status.value):
            setup_id = f"RANGE_{status.value}_001"
            record = _setup_record(setup_id, "RNGB", "range_breakout", status)
            self.repository.upsert_setup(record)
            self._seed_broker_reality()

            setup = self.repository.get_setup(setup_id)
            self.assertIsNotNone(setup)
            await self.engine._handle_signal(setup, status, _entry_ready_signal())

            self.assertEqual(self.repository.list_orders(setup_id), [])
            events = self.repository.list_events(limit=1, setup_id=setup_id)
            self.assertEqual(events[0]["event_type"], "entry_gate_blocked")
            self.assertEqual(events[0]["data"]["current_status"], status.value)
```

La boucle est bien sur `POST_ENTRY_STATUSES` (les 9 statuts post-entrée,
`:33-43`) — mais `setup_type` est **codé en dur à `"range_breakout"`**
(ligne 193), identique à chaque itération. Ce n'est pas un test paramétré par
type comme l'est le test nominal ; c'est un test paramétré par statut, exécuté
une seule fois pour un seul type.

### Inventaire complet des `setup_type` utilisés côté blocage, dans ce fichier

| Test | Ligne | `setup_type` utilisé | Statuts couverts |
|---|---|---|---|
| `test_entry_ready_blocked_for_each_post_entry_status` | `:193` | `"range_breakout"` (fixe) | les 9 `POST_ENTRY_STATUSES` |
| `test_whitelist_blocks_hypothetical_status_by_default` | `:211` | `"range_breakout"` (fixe) | `STALE_SETUP` (hypothétique) |
| `test_replay_2026_06_29_incident_no_second_order` | `:234` | `"range_breakout"` (fixe) | `ENTRY_ORDER_PLACED` (rejeu incident) |
| `EntryOrderExecutorDefenseInDepthTests` (gate 2, direct) | `:294` | `"breakout_retest"` (fixe) | `ENTRY_ORDER_PLACED` |

**`momentum_breakout` : zéro occurrence dans un test de blocage.** Il n'apparaît
que dans `ENTRY_CAPABLE_SETUP_TYPES` (côté nominal) et dans
`tests/test_momentum_breakout.py` (hors périmètre de ce fichier, tests du
moteur de signal `momentum_breakout.py` lui-même, pas du gate).
`pullback_continuation` et `aggressive_rebound` : également zéro occurrence
côté blocage.

### Réponse directe à la question posée

**Non, cette preuve n'existe pas pour `momentum_breakout`.** Aucun test ne
seed un setup `momentum_breakout` à `ENTRY_ORDER_PLACED` / `ENTRY_FILLED` /
`IN_POSITION` puis n'affirme qu'aucun ordre n'est placé. La preuve existe
seulement pour `range_breakout` (couvert 3 fois) et `breakout_retest`
(couvert 1 fois, sur le gate 2 uniquement).

**Facteur atténuant, pas un substitut à la preuve manquante** : le gate
lui-même est architecturalement agnostique au `setup_type`. Vérifié
directement :

```
app/engine/trading_engine.py:2472-2473
    if signal.action == SignalAction.ENTRY_READY and current_status not in ENTRY_ELIGIBLE_STATUSES:
app/engine/entry_order_executor.py:56
    if current_status not in ENTRY_ELIGIBLE_STATUSES:
```

Aucune des deux conditions ne lit `setup.get("setup_type")` ou équivalent —
le choix du type de setup n'intervient qu'**en amont**, dans la méthode
`evaluate()` propre à chaque type, pour décider *quand* émettre un signal
`ENTRY_READY` ; le gate qui bloque ensuite ce signal est un point de passage
unique, commun à tous les types. Confirmé par grep direct :

```
$ grep -n "current_status" app/setups/momentum_breakout.py app/setups/range_breakout.py
app/setups/momentum_breakout.py:25:        current_status: SetupStatus,
app/setups/momentum_breakout.py:36:        return self._analyze_long(snapshot, current_status)
app/setups/momentum_breakout.py:41:        current_status: SetupStatus,
app/setups/momentum_breakout.py:179:        if current_status == SetupStatus.MISSED_BREAKOUT and retest["touched_zone"]:
app/setups/range_breakout.py:22:        current_status: SetupStatus,
```

`momentum_breakout.py` ne lit `current_status` que pour sa branche
`MISSED_BREAKOUT` (ligne 179) — jamais pour sa branche d'émission
`ENTRY_READY`, exactement la même lacune que `range_breakout.py` (qui ne le
lit nulle part dans le corps). C'est précisément la raison pour laquelle
l'incident du 29 juin a nécessité un gate placé **au-dessus** de ces deux
méthodes plutôt qu'un correctif dans chacune (audit 05, §A.3, point 3) — donc,
par construction du point de passage unique, un test qui prouve le blocage
pour `range_breakout` prouve le même mécanisme de code pour
`momentum_breakout`. Mais **ce n'est qu'un argument de lecture de code, pas
un test qui l'affirme** — si une régression future introduisait une branche
conditionnelle au `setup_type` dans le gate (par exemple une exemption mal
placée), aucun test de cette suite ne la détecterait pour `momentum_breakout`
spécifiquement, alors qu'il la détecterait pour `range_breakout`.

**Conclusion Q2** : le test de blocage n'est pas paramétré par `setup_type`
comme l'est le test nominal — c'est un fait, cité au code (`:193`, `"range_breakout"`
fixe). La preuve testée du blocage pour `momentum_breakout` n'existe pas ;
seule une preuve architecturale (lecture de code, point de passage unique)
la remplace, ce qui est un cran plus faible que ce que l'ordre demandait.

---

## Q3 — La branche FILLED est-elle atteignable en conditions réelles ?

### INCERTITUDE 1 (audit 23) — un ordre FILLED reste-t-il visible dans `ib.trades()` ?

Audit 23 concluait : "non vérifiable en lecture statique, nécessiterait une
session TWS réelle ou la doc `ib_insync`." Le paquet `ib_async` réellement
installé dans ce dépôt (`C:\Users\AhmedJAOUADI\AppData\Local\Programs\Python\Python311\Lib\site-packages\ib_async`)
permet en réalité de trancher une partie de cette question par lecture
statique — ce n'est pas de la documentation, c'est le code source exécuté.

**Dans une session continue (pas de reconnexion)** :

```
ib_async/ib.py:594-596
    def trades(self) -> list[Trade]:
        """List of all order trades from this session."""
        return list(self.wrapper.trades.values())

ib_async/ib.py:598-604
    def openTrades(self) -> list[Trade]:
        """List of all open order trades."""
        return [v for v in self.wrapper.trades.values()
                if v.orderStatus.status not in OrderStatus.DoneStates]
```

`trades()` — utilisé par ce dépôt — ne filtre PAS les `DoneStates` (Filled y
compris), contrairement à `openTrades()`. Et le connecteur de ce dépôt utilise
bien `trades()`, pas `openTrades()`, pour construire les statuts de
réconciliation :

```
app/broker/tws_connector.py:1056-1078
    async def order_statuses(self) -> dict[str, str]:
        ...
        trades = list(self._ib.trades())
        ...
```

`self.wrapper.trades` est un simple `dict`, alimenté par les callbacks
`newOrder`/`orderStatus`/`completedOrder` (`ib_async/wrapper.py:686-730`) et
**jamais purgé** sauf par `wrapper.reset()` (`ib_async/wrapper.py:313-318`,
`self.trades = {}` ligne 318), lui-même appelé uniquement par
`ib.disconnect()` (`ib_async/ib.py:401-406`, commentaire du code source :
"clear ALL internal state from this connection").

**Conclusion tranchée par lecture statique** : tant que la connexion TWS
reste active sans coupure entre le fill et le prochain sondage de
réconciliation, un ordre FILLED **reste visible** dans `order_statuses()`
indéfiniment — ce n'est plus une incertitude.

**Ce qui reste une incertitude réelle : le cas d'une reconnexion entre le
fill et le sondage suivant.** `wrapper.reset()` vide `self.trades`, mais le
dépôt appelle `ib.connectAsync(...)` sans `fetchFields`
(`app/broker/tws_connector.py:648-653`), donc avec la valeur par défaut
`StartupFetchALL` (`ib_async/ib.py:71-78`), qui inclut
`StartupFetch.ORDERS_COMPLETE` → déclenche `reqCompletedOrdersAsync`
(`ib_async/ib.py:2273-2276`) → IB répond par des callbacks `completedOrder`
qui **repeuplent** `self.trades[order.permId]` pour chaque ordre terminé
(`ib_async/wrapper.py:722-730`) :

```python
def completedOrder(self, contract, order, orderState):
    ...
    if order.permId not in self.permId2Trade:
        self.trades[order.permId] = trade
        self.permId2Trade[order.permId] = trade
```

Donc le mécanisme de repeuplement existe et est câblé dans ce dépôt. Ce qui
**ne peut pas** être tranché par lecture du code client `ib_async` : sur
combien de temps le **serveur** TWS/IB conserve les ordres "complétés" pour
les retourner via `reqCompletedOrders` (comportement serveur IB, pas visible
dans la bibliothèque cliente présente dans ce dépôt). Ce point précis
nécessite bien la documentation officielle de l'API IB (absente de ce dépôt)
ou une session réelle.

**Verdict INCERTITUDE 1** : partiellement levée par lecture statique — le cas
"session continue" est prouvé (oui, visible indéfiniment) ; le cas
"reconnexion" a un mécanisme de récupération identifié mais sa fenêtre de
rétention côté serveur IB reste non vérifiable sans TWS réel ou doc
officielle.

### Fenêtre des exécutions (audit 24 Q2) — confirmée non tranchable statiquement

```
ib_async/ib.py:618-620
    def fills(self) -> list[Fill]:
        return list(self.wrapper.fills.values())
```

Alimenté en direct par `execDetails` et, à la connexion, par
`reqExecutionsAsync()` avec un `ExecutionFilter()` vide
(`ib_async/ib.py:2278-2285`, également déclenché par `StartupFetchALL` via
`StartupFetch.EXECUTIONS`). `ExecutionFilter` est un simple objet de requête
transmis au serveur — **rien dans le code client `ib_async` n'encode ou ne
documente la fenêtre "jour courant seulement"** revendiquée par l'audit 24 :
c'est un comportement du serveur IB, invisible depuis la bibliothèque
cliente installée dans ce dépôt. Ce point de l'audit 24 **n'est ni confirmé
ni infirmé** par cet audit — il reste tel quel, non vérifiable sans doc
officielle IB ou test réel.

### Trace en base d'un ordre d'entrée réel passé à FILLED — rejouable ?

Requête `mode=ro` sur `data/trading_state.sqlite` :

```
ord_7410bb0b3e7b | LUNR_20260630_001 | LUNR | BUY | STP_LMT | FILLED
  created_at 2026-06-30T09:51:59 | updated_at 2026-06-30T15:18:05
  broker_order_id=6332 | broker_perm_id=None
```

Événements associés à `LUNR_20260630_001` :
```
2026-06-30T15:17:05  ORDER  entry_order_submitted        "Accepted by TWS: PendingSubmit"
2026-06-30T15:18:05  SYNC   order_status_reconciled      "Order marked CANCELLED after broker reconciliation"  (le stop)
2026-06-30T15:18:05  SYNC   order_status_reconciled      "Order marked FILLED after broker reconciliation"     (l'entrée)
```

C'est un ordre d'entrée BUY réel, passé à FILLED par réconciliation réelle,
23 jours avant cet audit, toujours présent en base aujourd'hui.

**Effet à l'époque (code pré-3b-2)** : conforme à ce que prédisait l'audit 23
("tout autre statut — y compris FILLED — tombe directement dans le garde
CANCELLED et sort sans rien faire") — confirmé empiriquement : le setup
`LUNR_20260630_001` est **encore aujourd'hui** au statut `ENTRY_ORDER_PLACED`,
`last_event = "Bracket order submitted"` (le message d'origine de la
soumission, jamais mis à jour depuis). La table `positions` ne contient
**aucune ligne pour `LUNR`** (requête `SELECT * FROM positions WHERE
symbol='LUNR'` → vide). Le fill est réel, l'argent a été engagé au broker, et
le setup local n'en a jamais eu connaissance — preuve en base, pas
hypothétique, que le trou identifié par l'audit 23 a eu une conséquence
réelle en production.

**Les 4 setups du 29 juin (`GILT_20260628_001`, `LUNR_20260628_001`,
`QBTS_20260628_001`, `STM_20260628_001`)** : requêtés dans `orders` et
`positions` aujourd'hui — **0 ligne pour les 4**, confirmant la purge déjà
documentée par l'audit 05 (`order_history_deleted`, 58 occurrences). Ces
4 setups ne sont donc **pas** rejouables du tout, faute de toute donnée
résiduelle.

**Peut-on rejouer `LUNR_20260630_001` contre le nouveau code 3b-2 ? Non, pas
tel quel, pour deux raisons distinctes :**

1. **Aucune table `fills`/`executions` n'existe dans le schéma** (`SELECT
   name FROM sqlite_master WHERE type='table'` → 45 tables listées, aucune
   ne porte de ligne `BrokerExecution`). Le prix et la quantité de ce fill
   réel n'ont jamais été persistés nulle part — ils n'ont existé que dans le
   cache mémoire de session `ib.fills()` il y a 23 jours, et sont
   aujourd'hui irrécupérables. Le barreau 1 de la nouvelle branche FILLED
   (`_match_executions_to_order`, `reconciliation.py:718-741`), qui est
   précisément ce que 3b-1/3b-2 ont ajouté, ne peut donc pas être rejoué
   contre cet enregistrement historique.
2. **Le garde d'idempotence bloque toute réexécution.** `_reconcile_local_orders`
   (`reconciliation.py:372`) : `if current_status not in
   _ACTIVE_ORDER_STATUSES: continue` — l'ordre est **déjà** marqué `FILLED`
   localement (`orders.status`), donc toute future passe de réconciliation
   l'ignore purement et simplement, y compris avec le code 3b-2 actif. Rejouer
   la séquence exigerait de remettre manuellement `orders.status` à
   `SUBMITTED` en base — une mutation que ce mode lecture seule interdit et
   que je n'ai pas effectuée. **Aucun rejeu réel n'a donc été exécuté** ; ce
   qui précède est une trace de code (lecture), pas une exécution.

Conséquence non demandée mais factuelle et pertinente pour la clôture :
`LUNR_20260630_001` reste **aujourd'hui, en base réelle**, désynchronisé
(statut `ENTRY_ORDER_PLACED`, aucune position trackée) — le lot 3b-2 corrige
le flux à partir de maintenant mais ne répare rétroactivement rien de ce cas
précis, qui nécessiterait une intervention manuelle hors périmètre de cet
audit.

### Conclusion factuelle Q3 — part du lot 3b-2 non prouvée sans session TWS réelle

| Sous-question | Statut |
|---|---|
| Un ordre FILLED reste-t-il visible dans `ib.trades()` (session continue) ? | **Tranché par lecture statique : OUI** |
| Idem après une reconnexion ? | Mécanisme de récupération identifié, fenêtre de rétention **serveur IB non vérifiable statiquement** |
| Fenêtre des exécutions limitée au jour courant ? | **Non tranchable statiquement** (comportement serveur IB, ni confirmé ni infirmé) |
| Appariement exécution↔ordre (barreau 1, prix/quantité réels) | **Jamais exercé sur une donnée réelle** — aucune trace de `BrokerExecution` n'est persistée en base, et le seul ordre FILLED réel disponible ne peut pas être rejoué sans muter la base |
| Routage FILLED→BUY→branche (le "squelette" ajouté par 3b-1/3b-2) | Exercé en tests avec mocks (audit 25) ; confirmé cohérent avec un cas réel historique par lecture de code, mais **non exécuté** contre ce cas réel |

**La part non prouvée sans session TWS réelle** couvre exactement le cœur
métier du lot : le calcul du prix pondéré et l'appariement exécution↔ordre
(barreau 1) n'ont jamais tourné sur une exécution réelle, seulement sur des
`BrokerExecution` construits à la main dans les tests. "Testé avec des
mocks" n'est pas "prouvé en conditions réelles" — aucun des deux mécanismes
ci-dessus ne peut l'être sans TWS réel, et cet audit ne prétend pas avoir
comblé cet écart, seulement l'avoir borné plus précisément qu'avant.

---

## Q4 — A6 : inventaire à jour des écritures de statut

### Inventaire complet des sites d'écriture de `setups.status`

Toute écriture passe par une seule méthode repository :
`TradingRepository.update_setup_status` (`app/storage/repositories.py:454-479`,
`UPDATE setups SET status = ...`). Grep exhaustif de ses appelants dans
`app/` : **27 sites d'appel**, dans 8 fichiers.

| # | Fichier:ligne | Contexte | Passe par `state_machine` ? |
|---|---|---|---|
| 1 | `action_executor.py:69` | `transition_setup` (INVALIDATE/STATUS_CHANGE) | **OUI** — `can_transition` (:48) + `transition()` (:58) avant l'écriture |
| 2 | `position_action_executor.py:69` | `transition_setup` (RAISE_STOP) | **OUI** — `transition()` (:58) avant l'écriture |
| 3 | `setup_lifecycle_service.py:415` | revalidation périodique | **OUI** — `explain_transition()` (:410), écriture seulement si `decision.allowed` |
| 4 | `entry_order_executor.py:125` | blocage MANAGEMENT_ONLY | NON |
| 5 | `entry_order_executor.py:284` | exception `ManagementOnlyEntryError` | NON |
| 6 | `order_manager.py:126` | rejet broker de l'entrée | NON |
| 7 | `order_manager.py:180` | bracket soumis (`ENTRY_ORDER_PLACED`) | NON |
| 8 | `order_manager.py:352` | rejet broker du stop | NON |
| 9 | `order_manager.py:374` | stop soumis (`STOP_ORDER_PLACED`, `update_setup_status=True`) | NON |
| 10 | `order_manager.py:466` | stop rattaché (`attach_missing_stop`) | NON |
| 11 | `order_manager.py:543` | échec protection → annulation parent | NON |
| 12 | `post_fill_progression.py:45` | fill sans stop trailing configuré | NON |
| 13 | `post_fill_progression.py:64` | `record_fill` → `ENTRY_FILLED` | NON |
| 14 | `post_fill_progression.py:92` | `mark_in_position` → `IN_POSITION` | NON |
| 15 | `reconciliation.py:176` | adoption bloquée, position introuvable | NON |
| 16 | `reconciliation.py:192` | adoption bloquée, stop protecteur manquant | NON |
| 17 | `reconciliation.py:207` | adoption bloquée, prix sous le stop | NON |
| 18 | `reconciliation.py:228` | adoption bloquée, ordre stop introuvable | NON |
| 19 | `reconciliation.py:263` | position existante adoptée → `IN_POSITION` | NON |
| 20 | `reconciliation.py:465` | ordre SUBMITTED restauré | NON |
| 21 | `reconciliation.py:493` | **[3b-2]** FILLED sans détails fiables → `ENTRY_FILLED` | NON |
| 22 | `reconciliation.py:498` | **[3b-2]** idem → `MANUAL_REVIEW_REQUIRED` | NON |
| 23 | `reconciliation.py:522` | **[3b-2]** FILLED sans protection active → `MANUAL_REVIEW_REQUIRED` | NON |
| 24 | `reconciliation.py:541` | stop annulé chez le broker, position ouverte | NON |
| 25 | `reconciliation.py:556` | entrée annulée chez le broker | NON |
| 26 | `setup_engine.py:258` | `arm_setup` | NON |
| 27 | `setup_engine.py:277` | `disarm_setup` (`DISABLED`) | NON |

**Total : 27 sites. 3 passent par `state_machine` (11%). 24 ne passent pas.**

Comparaison avec l'audit 05/06 (4 mécanismes recensés hors state_machine) :
ce chiffre a largement augmenté depuis, pas seulement à cause du lot 3b-2 —
`reconciliation.py` à lui seul en compte 11 aujourd'hui. Le lot 3b-2 a
ajouté 3 nouveaux sites directs (#21, #22, #23 ci-dessus) ; il utilise aussi
`post_fill_progression.mark_in_position` (#14), site préexistant mais
désormais atteint par un second appelant réel (`reconciliation.py:520`, en
plus de `fill_executor.py:96`).

### Une transition illégale au regard d'`ALLOWED_TRANSITIONS` peut-elle être écrite silencieusement ?

**Oui — confirmé, et ce n'est pas hypothétique : c'est le chemin nominal du
lot 3b-2 lui-même.**

`ALLOWED_TRANSITIONS[SetupStatus.ENTRY_FILLED]` (`state_machine.py:145-150`) :

```python
SetupStatus.ENTRY_FILLED: {
    SetupStatus.STOP_ORDER_PLACED,
    SetupStatus.STOP_PLACED,
    SetupStatus.ERROR,
    SetupStatus.MANUAL_REVIEW_REQUIRED,
},
```

`IN_POSITION` **n'y figure pas**. Séquence réelle du chemin nominal de la
branche FILLED (`reconciliation.py:513-521`) :

```python
position = self.progression.record_fill(order_id, setup_id, quantity, fill_price, symbol)
if position is None:
    return
protection_verified = self.progression.has_active_protection(setup_id)
if protection_verified:
    self.progression.mark_in_position(setup_id, protection_verified=protection_verified)
    return
```

`record_fill` écrit `ENTRY_FILLED` (`post_fill_progression.py:64-68`) ;
`mark_in_position`, appelé immédiatement après, écrit `IN_POSITION`
(`post_fill_progression.py:92-96`) — **directement, sans passer par
`STOP_ORDER_PLACED` ni `STOP_PLACED`**, sans jamais consulter
`state_machine.can_transition`/`transition`/`explain_transition`. C'est
exactement le scénario prouvé par
`test_barreau1_nominal_weighted_price_reaches_in_position`
(`tests/test_reconciliation.py`, cas nominal du lot) — donc ce n'est pas un
cas limite rare, c'est le chemin de succès normal.

**Vérification que ce n'est pas un artefact de lecture** : qui écrit
`STOP_ORDER_PLACED` en pratique ? Seul `order_manager.py:374`, à l'intérieur
de `place_stop_order`, et seulement si son paramètre
`update_setup_status=True` (par défaut). Or ses deux seuls appelants
passent explicitement `update_setup_status=False` :
`place_entry_order` (`order_manager.py:164`) et `attach_missing_stop`
(`order_manager.py:444`). **`STOP_ORDER_PLACED` n'est donc, en pratique,
jamais écrit par le flux d'entrée réel** — la transition
`ENTRY_FILLED → STOP_ORDER_PLACED → IN_POSITION` que la table déclare comme
seule voie légale n'est empruntée par aucun appelant réel du code actuel.
Le seul chemin qui écrit effectivement `IN_POSITION` depuis `ENTRY_FILLED`
(`post_fill_progression.py:92`, appelé par `fill_executor.py:96` en
simulation et par `reconciliation.py:520` en réel) le fait **en dehors de la
table qu'il est censé respecter**.

**Nuance nécessaire** : ce n'est pas une régression introduite par 3b-2 — le
même contournement existait déjà côté simulation
(`fill_executor.py:96-99`, hors périmètre du lot, confirmé par l'audit 25
Q5 : aucune modification de `fill_executor.py`/`post_fill_progression.py`
dans ce lot). Ce que 3b-2 change, c'est qu'il **ajoute un second appelant
réel** (`reconciliation.py:520`) au même contournement préexistant — la
branche FILLED réelle hérite d'une incohérence architecturale qui n'a jamais
été corrigée, elle ne l'introduit pas.

**Sur l'exemple cité par la consigne** (`ENTRY_ORDER_PLACED →
MANUAL_REVIEW_REQUIRED`) : vérifié également absent de
`ALLOWED_TRANSITIONS[ENTRY_ORDER_PLACED]` (`state_machine.py:133-138`, qui
n'autorise que `ENTRY_PARTIALLY_FILLED`, `ENTRY_FILLED`, `CANCELLED`,
`ERROR`). Mais en pratique, aucun site du lot 3b-2 n'écrit cette transition
en un seul saut : les deux endroits qui finissent en
`MANUAL_REVIEW_REQUIRED` (`reconciliation.py:498` et `:522`) le font
**après** avoir déjà réécrit `ENTRY_FILLED` juste avant (ligne 495 ou via
`record_fill`), et `ENTRY_FILLED → MANUAL_REVIEW_REQUIRED` **est** autorisée
par la table. Donc, contrairement à `ENTRY_FILLED → IN_POSITION`, l'exemple
cité par la consigne n'est pas exploitable tel quel dans le code actuel —
c'est `ENTRY_FILLED → IN_POSITION` qui est le cas réel et grave, pas
l'exemple donné.

### Conclusion Q4

- **27 sites d'écriture, 3 gatés (11%), 24 non gatés.**
- **Oui**, une transition absente d'`ALLOWED_TRANSITIONS` est écrite
  silencieusement, sur le chemin nominal du lot 3b-2 : `ENTRY_FILLED →
  IN_POSITION` (`post_fill_progression.py:92`, appelé depuis
  `reconciliation.py:520`). C'est un contournement préexistant
  (`fill_executor.py`, hors 3b-2) auquel 3b-2 ajoute un second appelant réel,
  pas un contournement neuf. La table `ALLOWED_TRANSITIONS` documente donc
  une invariante que le code ne respecte pas dans son chemin de succès le
  plus fréquent — soit la table est fausse (il manque `IN_POSITION` dans les
  cibles d'`ENTRY_FILLED`), soit le code l'est (il devrait transiter par
  `STOP_ORDER_PLACED`/`STOP_PLACED`) ; dans l'état actuel, aucun test ni
  garde-fou ne détecterait laquelle des deux dérive en premier.

---

## Q5 — État git réel

```
$ git branch --show-current
fix/03b2-filled-branch

$ git status --short --branch
## fix/03b2-filled-branch
 D data/setups/CODI_20260628_001.json
 D data/setups/TXN_20260630_001.json
?? .codex/
?? audit/
?? data/setups/ALAB_20260713_001.json   (+ 12 autres fichiers de setups du jour)
?? tmp/
```

Aucun fichier suivi de `app/` ou `tests/` n'apparaît dans `git status` —
confirmé par `git diff HEAD -- app/engine/reconciliation.py
app/engine/post_fill_progression.py tests/test_reconciliation.py` : sortie
vide. **Contrairement à l'audit 25**, qui constatait que tout le travail du
lot 3b-2 était non commité, ce n'est plus le cas aujourd'hui : le lot 3b-2 a
depuis été commité (`c3a44df`). Le seul écart de forme relevé par l'audit 25
(#2, littéral `True` passé à `mark_in_position`) a également été corrigé
entre-temps : `git show HEAD:app/engine/reconciliation.py | grep
protection_verified` confirme que la ligne 520 passe désormais la variable
`protection_verified`, plus un littéral.

### Graphe des 12 derniers commits (`git log --oneline --graph -12`)

```
* c3a44df feat(reconciliation): write post-fill statuses on real fills          <- HEAD, fix/03b2-filled-branch
* 30e2385 fix(reconciliation): plumb broker_executions and add pure execution matcher   <- fix/03b1-executions-plumbing
* 2a3871f refactor: extract shared post-fill progression (no behaviour change)  <- feat/setup-conditions, fix/03a-extract-postfill
* a0d0650 fix(hygiene): skip transitions already known to be rejected           <- fix/02-gate-invalidate
* a9341c6 test: make entry gate tests deterministic (seed market data)          <- fix/01-gate-current-status
* 0aaf12a fix(safety): gate ENTRY_READY on current_status before broker submission
* 9544cab fix(setups): traduit les raisons d'invalidation en messages lisibles
* c485f5b docs: catalogue setup conditions (doc 21) + mises a jour associees
* 8b31d0d feat(api): expose setup_conditions sur le detail de setup
* 5a890ca feat(setups): section "Ce que cherche le setup" (backend + tracker + persistance)
* b9d4651 fix(lifecycle): n'invalide plus une entree en attente sur initial_stop
* ccd0f1d TODO, setups du jour et instructions de refactoring architecture      <- main (local)
```

Historique strictement linéaire (pas de merge commit dans cette section) :
chaque branche listée ci-dessus n'est qu'un pointeur posé à un stade
différent de la même ligne de développement.

### Ce qui est effectivement mergé dans `feat/setup-conditions`, et ce qui ne l'est pas

```
$ git merge-base --is-ancestor feat/setup-conditions HEAD && echo YES
YES
$ git log --oneline feat/setup-conditions..HEAD
c3a44df feat(reconciliation): write post-fill statuses on real fills
30e2385 fix(reconciliation): plumb broker_executions and add pure execution matcher
$ git log --oneline main..feat/setup-conditions
2a3871f refactor: extract shared post-fill progression (no behaviour change)
a0d0650 fix(hygiene): skip transitions already known to be rejected
a9341c6 test: make entry gate tests deterministic (seed market data)
0aaf12a fix(safety): gate ENTRY_READY on current_status before broker submission
9544cab fix(setups): traduit les raisons d'invalidation en messages lisibles
c485f5b docs: catalogue setup conditions (doc 21) + mises a jour associees
8b31d0d feat(api): expose setup_conditions sur le detail de setup
5a890ca feat(setups): section "Ce que cherche le setup" (backend + tracker + persistance)
b9d4651 fix(lifecycle): n'invalide plus une entree en attente sur initial_stop
```

- **`feat/setup-conditions` EST un ancêtre de `HEAD`** (`fix/03b2-filled-branch`) :
  il contient donc bien le gate de sécurité `0aaf12a` et les correctifs
  `a9341c6`/`a0d0650`.
- **Ce qui N'EST PAS mergé dans `feat/setup-conditions`** : les 2 commits du
  lot de réconciliation FILLED, `30e2385` (3b-1) et `c3a44df` (3b-2) —
  autrement dit, tout le travail audité en Q3/Q4 de ce document
  n'existe **que sur `fix/03b2-filled-branch`**, nulle part ailleurs.
- **`main` (local) est un ancêtre de `feat/setup-conditions`**
  (`git merge-base --is-ancestor main feat/setup-conditions` → `YES`), qui
  est 9 commits devant. **`HEAD` n'est PAS un ancêtre de `main`**
  (`git merge-base --is-ancestor HEAD main` → `NO`) : rien de tout ce que
  cet audit a examiné n'est mergé dans `main`.

### `main` local vs `origin/main`

```
$ git log --oneline origin/main..main
ccd0f1d TODO, setups du jour et instructions de refactoring architecture
7c1c0ce Fix presse-papiers JSON detaille + synchro ticker pendant saisie du setup
d966f72 Template stop: trailing natif IBKR desactive par defaut, timeframe ATR explicite
11ba0e5 Verrou d instance unique par base de donnees (anti-empilement de deploiements)
c39c9df Resilience au verrou SQLite: busy_timeout + ecriture d event non-fatale
84bd8fe Escalade fail-safe: echec persistant de revalidate_all bloque l auto-execution
bb83b21 Liveness honnete: stamp heartbeat poste apres la sonde legere, seuils coherents, diagnostics par tick
$ git log --oneline main..origin/main
(vide)
```

`main` local est strictement 7 commits devant `origin/main` (pas de
divergence — fast-forward propre), rien poussé. Et `feat/setup-conditions`,
`fix/01-gate-current-status`, `fix/02-gate-invalidate`,
`fix/03a-extract-postfill`, `fix/03b1-executions-plumbing`,
`fix/03b2-filled-branch` **n'existent sur aucune branche `origin/*`** —
seuls `origin/main` et `origin/refactor/split-app-js` sont présents côté
distant. **Le gate de sécurité (`0aaf12a`) et tout le travail de
réconciliation FILLED (`30e2385`, `c3a44df`) n'existent qu'en local, sur
cette seule machine, non poussés, non partagés.**

### Reste-t-il du travail non commité ?

Non, côté code. `git status` ne montre que : 2 fichiers de données supprimés
(`data/setups/CODI_20260628_001.json`, `TXN_20260630_001.json` — des fichiers
de setups, sans rapport avec le code applicatif), une quinzaine de nouveaux
fichiers `data/setups/*.json` (setups du jour, données runtime), et 3
répertoires non suivis (`.codex/`, `audit/` — ce document lui-même en cours
de rédaction —, `tmp/`). Aucun fichier de `app/` ou `tests/` n'est modifié
ni non suivi.

### État de la suite de tests (vérification, pas supposition)

```
$ python -m pytest -q
1 failed, 704 passed, 4 warnings, 98 subtests passed in 228.32s
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
```

L'échec est dans `test_account_metrics.py` (métriques de compte / positions
broker), sans rapport avec le gate d'entrée ou la réconciliation FILLED —
préexistant, déjà rapporté à l'identique par l'audit 25 avant les commits
`30e2385`/`c3a44df`. Toutes les suites touchées par cet audit
(`test_reconciliation.py`, `test_entry_gate_current_status.py`,
`test_entry_order_executor.py`, `test_order_manager.py`) passent
intégralement (42 passed, 14 subtests passed).

---

## TABLEAU DE CLÔTURE

Avertissement sur la numérotation : ce document reprend le libellé "A6" tel
que formulé dans la consigne de cet audit (inventaire des écritures de
statut, suite directe de l'A.5 de l'audit 05 — "4 mécanismes", devenu 27
sites aujourd'hui). Les items A.1, A.2 et A.4 de l'audit 05
(`order_manager.py` : statuts de départ ; incohérence `WAITING_RETEST`/
`WAITING_CONFIRMATION` ; `setup_lifecycle_service.py` transitions
empiriques) **n'ont pas été ré-audités dans cette session** — je ne leur
attribue donc aucun verdict FERMÉ/PARTIEL ici plutôt que d'en inventer un
sur la base d'une lecture vieille de plusieurs jours. Les lignes ci-dessous
couvrent ce que cette session a effectivement vérifié aujourd'hui (Q1-Q5),
plus un rappel explicite de ce qui reste hors périmètre.

| # | Problème | Statut | Preuve | Ce qui manque pour fermer |
|---|---|---|---|---|
| **A3 (incident 29/06)** — gate `current_status`, chemin **automatique** | **FERMÉ** | `trading_engine.py:2472-2486` + `entry_order_executor.py:56-57`, gate double, commit `0aaf12a` ; testé pour `range_breakout`/`breakout_retest` (`test_replay_2026_06_29_incident_no_second_order`, `EntryOrderExecutorDefenseInDepthTests`) ; suite verte | Rien côté chemin automatique |
| **A3bis — gate, chemin manuel** | **OUVERT** | `manual_order_service.py:146-231`/`:293`, aucune lecture de `current_status` ; inchangé par tout commit depuis `9544cab` (Q1) | Décider si un gate a un sens pour ce chemin (setup_id toujours neuf) et, si oui, l'implémenter ; sinon documenter explicitement pourquoi il n'en a pas besoin |
| **A2bis — preuve du gate par `setup_type`** | **PARTIEL** | Preuve testée pour `range_breakout` (×3) et `breakout_retest` (×1) ; **zéro** test de blocage pour `momentum_breakout`, `pullback_continuation`, `aggressive_rebound` (Q2) ; mécanisme agnostique au type par lecture de code | Paramétrer `test_entry_ready_blocked_for_each_post_entry_status` par `setup_type` comme l'est déjà le test nominal |
| **Fiabilité réelle de la branche FILLED (lot 3b)** | **PARTIEL** | Routage FILLED→BUY→branche testé (mocks, audit 25) et cohérent avec un cas réel historique (`LUNR_20260630_001`, Q3) ; appariement exécution↔ordre (barreau 1, le cœur du lot) **jamais exercé sur une exécution réelle** — aucune trace `BrokerExecution` n'est persistée en base | Une session TWS réelle avec un fill observé de bout en bout ; jour d'exécution + fenêtre de rétention IB non vérifiables sans elle |
| **A6 — écritures de statut hors state_machine** | **OUVERT** | 27 sites, 3 gatés (11%) (Q4) ; transition `ENTRY_FILLED → IN_POSITION` absente d'`ALLOWED_TRANSITIONS` et empruntée par le chemin nominal du lot 3b-2 (`post_fill_progression.py:92`, `reconciliation.py:520`) | Soit canaliser les écritures via `state_machine`, soit corriger la table pour refléter la réalité (`IN_POSITION` manquant des cibles d'`ENTRY_FILLED`) — aucun des deux n'est fait |
| **Déploiement / partage du correctif** | **OUVERT** | Gate (`0aaf12a`) et lot 3b (`30e2385`, `c3a44df`) 100 % locaux : absents de `main`, absents de tout `origin/*` (Q5) | Merger vers `main`, pousser vers `origin` — sinon le correctif n'existe que sur cette machine |
| A.1 / A.2 / A.4 (audit 05) | **NON RÉ-AUDITÉ** | Dernier statut connu : audit 05 (2026-0x-xx) | Nécessiterait une relecture dédiée pour confirmer si toujours vrai |

**Verdict global** : la phase sécurité ne peut pas être déclarée close. Le
mécanisme central de l'incident du 29 juin (chemin automatique) est
effectivement fermé, avec double gate et test de rejeu. Mais un chemin
d'entrée réel reste sans aucun gate (manuel), la preuve testée du gate ne
couvre pas tous les `setup_type`, la pièce la plus sensible du lot 3b
(appariement d'exécution réel) n'a jamais tourné sur une donnée réelle, une
transition hors table est empruntée par le chemin nominal de ce même lot, et
l'intégralité de ce travail est aujourd'hui invisible en dehors de cette
machine locale.
