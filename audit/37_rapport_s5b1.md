# Rapport de lot — S5b-1 (statuts d'alarme collants face à la réconciliation automatique)

## 1. Identification
- Lot / ordre de travail : `audit/ORDRE_S5b1.md` (S5b-1)
- Branche : `fix/s5b1-sticky-review-status` | Commit : `ed38de5b74d2cc185eb8686ecfaafb48f4b79260`
- Basée sur : `feat/setup-conditions` @ `3531ffe1b18471119616a189ed7108342e75e214`
- Mergée : non | Poussée : non

## 2. Fichiers touchés

```
$ git diff --stat feat/setup-conditions..HEAD
 app/engine/reconciliation.py | 30 ++++++++++++++--
 audit/ORDRE_S5b1.md          | 85 ++++++++++++++++++++++++++++++++++++++++++++
 tests/test_reconciliation.py | 77 +++++++++++++++++++++++++++++++++++++++
 3 files changed, 189 insertions(+), 3 deletions(-)
```

Confrontation à l'ordre (§2 Périmètre — autorisé : `app/engine/reconciliation.py` +
tests ; interdit : `state_machine.py`, `setup_engine.py`, toute autre logique
de `app/`) : **conforme**. Seul fichier de production touché :
`app/engine/reconciliation.py`. `audit/ORDRE_S5b1.md` est l'ordre lui-même,
recopié mot pour mot comme demandé au §0 de l'ordre. Aucun fichier hors
périmètre modifié.

## 3. Diff du code de production

Diff intégral de `app/engine/reconciliation.py` (28 lignes, sous le seuil de
150) :

```diff
@@ -459,9 +459,23 @@ class ReconciliationEngine:
                 if side == "SELL"
                 else SetupStatus.ENTRY_ORDER_PLACED.value
             )
-            if setup_status in _TERMINAL_SETUP_STATUSES or setup_status in {
-                SetupStatus.MANUAL_REVIEW_REQUIRED.value,
-            }:
+            if setup_status in _REVIEW_LOCKED_SETUP_STATUSES:
+                self.event_store.record(
+                    EventLevel.INFO,
+                    "reconciliation_skipped_review_locked",
+                    f"Setup left in {setup_status} instead of restoring "
+                    f"{target_status} from TWS",
+                    setup_id=setup_id,
+                    symbol=symbol,
+                    data={
+                        "order_id": str(order.get("id") or ""),
+                        "broker_order_id": order.get("broker_order_id"),
+                        "preserved_status": setup_status,
+                        "target_status": target_status,
+                    },
+                )
+                return
+            if setup_status in _TERMINAL_SETUP_STATUSES:
                 self.repository.update_setup_status(
                     setup_id,
                     target_status,
@@ -623,6 +637,16 @@ _TERMINAL_SETUP_STATUSES = {
     SetupStatus.ERROR.value,
     SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
 }
+# Statuses meaning "a human must look at this setup". The SUBMITTED branch of
+# _update_setup_after_reconciled_order must never overwrite these with an
+# order-restore status (S5b-1, audits 35/36): _TERMINAL_SETUP_STATUSES itself
+# is left untouched because it is also read by the position-adoption loop
+# (reconciliation.py, ~line 162) and by the CANCELLED branch (~line 538/552),
+# both out of scope for this fix.
+_REVIEW_LOCKED_SETUP_STATUSES = {
+    SetupStatus.MANUAL_REVIEW_REQUIRED.value,
+    SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
+}
```

## 4. Décisions prises

Un seul point non tranché à l'avance par l'ordre : le choix imposé au §3(a)
entre "modifier `_TERMINAL_SETUP_STATUSES`" et "construire un ensemble local
dans la branche SUBMITTED", décidé par grep AVANT toute édition, comme exigé.

```
$ grep -n "_TERMINAL_SETUP_STATUSES" app/engine/reconciliation.py   (avant édition)
162:            if str(setup.get("status") or "") in _TERMINAL_SETUP_STATUSES:
462:            if setup_status in _TERMINAL_SETUP_STATUSES or setup_status in {
538:        if setup_status in _TERMINAL_SETUP_STATUSES:
618:_TERMINAL_SETUP_STATUSES = {
```

Résultat : 4 occurrences, dont 2 hors de la branche SUBMITTED — ligne 162
(boucle d'adoption de position, dans `run()`) et ligne 538 (branche
CANCELLED, explicitement hors périmètre par l'ordre §2 : "Les branches
FILLED et CANCELLED de la même fonction : NE PAS les toucher"). Le grep
n'était donc pas ambigu : `_TERMINAL_SETUP_STATUSES` sert ailleurs, il ne
fallait pas le modifier. **Choix retenu : ensemble local**
(`_REVIEW_LOCKED_SETUP_STATUSES`, nouvelle constante module, définie à côté
de `_TERMINAL_SETUP_STATUSES` pour rester lisible), exactement la branche
prévue par l'ordre pour ce cas. `_TERMINAL_SETUP_STATUSES` reste inchangé
(vérifié après édition : lignes 162 et 552 — décalée de 538 à 552 par
l'insertion — toujours intactes, cf. §5.3 ci-dessous).

Aucune autre décision non spécifiée n'a été nécessaire : la structure de
l'événement (§3(b)) reprend le patron déjà utilisé par les autres branches
du fichier (`EventLevel`, `event_type`, `message`, `setup_id`, `symbol`,
`data`), sans nouveau champ ni nouveau système de log.

## 5. Preuves de sortie

### 5.1 — MANUAL_REVIEW_REQUIRED + ordre SELL SUBMITTED → statut préservé, event émis

```
$ python -m pytest tests/test_reconciliation.py::SubmittedBranchReviewLockTests::test_manual_review_required_survives_sell_order_reported_submitted -v
tests\test_reconciliation.py::SubmittedBranchReviewLockTests::test_manual_review_required_survives_sell_order_reported_submitted PASSED
```
Assertions : `_setup_status() == "MANUAL_REVIEW_REQUIRED"` et
`"reconciliation_skipped_review_locked" in _event_types()`. **PASS.**

### 5.2 — ERROR_REQUIRES_MANUAL_REVIEW + ordre BUY SUBMITTED → statut préservé

```
$ python -m pytest tests/test_reconciliation.py::SubmittedBranchReviewLockTests::test_error_requires_manual_review_survives_buy_order_reported_submitted -v
tests\test_reconciliation.py::SubmittedBranchReviewLockTests::test_error_requires_manual_review_survives_buy_order_reported_submitted PASSED
```
Assertion : `_setup_status() == "ERROR_REQUIRES_MANUAL_REVIEW"`. **PASS.**

### 5.3 — Non-régression : CLOSED (terminal non-alarme) + ordre SUBMITTED → comportement inchangé

```
$ python -m pytest tests/test_reconciliation.py::SubmittedBranchReviewLockTests::test_non_alarm_terminal_status_still_restored_from_tws -v
tests\test_reconciliation.py::SubmittedBranchReviewLockTests::test_non_alarm_terminal_status_still_restored_from_tws PASSED
```
Assertions : statut final `STOP_ORDER_PLACED`, `last_event ==
"Open order restored from TWS"` (message inchangé), et l'événement
`reconciliation_skipped_review_locked` n'est PAS émis. **PASS.**

Confirmation complémentaire que `_TERMINAL_SETUP_STATUSES` n'a pas bougé
(§4) :
```
$ grep -n "_TERMINAL_SETUP_STATUSES\|_REVIEW_LOCKED_SETUP_STATUSES" app/engine/reconciliation.py   (après édition)
162:            if str(setup.get("status") or "") in _TERMINAL_SETUP_STATUSES:
462:            if setup_status in _REVIEW_LOCKED_SETUP_STATUSES:
478:            if setup_status in _TERMINAL_SETUP_STATUSES:
552:        if setup_status in _TERMINAL_SETUP_STATUSES:
632:_TERMINAL_SETUP_STATUSES = {
646:_REVIEW_LOCKED_SETUP_STATUSES = {
```
Lignes 162 et 552 (boucle d'adoption et branche CANCELLED) : identiques à
avant, décalées seulement par l'insertion de code en amont dans le fichier.

### 5.4 — Preuve négative (test 1 muté puis reverté)

Assertion mutée (`STOP_ORDER_PLACED` au lieu de `MANUAL_REVIEW_REQUIRED`
attendu) :
```
$ python -m pytest tests/test_reconciliation.py::SubmittedBranchReviewLockTests::test_manual_review_required_survives_sell_order_reported_submitted -q
F
>       self.assertEqual(self._setup_status(), SetupStatus.STOP_ORDER_PLACED.value)
E       AssertionError: 'MANUAL_REVIEW_REQUIRED' != 'STOP_ORDER_PLACED'
E       - MANUAL_REVIEW_REQUIRED
E       + STOP_ORDER_PLACED
1 failed in 0.88s
```
**Échec confirmé** — le test détecte bien une régression, il n'est pas
vacueusement vert. Assertion revertée immédiatement après (édition inverse
strictement symétrique) :
```
$ sed -n '494,505p' tests/test_reconciliation.py
    def test_manual_review_required_survives_sell_order_reported_submitted(self) -> None:
        self.repository.update_setup_status(
            self.setup_id, SetupStatus.MANUAL_REVIEW_REQUIRED.value, "test setup"
        )

        self.reconciliation._update_setup_after_reconciled_order(
            _order(setup_id=self.setup_id, symbol=self.symbol, side="SELL"),
            OrderStatus.SUBMITTED.value,
        )

        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)
        self.assertIn("reconciliation_skipped_review_locked", self._event_types())
```
Diff nul confirmé : le contenu ci-dessus est identique caractère pour
caractère à l'état avant mutation (seule ligne éditée deux fois, dans le
même sens inverse). **PASS.**

### 5.5 — Suite complète

```
$ python -m pytest -q
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
1 failed, 712 passed, 4 warnings, 134 subtests passed in 235.75s (0:03:55)
```
Seul `test_account_metrics.py` en échec, échec pré-existant et sans rapport
avec la réconciliation (déjà documenté identique par l'audit 26, avant tout
travail de ce lot). **PASS**, conforme au point 5.5 de l'ordre.

## 6. Nettoyage (obligatoire)

- Aucun fichier temporaire, leurre ou script jetable créé en dehors des
  répertoires temporaires système utilisés par les tests eux-mêmes
  (`tempfile.TemporaryDirectory()`, nettoyés par leur propre `tearDown`).
- Aucun stash créé.
- Une branche créée : `fix/s5b1-sticky-review-status` (demandée par l'ordre
  §6, non supprimée — c'est la branche de livraison de ce lot).
- `git status --short` après commit :
```
 D data/setups/CODI_20260628_001.json
 D data/setups/TXN_20260630_001.json
?? .codex/
?? audit/28_pre_s2.md
?? audit/31_cloture_s3.md
?? audit/34_cloture_s4.md
?? data/setups/*.json (setups du jour, sans rapport avec ce lot)
?? tmp/
```
Aucun artefact du lot lui-même ne reste non commité : `app/engine/reconciliation.py`,
`tests/test_reconciliation.py` et `audit/ORDRE_S5b1.md` sont dans le commit
`ed38de5`. Les entrées `??`/`D` ci-dessus préexistaient avant ce lot
(setups du jour, dossiers `.codex/`/`tmp/`) — aucune n'a été créée ni
touchée par ce travail.

## 7. Suite de tests

```
$ python -m pytest -q
1 failed, 712 passed, 4 warnings, 134 subtests passed in 235.75s (0:03:55)
```
Avant ce lot (état `feat/setup-conditions`, audit 26) : `1 failed, 704
passed, 4 warnings, 98 subtests passed`. Après : `1 failed, 712 passed, ...,
134 subtests passed`. Delta : **+8 tests** passés au total sur le dépôt
entre les deux mesures — dont les **+3 tests** ajoutés par ce lot dans
`test_reconciliation.py` (20 → 23, `grep -cE "^    (async )?def test_"`,
confirmé avant/après). Les +5 tests restants proviennent des lots
intermédiaires (audits 27-34, S1 à S4) déjà mergés dans
`feat/setup-conditions` avant le début de ce lot, hors périmètre de cette
mesure delta.

## 8. Découvert mais NON corrigé

- Le second chemin d'effacement identifié par l'audit 35/36
  (`setup_engine.py:277`, `disarm_setup`) reste **entièrement non corrigé**
  par ce lot — explicitement hors périmètre (§2 de l'ordre : "INTERDIT :
  ... `setup_engine.py` (disarm_setup traité séparément)"). Signalé, pas
  touché.
- `SetupEngine.disable_setup` (`setup_engine.py:290-291`) reste du code mort
  sans appelant en production (constaté par l'audit 36, revérifié
  inchangé) — hors périmètre, non touché.

## 9. Écarts par rapport à l'ordre

Aucun.
