# 19 — Audit lecture seule : pré-implémentation Rang 3 (réconciliation des fills réels)

Mode lecture seule stricte. Aucun fichier de code modifié, aucune branche créée.
Complète `audit/05_normalisation.md` (A.3, INCERTITUDES #1/#2) et
`audit/06_fill_executor.md` (déjà confirmé : `fill_executor.py` ne s'exécute
que sur `SimulatedBrokerConnector`, `reconciliation.py` ne traite pas `FILLED`
par une écriture de statut). Ce lot revérifie ces constats par lecture de code
fraîche et les complète avec `tws_connector.py`, `state_machine.py`,
`order_manager.py`, `trading_engine.py`, `setup_engine.py`,
`setup_lifecycle_service.py`, et des requêtes SQL `mode=ro` sur
`data/trading_state.sqlite`.

---

## Q1 — Le chemin d'un fill réel, de bout en bout

### 1.1 — Comment le système apprend un fill réel : trace du connecteur au reconciliateur

Le connecteur TWS réel (classe démarrant `tws_connector.py:705` avec
`async def status`) utilise la librairie `ib_async` (`tws_connector.py:631`,
`from ib_async import IB`). **Aucun callback push n'est enregistré** sur
`execDetails`/`orderStatus`/`openOrder` : grep exhaustif de ces 3 noms dans
`app/broker/tws_connector.py` ne retourne aucune définition de méthode
(seulement des lectures d'attributs `trade.orderStatus.status`,
ex. `:847, :955, :1008, :1070` — ce sont des attributs de l'objet `Trade`
d'`ib_async`, pas des callbacks définis dans ce fichier). Le mécanisme est
donc **entièrement pull (polling), jamais événementiel côté application**.

Chaîne exacte, du plus bas niveau au plus haut :

1. `TWSConnector.recent_executions()` (`tws_connector.py:1080-1109`) — lit
   `self._ib.fills()` (ligne 1087, commentaire ligne 1083-1084 : "`ib.fills()`
   pairs each execution with its contract"), retourne des `BrokerExecution`.
2. `TWSConnector.order_statuses()` (`tws_connector.py:1056-1078`) — lit
   `self._ib.trades()` (ligne 1061, cache de session `ib_async` incluant les
   ordres remplis), normalise via `_tws_order_status_to_order_status`
   (ligne 1069-1071), retourne `dict[order_id|perm_id, status]`.
3. `TWSConnector.open_orders()` (`tws_connector.py:981-1054`) — rafraîchit
   via `reqAllOpenOrdersAsync` (ligne 986-992, commentaire ligne 984-985 :
   "reqAllOpenOrders includes orders entered manually in TWS"), puis lit
   `self._ib.openTrades()` (ligne 1000) — **un ordre FILLED n'apparaît plus
   dans `openTrades()`**, donc plus dans le retour de cette méthode.
4. `TWSConnector.positions()` (`tws_connector.py:1111-1177`) — rafraîchit via
   `reqPositionsAsync` (ligne 1116) puis lit `self._ib.positions()` (:1123)
   et `self._ib.portfolio()` (:1131).
5. `ReconciliationEngine.run()` (`app/engine/reconciliation.py:47-287`)
   appelle ces 4 méthodes (`positions()` ligne 56, `open_orders()` ligne 72,
   `_broker_recent_executions` ligne 89 → `recent_executions()`,
   `_broker_order_statuses` ligne 90-92 → `order_statuses()`), puis
   `_reconcile_local_orders` (ligne 132-137, définie ligne 325-367).

`ReconciliationEngine.run()` est invoqué depuis 3 sites de
`trading_engine.py`, tous des appels périodiques/à la demande, jamais un
callback broker :
- `TradingEngine.start()` : `trading_engine.py:323` (une fois, au démarrage
  du moteur).
- `TradingEngine._reconcile_if_due()` : `trading_engine.py:918-927`, appelée
  depuis la boucle de heartbeat (ligne 701 : `await
  self._reconcile_if_due(broker_status)`), au rythme de
  `_reconciliation_interval_seconds()` (`:954-964`, 45s par défaut ou
  `tracker["refresh_seconds"]` si le broker tracker est actif).
- `TradingEngine.force_sync()` : `trading_engine.py:2057-2064` (déclenché
  manuellement, route `/api/runtime/sync` d'après `routes_dashboard.py:88`).

**Conclusion 1.1** : un fill réel n'est appris par le système qu'au prochain
passage de `ReconciliationEngine.run()` (au plus tard 45s après le fill, ou
immédiatement via `force_sync`), jamais par un événement poussé par TWS au
moment du fill.

### 1.2 — Branches de `reconciliation.py` selon le statut d'ordre

`_reconcile_local_orders` (`reconciliation.py:325-367`) itère
`self.repository.list_orders()` (ligne 339) et calcule `open_status` (statut
si l'ordre apparaît encore dans `broker_orders`/`openTrades()`, ligne
338/344) et `known_status` (statut lu dans `order_statuses()`/`trades()` si
absent des ordres ouverts, ligne 356). Le statut retenu est ensuite passé à
`_mark_local_order_status` (ligne 369-410), qui met à jour `orders.status`
(ligne 382) puis appelle `_update_setup_after_reconciled_order` (ligne 410,
corps ligne 412-465) avec ce même statut.

`_update_setup_after_reconciled_order(order, status)` — table exhaustive des
branches par valeur de `status` (les seules valeurs normalisées par
`_normalize_order_status`, `reconciliation.py:583-594` :
`CREATED, SUBMITTED, FILLED, CANCELLED, REJECTED, ERROR`) :

| `status` broker | Écrit un statut de setup ? | Ligne | Condition |
|---|---|---|---|
| `SUBMITTED` | **Oui, conditionnel** | `:426-440` | Seulement si `setup_status in _TERMINAL_SETUP_STATUSES` (`:495-502`, inclut `ERROR_REQUIRES_MANUAL_REVIEW`) **ou** `setup_status == MANUAL_REVIEW_REQUIRED` (`:432-434`). Cible : `STOP_ORDER_PLACED` si `side=="SELL"`, sinon `ENTRY_ORDER_PLACED` (`:427-431`), message "Open order restored from TWS" (`:438`). Si le setup n'est dans aucun de ces 2 groupes, **aucune écriture** (le `if` ne s'exécute pas, la fonction retourne quand même à `:440`). |
| `CANCELLED` | **Oui, conditionnel** | `:441-465` | Si `setup_status in _TERMINAL_SETUP_STATUSES` → retour sans écriture (`:443-444`). Si `side=="SELL"` et une position existe (`repository.get_position(symbol)`) → `MANUAL_REVIEW_REQUIRED` (`:446-450`, "Protective stop cancelled in TWS"). Si `side=="BUY"` et `setup_status in _ORDER_DEPENDENT_SETUP_STATUSES` (`:489-494`) → `CANCELLED` (`:461-465`, "Entry order cancelled in TWS"). |
| `FILLED` | **Non** | — | Aucune branche ne teste `status == OrderStatus.FILLED.value`. La fonction atteint `:441` (`if status != OrderStatus.CANCELLED.value: return`) — `FILLED != CANCELLED` est vrai → retour immédiat sans écriture. |
| `REJECTED` | **Non** | — | Même chemin que `FILLED` : ne correspond ni à `SUBMITTED` ni à `CANCELLED`, retour à `:441-442` sans écriture. |
| `ERROR` | **Non** | — | Idem. |
| `PARTIALLY_FILLED` | **N/A** | — | Cette valeur n'existe pas dans `_normalize_order_status` (`:583-594`) : un ordre partiellement rempli côté TWS est normalisé soit en `SUBMITTED` (si le statut IB brut mappe ainsi via `_tws_order_status_to_order_status`, hors périmètre de ce fichier), soit ignoré. Aucune branche dédiée dans `reconciliation.py` pour un remplissage partiel. |

---

## Q2 — Comment le chemin simulé écrit les bons statuts (le modèle à répliquer)

### 2.1 — Séquence exacte de `fill_executor.py` (lue en entier, 134 lignes)

`FillExecutor.simulate_fill_order` (`app/engine/fill_executor.py:38-133`) —
garde d'entrée ligne 44 (`order["status"] != SUBMITTED` → `return None`) et
ligne 47-49 (`if not isinstance(broker, SimulatedBrokerConnector): return
None`, confirme `audit/06_fill_executor.md:39-42`).

Séquence linéaire, aucune boucle ni retour en arrière possible :

1. `broker.simulate_fill(broker_order_id, fill_price)` (`:64`) — remplissage
   simulé côté `SimulatedBrokerConnector`.
2. `repository.update_order_status(order_id, FILLED)` (`:68`) — statut
   d'**ordre**, pas de setup.
3. Lecture de `trailing_stop_loss.initial_stop` (`:73-76`). Si absent :
   écrit `SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value` (`:84-88`, valeur
   ligne 86) et **retourne** (`:89`) — chemin d'échec, sort de la fonction
   avant `ENTRY_FILLED`.
4. `repository.upsert_position(...)` (`:102`) — crée la position locale.
5. `repository.update_setup_status(setup_id, ENTRY_FILLED, "Entry order
   filled")` (`:103-107`, valeur ligne 105) — **1er statut de setup écrit
   côté succès**.
6. `protection_snapshot_for_setup(setup_id)` (`:117`) — si
   `has_active_stop_order` est déjà vrai (cas rare, stop déjà posé par
   ailleurs), saute l'étape 7.
7. Sinon : `self.stop_order_placer.place_stop_order(...)` (`:119-124`) — pose
   le stop protecteur. Si `stop_order.status in {REJECTED, ERROR}` (`:125`)
   → **retourne la position sans écrire `IN_POSITION`** (`:126`) : le setup
   reste bloqué sur `ENTRY_FILLED`, pas d'erreur explicite écrite non plus à
   ce point précis (aucun `update_setup_status` dans cette branche).
8. Si le stop est actif ou posé avec succès :
   `repository.update_setup_status(setup_id, IN_POSITION, "Position
   protected and open")` (`:128-132`, valeur ligne 130).

Résumé de la chaîne côté succès complet :
`ENTRY_FILLED` (`:103-107`) → pose du stop (`:119-124`, appel à
`OrderManager.place_stop_order` puisque `FillExecutor` reçoit
`stop_order_placer=self` depuis `OrderManager.__init__`,
`order_manager.py:56-62`) → `IN_POSITION` (`:128-132`).
Aucun statut `STOP_ORDER_PLACED`/`STOP_PLACED` n'est écrit entre les deux :
grep exhaustif de `update_setup_status` dans `fill_executor.py` confirme
exactement 3 occurrences (lignes 84, 103, 128 — reconfirme
`audit/06_fill_executor.md:34-37`). Le stop est posé au sens **ordre broker**
(`OrderManager.place_stop_order`, `order_manager.py:312-394`) mais son statut
de setup dédié (`STOP_ORDER_PLACED`, écrit normalement à
`order_manager.py:373-378` quand `update_setup_status=True`) est **court-
circuité** : l'appel depuis `fill_executor.py:119-124` ne passe pas
`update_setup_status=True` explicitement — signature de `place_stop_order`
(`order_manager.py:312-320`) : `update_setup_status: bool = True` est le
défaut, donc **il est bien appelé implicitement** (aucun argument
`update_setup_status=False` n'est passé ligne 119-124) — **contredit
partiellement l'hypothèse d'un saut direct** : en réalité `STOP_ORDER_PLACED`
est très probablement écrit par `place_stop_order` lui-même
(`order_manager.py:373-378`) avant que `fill_executor.py:128-132` écrive
`IN_POSITION` juste après. Voir précision au point 2.3 (transition
`ENTRY_FILLED → STOP_ORDER_PLACED → IN_POSITION` bien empruntée, deux
écritures distinctes, pas un saut).

### 2.2 — Passage par `state_machine.transition()` ou écriture directe ?

**Écriture directe, confirmé.** `fill_executor.py` ligne 1-10 (imports) :
`app.broker.tws_connector`, `app.engine.transaction_costs`, `app.models`,
`app.storage.event_store`, `app.storage.repositories` — **aucun import de
`app.engine.state_machine`**. Les 3 sites d'écriture (`:84-88, :103-107,
:128-132`) appellent tous `self.repository.update_setup_status(...)`, un
`UPDATE` SQL direct (`app/storage/repositories.py:454-489`, relu à ce lot,
confirme `audit/06_fill_executor.md:7-19`) : pas de `explain_transition`, pas
de `.transition()`, pas d'exception possible en cas de transition invalide.
Ceci **reconfirme** l'audit 06 tel que demandé par la question.

### 2.3 — Vérification des transitions dans `ALLOWED_TRANSITIONS`

Table relue intégralement (`state_machine.py:8-201`) pour ce lot :

| Transition | Présente dans `ALLOWED_TRANSITIONS` ? | Ligne |
|---|---|---|
| `ENTRY_ORDER_PLACED → ENTRY_FILLED` | **Oui** | `:133-138`, cible ligne 135 (`ENTRY_ORDER_PLACED: {ENTRY_PARTIALLY_FILLED, ENTRY_FILLED, CANCELLED, ERROR}`) |
| `ENTRY_FILLED → STOP_ORDER_PLACED` | **Oui** | `:145-150`, cible ligne 146 (`ENTRY_FILLED: {STOP_ORDER_PLACED, STOP_PLACED, ERROR, MANUAL_REVIEW_REQUIRED}`) |
| `ENTRY_FILLED → STOP_PLACED` | **Oui** | `:145-150`, cible ligne 147 |
| `STOP_ORDER_PLACED → IN_POSITION` | **Oui** | `:151-155`, cible ligne 152 (`STOP_ORDER_PLACED: {IN_POSITION, ERROR, MANUAL_REVIEW_REQUIRED}`) |
| `STOP_PLACED → IN_POSITION` | **Oui** | `:156-160`, cible ligne 157 |

**Aucune des transitions nécessaires n'est absente de la table.** La chaîne
complète `ENTRY_ORDER_PLACED → ENTRY_FILLED → STOP_ORDER_PLACED/STOP_PLACED
→ IN_POSITION` est intégralement couverte par `ALLOWED_TRANSITIONS`, malgré
le fait que `fill_executor.py` ne l'emprunte jamais réellement via
`state_machine.transition()` (point 2.2). **Ce n'est donc pas un obstacle**
pour une future implémentation qui voudrait faire passer l'écriture par
`state_machine.transition()` plutôt que par `update_setup_status` direct —
la table est déjà prête pour cette chaîne (voir aussi section "OBSTACLES À
LA CIBLE" en fin de document pour les points qui, eux, posent problème).

---

## Q3 — Le stop protecteur sur un fill réel

### 3.1 — Mécanisme réel : bracket transmis dès le départ, pas de pose réactive après fill

`OrderManager.place_entry_order` (`order_manager.py:64-204`) construit
l'ordre parent (BUY) et le soumet **avec `transmit=False`** :
`order_record_to_broker_request(order, transmit=False)` (`:114`, valeur par
défaut de `order_record_to_broker_request` est `transmit: bool = True`,
`order_mapper.py:11`, donc `transmit=False` est explicite ici). Puis, dans
le même appel (`:156-165`), il soumet immédiatement le stop protecteur
enfant via `self.place_stop_order(..., parent_id=order.id,
broker_parent_id=order.broker_order_id, transmit=True, ...)` (`:157-165`,
`transmit=True` explicite ligne 163).

Dans `TWSConnector._build_order` (`tws_connector.py:2003-2033`) : si
`request.parent_id` est fourni, `order.parentId = int(request.parent_id)`
(`:2028-2029`) lie l'ordre au parent ; `order.transmit = bool(
request.transmit)` (`:2032`). C'est le **mécanisme standard de bracket IB** :
le parent est envoyé à TWS avec `transmit=False` (accepté par TWS mais
retenu, non transmis au marché), puis l'enfant (stop) est envoyé avec
`transmit=True`, ce qui **transmet toute la famille d'ordres (parent + stop)
simultanément** au marché/à IBKR. Confirmé par le commentaire du code source
lui-même (`order_manager.py:212-217`, docstring de `_entry_order_prices`
n'en parle pas directement, mais le motif transmit=False/True est explicite
aux lignes 114 et 163).

**Conclusion** : l'ordre d'entrée réel est bien transmis **en bracket
(parent+stop) dès le départ** — le stop protecteur n'est jamais posé de
façon réactive après le fill sur le chemin réel (contrairement au chemin
simulé, `fill_executor.py:119-124`, qui pose le stop APRÈS le fill parce que
le broker simulé ne supporte pas nativement les brackets — à vérifier plus
avant si besoin, hors périmètre ici). Au moment où l'ordre parent est soumis
avec succès (`entry_order_submitted`, `order_manager.py:185-203`), le stop
est déjà transmis au broker.

### 3.2 — Fenêtre position-ouverte-sans-stop : ce que le code garantit et ce qu'il ne garantit pas

Le code applicatif ne réintroduit **pas** de fenêtre supplémentaire entre le
fill et l'existence du stop chez le broker : les deux ordres partent
ensemble. Cependant, deux points ne sont **pas vérifiables par lecture de
code seule** et doivent être traités comme faits non prouvés par ce lot :

1. **Activation du stop enfant côté IB entre soumission et fill du parent.**
   Le comportement standard documenté d'IB pour un bracket est que l'ordre
   enfant reste à l'état `PreSubmitted` tant que le parent n'est pas rempli,
   puis IB l'active automatiquement (côté serveur IB, hors du code de ce
   dépôt) dès que le parent est `Filled`. Ce dépôt ne contrôle ni n'observe
   directement cette activation : `TWSConnector` ne fait qu'émettre les 2
   ordres avec `transmit=True` sur le second, puis interroge périodiquement
   `open_orders()`/`order_statuses()` (Q1.1). **Aucun code de ce dépôt ne
   vérifie que le stop est bien passé à un état actif après le fill** — la
   confirmation que le stop protège réellement la position dépend
   entièrement du comportement serveur d'IB, non audité ici (hors du
   périmètre "lecture de ce dépôt").
2. **Rejet du stop après un fill déjà passé.** Le cas `protective_stop_
   rejected` observé le 2026-06-29 (Q4) prouve empiriquement qu'un stop
   enfant **peut être rejeté par TWS** (`order_manager.py:347-372`) même
   dans le flux bracket. Dans ce cas précis, `_cancel_parent_for_failed_
   protection` (`:527-563`) tente d'annuler le parent (`:537-539`) — mais
   **seulement si `entry_order.broker_order_id` existe** et **seulement si
   `cancel_order` est accepté par le broker** (`cancelled = bool(
   result.accepted)`, `:539`). Si le parent est déjà rempli (ou en cours de
   remplissage) au moment du rejet du stop, l'annulation échoue
   naturellement (un ordre rempli ne peut plus être annulé) — **la position
   reste alors ouverte avec un stop rejeté et non remplacé automatiquement**
   par ce code : aucun site de `order_manager.py` ne retente la pose d'un
   nouveau stop après un rejet dans ce chemin (seul `attach_missing_stop`,
   `:396-471`, le fait, mais c'est une action déclenchée séparément —
   `routes_orders.py:78`, `/api/orders/{order_id}/attach-stop`, pas
   automatique).

**Réponse à la question critique de sécurité** : le mécanisme normal
(bracket transmis en un seul geste) ne crée pas de fenêtre applicative
"position ouverte sans stop" par construction. Mais l'incident du
2026-06-29 démontre qu'un **stop rejeté** (par TWS, raison "Accepted by TWS:
Cancelled") peut coexister avec un **parent qui finit par se remplir**
(l'ordre `ord_ddc3cc183413` a fini `FILLED`, confirmé Q4) — dans ce cas
précis, **la position s'est ouverte sans stop actif**, et rien dans le code
audité ne rétablit automatiquement un stop après coup sur ce chemin. Un
correctif de Rang 3 qui ferait avancer le statut vers `IN_POSITION` sur
simple constat de `FILLED` **ne doit donc pas présumer que le stop est actif
sans le vérifier explicitement** (ex. via `protection_snapshot_for_setup`,
déjà utilisé par `fill_executor.py:117-118` sur le chemin simulé, ou via un
nouvel appel à `open_orders()`/`_matching_stop_order`, déjà utilisé par
`reconciliation.py:210,477-485` sur un autre chemin) — sous peine de
marquer `IN_POSITION` un setup dont la position réelle est en fait nue.

---

## Q4 — Le retour non tracé du 29 juin (incertitude ouverte depuis l'audit 05)

### 4.1 — Grep exhaustif : tout chemin qui écrit `ENTRY_ORDER_PLACED`

```
app\engine\order_manager.py:182   → ENTRY_ORDER_PLACED (bracket soumis avec succès, place_entry_order)
app\engine\order_manager.py:468   → ENTRY_ORDER_PLACED (stop rattaché avec succès, attach_missing_stop)
app\engine\reconciliation.py:430  → ENTRY_ORDER_PLACED (cible calculée, side != SELL)
app\engine\trading_engine.py:1603 → littéral de comparaison dans _setup_preference (lecture, pas écriture)
```

Seuls 3 sites **écrivent** effectivement `ENTRY_ORDER_PLACED` en base
(`order_manager.py:182`, `order_manager.py:468`, `reconciliation.py:435-439`
avec `target_status` calculé ligne 427-431). `trading_engine.py:1603` est une
lecture (`_setup_preference`, classement d'affichage), pas une écriture — 
confirmé par lecture complète de la fonction (`:1594-1615`).

Le **seul des 3 qui peut partir d'`ERROR_REQUIRES_MANUAL_REVIEW`** est
`reconciliation.py:412-440` (`_update_setup_after_reconciled_order`) :
`ERROR_REQUIRES_MANUAL_REVIEW` fait partie de `_TERMINAL_SETUP_STATUSES`
(`reconciliation.py:495-502`, ligne 501), donc la condition ligne 432
(`if setup_status in _TERMINAL_SETUP_STATUSES or ...`) est vraie pour ce
statut. Ce mécanisme se déclenche quand un ordre local passe (ou est
retrouvé) à l'état `SUBMITTED` côté broker alors que le setup est dans un
statut terminal ou `MANUAL_REVIEW_REQUIRED` — message "Open order restored
from TWS" (`:438`).

`order_manager.py:182` (bracket réussi) et `:468` (stop rattaché) exigent
tous deux un appel **actif et réussi** à `place_entry_order`/
`attach_missing_stop` — ni l'un ni l'autre ne "réinitialise" un statut
d'erreur automatiquement ; ils sont des conséquences d'une action, pas des
mécanismes de retour en arrière autonomes. Aucun des deux n'apparaît dans le
grep des événements bruts de la fenêtre concernée (voir 4.3).

**Aucun autre chemin** n'écrit `ENTRY_ORDER_PLACED` : ni
`setup_lifecycle_service.py` (le seul `revalidate_and_apply`,
`:359-437`, ne touche que `LIFECYCLE_MANAGED_STATUSES` = `{WAITING_
ACTIVATION, BLOCKED, STALE_SETUP, MISSED_BREAKOUT_WAIT_RETEST,
RECONCILING_EXISTING_POSITION}`, `setup_lifecycle_service.py:33-41` —
`ERROR_REQUIRES_MANUAL_REVIEW` **absent** de cet ensemble, donc
`revalidate_and_apply`/`revalidate_all` ne peuvent structurellement jamais
agir sur un setup dans ce statut, vérifié ligne 392 `if not setup_id or
current_status not in LIFECYCLE_MANAGED_STATUSES: return result` et ligne
448 `if str(setup.get("status") or "") not in LIFECYCLE_MANAGED_STATUSES:
continue`) ; ni `setup_engine.py` (`_status_after_config_save`,
`:293-304` : si le setup existe déjà, retourne
`SetupStatus(previous_status)` — **préserve** le statut existant lu en
base, ne le réinitialise jamais, y compris pour `ERROR_REQUIRES_MANUAL_
REVIEW` qui est une valeur `SetupStatus` valide donc le `except ValueError`
de la ligne 303-304 ne se déclenche pas) ; ni aucune route API (grep de
`update_setup_status(` et des routes POST/PATCH/PUT dans `app/api/` : aucune
route générique de type "set status" n'existe — seules `arm`/`disarm`/
`enable`/`disable` écrivent un statut, et `arm_setup`
(`setup_engine.py:240-271`) cible `setup.initial_status()`, jamais
`ENTRY_ORDER_PLACED`).

### 4.2 — Localisation : `reconciliation.py`, pas un service de lifecycle ni un retry dédié

Réponse directe : le seul mécanisme candidat identifié par le code est dans
`reconciliation.py` (`_update_setup_after_reconciled_order`, branche
`SUBMITTED`, `:426-440`), pas dans un service de lifecycle (exclu, 4.1), pas
dans un mécanisme de retry dédié (aucune fonction "retry"/"resend"/
"resubmit" trouvée par grep dans `app/engine/` — seul `attach_missing_stop`
existe et exige un appel API explicite), et **potentiellement** une
réconciliation au démarrage qui réadopte des ordres — développé en 4.4/4.5,
puisque `ReconciliationEngine.run()` fait partie de la séquence de
`TradingEngine.start()` (`trading_engine.py:322-325`).

### 4.3 — Croisement avec les événements réels du 29 juin (SQL `mode=ro`, lignes brutes)

Requête exécutée (script `python3`, `sqlite3.connect("file:data/trading_
state.sqlite?mode=ro", uri=True)`, table `events` indexée sur `setup_id` —
`idx_events_setup_id`, confirmé par `SELECT name FROM sqlite_master WHERE
type='index' AND tbl_name='events'` avant exécution) :

```sql
SELECT id, timestamp, event_type, message, data_json FROM events
WHERE setup_id=? AND timestamp >= '2026-06-29T15:30:00'
  AND timestamp <= '2026-06-29T17:30:00'
ORDER BY timestamp ASC;
```

Contrairement à `audit/05_normalisation.md:148-159` (table curatée), cette
requête est **exhaustive sur tous les `event_type`**, pas seulement les
événements notables présélectionnés. Résultat brut pour `GILT_20260628_001`
(18 lignes ; les 3 autres setup_id — `LUNR`, `QBTS`, `STM` — présentent
exactement le même squelette, résultats bruts complets conservés dans le
scratchpad de session) :

```
15:39:48.564186  protective_stop_rejected   "Accepted by TWS: Cancelled"  {broker_order_id:7984, order_id:stp_036f94d210b4, parent_order_id:ord_ddc3cc183413, status:CANCELLED, stop_loss:11.89}
15:41:15.769330  order_history_deleted      "Order removed from local history"  {order_id:stp_036f94d210b4, status:CANCELLED}
15:55:52.948664  active_entry_order_unprotected
15:55:54.855471  opportunity_ready          (ENTRY_READY, 100% READY AUTO)
16:18:57.043623  setup_loaded               "Setup loaded and validated"  {}
16:18:57.477043  order_status_reconciled    "Order marked FILLED after broker reconciliation"  {broker_order_id:43261, order_id:ord_ddc3cc183413, previous_status:SUBMITTED, source:broker_reconciliation, status:FILLED}
17:26:31.945942  setup_loaded               "Setup loaded and validated"  {}
17:26:47.086425  protective_stop_submitted  "Protective stop submitted"  {order_id:stp_bf7708b84fd6, parent_order_id:ord_3f7ab3181a2f, stop_loss:11.89}
17:26:47.095682  entry_order_submitted      "Accepted by TWS: PendingSubmit"  {order_id:ord_3f7ab3181a2f, ...}  <- 2e ordre réel
17:26:48.697257  opportunity_ready
17:28:03.782490  duplicate_order_blocked
   ... (répété jusqu'à 17:29:56)
```

**Constat capital, absent d'audit 05** : entre `15:41:15` (dernier événement
lié au rejet du stop) et `16:18:57` (fill reconcilié), **aucun événement
n'est enregistré du tout** pour ce setup en dehors de `active_entry_order_
unprotected` et `opportunity_ready` (des événements de **lecture**/alerte,
qui ne modifient pas `setups.status` — confirmé par grep : ni l'un ni
l'autre n'apparaît près d'un `update_setup_status` dans le fichier qui les
émet). Et surtout : **`setup_loaded` apparaît exactement 2 fois pour les 4
setup_id, à des timestamps quasi identiques (`16:18:57.0x`, `17:26:3x`)** —
or `setup_loaded` n'est émis que par `SetupEngine.load_all()`
(`setup_engine.py:201-207`), et `load_all()` n'est appelé que depuis
`TradingEngine.start()` (`trading_engine.py:322`, grep confirme : c'est
l'unique appelant dans tout `app/`). **Ceci indique fortement 2 redémarrages
du moteur ce jour-là, à 16:18:57 et à 17:26:31**, chacun immédiatement suivi
(même seconde) par `reconciliation.run()` (`trading_engine.py:323`) —
explique la coïncidence temporelle exacte entre `setup_loaded` et le fill
reconcilié à 16:18:57.

**Mais** : à `_status_after_config_save` (`setup_engine.py:293-304`), le
statut est **préservé**, pas réinitialisé — donc le premier redémarrage
(16:18:57) ne peut pas, par ce chemin, avoir écrit `ENTRY_ORDER_PLACED`. Et
à ce même redémarrage, `reconciliation.run()` ne trouve, pour l'ordre
d'entrée de `GILT`, que la transition vers `FILLED` (branche sans écriture
de statut, Q1.2) — cohérent avec l'événement observé. Le `setup_lifecycle.
revalidate_all(force=True)` qui suit (`trading_engine.py:325`) ne peut pas
non plus agir : `ERROR_REQUIRES_MANUAL_REVIEW` est absent de `LIFECYCLE_
MANAGED_STATUSES` (4.1).

**Au 2e redémarrage (17:26:31)** : l'ordre d'entrée est déjà `FILLED`
localement (écrit au 1er passage) — `_reconcile_local_orders`
(`reconciliation.py:339-367`) le saute (`current_status not in
_ACTIVE_ORDER_STATUSES`, `:354`, `FILLED` n'est pas dans `{CREATED,
SUBMITTED}`) : **aucun événement `order_status_reconciled` n'est d'ailleurs
observé entre 16:18:58 et 17:26:31** dans le relevé brut, ce qui confirme
qu'aucune transition d'ordre n'a été reconciliée pour ce setup à ce
2e passage. La branche `SUBMITTED` de `_update_setup_after_reconciled_order`
(`:426-440`, celle qui écrit `ENTRY_ORDER_PLACED` depuis un statut terminal)
**ne peut donc pas non plus s'être déclenchée** pour cet ordre précis à ce
moment, faute d'ordre local actif à reconcilier.

**Conclusion factuelle, plus précise qu'audit 05 mais toujours sans preuve
directe** : le retour à `ENTRY_ORDER_PLACED` s'est bien produit entre
`16:18:57` et `17:26:47` (l'ordre `ord_3f7ab3181a2f` du 2e envoi le prouve).
Aucun événement journalisé (`events`, requête exhaustive, pas curatée) ne
trace une écriture de `setups.status` dans cette fenêtre. Or, tous les
sites d'écriture connus de `ENTRY_ORDER_PLACED` (4.1) émettent
systématiquement un `event_store.record(...)` en même temps que
`update_setup_status(...)` (vérifié pour les 3 sites : `order_manager.py:
185-203` après `:180-184` ; `order_manager.py:466-470` sans event dédié
adjacent — **à noter, seul site sans event_store.record juste après**, voir
ci-dessous ; `reconciliation.py:394-409` avant `:410` qui appelle
`_update_setup_after_reconciled_order`). Le site `order_manager.py:466-470`
(`attach_missing_stop`, écrit `ENTRY_ORDER_PLACED` avec le message
"Protective stop attached to existing entry order") **n'émet aucun
`event_store.record` propre à cette écriture de statut** (l'event le plus
proche est celui de `place_stop_order` lui-même, `:379-393`, mais celui-ci
décrit le stop, pas le changement de statut du setup) — **c'est donc le
seul site des 3 qui pourrait écrire silencieusement, sans laisser de trace
distincte dans `events`**. Mais `attach_missing_stop` exige un appel API
explicite (`routes_orders.py:78`, `/api/orders/{order_id}/attach-stop`) sur
un ordre d'entrée **actif** (`entry_order.get("status") in {CREATED,
SUBMITTED}`, `order_manager.py:402-406`) — or l'ordre `ord_ddc3cc183413` de
`GILT` était déjà `FILLED` à 16:18:57, ce qui **exclurait** cette voie pour
lui (`attach_missing_stop` aurait levé `ValueError("Only active entry
orders can receive an attached stop")`, sans event et sans écriture, avant
d'atteindre `:466`).

**Le mécanisme exact reste donc non identifié dans le code de ce dépôt**,
mais ce lot élimine `reconciliation.py:426-440` pour CE cas précis
(faute d'ordre local actif à ce moment), élimine `setup_lifecycle_service.py`
et `setup_engine.py` par construction (statuts hors périmètre / préservation
du statut), et établit un fait nouveau non documenté par audit 05 : **les 2
occurrences de `setup_loaded` prouvent 2 redémarrages du moteur encadrant
précisément la fenêtre du retour non tracé** — une piste que le prochain lot
devrait creuser (logs d'infrastructure/process, hors de ce qui est
observable dans `data/trading_state.sqlite`).

### 4.4 — Ce mécanisme resterait-il dangereux une fois le Rang 3 en place ?

**Oui, potentiellement, pour un motif distinct de l'incident du 29 juin.**
Même si le gate de Rang 1 bloque le ré-envoi d'ordre (non vérifié dans ce
lot — hors périmètre, à confirmer contre le code actuel du gate), la branche
`reconciliation.py:426-440` reste une écriture de `ENTRY_ORDER_PLACED`/
`STOP_ORDER_PLACED` **structurellement capable de sortir un setup de
`ERROR_REQUIRES_MANUAL_REVIEW` ou `MANUAL_REVIEW_REQUIRED` sans revue
humaine**, dès qu'un ordre local revient à `SUBMITTED` côté broker. Ce
mécanisme est **indépendant** du correctif de Rang 3 (qui porterait sur la
branche `FILLED`, absente aujourd'hui) mais **partage le même point
d'entrée** (`_update_setup_after_reconciled_order`) — voir section
"OBSTACLES À LA CIBLE" pour l'interaction précise.

---

## Q5 — Réconciliation au démarrage et cohérence état interne / broker

### 5.1 — Existe-t-il une réconciliation au démarrage qui interroge IBKR ?

**Oui.** `TradingEngine.start()` (`trading_engine.py:306-337`) appelle
`await self.reconciliation.run()` à la ligne 323, après
`self.setup_engine.load_all()` (`:322`) et avant `self.setup_lifecycle.
revalidate_all(force=True)` (`:325`). C'est la même `ReconciliationEngine`
que celle utilisée en continu (Q1.1) — pas un mécanisme distinct dédié au
démarrage. Elle interroge positions (`reconciliation.py:56`,
`broker.positions()`), ordres ouverts (`:72`, `broker.open_orders()`),
résumé de compte (`:81`) et exécutions récentes (`:89`).

Traitement des divergences (relu intégralement à ce lot,
`reconciliation.py:47-287`) :
- **Ordres locaux vs broker** (`_reconcile_local_orders`, `:325-367`) : met
  à jour `orders.status` si le statut broker diffère du statut local
  (`:344-352`) ou si un ordre local actif a disparu des ordres ouverts
  (`:354-367`) — puis répercute conditionnellement sur `setups.status` via
  `_update_setup_after_reconciled_order` (branches `SUBMITTED`/`CANCELLED`
  uniquement, Q1.2/Q4.1).
- **Positions broker orphelines côté setup `MANAGEMENT_ONLY`** (`:143-266`)
  : uniquement pour les setups dont `setup_is_management_only(role)` est
  vrai (`:152`) **et** `config.position_source.mode ==
  "adopt_existing_ibkr_position"` (`:154-156`) — écrit `MANUAL_REVIEW_
  REQUIRED`, `ERROR_REQUIRES_MANUAL_REVIEW` ou `IN_POSITION` selon les cas
  (`:162-166, :178-182, :193-197, :213-218, :249-253`).
- **Tout autre setup** (non `MANAGEMENT_ONLY`, ce qui couvre les 5
  `setup_type` d'entrée normale — `range_breakout`, `momentum_breakout`,
  `breakout_retest`, `aggressive_rebound`, `pullback_continuation`) : la
  boucle `:143-266` le **saute explicitement** à la ligne 152
  (`if not setup_is_management_only(role): continue`) — **aucune
  vérification de cohérence position broker ↔ statut setup n'est faite pour
  ces types**, quel que soit leur statut courant (la garde `_TERMINAL_
  SETUP_STATUSES` de la ligne 148-149 ne s'applique même pas puisque la
  boucle sort avant, ligne 152, pour tout setup non-`MANAGEMENT_ONLY`).

### 5.2 — Un setup coincé `ENTRY_ORDER_PLACED` avec une position réellement ouverte chez IBKR : détecté ?

**Non, pas par ce mécanisme.** Un setup d'entrée normal (non
`MANAGEMENT_ONLY`) bloqué sur `ENTRY_ORDER_PLACED` alors que sa position est
réellement ouverte chez IBKR est **hors de portée** de la boucle
`:143-266` (exclu ligne 152, 5.1). Le seul chemin qui pourrait le toucher
est `_reconcile_local_orders` (`:325-367`) — mais celui-ci opère sur
`orders`, pas sur `positions` : si l'ordre d'entrée local est déjà passé
à `FILLED` (Q1.2/Q1.3, ce que fait bien `_reconcile_local_orders` pour
l'ordre lui-même), la fonction ne consulte jamais `broker.positions()`
pour vérifier que le setup reflète une position ouverte — c'est
`_update_setup_after_reconciled_order` qui aurait dû le faire et qui, pour
`FILLED`, ne fait explicitement rien (Q1.2). **Conclusion : la
réconciliation au démarrage passe à côté de ce cas précis, tel que le
code existe aujourd'hui** — confirme et complète `audit/06_fill_executor.
md:44-62`.

### 5.3 — Risque de conflit entre le futur correctif Rang 3 et la réconciliation au démarrage

Interactions identifiées par lecture de code (aucune conception proposée,
uniquement description du terrain) :

1. **Partage du même point d'entrée.** Si le correctif de Rang 3 ajoute une
   branche `FILLED` à `_update_setup_after_reconciled_order`
   (`reconciliation.py:412-465`, le point le plus naturel puisque c'est déjà
   lui qui reçoit `status="FILLED"` sans agir, Q1.2) — cette fonction est
   appelée à la fois par le cycle périodique (`_reconcile_if_due`,
   `trading_engine.py:918-927`, toutes les 45s) et par le passage de
   démarrage (`:323`). Un même setup pourrait donc voir sa transition
   `ENTRY_FILLED`/`IN_POSITION` déclenchée **soit** au tick périodique
   suivant le fill réel, **soit** au redémarrage suivant s'il a eu lieu
   entre-temps — pas un conflit d'écriture concurrente (le moteur est
   mono-thread côté boucle asyncio pour ce chemin, aucune preuve du
   contraire trouvée dans `trading_engine.py`), mais un **doublon logique
   inoffensif** si l'implémentation est idempotente (elle devrait l'être,
   puisque `explain_transition(current, target)` retourne `allowed=True`
   avec `reason="Already in target status"` si `current == target`,
   `state_machine.py:283-289`).
2. **Ordre des 3 étapes de `start()`.** `load_all()` (préserve le statut,
   5.1) s'exécute **avant** `reconciliation.run()`. Si un futur correctif
   fait écrire `ENTRY_FILLED`/`IN_POSITION` par `reconciliation.run()`, cette
   écriture arrive **après** `load_all()` — pas de conflit d'ordre, le
   nouveau statut sera bien celui persisté, puisque rien entre les deux ne
   relit `setups.status` pour le retraiter avant l'étape suivante
   (`revalidate_all`, qui ne s'applique de toute façon pas à ces statuts,
   absents de `LIFECYCLE_MANAGED_STATUSES`).
3. **Risque réel identifié : écrasement par la branche `SUBMITTED` de
   restauration (`:426-440`).** Si, au même passage de réconciliation, un
   AUTRE ordre du même setup (ex. le stop, side `SELL`) est simultanément
   trouvé `SUBMITTED` alors que le setup vient d'être marqué `MANUAL_REVIEW_
   REQUIRED` par le nouveau code du correctif de Rang 3 (ex. si le
   correctif écrit `MANUAL_REVIEW_REQUIRED` plutôt que `IN_POSITION`
   lorsqu'il détecte un fill sans stop actif, cf. Q3.2) — la boucle
   `for order in self.repository.list_orders(): ...` (`:339`) traite
   **chaque ordre indépendamment, dans l'ordre retourné par `list_orders()`
   (non trié explicitement par type)** ; si l'ordre stop est traité APRÈS
   l'ordre d'entrée dans la même passe, et que le nouveau code de l'entrée a
   déjà écrit `MANUAL_REVIEW_REQUIRED`, la branche `SUBMITTED` (`:432-434`)
   pourrait alors **re-écrire `STOP_ORDER_PLACED`** par-dessus ce
   `MANUAL_REVIEW_REQUIRED` fraîchement posé, dans la même exécution de
   `run()` — un **écrasement intra-passe**, pas une course inter-thread. Ce
   scénario n'est pas observé en production dans ce lot (pas de requête SQL
   dédiée exécutée pour le confirmer empiriquement, faute de cas réel
   disponible), il est déduit de la lecture du code des deux fonctions.
4. **`force_sync()` manuel** (`trading_engine.py:2057-2064`, route
   `/api/runtime/sync`) peut aussi déclencher `reconciliation.run()` à tout
   moment, y compris juste après qu'un opérateur ait manuellement changé un
   statut via une autre route — même famille de risque que le point 3, hors
   du seul cas "démarrage".

---

## INCERTITUDES RÉSIDUELLES

1. **Mécanisme exact du retour `ERROR_REQUIRES_MANUAL_REVIEW → ENTRY_ORDER_
   PLACED` du 29 juin (Q4) toujours non prouvé.** Ce lot élimine
   `setup_lifecycle_service.py`, `setup_engine.py`, et — pour ce cas
   précis — `reconciliation.py:426-440` (faute d'ordre local actif au
   moment utile) et `order_manager.py:466-470` (`attach_missing_stop`,
   l'ordre n'était plus "actif" au sens de la garde `:402-406`). Le fait
   nouveau (2 redémarrages moteur encadrant la fenêtre, 16:18:57 et
   17:26:31, déduit de la double occurrence de `setup_loaded`) n'a pas pu
   être relié à une écriture précise par lecture de code seule. Une piste
   non explorée dans ce lot, hors périmètre "lecture de ce dépôt" : des
   logs d'infrastructure/process (superviseur, orchestrateur de déploiement)
   qui expliqueraient pourquoi le moteur a redémarré 2 fois en ~68 minutes
   ce jour-là, et si un opérateur a modifié la base entre les deux.
2. **Comportement d'activation du stop enfant côté serveur IB (Q3.2)** n'est
   pas observable depuis ce dépôt : le code soumet les 2 ordres du bracket
   ensemble, mais la garantie que le stop devient effectivement actif au
   moment exact du fill du parent dépend du comportement serveur d'IBKR,
   non auditable par lecture de code.
3. **`_reconcile_local_orders` ne trie pas explicitement `list_orders()`
   par type/side avant de les traiter** (`reconciliation.py:339`) — l'ordre
   de traitement entrée-puis-stop ou stop-puis-entrée dans une même passe
   n'est pas garanti par le code lu ; dépend de l'ordre retourné par
   `TradingRepository.list_orders()` (non lu dans ce lot — repositories.py
   n'a été consulté que pour `update_setup_status`), ce qui affecte
   directement la plausibilité du scénario 5.3 point 3.
4. **`STOP_ORDER_PLACED` réellement écrit par le chemin simulé (Q2.1)** :
   déduit de l'absence de `update_setup_status=False` dans l'appel
   `fill_executor.py:119-124`, donc du comportement par défaut de
   `place_stop_order` (`order_manager.py:320`) — non confirmé par
   exécution/trace runtime dans ce lot (lecture de code seule).

---

## OBSTACLES À LA CIBLE

Objectif rappelé : faire écrire `ENTRY_FILLED`/`IN_POSITION` sur un fill réel
via la state machine.

1. **Aucune transition manquante dans `ALLOWED_TRANSITIONS`** (Q2.3) — non
   un obstacle, bonne nouvelle confirmée : la chaîne `ENTRY_ORDER_PLACED →
   ENTRY_FILLED → STOP_ORDER_PLACED/STOP_PLACED → IN_POSITION` est déjà
   entièrement couverte.
2. **Le point d'entrée le plus naturel (`_update_setup_after_reconciled_
   order`, `reconciliation.py:412-465`) n'appelle pas `state_machine`
   aujourd'hui** — aucun des 2 mécanismes existants qui y écrivent
   (`SUBMITTED`, `CANCELLED`) ne passe par `explain_transition`/
   `transition()` (même famille d'écriture directe que `fill_executor.py`,
   confirmé Q1.2). Faire passer la nouvelle branche `FILLED` par
   `state_machine.transition()` introduirait une **incohérence de style à
   l'intérieur du même fichier** (2 branches directes, 1 via state machine)
   sauf à migrer les 2 autres en même temps — décision de conception hors
   périmètre de cet audit.
3. **Le stop n'est pas garanti actif au moment du fill (Q3.2)** — un
   correctif qui marque `IN_POSITION` sur simple `FILLED` sans vérifier
   `protection_snapshot_for_setup`/l'existence réelle d'un stop chez le
   broker créerait un **faux positif de sécurité** : un setup affiché
   `IN_POSITION` (statut qui, sémantiquement dans ce dépôt, sous-entend une
   position protégée) alors que le stop a été rejeté sans remplacement,
   comme observé le 29 juin pour ces 4 mêmes setups.
4. **Risque d'écrasement intra-passe avec la branche `SUBMITTED` de
   restauration** (Q5.3 point 3) — si le correctif de Rang 3 s'insère dans
   `_update_setup_after_reconciled_order` aux côtés de la branche `SUBMITTED`
   existante (celle qui restaure `ENTRY_ORDER_PLACED`/`STOP_ORDER_PLACED`
   depuis un statut terminal ou `MANUAL_REVIEW_REQUIRED`, `:426-440`), les 2
   branches pourraient s'appliquer à des ordres différents du même setup
   dans la même exécution de `run()`, sans garantie d'ordre de traitement
   (incertitude résiduelle #3) — un cas à traiter explicitement dans la
   conception du correctif, pas seulement dans son implémentation.
5. **Le mécanisme de restauration `SUBMITTED` (`:426-440`) reste, en soi, un
   deuxième obstacle de sécurité indépendant** (Q4.4) : il peut sortir un
   setup de `ERROR_REQUIRES_MANUAL_REVIEW`/`MANUAL_REVIEW_REQUIRED` sans
   revue humaine dès qu'un ordre revient `SUBMITTED` côté broker — non
   corrigé par le Rang 3 tel que défini (qui vise la branche `FILLED`), et
   toujours présent après son implémentation.
6. **Aucune couverture pour les setups non-`MANAGEMENT_ONLY` dans la
   réconciliation position↔setup** (Q5.1/5.2) : la boucle
   `reconciliation.py:143-266` qui vérifie la cohérence position broker ↔
   statut setup exclut structurellement les 5 `setup_type` d'entrée normale
   (ligne 152). Un correctif de Rang 3 qui n'agirait que sur `_update_
   setup_after_reconciled_order` (niveau ORDRE) sans jamais consulter
   `broker.positions()` directement resterait incapable de détecter le cas
   où l'ordre local est absent/purgé (`order_history_deleted`, observé Q4.3)
   mais la position existe réellement chez IBKR — un scénario distinct de
   celui du fill classique, à couvrir séparément si jugé nécessaire.

### 1.3 — Confirmation directe : ordre d'entrée réel FILLED, quelle branche, écrit-il un statut ?

**Confirmé : non.** Preuve par le code (tableau 1.2, ligne `FILLED`) et par
la production (voir Q4, événement `order_status_reconciled` du
2026-06-29T16:18:57, `data_json.status="FILLED"`, `data_json.previous_status
="SUBMITTED"` — aucun événement `setup_status_changed` ni aucune autre trace
d'écriture de `setups.status` ne suit cet événement dans la fenêtre observée,
cf. Q4). Le setup reste sur son statut d'avant-fill
(`ENTRY_ORDER_PLACED`/`ENTRY_PARTIALLY_FILLED`, ou tout autre statut courant
au moment du fill, y compris `ERROR_REQUIRES_MANUAL_REVIEW` comme observé en
Q4) tant qu'aucun autre mécanisme ne le fait avancer. Ceci confirme et
précise `audit/06_fill_executor.md:49-62` : le mécanisme n'est pas qu'absent
de `fill_executor.py`, il est **explicitement exclu par construction** dans
`reconciliation.py` (la branche `FILLED` existe dans le vocabulaire du
fichier — `OrderStatus.FILLED.value` est utilisé ailleurs, ligne 386 pour un
compteur — mais n'a jamais été câblée à une écriture de `setups.status`).
