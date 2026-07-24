# Audit 23 — Pré-audit lot 3b (lecture seule)

Contexte : `PostFillProgression.record_fill(order_id, setup_id, quantity, fill_price, symbol)`
(`app/engine/post_fill_progression.py:21-28`) prend des scalaires. Le lot 3b doit
l'appeler depuis `app/engine/reconciliation.py` quand un ordre d'entrée réel
passe à `FILLED`. Objectif de ce document : établir si les données requises
(`symbol`, `quantity`, `fill_price`) sont disponibles au point d'appel, avant
toute implémentation (audit 20, INCERTITUDE #3).

Aucune modification de code n'a été faite pour produire ce document.

---

## Q1 — La donnée disponible au moment du fill

### L'ordre local (dict retourné par `list_orders()` / `get_order()`)

Schéma SQL (`app/storage/database.py:65-82`) :

```
CREATE TABLE IF NOT EXISTS orders (
    id, setup_id, symbol, side, order_type, quantity, status,
    trigger_price, limit_price, stop_price,
    broker_order_id, broker_perm_id, parent_id, oca_group,
    created_at, updated_at
);
```

Le dataclass `OrderRecord` (`app/models.py:292-308`) reflète exactement ces
colonnes — **aucun champ `fill_price` / `avg_fill_price` / `filled_quantity`**.
`quantity` est la quantité demandée à la création de l'ordre
(`order_manager.py:106` pour l'entrée, `order_manager.py:329` pour le stop) ;
elle n'est jamais réécrite. `update_order_status`
(`app/storage/repositories.py:716-720`) ne touche que `status` et
`updated_at` — jamais `quantity`. Donc l'ordre local ne porte ni prix de
remplissage ni quantité réellement exécutée, quel que soit le moment où on le
lit.

### L'objet broker qui permet de conclure FILLED

`_reconcile_local_orders` (`reconciliation.py:325-331`) reçoit trois entrées,
toutes construites dans `run()` :

- `broker_positions` — `await self.broker.positions()` (`reconciliation.py:56`),
  type `BrokerPosition` (`app/broker/ib_models.py:39-47` : symbol, quantity,
  average_price, current_price, market_price, unrealized/realized/daily_pnl).
  Pas de prix par exécution.
- `broker_orders` — `await self.broker.open_orders()` (`reconciliation.py:72`),
  type `BrokerOrderRequest` (`ib_models.py:7-26`, champ `filled_quantity`
  présent) construit depuis `ib.openTrades()`
  (`tws_connector.py:981-1054`, cache session, `filled_quantity` ligne 1015).
  **Un ordre totalement rempli sort de `openTrades()`** — donc au moment où
  la boucle conclut FILLED (voir plus bas), cet objet n'existe déjà plus pour
  cet ordre.
- `broker_order_statuses` — `await broker.order_statuses()` via le wrapper
  `_broker_order_statuses` (`reconciliation.py:90-92`, `505-519`), type
  `dict[str, str]` (statut seul, aucun prix/quantité), construit depuis
  `ib.trades()` (`tws_connector.py:1056-1078`).

Le statut FILLED réel est déterminé ligne `reconciliation.py:356`
(`known_status = _first_matching_status(local_keys, broker_order_statuses)`)
— une simple chaîne de statut — ou, à défaut, inféré ligne `358`
(`_infer_missing_order_status`, `reconciliation.py:597-605`) : FILLED si
`side == "BUY"` et le symbole apparaît dans les positions broker courantes.
Aucune des deux voies ne porte de prix.

### Le prix de remplissage réel est-il disponible ?

Non, à aucun des trois objets ci-dessus. La seule source de prix de
remplissage réel dans tout le module est `BrokerExecution.price`
(`ib_models.py:51-59`), peuplée depuis `ib.fills()`
(`tws_connector.py:1080-1109`, ligne 1103). Elle est récupérée dans `run()`
ligne `reconciliation.py:89` (`broker_executions =
await _broker_recent_executions(self.broker)`) — **mais cette variable n'est
transmise qu'à `_save_broker_reality_report`** (`reconciliation.py:267-280`,
pour le rapport d'affichage), jamais à `_reconcile_local_orders`
(signature `reconciliation.py:325-331`) ni à
`_update_setup_after_reconciled_order` (`reconciliation.py:412-416`). Au
point d'insertion visé par le lot 3b, le prix réel de remplissage est donc
**hors de portée dans le câblage actuel**.

### La quantité réellement remplie est-elle disponible ?

Non, pour la même raison structurelle : `filled_quantity` n'existe que sur
les objets `open_orders()` (déjà disparus une fois l'ordre totalement
rempli), et la colonne locale `quantity` est la quantité demandée, jamais
mise à jour. `BrokerExecution.quantity` (`ib_models.py:55`) porterait la
bonne valeur mais n'est, comme le prix, jamais transmise au point
d'insertion.

---

## Q2 — Le fill partiel

### `ENTRY_PARTIALLY_FILLED` est-il jamais écrit aujourd'hui ?

Défini `app/models.py:54`. Référencé en lecture dans plusieurs endroits :
cible de transition valide (`state_machine.py:133-138`, `139-144`), dans
`setup_condition_tracker.py:28`, `setup_status_reporter.py:141`,
`broker_reality.py:18`, et comme membre de l'ensemble
`_ORDER_DEPENDENT_SETUP_STATUSES` (`reconciliation.py:489-494`, lecture
seule). Recherche de tout appel `update_setup_status(..., ENTRY_PARTIALLY_FILLED...)`
dans `app/` : **aucun résultat**. Personne n'écrit ce statut aujourd'hui.

Cause racine : `_tws_order_status_to_order_status`
(`app/broker/tws_connector.py:3290-3300`) est le seul point qui convertit le
statut brut IB vers l'énum `OrderStatus` que consomme reconciliation. Il
reconnaît explicitement `cancelled/apicancelled`, `inactive/rejected`,
`filled`, `pendingcancel*`, et **fait retomber tout le reste — y compris
`"PartiallyFilled"` — sur `OrderStatus.SUBMITTED.value`** (ligne 3300, cas
par défaut). Un fill partiel réel est donc normalisé en simple SUBMITTED
avant même d'atteindre `reconciliation.py`. La distinction survit seulement
dans le champ séparé `broker_status`
(`_tws_raw_order_status_to_broker_status`, `tws_connector.py:3303-3317`, qui a
bien une branche `partiallyfilled/partialfilled` → `"PARTIALLY_FILLED"`,
lignes 3313-3314) — mais `_reconcile_local_orders` /
`_update_setup_after_reconciled_order` ne lisent jamais `.broker_status`
(recherche du littéral `broker_status` dans `reconciliation.py` : aucun
résultat), seulement `.status`.

### Reconciliation distingue-t-il PARTIALLY_FILLED de FILLED ?

Non — il ne peut pas, puisque "PartiallyFilled" est déjà réduit à SUBMITTED
en amont (voir ci-dessus). Ceinture-bretelles : même si la chaîne brute
"PARTIALLY_FILLED" atteignait `_normalize_order_status`
(`reconciliation.py:583-594`), elle n'est pas dans la liste blanche des 6
valeurs acceptées et serait rejetée (retour `""`).

### Risque de double `record_fill` sur un même ordre

Le champ `status` local de l'ordre est lui-même le verrou d'idempotence.
Une fois un ordre marqué FILLED (`update_order_status`,
`repositories.py:716`), le passage suivant de `_reconcile_local_orders` le
saute via la garde `if current_status not in _ACTIVE_ORDER_STATUSES: continue`
(`reconciliation.py:354`, `_ACTIVE_ORDER_STATUSES = {CREATED, SUBMITTED}`,
ligne 488) — donc **la même ligne d'ordre ne peut pas retraverser deux fois
la branche FILLED**.

Séquence fill partiel → fill complet : passe N, IB rapporte
"PartiallyFilled" → normalisé SUBMITTED → comparé au SUBMITTED local →
égal → aucune mise à jour → `_update_setup_after_reconciled_order` n'est
même pas invoqué (garde `reconciliation.py:360-367`). Passe N+k, IB rapporte
"Filled" → normalisé FILLED → diffère du SUBMITTED local → un seul appel à
`_mark_local_order_status`, donc un seul futur appel à `record_fill`, mais
avec le statut cumulé du broker seulement, sans distinction partiel/final ni
quantité fiable (cf. Q1). **Pas de risque de double appel entre passes**,
mais uniquement parce que l'état intermédiaire de fill partiel est
totalement invisible en amont, pas grâce à un garde-fou explicite sur le
fill partiel lui-même.

---

## Q3 — Le point d'insertion exact et l'ordre de traitement

### Ligne exacte d'insertion

`_update_setup_after_reconciled_order` (`reconciliation.py:412-465`) ne gère
aujourd'hui que deux statuts : SUBMITTED (`426-440`, `return` inconditionnel
ligne 440) et CANCELLED (`441-465`, gardé par
`if status != OrderStatus.CANCELLED.value: return` lignes 441-442). **Tout
autre statut — y compris FILLED, REJECTED, ERROR — tombe directement dans ce
garde CANCELLED et sort sans rien faire.** Concrètement : aujourd'hui, un
ordre réel qui passe à FILLED via reconciliation ne produit **aucun** effet
sur le statut du setup ; seule la ligne d'ordre elle-même est mise à jour en
amont par `_mark_local_order_status` (`reconciliation.py:382`, appelé avant
`_update_setup_after_reconciled_order`). Le point d'insertion naturel pour
une branche FILLED est donc entre le `return` de la branche SUBMITTED
(ligne 440) et le garde CANCELLED (ligne 441) : un nouveau bloc
`if status == OrderStatus.FILLED.value:` prendrait la place de la ligne 441,
repoussant le garde CANCELLED existant après.

### Distinguer entrée (BUY) et sortie/stop (SELL)

`side = str(order.get("side") or "").upper()` (`reconciliation.py:424`),
alimenté par la colonne locale `orders.side` (`database.py:69`), valeurs
`"BUY"`/`"SELL"` (`OrderSide` enum) écrites à la création — `"BUY"` pour
l'entrée (`order_manager.py:104`), `"SELL"` pour le stop
(`order_manager.py:327`). La branche SUBMITTED existante utilise déjà
exactement ce test (`STOP_ORDER_PLACED` si `side == "SELL"` sinon
`ENTRY_ORDER_PLACED`, lignes 427-431) — la branche FILLED devrait le
reprendre à l'identique : `side == "BUY"` → fill d'entrée → `record_fill` ;
`side == "SELL"` → fill de sortie/stop → traitement distinct, hors périmètre
déclaré du lot 3b, mais la branche ne doit pas router un fill de stop vers
`record_fill` par erreur d'aiguillage.

### Ordre de parcours réel (vérifié, pas supposé)

`_reconcile_local_orders` itère `self.repository.list_orders()`
(`reconciliation.py:339`), qui exécute
`SELECT * FROM orders ORDER BY created_at DESC` sans filtre `setup_id` à cet
appel (`repositories.py:685`, `689`) — **du plus récent créé au plus
ancien**, pas l'ordre d'insertion et pas de regroupement par setup.

Pour un bracket réel, `order_manager.place_entry_order` crée l'`OrderRecord`
d'entrée en premier (`order_manager.py:100`, `created_at` par défaut
`utc_now_iso()` à la construction, `models.py:9-10` / `307`), puis, toujours
dans le même appel, crée l'ordre stop via `place_stop_order`
(`order_manager.py:157-165` → `order_manager.py:323`, son propre
`created_at` postérieur). Donc pour un même setup, **le stop a un
`created_at` postérieur à celui de l'entrée**, ce qui signifie qu'en tri
DESC **le stop est visité avant l'entrée** à chaque passe de reconciliation.
`utc_now_iso()` (`models.py:9-10`, `datetime.now(UTC).isoformat()`) inclut
les microsecondes et se trie correctement en texte ; une collision d'égalité
n'est concevable qu'à la microseconde près, peu probable vu les appels
broker `await` intercalés entre les deux constructions.

### Risque d'écrasement entre la future branche FILLED et la branche SUBMITTED

Dans la passe où l'entrée se remplit, le stop (traité en premier, cf.
ci-dessus) est normalement toujours en repos/SUBMITTED sans changement de
statut, donc il n'invoque pas du tout `_update_setup_after_reconciled_order`
(court-circuité par les gardes `if open_status != current_status` /
`if known_status != current_status`, `reconciliation.py:344-353`,
`360-367` — statut inchangé = aucun appel). L'entrée (traitée en second)
atteint alors seule la future branche FILLED. Donc, dans le cas nominal
(un seul événement de fill par passe), pas de collision intra-passe.
À noter : la branche SUBMITTED existante (lignes 426-440) n'écrit déjà
que si `setup_status` est terminal ou `MANUAL_REVIEW_REQUIRED`
(ligne 432-434) — c'est-à-dire qu'elle a déjà été rendue no-op par défaut,
précisément contre ce type de risque d'ordonnancement (audit 19, obstacle
4). La future branche FILLED devrait adopter la même discipline : lire
`setup.get("status")` à jour via `self.repository.get_setup(setup_id)`
(déjà fait fraîchement à chaque appel, ligne 420) et garder son écriture
conditionnelle, pour qu'un réordonnancement intra-passe (stop-puis-entrée,
ou un cas dégénéré à plus de 2 ordres) ne permette pas à un ordre traité
plus tard d'écraser silencieusement le statut posé par un ordre traité plus
tôt.

---

## INCERTITUDES RÉSIDUELLES

1. Persistance garantie ou non d'un ordre FILLED dans `ib.trades()`
   (source de `order_statuses()`, `tws_connector.py:1059-1078`) jusqu'au
   prochain sondage de reconciliation — non vérifiable en lecture statique,
   nécessiterait une session TWS réelle ou la doc `ib_insync`.
2. Départage d'égalité de `list_orders()` en tri DESC si deux ordres
   partagent un `created_at` identique à la microseconde — théoriquement
   possible sous forte contention, non observé ni testé ici.
3. Comportement de `order_statuses()` côté `SimulatedBrokerConnector`
   (`tws_connector.py:433-451`) pour un ordre d'entrée simulé, et si le
   lot 3b est censé aussi transiter par ce chemin ou seulement par le
   broker réel — non précisé dans la demande.

## DONNÉES MANQUANTES POUR APPELER record_fill

- **`fill_price` réel** : absent de tous les objets atteignables depuis
  `_reconcile_local_orders` / `_update_setup_after_reconciled_order`. La
  seule source (`BrokerExecution.price`, `ib_models.py:56`) est récupérée
  dans `run()` (`reconciliation.py:89`) mais jamais transmise à ces deux
  fonctions (signatures `reconciliation.py:325-331`, `412-416`).
- **`quantity` réellement remplie** : absente pour la même raison
  structurelle — `filled_quantity` (`ib_models.py:25`) n'existe que sur les
  objets d'`open_orders()`, déjà disparus au moment où FILLED est détecté ;
  la colonne locale `orders.quantity` (`database.py:71`) est la quantité
  demandée, jamais réécrite (`update_order_status`,
  `repositories.py:716-720`, ne touche que `status`/`updated_at`).
- **`symbol`** : seule des trois valeurs réellement disponible et fiable à
  ce point — présente sur la ligne d'ordre locale
  (`order.get("symbol")`, `reconciliation.py:425`) et redondante sur tous
  les objets broker.

**Conclusion bloquante** : en l'état du câblage actuel, appeler
`record_fill(symbol, quantity, fill_price)` depuis
`_update_setup_after_reconciled_order` n'a pas de source fiable pour 2 des
3 paramètres. Deux options changeraient la conception, ni l'une ni l'autre
tranchée ici (lecture seule) :
(a) faire remonter `broker_executions` (déjà récupéré ligne 89) jusqu'à
`_reconcile_local_orders` / `_update_setup_after_reconciled_order` et
apparier l'exécution par `order_id`/`broker_perm_id` ;
(b) accepter une dégradation explicite (prix/quantité demandés en repli,
avec un event CRITICAL signalant l'approximation).
