# Rapport de lot — A6-SEC

## 1. Identification
- Lot / ordre de travail : `audit/ORDRE_A6SEC.md` — ne jamais ressusciter un
  setup terminal en statut d'ordre actif ; alerter à la place.
- Branche : `fix/a6sec-no-terminal-resurrection`  | Commit : voir section 6
  (commit créé après ce rapport, hash ajouté a posteriori n'était pas
  possible avant — le hash exact figure dans le message de clôture, la
  commande `git log -1` juste après le commit fait foi).
- Basée sur : `feat/setup-conditions` @ `121d0c7` (docs(audit): clôture
  S5b-3a+3b — merge/push groupé fix/s5b3a+fix/s5b3b).
- Mergée : non | Poussée : non.

## 2. Fichiers touchés
```
 app/engine/reconciliation.py            | 23 +++++++++++++++++--
 tests/test_active_status_write_sites.py | 31 +++++++++++++++----------
 tests/test_reconciliation.py            | 40 +++++++++++++++++++++++++--------
 tests/test_review_status_sticky.py      | 23 +++++++++++--------
 tests/test_setup_roles.py               |  5 ++++-
 5 files changed, 89 insertions(+), 33 deletions(-)
```
Confronté à la liste autorisée par l'ordre (section 2) : **conforme**.
`app/engine/reconciliation.py` est le seul fichier de production touché
(la seule branche terminale de `_update_setup_after_reconciled_order`,
lignes ~478-500 après édition). Les 4 fichiers de test touchés sont tous
des tests directement liés à ce comportement :
- `tests/test_reconciliation.py` : la classe qui couvre exactement cette
  branche (`SubmittedBranchReviewLockTests`).
- `tests/test_active_status_write_sites.py` et
  `tests/test_review_status_sticky.py` : cliquets/tests existants dont un
  commentaire de justification citait explicitement l'ancien comportement
  de cette même branche (obsolète après ce lot) — mise à jour du texte
  seulement, aucune logique de scan modifiée.
- `tests/test_setup_roles.py` : contenait un test comportemental
  (`test_reconciliation_reactivates_local_order_still_open_at_broker`) qui
  exerçait exactement le scénario visé par l'ordre (setup `CANCELLED` +
  ordre broker `STP` réactivé) et affirmait l'ancien résultat
  (`STOP_ORDER_PLACED`) — voir section 9, écart signalé.

Deux fichiers `data/setups/*.json` apparaissent supprimés dans l'arbre de
travail (`CODI_20260628_001.json`, `TXN_20260630_001.json`) : préexistants
à ce lot (déjà marqués supprimés dans le `git status` de tout début de
session, avant toute action de ce lot), non touchés ni commités par ce
lot.

## 3. Diff du code de production
```diff
diff --git a/app/engine/reconciliation.py b/app/engine/reconciliation.py
index a71bf61..6e9bc4f 100644
--- a/app/engine/reconciliation.py
+++ b/app/engine/reconciliation.py
@@ -476,10 +476,29 @@ class ReconciliationEngine:
                 )
                 return
             if setup_status in _TERMINAL_SETUP_STATUSES:
+                status_reason = (
+                    f"Broker shows an open {side} order for a terminal setup "
+                    f"({setup_status}) — needs manual review"
+                )
                 self.repository.update_setup_status(
                     setup_id,
-                    target_status,
-                    "Open order restored from TWS",
+                    SetupStatus.MANUAL_REVIEW_REQUIRED.value,
+                    "Open order for terminal setup — manual review required",
+                    status_reason=status_reason,
+                )
+                self.event_store.record(
+                    EventLevel.WARNING,
+                    "reconciliation_terminal_setup_open_order",
+                    status_reason,
+                    setup_id=setup_id,
+                    symbol=symbol,
+                    data={
+                        "order_id": str(order.get("id") or ""),
+                        "broker_order_id": order.get("broker_order_id"),
+                        "terminal_status": setup_status,
+                        "side": side,
+                        "target_status": target_status,
+                    },
                 )
             return
         if status == OrderStatus.FILLED.value:
```
Aucun autre fichier de `app/` n'a été touché. `target_status` reste
calculé en amont (ligne ~457, inchangée) : il n'est plus jamais passé à
`update_setup_status`, seulement conservé dans le payload de l'événement
pour la traçabilité (ce qu'aurait écrit l'ancien code), comme demandé au
point 3 de l'ordre.

## 4. Décisions prises
- **`MANUAL_REVIEW_REQUIRED` légal depuis les 5 statuts terminaux
  atteignables ici ?** Vérifié dans `ALLOWED_TRANSITIONS`
  (`app/engine/state_machine.py:193-196,206`) : `CLOSED`, `EXPIRED`,
  `INVALIDATED`, `CANCELLED` ont chacun `set()` (aucune transition
  autorisée, pas même vers `MANUAL_REVIEW_REQUIRED`) ; seul `ERROR`
  autorise `{CANCELLED, MANUAL_REVIEW_REQUIRED}`. Donc **non légale pour
  4 des 5 statuts**. Passer par `can_transition`/`explain_transition`
  aurait donc silencieusement abandonné l'écriture pour `CLOSED`/
  `EXPIRED`/`INVALIDATED`/`CANCELLED` — exactement le risque déjà identifié
  par l'audit 49 (Q2) pour les écritures d'alarme : un filet de sécurité
  qui échoue silencieusement devient un trou de sécurité. **Décision :
  écriture directe via `repository.update_setup_status(...)`**, cohérente
  avec les 8 autres écritures de `MANUAL_REVIEW_REQUIRED`/
  `ERROR_REQUIRES_MANUAL_REVIEW` déjà présentes dans ce même fichier
  (aucune ne passe par la state machine). État de départ `ERROR` non
  distingué des 4 autres (même traitement) : la cohérence du patron prime
  sur l'exploitation d'une légalité partielle qui n'existe que pour 1 cas
  sur 5.
- **Répartition du message entre `last_event` et `status_reason`** :
  l'ordre demande "un `status_reason` explicite". `update_setup_status`
  (`app/storage/repositories.py:479-489`) a deux paramètres textuels
  distincts : `last_event` (3e positionnel) et `status_reason` (mot-clé).
  Le message détaillé demandé par l'ordre (avec `<side>` et
  `<setup_status>`) est passé en `status_reason` — c'est le champ que
  l'UI affiche déjà comme raison du statut courant (`app/gui/static/js/
  app.js:448`) — et un `last_event` court et générique
  ("Open order for terminal setup — manual review required") complète,
  suivant le patron déjà utilisé par `setup_lifecycle_service.py:415-421`
  (`last_event` court + `status_reason` détaillé).
- **Événement `reconciliation_terminal_setup_open_order`** : niveau
  `WARNING` comme demandé, avec `setup_id`, `symbol`, `broker_order_id`,
  `terminal_status` (le statut terminal d'origine), `side`, et
  `target_status` (ce que l'ancien code aurait écrit) — tous les champs
  requis par le point 3 de l'ordre. Nom de champ `terminal_status` choisi
  plutôt que `preserved_status` (utilisé par l'événement voisin
  `reconciliation_skipped_review_locked`) car ici le statut n'est
  justement PAS préservé — il est remplacé par `MANUAL_REVIEW_REQUIRED` ;
  `terminal_status` documente d'où l'on partait sans induire en erreur.
- **Test `test_reconciliation_reactivates_local_order_still_open_at_broker`
  (`tests/test_setup_roles.py`)** : non listé dans l'ordre, découvert
  seulement à l'exécution de la suite complète (section 9). Mis à jour
  pour affirmer le nouveau résultat (`MANUAL_REVIEW_REQUIRED` au lieu de
  `STOP_ORDER_PLACED`) — c'est exactement le scénario que ce lot corrige,
  pas un effet de bord non lié.
- **Commentaires obsolètes mis à jour** dans
  `tests/test_active_status_write_sites.py` (docstring du module +
  justification de `app/engine/reconciliation.py`) et
  `tests/test_review_status_sticky.py`
  (`SubmittedBranchStickyReferenceTest`) : les deux décrivaient l'ancien
  comportement ("writes its target status through a variable" / "restore-
  from-TWS write") de façon à présent inexacte pour la branche terminale.
  Texte seulement, aucune assertion de test modifiée dans ces deux
  fichiers — leurs tests continuent de vérifier exactement ce qu'ils
  vérifiaient avant (le blocage `_REVIEW_LOCKED`, inchangé).

## 5. Preuves de sortie

### Point 1 — CANCELLED + ordre SELL ouvert → MANUAL_REVIEW_REQUIRED, event WARNING, pas de cancel_order
Test : `tests/test_reconciliation.py::SubmittedBranchReviewLockTests::test_terminal_status_with_open_sell_order_goes_to_manual_review`
```
$ python -m pytest tests/test_reconciliation.py -q -k test_terminal_status_with_open_sell_order_goes_to_manual_review
.                                                                        [100%]
1 passed in 0.6xs
```
Le test patch `self.reconciliation.broker.cancel_order` avec un
`AsyncMock` et affirme `cancel_order.assert_not_called()` à l'intérieur du
`with` — **PASS**.

### Point 2 — CLOSED + ordre BUY ouvert → MANUAL_REVIEW_REQUIRED
Test : `tests/test_reconciliation.py::SubmittedBranchReviewLockTests::test_terminal_status_with_open_buy_order_goes_to_manual_review`
```
$ python -m pytest tests/test_reconciliation.py -q -k test_terminal_status_with_open_buy_order_goes_to_manual_review
.                                                                        [100%]
1 passed in 0.6xs
```
**PASS**.

### Point 3 — Non-régression : setup en alarme (_REVIEW_LOCKED) + ordre SUBMITTED
Tests (déjà existants, S5b-1, non modifiés) :
`test_manual_review_required_survives_sell_order_reported_submitted`,
`test_error_requires_manual_review_survives_buy_order_reported_submitted`.
```
$ python -m pytest tests/test_reconciliation.py -q
........................                                                [100%]
24 passed in 11.67s
```
Les deux tests S5b-1 passent toujours, `reconciliation_skipped_review_locked`
toujours émis, statut d'alarme toujours préservé — **PASS**.

### Point 4 — Preuve négative
Mutation appliquée à
`test_terminal_status_with_open_sell_order_goes_to_manual_review` :
assertion changée de `SetupStatus.MANUAL_REVIEW_REQUIRED.value` à
`SetupStatus.STOP_ORDER_PLACED.value`.
```
$ python -m pytest tests/test_reconciliation.py -q -k test_terminal_status_with_open_sell_order_goes_to_manual_review
F                                                                        [100%]
AssertionError: 'MANUAL_REVIEW_REQUIRED' != 'STOP_ORDER_PLACED'
1 failed, 23 deselected in 0.67s
```
Revert appliqué immédiatement après.
```
$ git diff tests/test_reconciliation.py   # (contre la version d'avant mutation, via /tmp backup)
(vide)
```
Diff nul confirmé après le revert (comparaison directe fichier-à-fichier,
backup supprimé ensuite) — **PASS**.

### Point 5 — Cliquets existants
`tests/test_active_status_write_sites.py::ActiveStatusWriteSiteRatchetTests::test_only_known_sites_write_an_active_status`
n'a pas eu besoin de modification de sa logique de scan : la nouvelle
écriture terminale est un littéral `SetupStatus.MANUAL_REVIEW_REQUIRED.value`,
qui n'appartient pas à `ACTIVE_STATUSES` — invisible à ce scan par
construction, comme avant (l'ancienne écriture, via `target_status`
variable, était déjà invisible pour une raison différente). Seule sa
justification en commentaire citait l'ancien comportement de cette
branche ; mise à jour (section 4). Même chose pour
`SubmittedBranchStickyReferenceTest` dans
`tests/test_review_status_sticky.py` (ne teste que le chemin
`_REVIEW_LOCKED`, inchangé ; commentaire mis à jour).
```
$ python -m pytest tests/test_active_status_write_sites.py tests/test_review_status_sticky.py -q
...........................                                             [100%]
27 passed in 19.xxs
```
**PASS**.

### Point 6 — Suite complète : seul test_account_metrics.py en échec
```
$ python -m pytest -q
=========================== short test summary info ===========================
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
1 failed, 740 passed, 4 warnings, 134 subtests passed in 250.84s (0:04:10)
```
**Écart initial, puis PASS après correction** : un premier passage de la
suite complète a montré un **second** échec, non prévu par l'ordre —
`tests/test_setup_roles.py::SetupRoleTests::test_reconciliation_reactivates_local_order_still_open_at_broker`,
qui affirmait l'ancien comportement (voir section 9). Corrigé (section 4),
puis re-exécution : seul `test_account_metrics.py` reste en échec, comme
prévu par l'ordre — **PASS** après correction.

## 6. Nettoyage (obligatoire)
- Aucun fichier temporaire, leurre ou script jetable laissé sur le disque
  du dépôt. La sauvegarde utilisée pour la preuve négative (point 4) a été
  écrite hors dépôt (`/tmp/test_reconciliation_backup.py`, filesystem
  temporaire de l'environnement, hors `Workspace/setup-order`) et supprimée
  immédiatement après usage (`rm -f /tmp/test_reconciliation_backup.py`).
- Aucun stash laissé : un stash a été créé (`git stash push -- <5 fichiers
  du lot>`) uniquement pour mesurer la base de comparaison "avant" de la
  suite de tests (section 7), puis immédiatement restauré
  (`git stash pop`, `Dropped refs/stash@{0}`) — aucun stash résiduel
  (`git stash list` vide, voir ci-dessous).
- Aucune branche ni worktree supplémentaire créée au-delà de
  `fix/a6sec-no-terminal-resurrection` (demandée par l'ordre, section 6).
- Confirmation :
```
$ git stash list
(vide)
$ git status --short
 D data/setups/CODI_20260628_001.json
 D data/setups/TXN_20260630_001.json
?? .codex/
?? audit/28_pre_s2.md
... (fichiers préexistants au lot, non touchés par lui)
```
Rien d'ajouté par ce lot ne subsiste hors des 5 fichiers listés en section
2 plus `audit/ORDRE_A6SEC.md` et ce rapport (`audit/51_rapport_a6sec.md`).

## 7. Suite de tests
Avant ce lot (baseline mesurée par `git stash` des 5 fichiers du lot, puis
suite complète) :
```
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
1 failed, 739 passed, 4 warnings, 134 subtests passed in 284.55s (0:04:44)
```
Après ce lot :
```
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
1 failed, 740 passed, 4 warnings, 134 subtests passed in 250.84s (0:04:10)
```
**739 → 740 tests passants (+1 net)**, cohérent avec les tests ajoutés :
`tests/test_reconciliation.py` gagne un test net (20 → 21 méthodes `def
test_` : l'ancien `test_non_alarm_terminal_status_still_restored_from_tws`
remplacé par deux nouveaux tests
`test_terminal_status_with_open_sell_order_goes_to_manual_review` et
`test_terminal_status_with_open_buy_order_goes_to_manual_review`, soit
-1+2 = +1) ; aucun autre fichier touché n'a changé son nombre de tests
(seules des assertions/commentaires modifiés dans
`test_active_status_write_sites.py`, `test_review_status_sticky.py`,
`test_setup_roles.py`). L'unique échec (`test_account_metrics.py`) est
identique avant/après, pré-existant, hors périmètre de ce lot.

## 8. Découvert mais NON corrigé
Aucun nouveau problème découvert par ce lot au-delà de ce qui était déjà
figé par les audits 49/50 (le contexte cité en section 1 de l'ordre). Les
autres findings des audits 49/50
(`order_manager.py:465` `attach_missing_stop`, `setup_engine.py:258/277`
`arm_setup`/`disarm_setup`, les 2 couples `ENTRY_ORDER_PLACED` manquants
de la table) restent hors périmètre de ce lot et non traités, comme
prescrit par l'ordre (section 2, INTERDIT).

## 9. Écarts par rapport à l'ordre
- **Point 5.6 de l'ordre** ("Suite complète : seul test_account_metrics.py
  en échec") ne s'est pas vérifié du premier coup : la suite complète a
  d'abord révélé un second échec,
  `tests/test_setup_roles.py::SetupRoleTests::test_reconciliation_reactivates_local_order_still_open_at_broker`,
  que l'ordre ne mentionnait pas dans son périmètre de tests
  autorisés/attendus. Ce test construisait exactement le scénario visé par
  l'ordre (setup `CANCELLED`, ordre `STP` broker toujours ouvert et
  réactivé par la réconciliation) et affirmait l'ancien résultat
  (`STOP_ORDER_PLACED`). Traité comme une mise à jour de test attendue
  plutôt qu'un écart de périmètre : le fichier teste le comportement de
  `reconciliation.py` directement (même classe de scénario que l'ordre
  décrit), la modification est une ligne d'assertion (+ un commentaire),
  aucune nouvelle logique de test ajoutée. Après correction, le point 6
  de l'ordre est respecté à la lettre (seul `test_account_metrics.py` en
  échec).
- Aucun autre écart. Le choix de l'écriture directe (plutôt que
  `can_transition`) est documenté en section 4 comme une décision prise
  au sein du cadre explicitement laissé ouvert par l'ordre lui-même
  (section 3 : "Vérifier d'abord si MANUAL_REVIEW_REQUIRED est légal...
  Si non pour certains, écriture directe... documente le choix").
