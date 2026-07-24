# Audit 24 — Appariement exécution → ordre (lecture seule, très court)

## Q1 — Appariement exécution → ordre

**`BrokerExecution`** (`app/broker/ib_models.py:51-59`), citée intégralement :

```python
@dataclass(slots=True)
class BrokerExecution:
    execution_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    order_id: str | None = None
    broker_perm_id: str | None = None
    timestamp: str | None = None
```

Deux identifiants d'ordre : `order_id` et `broker_perm_id`.

Construction (`tws_connector.py:1093-1108`, depuis `ib.fills()`) :
- `execution_id` ← `exec_detail.execId`
- `order_id` ← `exec_detail.orderId` (IB `Execution.orderId`)
- `broker_perm_id` ← `exec_detail.permId` (IB `Execution.permId`)

La table locale `orders` stocke `broker_order_id` et `broker_perm_id`
(`database.py:65-82`). Ces deux colonnes sont peuplées, à la création de
l'ordre, depuis exactement les mêmes champs IB : `submit_order`
(`tws_connector.py:845-846`) lit `broker_order_id = trade.order.orderId` et
`perm_id = trade.order.permId`, renvoyés dans `BrokerOrderResult` puis
copiés sur `order.broker_order_id`/`order.broker_perm_id`
(`order_manager.py:117-118` pour l'entrée, équivalent pour le stop) avant
`upsert_order`.

Donc `BrokerExecution.order_id` ↔ `orders.broker_order_id` et
`BrokerExecution.broker_perm_id` ↔ `orders.broker_perm_id` sont **le même
identifiant IB de part et d'autre** (`orderId`/`permId`), avec un double
appariement possible (croisement des deux clés, sur le modèle de
`_local_order_keys`/`_broker_order_keys`, `reconciliation.py:553-568`).
**Appariement fiable possible, oui**, à condition de câbler ce rapprochement
(non fait aujourd'hui : `_reconcile_local_orders` ne reçoit pas
`broker_executions`, cf. audit 23).

## Q2 — Fenêtre de rétention des exécutions

`recent_executions()` → `ib.fills()` → `list(self.wrapper.fills.values())`
(`ib_async/ib.py:618-620`), un dict en mémoire alimenté uniquement par le
callback `execDetails` (`ib_async/wrapper.py:800-827`).

Ce dict est peuplé par deux voies :
1. **Fills reçus en direct** pendant la session (callback `execDetails` sur
   chaque exécution survenant après connexion).
2. **Un `reqExecutions()` automatique au connect** : `ib.connectAsync(...)`
   (appelé sans `fetchFields` dans ce repo, `tws_connector.py:648-653`, donc
   avec le défaut `StartupFetchALL` qui inclut `StartupFetch.EXECUTIONS`,
   `ib_async/ib.py:71-78`) déclenche `reqExecutionsAsync()`
   (`ib_async/ib.py:2087-2093`) avec un `ExecutionFilter()` vide
   (`ib_async/ib.py:2278-2285`) — sans filtre temporel explicite, l'API IB
   ne retourne par défaut que **les exécutions du jour courant**.

Donc : si le moteur redémarre le **même jour** après un fill, l'exécution
est **encore visible** au prochain `run()` (récupérée par le
`reqExecutions()` du reconnect). Si le redémarrage a lieu un **jour
ultérieur**, elle **ne l'est plus** via ce chemin par défaut (aucun filtre
temporel étendu n'est utilisé dans ce repo). Le barreau 1 (exécutions) doit
donc être conçu pour dégrader proprement (retour vide / statut « inconnu »)
plutôt que supposer une disponibilité garantie au-delà de la session/jour
courant — pas d'échec silencieux si `recent_executions()` renvoie `[]`.

## Q3 — BrokerPosition comme barreau 2

Signatures : `BrokerPosition` est un paramètre de `_reconcile_local_orders`
(`reconciliation.py:325-337`, utilisé lignes 333-337 et 358) mais **absent**
de la signature de `_update_setup_after_reconciled_order`
(`reconciliation.py:412-416`, seuls `order` et `status`). Il n'atteint donc
pas non plus ce point aujourd'hui.

`average_price` = coût moyen **blendé** de la position entière, pas le prix
de ce fill. Mapping IB (`tws_connector.py:1148-1158`) : valeur de base
`position.avgCost` (`ib.positions()`), écrasée si disponible par
`portfolio_item.averageCost` (`ib.portfolio()`) — les deux champs IB
documentés comme coût moyen d'entrée sur l'ensemble de la position
(confirmé aussi par `docs/Lecture_des_donnees_TWS_IBKR.md:76-81`,
« PRX MYN / Prix moyen = coût moyen d'entrée »). Si la position préexistait
et que ce fill vient s'y ajouter, `average_price` mélange l'ancien et le
nouveau coût — inutilisable tel quel comme prix de ce fill précis.

Détection d'une position préexistante : **aucun champ broker** ne porte ce
signal (`BrokerPosition`, `ib_models.py:39-47` : symbol, quantity,
average_price, current_price, market_price, unrealized/realized/daily_pnl —
rien d'assimilable à "nouvelle vs déjà détenue"). Le seul moyen est
applicatif : interroger `self.repository.get_position(symbol)`
(`repositories.py:805`, déjà utilisé ainsi ligne `reconciliation.py:445`
pour la branche stop annulé) **avant** tout `upsert_position` de cette même
passe — une ligne existante en base à ce moment signale que la position
n'est pas née de ce fill, donc que `average_price` n'est pas son prix.

---

## APPARIEMENT FIABLE : OUI / NON

**OUI pour exécution ↔ ordre (Q1)** : `BrokerExecution.order_id` /
`.broker_perm_id` correspondent exactement à `orders.broker_order_id` /
`orders.broker_perm_id`. Rien ne manque du côté identifiants — seul le
câblage (transmettre `broker_executions` jusqu'au point d'appel, cf. audit
23) reste à faire.

**Ce qui manque encore** :
- Le barreau 1 (exécutions) n'est fiable que **le jour même** de la
  connexion en cours (Q2) — pas de garantie après redémarrage tardif ;
  nécessite une dégradation explicite, pas un échec silencieux.
- Le barreau 2 (`BrokerPosition.average_price`) n'est **pas un substitut
  fiable** de `fill_price` dès qu'une position préexistait (Q3) ; son
  usage en repli demanderait de vérifier d'abord `get_position(symbol)` et,
  si une position existait déjà, d'exclure ce barreau ou de le signaler
  comme dégradé.
