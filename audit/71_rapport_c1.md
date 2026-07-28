# Rapport de lot — C-1

## 1. Identification
- Lot / ordre de travail : C-1 — réparer les setups figés au redémarrage
  (S58.3 + fenêtre A-1) (`audit/ORDRE_C1.md`)
- Branche : fix/c1-repair-frozen-setups | Commit : effb6c6a31bc85f603d4ea050f78086253a01af8
- Basée sur : feat/setup-conditions + 3caa91100c323352c202eeb491406f31c7dca382
- Mergée : non | Poussée : non

## 2. Fichiers touchés
```
$ git diff --stat feat/setup-conditions..HEAD
 app/engine/reconciliation.py |  79 ++++++++++++++++++++
 audit/ORDRE_C1.md            | 106 ++++++++++++++++++++++++++
 tests/test_reconciliation.py | 173 +++++++++++++++++++++++++++++++++++++++++++
 3 files changed, 358 insertions(+)
```
Conforme au périmètre autorisé (§2 de l'ordre) : seul
`app/engine/reconciliation.py` est touché côté production, plus tests.
Aucune touche à `post_fill_progression.py`, `position_manager.py`,
`repositories.py`, `state_machine.py`/`ALLOWED_TRANSITIONS` ou
`order_manager.py`. Aucune branche d'alarme d'A-3
(`_detect_unprotected_entry_orphans`) modifiée — une seule ligne y a été
ajoutée juste après son appel dans `run()`, pour brancher la nouvelle
méthode sœur. `audit/ORDRE_C1.md` ajouté conformément à la consigne d'en-tête.

## 3. Diff du code de production
```diff
diff --git a/app/engine/reconciliation.py b/app/engine/reconciliation.py
index 4d055ad..96e5995 100644
--- a/app/engine/reconciliation.py
+++ b/app/engine/reconciliation.py
@@ -154,6 +154,7 @@ class ReconciliationEngine:
             )
         if startup:
             self._detect_unprotected_entry_orphans(local_setups)
+            self._repair_frozen_setups(local_setups)
         positions_by_symbol = {
             position.symbol.upper(): position
             for position in broker_positions
@@ -358,6 +359,76 @@ class ReconciliationEngine:
                     data=data,
                 )
 
+    def _repair_frozen_setups(self, local_setups: list[dict[str, Any]]) -> None:
+        # Root C (audits 69/70): two non-atomic crash windows leave a setup
+        # frozen after a restart even though the broker/local state is
+        # otherwise consistent. Both branches here write a normal progression
+        # status (never MANUAL_REVIEW_REQUIRED) and are idempotent by
+        # construction: the entry branch only fires while status is still
+        # ENTRY_FILLED, and the exit branch's target (CLOSED) is terminal and
+        # filtered out on the next startup.
+        for setup in local_setups:
+            setup_id = str(setup.get("setup_id") or "")
+            if not setup_id:
+                continue
+            current_setup = self.repository.get_setup(setup_id)
+            if current_setup:
+                setup = current_setup
+            status = str(setup.get("status") or "")
+            if status in _TERMINAL_SETUP_STATUSES:
+                continue
+            symbol = str(setup.get("symbol") or "").upper()
+
+            if status == SetupStatus.ENTRY_FILLED.value:
+                # S58.3: crash after the stop was placed at the broker but
+                # before the setup progressed past ENTRY_FILLED. A-3's
+                # orphan scan does not cover this signature because a stop
+                # IS active -- protection_status is POSITION_OPEN_STOP_ACTIVE,
+                # not one of the unprotected-orphan statuses.
+                snapshot = self.repository.protection_snapshot_for_setup(setup_id)
+                if str(snapshot.get("protection_status") or "") != "POSITION_OPEN_STOP_ACTIVE":
+                    continue
+                protection_verified = self.progression.has_active_protection(setup_id)
+                self.progression.mark_in_position(setup_id, protection_verified=protection_verified)
+                if protection_verified:
+                    self.event_store.record(
+                        EventLevel.SYNC,
+                        "startup_frozen_entry_repaired",
+                        "Filled entry with active protective stop reconciled to IN_POSITION at startup",
+                        setup_id=setup_id,
+                        symbol=symbol,
+                        data={"previous_status": status},
+                    )
+                continue
+
+            if status in _FROZEN_EXIT_SETUP_STATUSES:
+                # Root A / fenêtre A-1: the sell fill soldered the local
+                # position to quantity 0 but the crash landed before
+                # _handle_sell_fill wrote the setup to CLOSED. positions is
+                # indexed by symbol, not setup_id, so the same-setup check is
+                # mandatory: a different setup may since have reused the
+                # symbol (audit 70 Q1).
+                position = self.repository.get_position(symbol)
+                if position is None:
+                    continue
+                if str(position.get("setup_id") or "") != setup_id:
+                    continue
+                if int(position["quantity"]) != 0:
+                    continue
+                self.repository.update_setup_status(
+                    setup_id,
+                    SetupStatus.CLOSED.value,
+                    "Position closed reconciled at startup",
+                )
+                self.event_store.record(
+                    EventLevel.SYNC,
+                    "startup_frozen_exit_repaired",
+                    "Position closed reconciled at startup",
+                    setup_id=setup_id,
+                    symbol=symbol,
+                    data={"previous_status": status},
+                )
+
     def _save_broker_reality_report(
         self,
         *,
@@ -873,6 +944,14 @@ _UNPROTECTED_ENTRY_ORPHAN_STATUSES = {
     "ENTRY_ORDER_PENDING_WITHOUT_STOP_BLOCKED",
     "POSITION_OPEN_STOP_MISSING_CRITICAL",
 }
+# root C / fenêtre A-1 (audits 69/70): setup statuses that can be left
+# stranded when a fully-sold position's local quantity reaches 0 but the
+# crash lands before _handle_sell_fill writes CLOSED.
+_FROZEN_EXIT_SETUP_STATUSES = {
+    SetupStatus.IN_POSITION.value,
+    SetupStatus.MANAGING_POSITION.value,
+    SetupStatus.PARTIAL_EXIT.value,
+}
 
 
 async def _broker_order_statuses(broker: BrokerConnector) -> dict[str, str]:
```
1 ligne ajoutée au branchement de `run()`, 70 lignes pour la nouvelle
méthode privée `_repair_frozen_setups`, 8 lignes pour la nouvelle constante
`_FROZEN_EXIT_SETUP_STATUSES`. Aucune ligne existante modifiée hors ce point
d'insertion unique.

## 4. Décisions prises
- **Une seule méthode sœur pour C-1 et C-1b** plutôt que deux méthodes
  séparées : les deux branches partagent le même garde d'entrée (statut
  terminal, rafraîchissement du setup) et sont mutuellement exclusives par
  construction (`ENTRY_FILLED` vs `{IN_POSITION, MANAGING_POSITION,
  PARTIAL_EXIT}` ne se recoupent jamais), donc un seul passage sur
  `local_setups` suffit — cohérent avec le style de
  `_detect_unprotected_entry_orphans` (une boucle, plusieurs branches).
- **Garde d'entrée C-1 posée sur `status == ENTRY_FILLED`** plutôt que sur le
  seul `protection_status == POSITION_OPEN_STOP_ACTIVE` : ce dernier peut en
  théorie rester vrai pour un setup déjà `IN_POSITION` ou
  `MANAGING_POSITION` (stop toujours actif, position toujours ouverte).
  Sans le garde de statut, un second passage rappellerait
  `mark_in_position` sur un setup ayant déjà progressé plus loin, ce qui ne
  changerait rien pour `IN_POSITION` mais **régresserait silencieusement**
  un setup `MANAGING_POSITION`/`PARTIAL_EXIT` vers `IN_POSITION` —
  hors périmètre et contraire à l'invariant §4 (« aucune régression »). Le
  garde `status == ENTRY_FILLED` a le même effet que « n'émettre l'événement
  que si le statut a réellement changé » demandé au §3, et le produit
  naturellement : dès que le setup n'est plus `ENTRY_FILLED` (parce que déjà
  réparé), la branche entière est sautée au démarrage suivant → silence
  garanti sans code de comparaison supplémentaire.
- **`has_active_protection` rappelé plutôt que court-circuité** : bien que
  dans cette branche `protection_status == POSITION_OPEN_STOP_ACTIVE`
  implique déjà `has_active_stop_order = True` (même calcul dans
  `_protection_snapshot`), l'ordre §3/§4 demande explicitement de passer le
  résultat réel de `has_active_protection`, jamais un littéral. Fait tel
  quel, à l'identique du patron `entry_filled` existant
  (`reconciliation.py:618-621`).
- **Niveau d'événement `SYNC`** pour les deux nouveaux types
  (`startup_frozen_entry_repaired`, `startup_frozen_exit_repaired`) : ce
  sont des réparations de progression normale, pas des alarmes — cohérent
  avec `existing_position_adopted` et `position_closed_on_sell`, tous deux
  `SYNC`, par opposition aux événements `CRITICAL`/`RISK` d'A-3.
Aucun doute résiduel n'a nécessité d'arrêt.

## 5. Preuves de sortie

### Point 1 — C-1 : entrée figée → IN_POSITION, événement de réparation
```
$ python -m pytest tests/test_reconciliation.py -k test_c1_frozen_entry_reaches_in_position -v
tests/test_reconciliation.py::FrozenSetupRepairTests::test_c1_frozen_entry_reaches_in_position PASSED [100%]
1 passed in 0.52s
```
PASS. (Setup `ENTRY_FILLED` + ordre d'entrée `FILLED` + stop actif + position
ouverte même `setup_id` → `run(startup=True)` → `IN_POSITION` +
`startup_frozen_entry_repaired`.)

### Point 2 — C-1 idempotence : 2e run, aucun nouvel événement
```
$ python -m pytest tests/test_reconciliation.py -k test_c1_idempotent_second_startup_run_does_not_re_emit -v
tests/test_reconciliation.py::FrozenSetupRepairTests::test_c1_idempotent_second_startup_run_does_not_re_emit PASSED [100%]
1 passed in 0.50s
```
PASS. 1er passage : 1 événement. 2e passage : toujours `IN_POSITION`, compte
d'événement inchangé (le setup n'est plus `ENTRY_FILLED`, la branche est
sautée entièrement).

### Point 3 — C-1 exclusion mutuelle avec l'alarme A-3
```
$ python -m pytest tests/test_reconciliation.py -k test_c1_excludes_entry_without_active_stop -v
tests/test_reconciliation.py::FrozenSetupRepairTests::test_c1_excludes_entry_without_active_stop PASSED [100%]
1 passed in 0.51s
```
PASS. (Setup `ENTRY_FILLED` + position ouverte SANS stop actif →
`protection_status = POSITION_OPEN_STOP_MISSING_CRITICAL` → c'est la branche
d'alarme A-3 (`_detect_unprotected_entry_orphans`) qui agit :
`MANUAL_REVIEW_REQUIRED` + `startup_filled_entry_without_stop` ;
`startup_frozen_entry_repaired` absent.)

### Point 4/5/6/7 — C-1b : clôture, faux positif évité, idempotence, garde jamais-entré
```
$ python -m pytest tests/test_reconciliation.py -k "test_c1b_frozen_full_exit_reaches_closed or test_c1b_does_not_close_on_other_setups_symbol_reuse or test_c1b_idempotent_second_startup_run_does_not_re_emit or test_c1b_never_entered_guard_no_position_at_all" -v
tests/test_reconciliation.py::FrozenSetupRepairTests::test_c1b_does_not_close_on_other_setups_symbol_reuse PASSED [ 25%]
tests/test_reconciliation.py::FrozenSetupRepairTests::test_c1b_frozen_full_exit_reaches_closed PASSED [ 50%]
tests/test_reconciliation.py::FrozenSetupRepairTests::test_c1b_idempotent_second_startup_run_does_not_re_emit PASSED [ 75%]
tests/test_reconciliation.py::FrozenSetupRepairTests::test_c1b_never_entered_guard_no_position_at_all PASSED [100%]
4 passed in 1.72s
```
PASS sur les 4 :
- `IN_POSITION` + position même `setup_id` + `quantity=0` → `CLOSED` +
  `startup_frozen_exit_repaired`.
- Setup A `IN_POSITION` + ligne `positions` du même symbole mais
  `setup_id="OTHER_SETUP_001"` + `quantity=0` → A reste `IN_POSITION`,
  aucun événement de réparation (le test critique de l'audit 70).
- Après clôture, 2e `run(startup=True)` → `CLOSED` exclu par
  `_TERMINAL_SETUP_STATUSES`, un seul événement au total.
- `IN_POSITION` sans aucune ligne `positions` → pas de clôture (garde
  prouvée, cas qui ne peut pas arriver en pratique).

### Point 8 — Non-régression : branches d'alarme A-3 et `StartupOrphanDetectionTests`
```
$ python -m pytest tests/test_reconciliation.py -v
[...]
tests/test_reconciliation.py::StartupOrphanDetectionTests::test_filled_entry_without_stop_is_critical PASSED
tests/test_reconciliation.py::StartupOrphanDetectionTests::test_idempotent_second_startup_run_does_not_re_alert PASSED
tests/test_reconciliation.py::StartupOrphanDetectionTests::test_no_alert_when_stop_is_active PASSED
tests/test_reconciliation.py::StartupOrphanDetectionTests::test_pending_entry_without_stop_raises_lesser_severity PASSED
tests/test_reconciliation.py::StartupOrphanDetectionTests::test_periodic_cycle_does_not_detect_orphan PASSED
[...]
43 passed in 15.65s
```
43/43 passés (36 préexistants + 7 nouveaux), aucune assertion existante
modifiée. PASS.

### Point 9 — Suite complète : seul `test_account_metrics.py` en échec
```
$ python -m pytest -q
[...]
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
1 failed, 762 passed, 4 warnings, 134 subtests passed in 279.71s (0:04:39)
```
Seul `test_account_metrics.py` en échec (pré-existant, hors périmètre,
inchangé depuis les lots précédents — voir audit 67). PASS.

## 6. Nettoyage (obligatoire)
- Aucun fichier temporaire, leurre ou script jetable créé pendant ce lot.
- Aucun stash créé/poppé.
- Une seule branche créée : `fix/c1-repair-frozen-setups` (depuis
  `feat/setup-conditions`), conservée conformément à l'ordre — pas de
  suppression.
```
$ git status --short -- app/ tests/ audit/ORDRE_C1.md
(sortie vide)
```
Aucun artefact propre à C-1 ne reste hors du commit. Les éléments non
indexés visibles par ailleurs (`data/setups/*`, `audit/*_pre_*.md`, `.codex/`,
`tmp/`, autres `audit/ORDRE_*preaudit.md`) sont pré-existants à cette
session, hors périmètre de ce lot.

## 7. Suite de tests
```
$ python -m pytest -q
[...]
1 failed, 762 passed, 4 warnings, 134 subtests passed in 279.71s (0:04:39)
```
Avant ce lot (référence audit 67, A-3) : 755 passed, 1 failed (pré-existant).
Après : 762 passed (+7 tests ajoutés : `FrozenSetupRepairTests`, un par point
de preuve du §5 de l'ordre), 1 failed inchangé (même échec pré-existant,
`test_account_metrics.py`, non lié à ce lot).

## 8. Découvert mais NON corrigé
La sortie PARTIELLE figée (setup `PARTIAL_EXIT` ou `MANAGING_POSITION` avec
une vente partielle interrompue par un crash avant l'écriture du nouveau
statut) reste hors périmètre : aucun critère local sûr ne permet de la
détecter sans risque de faux positif (contrairement à C-1b où
`quantity == 0` est un signal univoque). Documenté ici comme dette, non
traité — conforme à l'interdiction explicite du §8 de l'ordre.

## 9. Écarts par rapport à l'ordre
Aucun.
