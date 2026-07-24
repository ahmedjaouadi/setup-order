# 07 — Audit ciblé : `STOP_ORDER_PLACED` dans `app/engine/order_manager.py`

Mode lecture seule. Fichier `order_manager.py` lu en entier (693 lignes).
Complète `audit/06_fill_executor.md` (point 2), qui notait seulement que
`STOP_ORDER_PLACED` est "écrit ailleurs (`order_manager.py:376`)" sans en
vérifier le contexte exact.

## Site d'écriture unique

`SetupStatus.STOP_ORDER_PLACED.value` n'apparaît qu'à un seul endroit dans
tout le fichier (grep exhaustif) : `order_manager.py:373-378`, à
l'intérieur de `place_stop_order` (méthode `:312-394`) :

```
373	        if update_setup_status:
374	            self.repository.update_setup_status(
375	                setup["setup_id"],
376	                SetupStatus.STOP_ORDER_PLACED.value,
377	                "Protective stop submitted",
378	            )
```

L'écriture est **conditionnelle** au paramètre `update_setup_status: bool =
True` de la signature (`:320`) — même mécanisme d'écriture directe que les
autres sites déjà audités (pas de `state_machine.transition()`, import
absent du fichier).

## Les 3 appelants de `place_stop_order` — un seul active réellement l'écriture

Grep exhaustif de `place_stop_order` sur tout le dépôt : 3 appels, tous
dans `order_manager.py`/`fill_executor.py` (aucun autre fichier).

| Appelant | Ligne | `update_setup_status` passé | Écrit `STOP_ORDER_PLACED` ? |
|---|---|---|---|
| `OrderManager.place_entry_order` | `order_manager.py:157-165` | `False` (explicite, `:164`) | **Non** |
| `OrderManager.attach_missing_stop` | `order_manager.py:437-445` | `False` (explicite, `:444`) | **Non** |
| `FillExecutor.simulate_fill_order` | `fill_executor.py:119-124` | **absent** → défaut `True` (`:320`) | **Oui** |

Dans les 2 premiers cas (`place_entry_order`, `attach_missing_stop`), le
statut affiché à l'utilisateur après un stop réussi est directement
`ENTRY_ORDER_PLACED` (`order_manager.py:180-184` et `:466-470`), jamais
`STOP_ORDER_PLACED` — cohérent avec l'audit 05 (A.1) qui n'observait que
`ENTRY_ORDER_PLACED` comme statut stable après soumission du bracket.
**`STOP_ORDER_PLACED` n'est donc atteignable que par le 3e chemin, celui
initié depuis `fill_executor.py` après un fill (paper/simulé
uniquement — cf. audit 06, point 2).**

## Contexte exact du seul chemin qui l'écrit : une valeur transitoire, aussitôt écrasée

Séquence réelle pour ce chemin (`fill_executor.py:38-133`, rappel) :

1. `fill_executor.py:103-107` écrit `ENTRY_FILLED`.
2. `fill_executor.py:119-124` appelle
   `self.stop_order_placer.place_stop_order(setup, quantity=..., stop_loss=...,
   parent_id=order_id)` **sans** l'argument `update_setup_status` → l'appel
   arrive dans `order_manager.py:312` avec la valeur par défaut `True`.
3. Deux issues possibles dans `place_stop_order` :
   - **Échec broker** (`order.status in {REJECTED, ERROR}`,
     `order_manager.py:347-350`) : écrit `ERROR_REQUIRES_MANUAL_REVIEW`
     (`:352-356`) et **retourne avant** la ligne `373` — le bloc `if
     update_setup_status` n'est jamais atteint. `STOP_ORDER_PLACED` n'est
     **pas** écrit dans ce cas, quelle que soit la valeur du flag.
     `fill_executor.py:125-126` détecte ensuite `stop_order.status in
     {REJECTED, ERROR}` et retourne sans réécrire de statut — le statut
     final reste `ERROR_REQUIRES_MANUAL_REVIEW` (terminal, correctement
     gaté).
   - **Succès broker** : `place_stop_order` écrit `STOP_ORDER_PLACED`
     (`:373-378`), **puis rend la main à `fill_executor.py`**, qui,
     toujours dans la même coroutine synchrone (pas d'`await` entre les
     deux), écrit immédiatement `IN_POSITION` (`fill_executor.py:128-132`).

**Conclusion sur le contexte exact** : sur son unique chemin d'écriture
réelle, `STOP_ORDER_PLACED` n'est jamais l'état stable/final observé par un
tick suivant du moteur de signal — c'est une valeur intermédiaire posée et
immédiatement remplacée par `IN_POSITION` avant que `evaluate()` ne
puisse la relire (pas d'`await` entre les deux écritures, donc pas de point
de suspension où un autre tick pourrait s'intercaler entre `STOP_ORDER_
PLACED` et `IN_POSITION` pour ce même setup). Le seul risque théorique
serait qu'un tick **concurrent** sur un thread/tâche différente lise la
base entre les deux `UPDATE` (pas de transaction englobante visible dans
`update_setup_status`, `app/storage/repositories.py:454-489, ` chaque appel
est un `UPDATE` autocommit indépendant) — mais cela sort du périmètre de ce
fichier et n'a pas été vérifié ici.

Ceci **ne change rien** à la conclusion de l'audit 06 : `STOP_ORDER_PLACED`
reste absent de `TERMINAL_SIGNAL_STATUSES` et doit y figurer (ou dans le
gate `current_status` des types d'entrée) pour rester cohérent avec
`ENTRY_ORDER_PLACED`/`ENTRY_FILLED`/`IN_POSITION`, notamment pour le
chemin réel (non simulé) où, comme noté en audit 06, **aucun** code
n'atteint jamais `place_stop_order` avec effet sur le statut après un fill
TWS réel — le setup resterait alors bloqué en amont
(`ENTRY_ORDER_PLACED`), jamais même sur `STOP_ORDER_PLACED`.
