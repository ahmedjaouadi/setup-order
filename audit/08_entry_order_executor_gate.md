# 08 — Audit ciblé : gate `current_status` manquant dans `app/engine/entry_order_executor.py`

Mode lecture seule. Fichier lu en entier (446 lignes), plus les points
d'appel amont (`trading_engine.py`) et le seul contrôle indirect
disponible (`setup_lifecycle_service.py`), pour vérifier précisément
l'absence de gate déjà pressentie dans `audit/05_normalisation.md` (A.3,
point 4) et `audit/06_fill_executor.md` (points 4-5).

## 1. `execute_entry_ready` ne reçoit même pas `current_status`

Signature : `entry_order_executor.py:50-53`

```python
async def execute_entry_ready(
    self,
    setup: dict[str, Any],
    signal: Any,
) -> bool:
```

Aucun paramètre `current_status`/`SetupStatus`. Par contraste,
`ActionExecutor.execute_simple_action` (`action_executor.py:25-30`) et
`transition_setup` (`action_executor.py:41-49`) **prennent bien**
`current_status` et l'utilisent pour appeler
`self.state_machine.transition(current_status, target_status)`
(`:49`) — donc le patron "gater sur le statut courant via
`state_machine`" existe déjà ailleurs dans le moteur, il est simplement
absent de ce fichier.

## 2. Le point d'appel amont possède `current_status` mais ne le transmet pas

`trading_engine.py:2463-2470` :

```python
async def _handle_signal(
    self, setup: dict[str, Any], current_status: SetupStatus, signal: Any
) -> None:
    if self.action_executor.execute_simple_action(setup, current_status, signal):
        return
    if self.position_action_executor.execute_raise_stop_signal(setup, current_status, signal):
        return
    await self.entry_order_executor.execute_entry_ready(setup, signal)
```

`current_status` est bien disponible dans `_handle_signal` (paramètre
d'entrée, alimenté par `SignalEvaluation.current_status` produit dans
`signal_engine.py:96`) et **est utilisé** pour les 2 premiers gestionnaires
(`execute_simple_action` pour `HOLD`/`INVALIDATE`/`STATUS_CHANGE`,
`execute_raise_stop_signal` pour `RAISE_STOP`) — mais **n'est pas transmis**
au 3e appel, ligne 2470. C'est structurellement le même paramètre qui
existe, circule, et s'arrête juste avant le seul appelant qui en aurait
besoin pour bloquer un ré-envoi.

## 3. Aucune vérification de `setup["status"]` nulle part dans le corps de la méthode

Grep exhaustif de `status` dans `entry_order_executor.py` (résultat complet
: lignes 73, 105, 121, 162, 179, 280, 318, 319, 326, 336, 342). Aucune de
ces occurrences ne lit `setup["status"]`/`setup.get("status")` ni ne
compare à un `current_status` reçu en paramètre. Elles se répartissent en :
- `decision_status` (73, 105, 162, 179, 336, 342) — champs de métadonnées
  d'analyse, pas le statut du setup.
- lignes 121, 280 — `self.repository.update_setup_status(...)` : les 2
  **écritures** du fichier (toujours `ERROR_REQUIRES_MANUAL_REVIEW`,
  cas `ManagementOnlyEntryError`/`ManagementOnlyEntryError` capturée
  après l'appel broker), pas des lectures/gates en amont.
- lignes 318-326 — le seul contrôle qui ressemble à un gate de statut,
  détaillé au point 4.

La séquence complète des contrôles avant l'appel broker
(`order_manager.place_entry_order`, ligne 262-263) est, dans l'ordre :
`signal.action != ENTRY_READY` (55) → politique de session (58) →
fenêtre horaire (80) → `trade_guards.evaluate_entry` (95, garde-fous
globaux : exposition/halt/PDT, pas par setup) → rôle `MANAGEMENT_ONLY`
(112) → `auto_execution_enabled` (128) → `_lifecycle_allows_transmission`
(149, voir point 4) → trailing stop présent/prêt (151-186) →
`risk_engine.evaluate` (194, agrégé sur les positions ouvertes, pas sur ce
setup précis) → cost gate (212) → broker reality / engine safety
(242-260) → **appel broker** (262-263). **Aucun de ces 12 contrôles ne
lit le statut persistant du setup ciblé.**

## 4. Le seul contrôle indirect (`_lifecycle_allows_transmission`) ne couvre pas les statuts post-entrée

`entry_order_executor.py:307-350` :

```python
blocked_statuses = {
    SetupStatus.INVALIDATED.value,
    SetupStatus.EXPIRED.value,
    SetupStatus.STALE_SETUP.value,
    SetupStatus.MISSED_BREAKOUT_WAIT_RETEST.value,
    SetupStatus.BLOCKED.value,
}
if status not in blocked_statuses:
    return True
```

`status` provient de `self.lifecycle_service.revalidate(effective_setup)`
(`:315`), qui appelle `revalidate_setup` (`setup_lifecycle_service.py:87`).
Or `revalidate_setup` court-circuite explicitement les statuts
post-entrée : `setup_lifecycle_service.py:143-145`

```python
if current_status not in EVALUABLE_STATUSES:
    # Position/order/terminal statuses are owned by other engines.
    return result(current_status, "NOT_REVALIDATED", can_be_armed=False)
```

`EVALUABLE_STATUSES` (`setup_lifecycle_service.py:56-69`) ne contient que
des statuts **pré-fill** (`DISABLED`, `VALIDATED`, `WAITING_*`,
`MISSED_BREAKOUT`, `REARMED_ON_NEW_BASE`, `ENTRY_READY`, plus les
`LIFECYCLE_MANAGED_STATUSES`) — `ENTRY_ORDER_PLACED`,
`ENTRY_PARTIALLY_FILLED`, `ENTRY_FILLED`, `STOP_ORDER_PLACED`,
`STOP_PLACED`, `IN_POSITION`, `MANAGING_POSITION` en sont tous absents.
**Conséquence directe** : pour un setup déjà `ENTRY_FILLED` ou
`IN_POSITION`, `result["status"]` renvoyé vaut le statut courant lui-même
(`ENTRY_FILLED`/`IN_POSITION`, inchangé) avec `status_reason =
"NOT_REVALIDATED"` — cette valeur n'est **jamais** dans
`blocked_statuses` (qui ne liste que des statuts pré-entrée), donc
`_lifecycle_allows_transmission` retourne `True` (ligne 326) : **la
transmission n'est jamais bloquée par ce mécanisme pour un setup déjà
entré, peu importe son statut réel.**

## 5. La seule protection restante est le garde-fou d'ordre actif d'`OrderManager` — déjà connu pour ne pas couvrir le cas `FILLED`

`order_manager.py:place_entry_order` (`:64-72`) lève `DuplicateOrderError`/
`UnprotectedActiveOrderError` (`:73-83`) via
`self.repository.protection_snapshot_for_setup(setup["setup_id"])`
(`repositories.py:761`), qui s'appuie sur `_is_active_order`
(`repositories.py:281-282`) : `ACTIVE_ORDER_STATUSES = {"CREATED",
"SUBMITTED"}` (`repositories.py:260`). Un ordre déjà `FILLED` **ne compte
plus comme actif** — donc `protection.get("active_entry_order_id")`
redevient `None` et aucun des deux garde-fous ne se déclenche. C'est
exactement le mécanisme démontré empiriquement dans l'incident réel A.3
(`audit/05_normalisation.md:202-211`) : 4 setups `range_breakout` ont reçu
un second ordre d'entrée réel après que le premier avait déjà été
reconcilié `FILLED`, précisément parce qu'aucune couche entre le signal
`ENTRY_READY` et l'appel broker ne vérifie le statut du setup.

## Conclusion

`entry_order_executor.py` ne contient et ne reçoit **aucun gate sur
`current_status`/`setup["status"]`** entre la production du signal
`ENTRY_READY` (par `signal_engine.evaluate_snapshot`, potentiellement
réémis à chaque tick pour `range_breakout`/`momentum_breakout` — audit 06,
point 4) et l'appel broker réel. Les 3 lignes de défense existantes
(`_lifecycle_allows_transmission`, garde-fous `OrderManager`, statut
persistant) sont soit hors périmètre des statuts post-entrée (lifecycle),
soit contournables dès que l'ordre précédent est `FILLED` (OrderManager).

**Correctif minimal cohérent avec les audits 06/07** : ajouter, en tête de
`execute_entry_ready` (ou dans `_handle_signal` avant l'appel ligne 2470,
en réutilisant le `current_status` déjà en scope), un gate explicite du
type :

```python
if current_status not in ENTRY_ELIGIBLE_STATUSES:  # ex. WAITING_ACTIVATION, WAITING_ENTRY_SIGNAL, ENTRY_READY
    return True  # no-op, cohérent avec les autres branches de _handle_signal
```

ce qui court-circuiterait toute réémission d'`ENTRY_READY` pour un setup
déjà `ENTRY_ORDER_PLACED`/`ENTRY_PARTIALLY_FILLED`/`ENTRY_FILLED`/
`STOP_ORDER_PLACED`/`STOP_PLACED`/`IN_POSITION`/`MANAGING_POSITION`/
`PARTIAL_EXIT`/`RECONCILING_EXISTING_POSITION` — la même liste de 10
statuts déjà établie comme candidats à `TERMINAL_SIGNAL_STATUSES` dans
`audit/06_fill_executor.md`, sans attendre une modification de
`signal_engine.py` ni des `evaluate()` de chaque type de setup.
