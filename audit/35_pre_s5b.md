# Audit 35 — Pré-S5b : la branche SUBMITTED peut-elle effacer l'alarme MANUAL_REVIEW_REQUIRED/ERROR_REQUIRES_MANUAL_REVIEW ? (lecture seule)

Mode : audit lecture seule strict. Aucune modification de code. Commandes
exécutées en dehors de lectures de fichiers, `Grep`/`Glob` et
`sqlite3`-via-Python en `mode=ro` (`sqlite3.connect('file:...?mode=ro',
uri=True)`) sur `data/trading_state.sqlite`. Aucune commande git destructrice.
Date d'audit : 2026-07-24, branche `feat/setup-conditions`.

---

## Q1 — Conditions exactes de l'effacement

### Citation intégrale de la branche SUBMITTED (état actuel, `app/engine/reconciliation.py:456-470`)

```python
456	        if status == OrderStatus.SUBMITTED.value:
457	            target_status = (
458	                SetupStatus.STOP_ORDER_PLACED.value
459	                if side == "SELL"
460	                else SetupStatus.ENTRY_ORDER_PLACED.value
461	            )
462	            if setup_status in _TERMINAL_SETUP_STATUSES or setup_status in {
463	                SetupStatus.MANUAL_REVIEW_REQUIRED.value,
464	            }:
465	                self.repository.update_setup_status(
466	                    setup_id,
467	                    target_status,
468	                    "Open order restored from TWS",
469	                )
470	            return
```

Elle est appelée depuis `_mark_local_order_status` (`reconciliation.py:432-437`),
elle-même appelée depuis `_reconcile_local_orders` (`reconciliation.py:355-387`,
la boucle sur `self.repository.list_orders()`).

### Ensemble exact des statuts depuis lesquels elle écrit

`_TERMINAL_SETUP_STATUSES` (`reconciliation.py:618-625`) :
```python
_TERMINAL_SETUP_STATUSES = {
    SetupStatus.CLOSED.value,
    SetupStatus.CANCELLED.value,
    SetupStatus.EXPIRED.value,
    SetupStatus.INVALIDATED.value,
    SetupStatus.ERROR.value,
    SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
}
```
Union avec `{MANUAL_REVIEW_REQUIRED.value}` à la ligne 462-464. **Ensemble
complet testé : 7 statuts** — `CLOSED`, `CANCELLED`, `EXPIRED`, `INVALIDATED`,
`ERROR`, `ERROR_REQUIRES_MANUAL_REVIEW`, `MANUAL_REVIEW_REQUIRED`.

**Réponse directe** : oui, `MANUAL_REVIEW_REQUIRED` en fait partie (ajouté
explicitement, lignes 462-464). Oui, `ERROR_REQUIRES_MANUAL_REVIEW` aussi (il
est membre de `_TERMINAL_SETUP_STATUSES`, ligne 624). Les deux statuts que le
lot 3b-2 écrit comme alarme sont donc, tous les deux, dans l'ensemble
effaçable.

### Conditions de déclenchement

- **Ordre local** : n'importe quel ordre déjà présent dans la table `orders`
  (`_reconcile_local_orders` itère `self.repository.list_orders()`,
  `reconciliation.py:355`) — peu importe son statut local courant, du moment
  qu'un `open_status` ou `known_status` différent est détecté côté broker.
- **Ordre broker** : le broker doit rapporter ce même ordre (apparié par
  `client_order_id`/`broker_order_id`/`broker_perm_id`, `_local_order_keys` /
  `_broker_order_keys`, lignes 676-691) avec un statut qui, une fois normalisé
  (`_normalize_order_status`, lignes 752-763), vaut `SUBMITTED` — soit via
  `broker.open_orders()` (ligne 360, `open_status` alors forcé à `SUBMITTED`
  par défaut si absent, ligne 670 : `_normalize_order_status(order.status) or
  OrderStatus.SUBMITTED.value`), soit via `broker.order_statuses()`
  (`known_status`, ligne 374).
- **Comparaison** : `open_status != current_status` (ligne 362) ou
  `known_status != str(order.get("status") or "")` (ligne 378). Ce n'est donc
  **pas** une comparaison sur le statut du setup — le setup n'est consulté
  qu'*après*, dans `_update_setup_after_reconciled_order`, pour décider si
  l'écriture doit avoir lieu.
- **Aucune vérification que le setup a explicitement demandé cette
  "restauration"** : l'écriture est inconditionnelle dès que les deux
  conditions précédentes sont réunies et que `setup_status` est dans les 7
  statuts ci-dessus.

---

## Q2 — Le scénario le plus grave

### Le scénario est-il atteignable ?

**Oui, mais pas en un seul passage de réconciliation contre la même
détection d'absence de protection — il faut deux passages (ou davantage),
typiquement séparés par un redémarrage moteur.** Détail :

`has_active_protection()` (`app/engine/post_fill_progression.py:79-81`) lit
`protection_snapshot_for_setup` → `_protection_snapshot`
(`app/storage/repositories.py:302-348`), qui ne compte un stop comme actif
que si son statut local est dans `ACTIVE_ORDER_STATUSES = {"CREATED",
"SUBMITTED"}` (`repositories.py:260`, `_is_active_order`, ligne 281-282). Donc
**si le stop est réellement `SUBMITTED` au moment précis où la branche FILLED
teste la protection, `has_active_protection()` renvoie `True`** et
`mark_in_position` est appelé au lieu d'écrire `MANUAL_REVIEW_REQUIRED`
(`reconciliation.py:518-521`) — l'alarme n'est alors jamais posée, il n'y a
rien à effacer. Le cas "fill sans stop actif" exige donc qu'au moment du
test, le stop soit : absent (aucune ligne), ou dans un statut hors
`{CREATED, SUBMITTED}` — typiquement `REJECTED`/`ERROR`/`CANCELLED`, ou pas
encore soumis du tout.

**Conditions nécessaires pour que le scénario se réalise ensuite** :
1. Une **passe de réconciliation P1** traite l'ordre BUY comme `FILLED` alors
   que le stop associé n'est pas actif → écrit `MANUAL_REVIEW_REQUIRED`
   ("Filled without active protective stop", `reconciliation.py:522-526`).
2. **Après** P1 (passe suivante, généralement après un redémarrage — voir
   Q3), le broker doit se remettre à rapporter l'ordre stop (même
   `setup_id`, `side == SELL`) dans un état qui se normalise en `SUBMITTED`,
   ET ce statut doit différer du statut local enregistré à ce moment
   (ligne 362/378) — sinon `_mark_local_order_status` n'est jamais appelé et
   la branche SUBMITTED ne s'exécute pas du tout.
3. **Si le stop est `REJECTED`** : la fonction ne fait rien avec ce statut
   (`_update_setup_after_reconciled_order` ne traite que `SUBMITTED`,
   `FILLED`, `CANCELLED` — un ordre à `REJECTED` tombe dans aucune des trois
   branches et la fonction retourne silencieusement,
   `reconciliation.py:536-537`). **`REJECTED` n'efface donc rien** — le
   scénario exige spécifiquement que le broker représente l'ordre comme
   encore vivant/`SUBMITTED`, pas qu'il l'ait rejeté.

### Si atteignable, la branche SUBMITTED écrase-t-elle l'alarme ?

**Oui, sans condition supplémentaire.** Dès que P2 détecte le stop `SUBMITTED`
pour un `setup_id` dont le statut courant est `MANUAL_REVIEW_REQUIRED`, la
condition ligne 462-464 est vraie (l'ensemble contient `MANUAL_REVIEW_REQUIRED`)
et la ligne 465-469 écrit `STOP_ORDER_PLACED` avec le message "Open order
restored from TWS" — **directement par-dessus** l'alarme "Filled without
active protective stop", sans lire ni consulter cette alarme, sans
`state_machine` (voir Q3). Un statut affirmant "stop protecteur soumis"
remplace bien un statut affirmant "rempli sans protection" — la même
information (aucun stop actif protégeant un remplissage réel) devient donc,
en base, l'inverse de ce qu'elle était.

### Effet de l'ordre de parcours (`list_orders()` — `created_at DESC`)

`app/storage/repositories.py:689` : `ORDER BY created_at DESC`. Confirmé sans
ambiguïté. Effet **dans un seul et même passage** (pas le scénario Q2
qui est cross-passage, mais la question posée par la consigne) :

- Le stop, généralement créé après l'entrée, est visité **avant** l'entrée
  dans la boucle (`created_at` plus récent en tête).
- **Sens 1 (protecteur)** : si, dans ce même passage, le stop est confirmé
  `SUBMITTED` en premier, `_mark_local_order_status` persiste
  immédiatement `orders.status = SUBMITTED` en base (ligne 404) *avant* que
  l'entrée (plus ancienne) ne soit traitée. Quand la branche FILLED de
  l'entrée appelle ensuite `has_active_protection()`, elle relit les ordres
  et voit désormais le stop actif → **aucune alarme n'est posée du tout**
  dans ce passage (protection réellement vérifiée). Cet ordre de parcours
  *empêche* donc l'effacement en un seul passage, il ne le cause pas.
- **Sens 2 (masquant)** : si le setup était déjà `MANUAL_REVIEW_REQUIRED`
  *avant* le début de ce passage (alarme posée à un passage antérieur), et
  que ce même passage traite le stop en premier : la condition ligne 462-464
  est vraie (statut hérité du passage précédent) → écrit
  `STOP_ORDER_PLACED` ("Open order restored from TWS"). Puis, quand la
  boucle atteint l'entrée (plus ancienne), le garde ligne 474-477 de la
  branche FILLED exige `setup_status in {ENTRY_ORDER_PLACED,
  ENTRY_PARTIALLY_FILLED}` — mais le statut vient d'être changé en
  `STOP_ORDER_PLACED` par le traitement du stop dans le **même** passage, donc
  ce garde échoue et l'entrée est ignorée (`logger.debug`, ligne 478-483,
  aucune trace `events`). Dans ce sens, l'ordre DESC ne fait qu'accélérer de
  quelques microsecondes une effacement qui, de toute façon, se serait produit
  au tour de boucle suivant ou au passage suivant — il ne crée pas de
  scénario supplémentaire, il change seulement lequel des deux ordres
  "gagne" en premier dans le même passage.

**Conclusion Q2** : le scénario est atteignable, sur au moins deux passages
de réconciliation (typiquement autour d'un redémarrage — voir Q3), pas
en un seul passage contre le fill qui vient tout juste de poser l'alarme
(l'ordre DESC protège précisément ce cas immédiat). Il est en revanche
pleinement atteignable dès que l'alarme préexiste et qu'un nouveau passage
retraite le stop.

---

## Q3 — Légalité et autres chemins d'effacement

### `MANUAL_REVIEW_REQUIRED` → `ENTRY_ORDER_PLACED` / `STOP_ORDER_PLACED` sont-elles dans `ALLOWED_TRANSITIONS` ?

`app/engine/state_machine.py:197-201` :
```python
SetupStatus.MANUAL_REVIEW_REQUIRED: {
    SetupStatus.CANCELLED,
    SetupStatus.ERROR,
    SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW,
},
```
Ni `ENTRY_ORDER_PLACED` ni `STOP_ORDER_PLACED` n'y figurent. **Les deux
transitions sont illégales.**

`app/engine/state_machine.py:202-205` :
```python
SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW: {
    SetupStatus.CANCELLED,
    SetupStatus.MANUAL_REVIEW_REQUIRED,
},
```
Même verdict : `ENTRY_ORDER_PLACED`/`STOP_ORDER_PLACED` absentes. **Illégale
aussi depuis ce statut.**

Et la confirmation que ces écritures sont bien "directes" : `update_setup_status`
(`app/storage/repositories.py:454-479`) est un simple `UPDATE ... SET status =
?` — **aucun appel à `StateMachine.can_transition`/`transition`/
`explain_transition` n'existe dans son corps ni dans le code de
`reconciliation.py`**. La transition illégale passe donc silencieusement,
exactement comme le pointait la consigne — `ALLOWED_TRANSITIONS` ne protège
que les 3 sites qui la consultent explicitement (voir tableau ci-dessous),
`reconciliation.py` n'en fait pas partie.

### Grep exhaustif — autres écritures par-dessus `MANUAL_REVIEW_REQUIRED` / `ERROR_REQUIRES_MANUAL_REVIEW`

27 sites d'écriture de `setups.status` recensés dans `app/` (inventaire déjà
établi par l'audit 26 Q4, revérifié aujourd'hui — mêmes lignes) :

| Fichier:ligne | Passe par `state_machine` ? | Peut écraser MRR/ERMR ? |
|---|---|---|
| `action_executor.py:69` | **OUI** (`can_transition`+`transition`) | Non — bloqué par la table (Q3 ci-dessus) |
| `position_action_executor.py:69` | **OUI** (`transition`) | Non — même blocage |
| `setup_lifecycle_service.py:415` | **OUI** (`explain_transition`, écriture seulement si `decision.allowed`) | Non — même blocage |
| `reconciliation.py:465` (SUBMITTED restore) | NON | **OUI — c'est le sujet de cet audit (Q1/Q2)** |
| `order_manager.py:180` (`place_entry_order`, bracket soumis) | NON | Structurellement non atteint aujourd'hui : les deux seuls appelants (`entry_order_executor.py:267` et `manual_order_service.py:293`) sont gatés en amont sur `current_status` (chemin auto, audit 26 A3 — FERMÉ) ou opèrent sur un `setup_id` neuf à chaque appel (chemin manuel, audit 26 A3bis — jamais un setup existant en MRR/ERMR) |
| `order_manager.py:466` (`attach_missing_stop`) | NON | Garde interne exige que l'ordre d'entrée soit encore `CREATED`/`SUBMITTED` (`order_manager.py:402-406`) — **aucun site n'écrit actuellement MRR/ERMR pendant que l'entrée est encore active** (tous passent par `ENTRY_FILLED`/`CANCELLED` d'abord), donc non atteint en pratique, mais **aucune garde n'empêcherait ce chemin si un futur site écrivait MRR/ERMR avec une entrée encore `SUBMITTED`** — fragilité structurelle, pas un chemin actif aujourd'hui |
| `setup_engine.py:258` (`arm_setup`) | NON en interne | Non atteint : le seul appelant, `trading_engine.arm_setup` (`trading_engine.py:2318-2344`), bloque via `lifecycle.can_be_armed`, qui vaut `False` pour tout statut hors `EVALUABLE_STATUSES` (`setup_lifecycle_service.py:143-145`) — MRR et ERMR n'y figurent pas |
| `setup_engine.py:277` (`disarm_setup` → `DISABLED`) | NON en interne | **Atteignable via l'API humaine** `POST /api/setups/{id}/disarm` : `trading_engine._disarm_blockers` (`trading_engine.py:2383-2399`) ne vérifie que "pas d'ordre actif" et "pas de position ouverte" — **pas** le statut courant. Un setup en `MANUAL_REVIEW_REQUIRED` sans ordre actif ni position (ex. adoption bloquée "position introuvable", `reconciliation.py:174-180`) peut être désarmé vers `DISABLED` sans blocage. Différence notable avec le scénario Q2 : c'est une action humaine explicite via l'API, pas une écriture automatique de fond, et `DISABLED` n'affirme pas faussement "ordre soumis" comme le fait `STOP_ORDER_PLACED`/`ENTRY_ORDER_PLACED` — mais c'est bien une transition MRR→cible absente d'`ALLOWED_TRANSITIONS` (`{CANCELLED, ERROR, ERROR_REQUIRES_MANUAL_REVIEW}`), écrite silencieusement |
| tous les autres sites `reconciliation.py`/`post_fill_progression.py`/`order_manager.py` (rejets broker, adoption, annulation stop) | NON | Ces sites **écrivent** MRR/ERMR (posent l'alarme), ils ne l'effacent pas |

**Résultat : un seul chemin automatique (`reconciliation.py:465`, sujet de
cet audit) et un chemin humain via l'API (`setup_engine.py:277` /
`disarm_setup`) peuvent aujourd'hui écraser silencieusement `MANUAL_REVIEW_REQUIRED`
ou `ERROR_REQUIRES_MANUAL_REVIEW` vers un statut hors `ALLOWED_TRANSITIONS`.**

### Un redémarrage suffit-il, ou faut-il un ordre encore ouvert côté TWS ?

`TradingEngine.start()` (`app/engine/trading_engine.py:306-337`) :
```python
306	    async def start(self) -> None:
307	        self._mark_engine_started()
308	        await self.broker.connect()
309	        broker_status = await self._broker_health_check()
...
322	        loaded = self.setup_engine.load_all()
323	        reconciliation_result = await self.reconciliation.run()
```
`reconciliation.run()` (`reconciliation.py:60-143`) **retourne immédiatement**
si le broker n'est pas connecté (ligne 125-143 : `if not broker_connected: ...
return result`), sans toucher à un seul ordre. **Un redémarrage seul, broker
déconnecté, ne déclenche donc rien.**

**Réponse directe** : un redémarrage ne suffit **pas** à lui seul. Il faut, en
plus : (a) le broker connecté (`broker_status == ConnectionStatus.CONNECTED`),
ET (b) que l'ordre stop concerné soit encore rapporté par le broker dans un
état qui se normalise en `SUBMITTED` (via `broker.open_orders()` ou
`broker.order_statuses()`) — c'est-à-dire un ordre réellement encore vivant
côté TWS, pas seulement "le moteur a redémarré". C'est exactement la
formulation de la consigne, confirmée par le code.

---

## Q4 — Portée réelle en production

Requêtes exécutées en `mode=ro` sur `data/trading_state.sqlite`
(`sqlite3.connect('file:...?mode=ro', uri=True)`, aucune écriture).

### Setups actuellement en `MANUAL_REVIEW_REQUIRED` / `ERROR_REQUIRES_MANUAL_REVIEW`

`SELECT ... FROM setups WHERE status IN ('MANUAL_REVIEW_REQUIRED','ERROR_REQUIRES_MANUAL_REVIEW')`
→ **0 ligne.** Aucun setup n'est actuellement bloqué sur ces statuts.

### Le message "Open order restored from TWS" apparaît-il en base ?

- `setups.last_event LIKE '%restored from TWS%'` → **0 ligne.**
- `events.message LIKE '%restored from TWS%'` → **0 ligne** (attendu : la
  branche SUBMITTED, contrairement à toutes les autres branches de
  `_update_setup_after_reconciled_order`, n'appelle jamais
  `self.event_store.record(...)` — c'est la seule trace possible étant
  `setups.last_event`, un champ **écrasable par toute écriture ultérieure**,
  donc ce test ne peut prouver une absence historique, seulement une absence
  *actuelle*).

**Conclusion factuelle** : soit ce chemin n'a jamais écrit ce message pour un
setup encore présent en base, soit il l'a fait et une écriture ultérieure a
recouvert `last_event` depuis — **indécidable avec les données disponibles**,
faute d'historique de statut. Ce n'est pas une preuve d'innocuité.

### Les alarmes du lot 3b-2 ont-elles déjà été posées ?

```
SELECT ... FROM events WHERE event_type='entry_filled_without_protection'  → 0 ligne
SELECT ... FROM events WHERE event_type='entry_filled_unknown_fill_details' → 0 ligne
SELECT ... FROM setups WHERE last_event LIKE '%Filled without active%'     → 0 ligne
SELECT ... FROM setups WHERE last_event LIKE '%fill price%'                → 0 ligne
```
**Zéro occurrence, toutes requêtes confondues.** Les deux alarmes que le lot
3b-2 a ajoutées (`reconciliation.py:500` et `:524`) **n'ont jamais été posées
dans cette base**, cohérent avec l'audit 26 Q3 : le lot 3b-2 n'a jamais tourné
contre un fill réel appairé (aucune table `BrokerExecution`/`fills` en base,
et le seul fill réel historique connu, `LUNR_20260630_001`, est antérieur au
commit du lot). **Le scénario Q2 est donc prouvé atteignable par le code,
mais zéro fois observé empiriquement dans cette base à ce jour** — le risque
est réel mais pas encore matérialisé.

### Les 4 setups du 29 juin — mécanisme de restauration confirmé, mais sur l'ANCIEN chemin (pré-3b-2)

`GILT_20260628_001`, `LUNR_20260628_001`, `QBTS_20260628_001`,
`STM_20260628_001` : tous supprimés (`setup_deleted`, `file_deleted: true`),
0 ligne dans `orders`/`positions` aujourd'hui (confirmé, cohérent avec
l'audit 26 Q3). Leurs événements restent lisibles. Exemple représentatif
(`QBTS_20260628_001`) :
```
2026-06-29T08:15:46  ERROR  entry_order_rejected        "Accepted by TWS: Cancelled"   <- écrit ERROR_REQUIRES_MANUAL_REVIEW (order_manager.py:126-130)
2026-06-29T13:35:29  INFO   setup_loaded                 (redémarrage)
2026-06-29T14:58:22  INFO   setup_loaded                 (redémarrage)
2026-06-29T15:39:52  CRITICAL protective_stop_rejected   <- un NOUVEL ordre stop a été soumis, donc le setup n'était déjà plus en ERROR_REQUIRES_MANUAL_REVIEW à ce moment
2026-06-29T16:18:57  SYNC   order_status_reconciled      "Order marked FILLED"  (previous_status: SUBMITTED)
```
Entre `08:15:46` (alarme posée) et `15:39:52` (nouvel ordre stop, preuve
indirecte que le setup était redevenu actif), **aucun événement visible ne
documente le changement de statut** — cohérent avec le fait que la branche
SUBMITTED n'émet aucun événement (Q1). Le même schéma se répète
identiquement pour `GILT`, `LUNR`, `STM` (protective_stop_rejected /
active_entry_order_unprotected après un ou plusieurs `setup_loaded`, alors
que le setup avait été mis en `ERROR_REQUIRES_MANUAL_REVIEW` juste avant).
**Ceci confirme empiriquement que le mécanisme d'effacement décrit en Q1/Q2
s'est bien produit en production, 4 fois, le 2026-06-29** — mais sur
l'ancien déclencheur d'alarme (`entry_order_rejected`,
`ERROR_REQUIRES_MANUAL_REVIEW`), pas sur les nouvelles alarmes du lot 3b-2
(`MANUAL_REVIEW_REQUIRED` "Filled without active protective stop"), qui elles
n'ont jamais été exercées (paragraphe précédent). Le mécanisme lui-même n'a
donc rien d'hypothétique ; seule son application à l'alarme spécifiquement
ajoutée par 3b-2 reste non observée.

### Coïncidence avec des redémarrages (`setup_loaded`) ?

Oui pour les 4 cas du 29 juin : chacun montre au moins un `setup_loaded`
entre l'alarme et la réapparition d'une activité d'ordre active
(`protective_stop_rejected`/`entry_order_submitted`/nouvel ordre). Note
factuelle : cette journée compte nettement plus de 2 `setup_loaded` par
setup (5 à 7 selon le setup, entre 22:42 la veille et 18:46 le 29), plus que
les "2 redémarrages" mentionnés dans le contexte figé de cet audit — sans
enquête supplémentaire je ne peux pas trancher si ces `setup_loaded`
supplémentaires correspondent à de vrais redémarrages processus ou à un
autre mécanisme d'émission ; je signale l'écart de comptage sans le
résoudre, hors périmètre strict des 4 questions posées.

### Portée temporelle de la base

`events` couvre `2026-05-31T08:59:14` → `2026-07-17T20:23:26` (2 418 019
lignes). Aujourd'hui est le 2026-07-24 : **aucun événement des 7 derniers
jours** — le moteur ne semble pas avoir tourné en conditions réelles depuis
au moins une semaine, ce qui renforce la lecture "zéro occurrence des
alarmes 3b-2" ci-dessus (elles n'ont simplement pas eu l'occasion de
s'exercer récemment), plutôt que de la contredire.

---

## RISQUE QUALIFIÉ

**Le scénario Q2 est-il atteignable aujourd'hui ?** Oui, par lecture directe
du code (Q1/Q2), sans aucune modification supplémentaire requise :
- Le chemin d'écriture (`reconciliation.py:456-470`) traite
  `MANUAL_REVIEW_REQUIRED` et `ERROR_REQUIRES_MANUAL_REVIEW` de façon
  identique et les efface tous les deux vers `ENTRY_ORDER_PLACED`/
  `STOP_ORDER_PLACED` dès qu'un ordre du même `setup_id` est retrouvé
  `SUBMITTED` côté broker.
- Ces deux transitions sont absentes d'`ALLOWED_TRANSITIONS` mais l'écriture
  ne passe par aucun garde-fou de state machine (Q3) — rien ne les
  intercepte.
- Le mécanisme générique (statut post-entrée effacé vers un statut actif par
  la branche SUBMITTED) s'est **déjà produit 4 fois en production**, le
  2026-06-29 (Q4), sur l'ancien déclencheur d'alarme.
- La nouvelle paire d'alarmes ajoutée par le lot 3b-2 spécifiquement (celle
  visée par cet audit) n'a, elle, jamais été exercée à ce jour (Q4) — le
  risque est donc démontré par le code et par un précédent structurellement
  identique, mais pas encore observé sur cette alarme précise.
- Un redémarrage seul ne suffit pas ; il faut en plus que le broker soit
  connecté et rapporte encore l'ordre stop concerné comme vivant (Q3) — ce
  n'est donc pas garanti à chaque restart, mais ce n'est pas rare non plus
  (c'est précisément la situation qui s'est produite 4 fois le 29 juin).
- Un second chemin, humain cette fois (`disarm_setup` via l'API,
  `setup_engine.py:277`), peut également écraser silencieusement
  `MANUAL_REVIEW_REQUIRED` vers `DISABLED` sans vérifier le statut courant
  (Q3) — distinct du scénario Q2 mais dans la même famille de problème
  (transition hors `ALLOWED_TRANSITIONS`, écrite sans garde).

**Options factuelles existantes pour fermer ce chemin précis**
(`reconciliation.py:456-470`), sans en recommander une :
1. Retirer `MANUAL_REVIEW_REQUIRED` (et/ou `ERROR_REQUIRES_MANUAL_REVIEW`) de
   l'ensemble testé ligne 462-464, pour que la branche SUBMITTED ne "restaure"
   plus jamais un setup déjà en alarme.
2. Faire passer cette écriture par `StateMachine.explain_transition`/
   `can_transition`, comme le font déjà les 3 sites gatés
   (`action_executor.py`, `position_action_executor.py`,
   `setup_lifecycle_service.py`) — la table `ALLOWED_TRANSITIONS` bloquerait
   alors nativement `MANUAL_REVIEW_REQUIRED`/`ERROR_REQUIRES_MANUAL_REVIEW` →
   `ENTRY_ORDER_PLACED`/`STOP_ORDER_PLACED`.
3. Distinguer, dans l'ensemble effaçable, les causes de mise en alarme qui
   sont réellement "en attente de confirmation broker" (ex. l'ancien
   `entry_order_rejected` suivi d'un désaveu du broker) de celles qui
   affirment un fait déjà constaté et grave (un fill réel sans protection) —
   ces deux catégories partagent aujourd'hui le même statut
   `MANUAL_REVIEW_REQUIRED` et donc le même traitement en aval.
4. Ajouter un événement (`event_store.record`) à l'écriture ligne 465-469,
   pour au moins rendre l'effacement observable après coup — n'empêche rien,
   mais aurait permis à cet audit de répondre à Q4 sans ambiguïté.

Aucune de ces options n'a été mise en œuvre par cet audit (lecture seule
stricte, aucune modification de code).
