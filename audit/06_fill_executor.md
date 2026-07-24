# 06 — Audit ciblé : `app/engine/fill_executor.py`

Mode lecture seule. Fichier lu en entier (134 lignes). Complète le constat
d'incertitude laissé par `audit/05_normalisation.md:304-308` ("5e site
d'écriture directe non audité").

## 1. Écrit-il directement `setups.status`, en contournant `state_machine.transition()` ?

Oui, sur les 3 seuls sites d'écriture de statut du fichier. Aucun import de
`app.engine.state_machine` n'existe dans le fichier (imports lignes 1-10 :
`broker.tws_connector`, `engine.transaction_costs`, `models`,
`storage.event_store`, `storage.repositories` — pas de `state_machine`).
Les 3 sites appellent `self.repository.update_setup_status(...)`, qui est
un `UPDATE` SQL direct (`app/storage/repositories.py:454-489`) sans passage
par `explain_transition`/`transition()` ni levée d'exception en cas de
transition invalide — même mécanisme que les 3 sites déjà identifiés dans
l'audit 05 (A.5, `app/engine/order_manager.py` x4 et
`app/engine/entry_order_executor.py` x1). **Ce fichier est donc bien le 4e
mécanisme distinct (le "5e site" pressenti en A.5 se confirme).**

Sites exacts :
- `fill_executor.py:84-88` → `SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value` (valeur ligne 86)
- `fill_executor.py:103-107` → `SetupStatus.ENTRY_FILLED.value` (valeur ligne 105)
- `fill_executor.py:128-132` → `SetupStatus.IN_POSITION.value` (valeur ligne 130)

## 2. Liste exhaustive des statuts post-entrée posés

| Statut | Fichier:ligne (appel / valeur) | Condition |
|---|---|---|
| `ERROR_REQUIRES_MANUAL_REVIEW` | `fill_executor.py:84-88` (valeur 86) | `trailing_stop_loss.initial_stop` absent sur le fill |
| `ENTRY_FILLED` | `fill_executor.py:103-107` (valeur 105) | Fill simulé accepté par le broker de test |
| `IN_POSITION` | `fill_executor.py:128-132` (valeur 130) | Stop protecteur déjà actif, ou stop placé avec succès (pas `REJECTED`/`ERROR`, ligne 125) |

`STOP_ORDER_PLACED` n'apparaît **jamais** dans ce fichier (grep exhaustif de
`update_setup_status` : 3 occurrences seulement, lignes 84, 103, 128,
aucune ne porte cette valeur). Ce statut est écrit ailleurs
(`order_manager.py:376`), hors périmètre de ce fichier.

**Constat additionnel important** : `simulate_fill_order` (ligne 38) ne
s'exécute que si le broker courant est un `SimulatedBrokerConnector`
(garde ligne 47-49 : `if not isinstance(broker, SimulatedBrokerConnector):
return None`). C'est la **seule** fonction du fichier, et grep exhaustif de
`ENTRY_FILLED`/`IN_POSITION` dans `app/` confirme qu'aucun autre fichier
n'écrit `ENTRY_FILLED` — le seul autre site `IN_POSITION` est
`reconciliation.py:251`, restreint au cas d'adoption d'une position IBKR
existante (`position_source.mode == "adopt_existing_ibkr_position"`,
setups `MANAGEMENT_ONLY` uniquement). **Pour un ordre d'entrée réel rempli
par TWS (broker live, pas le broker de simulation), aucun code du dépôt
n'écrit `ENTRY_FILLED` ni `IN_POSITION`.** `reconciliation.py:_update_setup_
after_reconciled_order` (`:412-465`) ne traite que les statuts d'ordre
`SUBMITTED` et `CANCELLED` — le cas `FILLED` (l'événement
`order_status_reconciled` vu dans l'incident A.3 à 16:18:57) ne déclenche
aucune écriture de `setups.status`. Un setup dont l'ordre d'entrée réel est
rempli reste donc bloqué sur son statut d'avant-fill (`ENTRY_ORDER_PLACED`
ou `ENTRY_PARTIALLY_FILLED`) tant qu'aucun autre mécanisme ne le fait
avancer — ce qui est exactement l'état observé dans la timeline A.3
(`"status"` encore `ENTRY_ORDER_PLACED` juste avant 17:26:47, alors que
l'ordre était déjà reconcilié `FILLED` depuis 16:18:57). Ce n'est pas la
cause du retour non tracé documenté en A.3 (qui se produit avant 16:18:57),
mais c'est un mécanisme structurel distinct qui produit le même symptôme
(statut d'entrée non-terminal qui persiste après un fill réel) sans nécessiter
aucun "retour" — le statut n'a simplement jamais quitté la zone non-terminale.

## 3. Ces statuts sont-ils dans `TERMINAL_SIGNAL_STATUSES` (`signal_engine.py:24-37`) ?

| Statut | Présent ? |
|---|---|
| `ERROR_REQUIRES_MANUAL_REVIEW` | **Présent** (`signal_engine.py:31`) |
| `ENTRY_FILLED` | **Absent** |
| `IN_POSITION` | **Absent** |
| (`STOP_ORDER_PLACED`, hors fichier, pour mémoire) | **Absent** |

`TERMINAL_SIGNAL_STATUSES` contient uniquement : `CLOSED`, `CANCELLED`,
`EXPIRED`, `INVALIDATED`, `DISABLED`, `ERROR`,
`ERROR_REQUIRES_MANUAL_REVIEW`, `BLOCKED`, `STALE_SETUP`,
`MISSED_BREAKOUT_WAIT_RETEST` (`signal_engine.py:24-37`). Tous les statuts
post-fill positifs (`ENTRY_FILLED`, `STOP_ORDER_PLACED`, `IN_POSITION`) en
sont absents.

## 4. `evaluate()` est-il encore appelé au tick suivant ?

Gate : `signal_engine.py:79-80` — `if current_status in
TERMINAL_SIGNAL_STATUSES: continue`.

- Après `ERROR_REQUIRES_MANUAL_REVIEW` : **non**, `evaluate()` n'est plus
  appelé (statut terminal, ligne 79-80 saute le setup).
- Après `ENTRY_FILLED` : **oui**, `evaluate()` est rappelé à chaque tick
  (statut absent de `TERMINAL_SIGNAL_STATUSES`).
- Après `IN_POSITION` : **oui**, idem.

Conséquence par type de setup (lecture de `evaluate()` de chaque
implémentation, `app/setups/*.py`) :
- `range_breakout.py:19-43` : le paramètre `current_status` n'est **jamais
  testé dans le corps de la méthode** (grep : il n'apparaît que dans la
  signature, ligne 22) — `evaluate()` réémet `ENTRY_READY`
  (`range_breakout.py:36-42`) dès que `snapshot.price > high`, **quel que
  soit** `current_status`, y compris `ENTRY_FILLED`/`IN_POSITION`. Ceci est
  exactement le mécanisme confirmé par l'incident réel A.3 (`audit/
  05_normalisation.md:194-201`).
- `momentum_breakout.py:22-36,177-302` : `current_status` n'est comparé
  qu'à `SetupStatus.MISSED_BREAKOUT` (ligne 179) ; en dehors de ce cas,
  toute la chaîne de validation (spread, volume, risque) mène à
  `ENTRY_READY` (ligne 295-302) sans jamais vérifier que le setup n'est pas
  déjà rempli/en position — même défaut structurel que `range_breakout`.
- `breakout_retest.py:45-94`, `pullback_continuation.py:17-47`,
  `aggressive_rebound.py:39-` : ces trois gatent explicitement l'entrée sur
  `current_status == SetupStatus.WAITING_ENTRY_SIGNAL` (respectivement
  lignes 80, 36, 63) et retombent sur `SetupSignal.hold(...)` pour tout
  autre statut, y compris `ENTRY_FILLED`/`IN_POSITION` — **pas de
  réémission d'`ENTRY_READY` pour ces 3 types dans ce cas précis** (mais
  `aggressive_rebound.py:51-56` réémet quand même une `INVALIDATE`
  inconditionnelle si `close < close_below`, même en position — bug
  distinct, hors périmètre de cette question).
- `trailing_runner.py:14-34` (`runner`/`trailing_runner`) : gate sur
  `{IN_POSITION, MANAGING_POSITION}` (ligne 19) mais ne produit jamais
  `ENTRY_READY`, seulement `RAISE_STOP` — pas de risque de réenvoi
  d'ordre d'entrée pour ce type.

## 5. Un de ces statuts peut-il revenir à une valeur non-terminale par un mécanisme de CE fichier ?

**Non.** `fill_executor.py` ne contient aucune logique de réconciliation,
d'annulation de fill ni de retour en arrière : la seule porte d'entrée,
`simulate_fill_order` (lignes 38-133), est un chemin strictement
séquentiel et à sens unique — `SUBMITTED` → (fill broker simulé) →
`FILLED` (ordre) → `ENTRY_FILLED` (setup) → pose du stop → `IN_POSITION`
(setup). Le seul garde-fou du fichier est l'early-return ligne 44 (`if not
order or order["status"] != OrderStatus.SUBMITTED.value: return None`),
qui ne fait que ne rien faire silencieusement si l'ordre n'est plus
`SUBMITTED` — ce n'est pas un mécanisme de retour, juste un no-op.

Le retour non tracé de l'incident A.3 (`ERROR_REQUIRES_MANUAL_REVIEW` →
`ENTRY_ORDER_PLACED` entre 15:41 et 16:18 le 2026-06-29) **n'est donc pas
imputable à ce fichier** : `fill_executor.py` ne référence même pas
`ERROR_REQUIRES_MANUAL_REVIEW` en tant que statut de départ nulle part, il
ne fait qu'y écrire (ligne 86) en cas d'échec. Le mécanisme de retour reste
non identifié, comme conclu dans `audit/05_normalisation.md:182-193`
(INCERTITUDES) — cette lecture ne lève pas cette incertitude, elle
l'exclut seulement comme provenant de ce fichier précis.

Cela dit, comme noté au point 2, ce fichier crée un **risque de nature
différente mais de conséquence identique** : pour un fill réel (broker non
simulé), aucun code n'avance jamais le statut vers `ENTRY_FILLED`/
`IN_POSITION`, donc un setup peut rester indéfiniment sur un statut
non-terminal d'avant-fill (`ENTRY_ORDER_PLACED`) — sans qu'aucun "retour"
soit nécessaire, puisqu'il n'en est jamais sorti.

## Liste complète et exhaustive — statuts requis dans le gate `current_status`

Objectif : empêcher tout ré-envoi d'ordre d'entrée après qu'un ordre réel a
déjà été transmis/rempli, tous types de setup confondus (`range_breakout`,
`momentum_breakout`, `aggressive_rebound`, `breakout_retest`,
`pullback_continuation`).

1. `ENTRY_READY` (déjà émis, ordre en cours de constitution)
2. `ENTRY_ORDER_PLACED` — écrit `order_manager.py:182,468`, `fill_executor.py`
   ne le lit ni ne l'écrit mais c'est le statut qui reste bloqué après un
   fill réel (point 2/5 ci-dessus) ; **statut exact impliqué dans
   l'incident A.3**
3. `ENTRY_PARTIALLY_FILLED`
4. `ENTRY_FILLED` — écrit `fill_executor.py:103-107`
5. `STOP_ORDER_PLACED` — écrit `order_manager.py:376`
6. `STOP_PLACED`
7. `IN_POSITION` — écrit `fill_executor.py:128-132`, `reconciliation.py:249-253`
8. `MANAGING_POSITION`
9. `PARTIAL_EXIT`
10. `RECONCILING_EXISTING_POSITION`

Aucun de ces 10 statuts n'est actuellement dans `TERMINAL_SIGNAL_STATUSES`
(`signal_engine.py:24-37`). Tant que `range_breakout.py` et
`momentum_breakout.py` ne testent pas `current_status` avant d'émettre
`ENTRY_READY`, la seule protection contre un second ordre réel est le
garde-fou d'ordre actif (`CREATED`/`SUBMITTED`) dans `OrderManager`
(`app/storage/repositories.py:260-261,281-282`) — garde-fou qui, comme
démontré empiriquement en A.3, **ne couvre pas** le cas où l'ordre
précédent est déjà `FILLED`.
