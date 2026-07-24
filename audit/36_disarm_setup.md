# Audit 36 — disarm_setup : action humaine ou chemin automatique ? (complément S5b, lecture seule)

Mode : audit lecture seule strict. Aucune modification de code. Commandes
exécutées en dehors de lectures de fichiers, `Grep`/`Glob` et
`sqlite3`-via-Python en `mode=ro` (`sqlite3.connect('file:...?mode=ro',
uri=True)`) sur `data/trading_state.sqlite`. Aucune commande git destructrice.
Date d'audit : 2026-07-24, branche `feat/setup-conditions`.

Contexte : l'audit 35 (Q3) a identifié `setup_engine.py:277` (`disarm_setup`)
comme un second chemin capable d'écraser silencieusement
`MANUAL_REVIEW_REQUIRED`/`ERROR_REQUIRES_MANUAL_REVIEW` vers `DISABLED`, en
plus de la branche SUBMITTED de la réconciliation (`reconciliation.py:456-470`).
Objet de cet audit : déterminer si ce chemin est une action humaine
légitime ou un mécanisme automatique.

---

## Q1 — Citation intégrale, statuts de départ, direct ou via state_machine, cible légale ?

### `SetupEngine.disarm_setup` (`app/engine/setup_engine.py:273-291`), citée intégralement

```python
273	    def disarm_setup(self, setup_id: str) -> None:
274	        existing = self.repository.get_setup(setup_id)
275	        if existing is None:
276	            raise KeyError(setup_id)
277	        self.repository.update_setup_status(
278	            setup_id,
279	            SetupStatus.DISABLED.value,
280	            "Setup disarmed",
281	        )
282	        self.event_store.record(
283	            EventLevel.INFO,
284	            "setup_disarmed",
285	            "Setup disarmed",
286	            setup_id=setup_id,
287	            symbol=str(existing.get("symbol", "")).upper() or None,
288	        )
289	
290	    def disable_setup(self, setup_id: str) -> None:
291	        self.disarm_setup(setup_id)
```

### Statuts de départ acceptés

**Tous, sans exception.** `existing` (le setup récupéré ligne 274) n'est lu
que pour vérifier qu'il existe (`is None`, ligne 275-276) et pour son
`symbol` (ligne 287) — **son champ `status` n'est jamais lu ni comparé**
nulle part dans cette méthode. Aucun statut de départ n'est exclu.

### Écriture directe ou via `state_machine` ?

**Directe.** Ligne 277-281 : appel nu à
`self.repository.update_setup_status(...)`, qui (`repositories.py:454-479`)
exécute un `UPDATE setups SET status = ...` sans consulter
`StateMachine.can_transition`/`transition`/`explain_transition`. Aucune
importation de `state_machine` dans `setup_engine.py`
(confirmé par `Grep "state_machine" app/engine/setup_engine.py` → aucun
résultat).

### `DISABLED` est-elle une cible légale depuis `MANUAL_REVIEW_REQUIRED` dans `ALLOWED_TRANSITIONS` ?

`app/engine/state_machine.py:197-201` :
```python
SetupStatus.MANUAL_REVIEW_REQUIRED: {
    SetupStatus.CANCELLED,
    SetupStatus.ERROR,
    SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW,
},
```
**Non — `DISABLED` n'y figure pas.** Même verdict depuis
`ERROR_REQUIRES_MANUAL_REVIEW` (`state_machine.py:202-205` :
`{CANCELLED, MANUAL_REVIEW_REQUIRED}`, `DISABLED` absente également).
`disarm_setup` peut donc écrire, sans aucun garde, une transition absente de
la table pour l'un et l'autre statut d'alarme.

---

## Q2 — Qui appelle `disarm_setup` ? Chemin humain ou automatique ?

Grep exhaustif de `\.disarm_setup\(` et `\.disable_setup\(` sur tout le
dépôt (`*.py`) :

| Fichier:ligne | Nature |
|---|---|
| `app/api/routes_setups.py:194` | `POST /api/setups/{setup_id}/disarm` — endpoint API HTTP, appelé uniquement sur requête entrante |
| `app/engine/trading_engine.py:2376` | `TradingEngine.disarm_setup`, appelé **seulement** depuis la route ci-dessus (aucun autre appelant de `trading_engine.disarm_setup` trouvé hors tests) |
| `app/engine/setup_engine.py:291` | `SetupEngine.disable_setup` délègue à `disarm_setup` — **mais `disable_setup` lui-même n'a aucun appelant en production** (voir note ci-dessous) |
| `tests/test_setup_tools.py:219,245,266,296` | tests, hors périmètre |

**Note sur `disable_setup`** : la route qui porte ce nom dans l'API,
`app/api/routes_setups.py:293` (`POST /api/setups/{setup_id}/disable`),
n'appelle **pas** `setup_engine.disable_setup` — elle appelle
`engine.set_setup_enabled(setup_id, False)`
(`routes_setups.py:295` → `trading_engine.py:2156-2161` →
`repositories.set_setup_enabled`, `repositories.py:506`), qui ne touche que
la colonne `enabled`, jamais `status`. **`SetupEngine.disable_setup`
(`setup_engine.py:290-291`) n'a donc aucun appelant réel dans `app/` —
c'est du code mort.**

**Recherche de tout déclencheur automatique** : `Grep` de
`disarm_setup|disable_setup` sur `app/engine/trading_engine.py` en entier,
sur les boucles de fond (`_start_monitor`, `_heartbeat`, `reconciliation.py`,
`setup_lifecycle_service.py::revalidate_all`) → **aucune occurrence** en
dehors des deux lignes déjà citées (`trading_engine.py:2376`,
`routes_setups.py:194`).

**Réponse directe** : `disarm_setup` est déclenché **uniquement** par une
requête HTTP explicite (`POST /api/setups/{setup_id}/disarm`) — aucun
scheduler, aucune passe de réconciliation, aucune revalidation périodique ne
l'appelle. C'est structurellement une action humaine (ou, en théorie, un
script tiers qui appellerait cette route — mais rien dans `app/` ne le fait
automatiquement).

---

## Q3 — En base : disarm_setup a-t-il déjà été appliqué à un setup en alarme ?

Requêtes `mode=ro` sur `data/trading_state.sqlite` :

```
SELECT ... FROM events WHERE event_type='setup_disarmed'        → 0 ligne
SELECT ... FROM events WHERE event_type='setup_disarm_blocked'  → 0 ligne
SELECT ... FROM setups WHERE last_event LIKE '%disarmed%'       → 0 ligne
```

**Zéro occurrence, dans les trois cas.** `disarm_setup` n'a jamais été
exécuté avec succès dans cette base (aucun événement `setup_disarmed`), et
n'a jamais non plus été bloqué (`setup_disarm_blocked`) — il n'a
tout simplement jamais été appelé, sur aucun setup, alarme ou non.

Un seul setup est actuellement `DISABLED` en base
(`ALAB_20260713_001`), mais son `last_event` vaut `"Setup saved"` — il a été
créé directement désactivé (`enabled=false` à la sauvegarde), pas désarmé
via `disarm_setup`. Ceci confirme, par une seconde voie, l'absence totale
d'usage historique de ce chemin.

**Conclusion Q3** : `disarm_setup` n'a jamais été appelé sur un setup en
`MANUAL_REVIEW_REQUIRED`/`ERROR_REQUIRES_MANUAL_REVIEW` (ni sur aucun autre
statut) — le risque est structurel (Q1/Q2), pas encore matérialisé (comme
pour les alarmes du lot 3b-2 dans l'audit 35 Q4).

---

## Conclusion — HUMAIN ou AUTOMATIQUE ?

**`disarm_setup` est une action HUMAINE** dans son déclenchement (seul point
d'entrée : une requête API explicite, jamais un mécanisme de fond) — mais son
**exécution est aussi non gardée que la branche SUBMITTED** : aucune
vérification du statut de départ, écriture directe hors `state_machine`,
`DISABLED` absente d'`ALLOWED_TRANSITIONS` depuis les deux statuts d'alarme.
Ce n'est donc pas un chemin à bloquer comme la réconciliation automatique (il
ne s'exécute jamais sans une action humaine explicite), mais ce n'est pas non
plus, en l'état, une "sortie légitime" de l'alarme au sens du principe
d'état collant : rien aujourd'hui ne distingue, dans son code, un humain qui
clique "disarm" en connaissance de cause qu'il quitte une alarme, d'un humain
qui clique le même bouton sur un setup dans n'importe quel autre statut —
la légitimité de l'action n'est pas conditionnée à une confirmation ou à un
garde spécifique à `MANUAL_REVIEW_REQUIRED`/`ERROR_REQUIRES_MANUAL_REVIEW`.
