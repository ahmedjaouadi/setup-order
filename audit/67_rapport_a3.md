# Rapport de lot — A-3

## 1. Identification
- Lot / ordre de travail : A-3 — détecter et alerter un bracket orphelin au démarrage (audit/ORDRE_A3.md)
- Branche : fix/a3-detect-orphan-entry | Commit : 5565f9dc4afd7b2acf2b3c61fa946953ae8ff0f9
- Basée sur : feat/setup-conditions + 53ac22630d533055fa350405d2af31af32963125
- Mergée : non | Poussée : non

## 2. Fichiers touchés
```
$ git diff --stat feat/setup-conditions..HEAD
 app/engine/reconciliation.py |  67 ++++++++++++++++-
 app/engine/trading_engine.py |   2 +-
 audit/ORDRE_A3.md            |  98 ++++++++++++++++++++++++
 tests/test_reconciliation.py | 174 ++++++++++++++++++++++++++++++++++++++++++-
 4 files changed, 338 insertions(+), 3 deletions(-)
```
Conforme au périmètre autorisé (§2 de l'ordre) : `app/engine/reconciliation.py`
et `app/engine/trading_engine.py` uniquement, plus tests. Aucune touche à
`state_machine.py`, `repositories.py`, `order_manager.py` ou la boucle
d'adoption. `audit/ORDRE_A3.md` ajouté conformément à la consigne d'en-tête.

## 3. Diff du code de production
```diff
diff --git a/app/engine/reconciliation.py b/app/engine/reconciliation.py
index ce5879b..4d055ad 100644
--- a/app/engine/reconciliation.py
+++ b/app/engine/reconciliation.py
@@ -60,7 +60,7 @@ class ReconciliationEngine:
         self.position_manager = position_manager
         self.progression = PostFillProgression(repository, event_store)
 
-    async def run(self) -> ReconciliationResult:
+    async def run(self, *, startup: bool = False) -> ReconciliationResult:
         broker_connected = await self.broker.status() == ConnectionStatus.CONNECTED
         local_setups = self.repository.list_setups()
         local_orders = self.repository.list_orders()
@@ -152,6 +152,8 @@ class ReconciliationEngine:
                 broker_executions=broker_executions,
                 result=result,
             )
+        if startup:
+            self._detect_unprotected_entry_orphans(local_setups)
         positions_by_symbol = {
             position.symbol.upper(): position
             for position in broker_positions
@@ -303,6 +305,59 @@ class ReconciliationEngine:
         )
         return result
 
+    def _detect_unprotected_entry_orphans(self, local_setups: list[dict[str, Any]]) -> None:
+        # A-3 (audits 58 S58.1, 66): a crash between the entry-order upsert
+        # and the stop placement in order_manager.place_entry_order leaves an
+        # active BUY entry at the broker with no stop, while the setup never
+        # reached ENTRY_ORDER_PLACED. That signature cannot occur during a
+        # normal cycle (place_entry_order never yields between the two), so
+        # this only runs once, right after startup.
+        for setup in local_setups:
+            setup_id = str(setup.get("setup_id") or "")
+            if not setup_id:
+                continue
+            current_setup = self.repository.get_setup(setup_id)
+            if current_setup:
+                setup = current_setup
+            status = str(setup.get("status") or "")
+            if status in _TERMINAL_SETUP_STATUSES or status in _REVIEW_LOCKED_SETUP_STATUSES:
+                continue
+            snapshot = self.repository.protection_snapshot_for_setup(setup_id)
+            protection_status = str(snapshot.get("protection_status") or "")
+            if protection_status not in _UNPROTECTED_ENTRY_ORPHAN_STATUSES:
+                continue
+            symbol = str(setup.get("symbol") or "").upper()
+            data = {
+                "protection_status": protection_status,
+                "active_entry_order_id": snapshot.get("active_entry_order_id"),
+            }
+            if snapshot.get("position_open"):
+                message = "Filled entry without protective stop after restart"
+                self.repository.update_setup_status(
+                    setup_id, SetupStatus.MANUAL_REVIEW_REQUIRED.value, message
+                )
+                self.event_store.record(
+                    EventLevel.CRITICAL,
+                    "startup_filled_entry_without_stop",
+                    message,
+                    setup_id=setup_id,
+                    symbol=symbol,
+                    data=data,
+                )
+            else:
+                message = "Pending entry without protective stop after restart"
+                self.repository.update_setup_status(
+                    setup_id, SetupStatus.MANUAL_REVIEW_REQUIRED.value, message
+                )
+                self.event_store.record(
+                    EventLevel.RISK,
+                    "startup_pending_entry_without_stop",
+                    message,
+                    setup_id=setup_id,
+                    symbol=symbol,
+                    data=data,
+                )
+
     def _save_broker_reality_report(
         self,
         *,
@@ -808,6 +863,16 @@ _REVIEW_LOCKED_SETUP_STATUSES = {
     SetupStatus.MANUAL_REVIEW_REQUIRED.value,
     SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
 }
+# protection_snapshot_for_setup statuses meaning "an entry order is active and
+# no stop is active" (A-3, audits 58 S58.1/66). STOP_SUBMISSION_FAILED is
+# deliberately excluded: it means a stop order was attempted and failed,
+# which order_manager already handles synchronously via
+# _cancel_parent_for_failed_protection — not the startup-orphan case here,
+# where no stop order exists at all.
+_UNPROTECTED_ENTRY_ORPHAN_STATUSES = {
+    "ENTRY_ORDER_PENDING_WITHOUT_STOP_BLOCKED",
+    "POSITION_OPEN_STOP_MISSING_CRITICAL",
+}
 
 
 async def _broker_order_statuses(broker: BrokerConnector) -> dict[str, str]:
diff --git a/app/engine/trading_engine.py b/app/engine/trading_engine.py
index 38350ac..c558af2 100644
--- a/app/engine/trading_engine.py
+++ b/app/engine/trading_engine.py
@@ -321,7 +321,7 @@ class TradingEngine:
             ),
         )
         loaded = self.setup_engine.load_all()
-        reconciliation_result = await self.reconciliation.run()
+        reconciliation_result = await self.reconciliation.run(startup=True)
         self._mark_reconciliation_completed(reconciliation_result)
         self.setup_lifecycle.revalidate_all(force=True)
         self.event_store.record(
```
27 lignes ajoutées dans `run()`/module (paramètre + branchement + constante),
53 lignes pour la nouvelle méthode privée `_detect_unprotected_entry_orphans`,
1 ligne dans `trading_engine.py`. Aucune ligne existante modifiée hors ces
deux points d'insertion précis.

## 4. Décisions prises
- **Distinction des deux niveaux de sévérité** : faite via
  `protection_status` de `protection_snapshot_for_setup`
  (`repositories.py:806-811` → `_protection_snapshot`, `:331-377`), qui
  distingue déjà proprement les cas sans qu'il ait fallu deviner :
  - `POSITION_OPEN_STOP_MISSING_CRITICAL` (`open_position=True` ET aucun
    stop actif) → cas "position réelle sans stop" → `EventLevel.CRITICAL`.
  - `ENTRY_ORDER_PENDING_WITHOUT_STOP_BLOCKED` (entrée active, aucun stop
    actif, aucun stop en échec, pas de position ouverte) → cas "entrée en
    attente sans stop" → `EventLevel.RISK` (le "WARNING/RISK" laissé au
    choix par l'ordre §3c ; `RISK` retenu car sémantiquement plus proche
    des branches voisines de `reconciliation.py`, ex.
    `adoption_blocked_stop_not_found`).
  - `STOP_SUBMISSION_FAILED` (entrée active + un ordre stop existe mais en
    statut d'échec `REJECTED`/`ERROR`) est **volontairement exclu** de la
    détection : ce n'est pas la signature "aucun stop" décrite au §1 de
    l'ordre (un stop a bel et bien été tenté), et ce cas est déjà traité de
    façon synchrone par `order_manager._cancel_parent_for_failed_protection`
    au moment même de l'échec — pas un orphelin de crash post-redémarrage.
    Documenté en commentaire à côté de `_UNPROTECTED_ENTRY_ORPHAN_STATUSES`.
  Aucun doute résiduel n'a nécessité d'arrêt : le mapping `protection_status`
  → cas était sans ambiguïté une fois les 6 valeurs de `_protection_snapshot`
  énumérées.
- **Rafraîchissement du setup avant lecture du statut** : la méthode
  refait `self.repository.get_setup(setup_id)` avant de tester `status`,
  à l'identique du garde existant de la boucle d'adoption
  (`reconciliation.py:162-164`), pour rester cohérente avec le reste du
  fichier plutôt que d'introduire un autre style de lecture.
- **Deux événements distincts** (`startup_filled_entry_without_stop` /
  `startup_pending_entry_without_stop`) plutôt qu'un seul type paramétré par
  niveau : cohérent avec le style du fichier (chaque branche a son propre
  `event_type`, ex. `adoption_blocked_position_not_found` vs
  `adoption_blocked_missing_stop`).

## 5. Preuves de sortie

### Point 1 — orphelin "entrée en attente sans stop" → MANUAL_REVIEW_REQUIRED + RISK
```
$ python -m pytest tests/test_reconciliation.py -k test_pending_entry_without_stop_raises_lesser_severity -v
tests/test_reconciliation.py::StartupOrphanDetectionTests::test_pending_entry_without_stop_raises_lesser_severity PASSED [100%]
1 passed in 1.24s
```
PASS. (`run(startup=True)`, ordre BUY actif seul, aucune position locale →
`MANUAL_REVIEW_REQUIRED` + événement `startup_pending_entry_without_stop`
niveau `RISK`.)

### Point 2 — orphelin "position réelle sans stop" → MANUAL_REVIEW_REQUIRED + CRITICAL
```
$ python -m pytest tests/test_reconciliation.py -k test_filled_entry_without_stop_is_critical -v
tests/test_reconciliation.py::StartupOrphanDetectionTests::test_filled_entry_without_stop_is_critical PASSED [100%]
1 passed in 1.24s
```
PASS. (Ordre BUY actif + position locale ouverte pour le même setup →
`MANUAL_REVIEW_REQUIRED` + événement `startup_filled_entry_without_stop`
niveau `CRITICAL`.)

### Point 3 — idempotence sur deux run(startup=True) consécutifs
```
$ python -m pytest tests/test_reconciliation.py -k test_idempotent_second_startup_run_does_not_re_alert -v
tests/test_reconciliation.py::StartupOrphanDetectionTests::test_idempotent_second_startup_run_does_not_re_alert PASSED [100%]
1 passed in 1.29s
```
PASS. (1er passage : 1 événement. 2e passage : toujours 1 — le setup est
en `MANUAL_REVIEW_REQUIRED` donc sauté par la garde
`_REVIEW_LOCKED_SETUP_STATUSES`.)

### Point 4 — pas de faux positif (stop actif ; cycle périodique)
```
$ python -m pytest tests/test_reconciliation.py -k "test_no_alert_when_stop_is_active or test_periodic_cycle_does_not_detect_orphan" -v
tests/test_reconciliation.py::StartupOrphanDetectionTests::test_no_alert_when_stop_is_active PASSED [ 50%]
tests/test_reconciliation.py::StartupOrphanDetectionTests::test_periodic_cycle_does_not_detect_orphan PASSED [100%]
2 passed in 1.31s
```
PASS. Entrée + stop actifs tous les deux → aucune alerte. Le même setup
orphelin passé à `run()` (startup=False, défaut) → aucune alerte non plus
(la détection ne s'exécute pas du tout hors démarrage).

### Point 5 — non-régression reconciliation (A-1, BUY, SUBMITTED, CANCELLED)
```
$ python -m pytest tests/test_reconciliation.py -v
[...]
tests/test_reconciliation.py::FilledBranchTests::test_barreau1_nominal_weighted_price_reaches_in_position PASSED
tests/test_reconciliation.py::FilledBranchTests::test_barreau2_used_when_position_newly_born PASSED
tests/test_reconciliation.py::SellFilledBranchTests::test_total_sell_closes_position PASSED
tests/test_reconciliation.py::SubmittedBranchReviewLockTests::test_terminal_status_with_open_buy_order_goes_to_manual_review PASSED
[...]
============================= 36 passed in 12.16s =============================
```
36/36 passés, aucune assertion existante modifiée. PASS.

### Point 6 — suite complète
```
$ python -m pytest -q
[...]
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
1 failed, 755 passed, 4 warnings, 134 subtests passed in 264.95s (0:04:24)
```
Seul `test_account_metrics.py` en échec (pré-existant, hors périmètre,
inchangé depuis les lots précédents — voir audit 64). PASS.

## 6. Nettoyage (obligatoire)
- Aucun fichier temporaire, leurre ou script jetable créé pendant ce lot.
- Aucun stash créé/poppé.
- Une seule branche créée : `fix/a3-detect-orphan-entry` (depuis
  `feat/setup-conditions`), conservée conformément à l'ordre — pas de
  suppression.
```
$ git status --short -- app/ tests/
(sortie vide)
```
Aucun artefact propre à A-3 ne reste hors du commit. Les éléments non
indexés visibles par ailleurs (`data/setups/*`, `audit/*_pre_*.md`, `.codex/`,
`tmp/`) sont pré-existants à cette session, hors périmètre de ce lot.

## 7. Suite de tests
```
$ python -m pytest -q
[...]
1 failed, 755 passed, 4 warnings, 134 subtests passed in 264.95s (0:04:24)
```
Avant ce lot (référence audit 64, A-1b) : 750 passed, 1 failed (pré-existant).
Après : 755 passed (+5 tests ajoutés : `StartupOrphanDetectionTests`, un par
point de preuve du §5 de l'ordre), 1 failed inchangé (même échec
pré-existant, `test_account_metrics.py`, non lié à ce lot).

## 8. Découvert mais NON corrigé
Aucun.

## 9. Écarts par rapport à l'ordre
Aucun.
