# Rapport de lot — A-1b

## 1. Identification
- Lot / ordre de travail : A-1b — protéger les alarmes contre l'écrasement par une clôture (audit/ORDRE_A1b.md)
- Branche : fix/a1b-closed-sticky-alarm | Commit : c7da0ab924a1308d288be9375cab297540a1998e
- Basée sur : fix/a1-close-on-sell + 54e51bddbc13b5c67d158b5ae138531e430dbb77
- Mergée : non | Poussée : non

## 2. Fichiers touchés
```
$ git diff --stat fix/a1-close-on-sell..HEAD
 app/storage/repositories.py             |  4 +++
 audit/ORDRE_A1b.md                      | 63 +++++++++++++++++++++++++++++++++
 tests/test_active_status_write_sites.py |  5 +++
 tests/test_reconciliation.py            | 34 ++++++++++++++++++
 tests/test_review_status_sticky.py      | 45 +++++++++++++++++++++++
 5 files changed, 151 insertions(+)
```
Conforme au périmètre autorisé (§2 de l'ordre) : `app/storage/repositories.py`
(uniquement `_ACTIVE_STATUSES`) + tests. `reconciliation.py`,
`app/setups/`, `order_manager.py` non touchés. `audit/ORDRE_A1b.md` ajouté
conformément à la consigne d'en-tête ("Écris d'abord audit/ORDRE_A1b.md").

## 3. Diff du code de production
```diff
diff --git a/app/storage/repositories.py b/app/storage/repositories.py
index dda8437..d9e66e6 100644
--- a/app/storage/repositories.py
+++ b/app/storage/repositories.py
@@ -35,6 +35,10 @@ _ACTIVE_STATUSES = frozenset(
         SetupStatus.MANAGING_POSITION.value,
         SetupStatus.PARTIAL_EXIT.value,
         SetupStatus.RECONCILING_EXISTING_POSITION.value,
+        # CLOSED is a terminal status but must not overwrite a review alarm:
+        # a closed position does not cancel the need for human review
+        # ("l'alarme prime", audit 61 P3).
+        SetupStatus.CLOSED.value,
     }
 )
```
Un seul membre ajouté à l'ensemble. La garde elle-même
(`update_setup_status`, ligne 489) n'a pas été touchée.

## 4. Décisions prises
- Vérification préalable (grep) : `_ACTIVE_STATUSES` n'est référencé qu'à
  `repositories.py:489`, à l'intérieur de la garde S5b-3a. Aucun autre
  usage dans le code de production. Aucune ambiguïté rencontrée, donc pas
  d'arrêt nécessaire.
- Le ratchet `tests/test_active_status_write_sites.py` maintient son propre
  ensemble `ACTIVE_STATUSES` (miroir textuel de `_ACTIVE_STATUSES`, en
  chaînes plutôt qu'en valeurs d'enum, utilisé pour détecter tout nouveau
  site écrivant littéralement un statut ACTIF). Ajout de `"CLOSED"` à ce
  miroir pour rester cohérent avec le nouvel ensemble de la garde. Vérifié
  au préalable qu'aucun appel `update_setup_status(...)` existant n'écrit
  `CLOSED` en littéral (le seul site, `reconciliation.py:748`, passe par la
  variable `new_status` — c'est le "blind spot structurel" déjà documenté
  dans ce fichier de test) : l'ajout ne fait donc apparaître aucun site
  inattendu, le ratchet reste vert sans autre changement.
- Tests ajoutés : deux tests directs sur la garde (CentralReviewGuardDirectTests,
  miroir des tests IN_POSITION existants) pour MANUAL_REVIEW_REQUIRED et
  ERROR_REQUIRES_MANUAL_REVIEW → CLOSED bloqué ; un test de non-régression
  (CLOSED depuis un statut non-alarme passe) ; un test bout-en-bout dans
  `SellFilledBranchTests` (`test_total_sell_on_alarmed_setup_closes_position_but_keeps_alarm`)
  prouvant que le fill SELL ferme bien la position au broker
  (PositionManager tourne sans condition) alors que l'écriture CLOSED est
  bloquée et le setup reste en alarme.

## 5. Preuves de sortie

### Point 1 — MANUAL_REVIEW_REQUIRED, écriture CLOSED bloquée
```
$ python -m pytest tests/test_review_status_sticky.py -k "closed" -v
tests/test_review_status_sticky.py::CentralReviewGuardDirectTests::test_guard_blocks_error_requires_manual_review_to_closed_and_logs PASSED [ 33%]
tests/test_review_status_sticky.py::CentralReviewGuardDirectTests::test_guard_blocks_manual_review_required_to_closed_and_logs PASSED [ 66%]
tests/test_review_status_sticky.py::CentralReviewGuardDirectTests::test_non_alarm_closed_write_is_unaffected PASSED [100%]
3 passed, 26 deselected in 1.49s
```
PASS.

### Point 2 — ENTRY_ORDER_PLACED (non alarme), écriture CLOSED passe
Couvert par `test_non_alarm_closed_write_is_unaffected` ci-dessus (même
commande). PASS.

### Point 3 — bout-en-bout : fill SELL total, alarme vs non-alarme
```
$ python -m pytest tests/test_reconciliation.py -k "sell" -v
tests/test_reconciliation.py::FilledBranchTests::test_sell_filled_without_resolvable_fill_goes_to_manual_review PASSED [ 10%]
tests/test_reconciliation.py::SellFilledBranchTests::test_broker_position_mismatch_triggers_manual_review PASSED [ 20%]
tests/test_reconciliation.py::SellFilledBranchTests::test_partial_sell_sets_partial_exit_and_keeps_entry_cost PASSED [ 30%]
tests/test_reconciliation.py::SellFilledBranchTests::test_realized_pnl_uses_real_sell_price_not_generic_quote PASSED [ 40%]
tests/test_reconciliation.py::SellFilledBranchTests::test_sell_resolved_but_no_local_position_triggers_manual_review PASSED [ 50%]
tests/test_reconciliation.py::SellFilledBranchTests::test_total_sell_closes_position PASSED [ 60%]
tests/test_reconciliation.py::SellFilledBranchTests::test_total_sell_on_alarmed_setup_closes_position_but_keeps_alarm PASSED [ 70%]
tests/test_reconciliation.py::SellFilledBranchTests::test_unresolved_executions_trigger_manual_review PASSED [ 80%]
tests/test_reconciliation.py::SubmittedBranchReviewLockTests::test_manual_review_required_survives_sell_order_reported_submitted PASSED [ 90%]
tests/test_reconciliation.py::SubmittedBranchReviewLockTests::test_terminal_status_with_open_sell_order_goes_to_manual_review PASSED [100%]
10 passed, 21 deselected in 4.03s
```
`test_total_sell_closes_position` (setup non alarmé) → CLOSED (A-1 nominal
inchangé). `test_total_sell_on_alarmed_setup_closes_position_but_keeps_alarm`
(setup MANUAL_REVIEW_REQUIRED) → position fermée au broker (quantity == 0,
événement `position_closed_on_sell` émis), setup reste
MANUAL_REVIEW_REQUIRED. PASS.

### Point 4 — cliquet test_active_status_write_sites.py
```
$ python -m pytest tests/test_active_status_write_sites.py -v
tests/test_active_status_write_sites.py::ActiveStatusWriteSiteRatchetTests::test_only_known_sites_write_an_active_status PASSED [100%]
1 passed in 2.57s
```
Mis à jour (CLOSED ajouté à son ensemble miroir), passe sans détecter de
site inattendu. PASS.

### Point 5 — suite complète
```
$ python -m pytest tests/ -q
[...]
================================== FAILURES ===================================
_ AccountMetricsTests.test_snapshot_uses_broker_positions_when_local_positions_are_empty _
[...]
E       AssertionError: None != 2.5
tests\test_account_metrics.py:182: AssertionError
[...]
=========================== short test summary info ===========================
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
1 failed, 750 passed, 4 warnings, 134 subtests passed in 349.75s (0:05:49)
```
Seul `test_account_metrics.py` en échec, comme prescrit. PASS.

## 6. Nettoyage (obligatoire)
- Aucun fichier temporaire, leurre ou script jetable créé pendant ce lot.
- Aucun stash créé/poppé.
- Une seule branche créée : `fix/a1b-closed-sticky-alarm` (empilée sur
  `fix/a1-close-on-sell`, conservée conformément à l'ordre — pas de
  suppression).
- `git status --short` après commit : seuls les artefacts pré-existants du
  répertoire de travail (audits/documents non liés à ce lot, suppressions
  `data/setups/*` antérieures à cette session) subsistent en non indexé ;
  aucun artefact propre à A-1b ne reste hors du commit.

## 7. Suite de tests
```
$ python -m pytest tests/ -q
[...]
1 failed, 750 passed, 4 warnings, 134 subtests passed in 349.75s (0:05:49)
```
Avant ce lot (référence audit 63, A-1) : 746 passed, 1 failed
(test_account_metrics, pré-existant). Après : 750 passed (+4 tests ajoutés :
2 gardes CLOSED, 1 non-régression CLOSED, 1 bout-en-bout alarme), 1 failed
inchangé (même échec pré-existant, non lié à ce lot).

## 8. Découvert mais NON corrigé
Aucun.

## 9. Écarts par rapport à l'ordre
Aucun.
