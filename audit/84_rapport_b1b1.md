# Rapport de lot — B-1b-1

## 1. Identification
- Lot / ordre de travail : `audit/ORDRE_B1b1.md` — router RAISE_STOP vers le
  broker via `StopModificationService.modify_stop` (root B, B-1b-1).
- Branche : `fix/b1b1-route-raisestop-to-broker` | Commit : `a3b2a93c640ed5e7555c4d1111fac54e9cae18b1`.
- Basée sur : `feat/setup-conditions`, `eff5a0a` (docs(audit): cloture B-1a —
  fusion ff-only et push verifies).
- Mergée : non | Poussée : non.

```
$ git log -1 --format="%H %s"
a3b2a93c640ed5e7555c4d1111fac54e9cae18b1 feat(signals): route RAISE_STOP to broker via StopModificationService (root B, B-1b-1)
```

## 2. Fichiers touchés
```
$ git diff --stat eff5a0a..a3b2a93
 app/engine/position_action_executor.py |  21 ++++-
 app/engine/trading_engine.py           |  13 ++--
 audit/ORDRE_B1b1.md                    | 105 +++++++++++++++++++++++++
 tests/test_position_action_executor.py | 137 ++++++++++++++++++++++++++++++---
 4 files changed, 255 insertions(+), 21 deletions(-)
```
Confrontation au périmètre autorisé (§2 de l'ordre — `position_action_executor.py`,
`trading_engine.py` + tests) : **conforme**. `audit/ORDRE_B1b1.md` est le
fichier d'ordre lui-même (exigé par la première ligne de l'ordre), pas du
code de production. Aucun fichier interdit touché :
`app/engine/stop_modification_service.py`, `PositionManager.raise_stop`,
les `evaluate()` des setups (`RunnerBaseSetup`, `PositionManagementSetup`),
`routes_positions.py` — tous absents du diff-stat ci-dessus, donc inchangés.

## 3. Diff du code de production
Diff intégral des deux fichiers de `app/` touchés :

```diff
diff --git a/app/engine/position_action_executor.py b/app/engine/position_action_executor.py
index 550a9e0..3c0b951 100644
--- a/app/engine/position_action_executor.py
+++ b/app/engine/position_action_executor.py
@@ -5,6 +5,7 @@ from typing import Any
 
 from app.engine.position_manager import PositionManager
 from app.engine.state_machine import StateMachine
+from app.engine.stop_modification_service import StopModificationService
 from app.models import EventLevel, SetupStatus, SignalAction
 from app.storage.event_store import EventStore
 from app.storage.repositories import TradingRepository
@@ -19,13 +20,15 @@ class PositionActionExecutor:
         event_store: EventStore,
         position_manager: PositionManager,
         state_machine: StateMachine,
+        stop_modification_service: StopModificationService,
     ) -> None:
         self.repository = repository
         self.event_store = event_store
         self.position_manager = position_manager
         self.state_machine = state_machine
+        self.stop_modification_service = stop_modification_service
 
-    def execute_raise_stop_signal(
+    async def execute_raise_stop_signal(
         self,
         setup: dict[str, Any],
         current_status: SetupStatus,
@@ -34,9 +37,19 @@ class PositionActionExecutor:
         if signal.action != SignalAction.RAISE_STOP or signal.new_stop is None:
             return False
 
-        moved = self.move_stop(setup["symbol"], signal.new_stop)
-        if moved and signal.target_status:
-            self.transition_setup(setup, current_status, signal.target_status, signal.reason)
+        result = await self.stop_modification_service.modify_stop(setup["symbol"], signal.new_stop)
+        if result["ok"]:
+            if signal.target_status:
+                self.transition_setup(setup, current_status, signal.target_status, signal.reason)
+        else:
+            self.event_store.record(
+                EventLevel.WARNING,
+                "raise_stop_rejected",
+                result.get("reason", "Stop modification service rejected the raise"),
+                setup_id=setup["setup_id"],
+                symbol=setup["symbol"],
+                data={"reason_code": result.get("reason_code")},
+            )
         return True
 
     def move_stop(
diff --git a/app/engine/trading_engine.py b/app/engine/trading_engine.py
index c558af2..4739c62 100644
--- a/app/engine/trading_engine.py
+++ b/app/engine/trading_engine.py
@@ -206,18 +206,19 @@ class TradingEngine:
             self.event_store,
             self.state_machine,
         )
-        self.position_action_executor = PositionActionExecutor(
+        self.stop_modification_service = StopModificationService(
             repository,
             self.event_store,
+            self.broker,
             self.position_manager,
-            self.state_machine,
+            trade_guards=self.trade_guards,
         )
-        self.stop_modification_service = StopModificationService(
+        self.position_action_executor = PositionActionExecutor(
             repository,
             self.event_store,
-            self.broker,
             self.position_manager,
-            trade_guards=self.trade_guards,
+            self.state_machine,
+            self.stop_modification_service,
         )
         self.entry_order_executor = EntryOrderExecutor(
             repository,
@@ -2468,7 +2469,7 @@ class TradingEngine:
     ) -> None:
         if self.action_executor.execute_simple_action(setup, current_status, signal):
             return
-        if self.position_action_executor.execute_raise_stop_signal(setup, current_status, signal):
+        if await self.position_action_executor.execute_raise_stop_signal(setup, current_status, signal):
             return
         if signal.action == SignalAction.ENTRY_READY and current_status not in ENTRY_ELIGIBLE_STATUSES:
             self.event_store.record(
```

Point d'insertion et forme : exactement ceux prescrits par l'ordre §3 —
`execute_raise_stop_signal` devient `async def`, l'appel local
`self.move_stop(...)` est remplacé par
`await self.stop_modification_service.modify_stop(setup["symbol"], signal.new_stop)`
(mêmes valeurs déjà extraites à l'ancienne ligne :37, aucun nouvel accès
donnée), et `result["ok"]` gouverne la transition. Construction réordonnée
dans `trading_engine.py` : `stop_modification_service` est maintenant bâti
avant `position_action_executor` (au lieu d'après), pour lui être injecté ;
ses dépendances (`self.broker`, `self.trade_guards`, `self.position_manager`)
sont toutes définies plus tôt dans `__init__` (lignes 133, 164, 165), donc
ce réordonnancement ne casse aucune autre dépendance — vérifié avant
d'écrire le diff, pas de cas "ARRÊTE-TOI" rencontré.

## 4. Décisions prises
- **Événement de rejet nommé `raise_stop_rejected`, niveau WARNING** : l'ordre
  §3(a) suggère le nom à titre d'exemple ("ex. raise_stop_rejected") sans
  imposer le niveau. Choix : `EventLevel.WARNING` (cohérent avec
  `entry_gate_blocked`, un autre événement de blocage non-erreur dans le
  même fichier `trading_engine.py`) plutôt que `RISK` ou `ERROR` — un
  RAISE_STOP rejeté par la garde B-2 ou une fenêtre de marché fermée est un
  refus de sécurité attendu, pas une panne.
- **`data={"reason_code": result.get("reason_code")}`** : le service
  (`stop_modification_service.py`) renvoie systématiquement `reason_code`
  sur un rejet (`REASON_STOP_LOWERING_FORBIDDEN`, `REASON_NO_STOP_TARGET`,
  `REASON_BROKER_REJECTED`, ou un code de `trade_guards`) — reporté tel
  quel pour tracer la cause exacte sans dupliquer sa logique de
  classification, conformément à l'invariant §4 "aucun code ajouté" pour
  hériter de B-2.
- **Test central via un spy dédié (`_SpyStopModificationService`)** plutôt
  qu'un mock de bibliothèque : l'ordre §5.1 demande explicitement
  "spy/mock" ; un objet minimal enregistrant `(symbol, new_stop)` et
  renvoyant un résultat scripté rend la preuve de routage lisible sans
  dépendre de `unittest.mock.AsyncMock` (déjà peu utilisé dans ce dépôt,
  vérifié par grep avant d'écrire le test).

## 5. Preuves de sortie

### 5.1 — Test central : routage vers le service, pas vers `move_stop`
Test : `test_raise_stop_signal_routes_to_broker_service_not_local`.
Un spy remplace `stop_modification_service` ; après `execute_raise_stop_signal`,
`spy.calls == [(symbol, 14.25)]` (exactement `setup["symbol"]` et
`signal.new_stop`) et la position locale reste à son stop d'origine
(`13.85`, jamais modifiée par `move_stop`/`PositionManager.raise_stop`
directement).
```
$ python -m pytest tests/test_position_action_executor.py::PositionActionExecutorTests::test_raise_stop_signal_routes_to_broker_service_not_local -q
.
1 passed in ...s
```
**PASS.**

### 5.2 — `ok=True` → transition
Test : `test_result_ok_true_transitions_setup`. Spy renvoie `{"ok": True}`,
le setup transite vers `MANAGING_POSITION`.
**PASS** (inclus dans la commande §7).

### 5.3 — `ok=False` → pas de transition, événement de rejet, signal consommé
Test : `test_result_ok_false_no_transition_but_signal_consumed`. Spy renvoie
`{"ok": False, "reason_code": "STOP_LOWERING_FORBIDDEN", ...}` ; le statut du
setup reste `IN_POSITION`, un événement `raise_stop_rejected` est présent, et
la méthode retourne `True` (signal traité).
**PASS.**

### 5.4 — Garde B-2 héritée sans code ajouté
Test : `test_b2_guard_inherited_via_real_service`. Cette fois le
`stop_modification_service` réel (`StopModificationService`, pas un spy)
est branché sur l'exécuteur ; un RAISE_STOP `new_stop=13.50` sous le stop
courant `13.85` est rejeté par la garde `never_lower_stop` interne à
`modify_stop` — aucune ligne de garde écrite dans ce lot. Pas de
transition, stop position inchangé.
**PASS.**

### 5.5 — Non-régression : signal non-RAISE_STOP toujours ignoré
Test : `test_non_raise_stop_signal_is_not_handled` (équivalent async du
comportement de filtre déjà présent avant ce lot, non couvert explicitement
par un test dédié auparavant). `HOLD` → `execute_raise_stop_signal` retourne
`False`.
**PASS.**

### 5.6 — Adaptations async des tests existants (audit 83 Q2)
Les 3 anciens tests synchrones de `test_position_action_executor.py` ont
été adaptés :
- `test_raise_stop_signal_updates_position_and_transitions_setup` →
  remplacé par 5.1+5.2 (le test original vérifiait la mise à jour LOCALE de
  la position par le même appel qui gère la transition ; ce couplage
  n'existe plus après le reroutage — la mise à jour de position est
  désormais la responsabilité du service, hors périmètre de ce lot — donc
  scindé en un test de routage (spy) et un test de transition).
- `test_lower_stop_signal_is_rejected_without_transition` → remplacé par
  5.3 (rejet scripté) + 5.4 (rejet réel via B-2) ; l'ancien test dépendait
  du rejet LOCAL de `PositionManager.raise_stop` (`allow_lower=False`,
  événement `stop_move_rejected`), qui n'est plus le chemin emprunté par
  RAISE_STOP — le rejet visible côté `execute_raise_stop_signal` est
  désormais celui du service (`raise_stop_rejected`), pas celui de
  `move_stop`.
- `test_manual_move_stop_uses_same_position_manager_rules` → conservé quasi
  inchangé (`move_stop` lui-même n'a pas changé), avec un commentaire
  documentant qu'il exerce désormais du code mort en production (§8).
La classe de test est passée de `unittest.TestCase` à
`unittest.IsolatedAsyncioTestCase` (nécessaire : `execute_raise_stop_signal`
est maintenant une coroutine) ; `setUp`/`tearDown` sont devenus
`asyncSetUp`/`asyncTearDown`. Les tests des `evaluate()` (`test_setup_roles.py`
et autres) sont strictement inchangés — non touchés par ce lot.

### 5.7 — Suite complète
```
$ python -m pytest -q
...
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
1 failed, 778 passed in 271.45s (0:04:31)
```
Conforme à l'attendu de l'ordre §5.6 : "seul test_account_metrics.py en
échec". Vérifié pré-existant et sans rapport avec ce lot : le même test,
rejoué isolément après un `git stash` des changements de ce lot (retour à
`eff5a0a`), échoue de façon identique :
```
$ git stash && python -m pytest tests/test_account_metrics.py -q && git stash pop
FAILED ... AssertionError: None != 2.5
1 failed, 5 passed in 2.13s
```
**PASS** (écart pré-existant confirmé hors périmètre).

## 6. Nettoyage (obligatoire)
- Fichiers temporaires / leurres / scripts jetables créés pendant le lot :
  aucun. Toutes les investigations (lecture de `trading_engine.py`,
  `stop_modification_service.py`, tests de référence) se sont faites par
  lecture de fichiers existants, rien écrit puis supprimé.
- Stash créés/poppés : un stash temporaire pour vérifier la préexistence de
  l'échec `test_account_metrics.py` sur le parent (§5.7) — créé et poppé
  dans la même séquence de commandes ; `git status` après confirme le
  retour à l'état d'avant stash (les 4 fichiers de ce lot toujours
  présents et staged/commités, rien perdu).
- Branches ou worktrees créés/supprimés : branche
  `fix/b1b1-route-raisestop-to-broker` créée depuis `feat/setup-conditions`,
  conservée (pas de suppression, règle générale de ne jamais supprimer sans
  accord explicite).
- Confirmation qu'aucun artefact du lot ne subsiste hors commit :
  ```
  $ git status --short -- app/ tests/ audit/ORDRE_B1b1.md
  (vide après le commit a3b2a93)
  ```
  Les fichiers non liés à ce lot déjà présents avant son démarrage
  (`data/setups/*`, `audit/*.md` d'autres sessions, `.codex/`, `tmp/`) ne
  sont ni touchés ni commités par ce lot.

## 7. Suite de tests
```
$ python -m pytest -q
...
1 failed, 778 passed in 271.45s (0:04:31)
```
Avant ce lot (sur `eff5a0a`, base de la branche), `test_position_action_executor.py`
comptait 3 tests. Après ce lot : 6 tests dans ce même fichier (+3 nets — les
2 anciens tests couplés local/transition ont été scindés en 4 tests
spécifiques (5.1, 5.2, 5.3, 5.5) plus 5.4 nouveau, et l'ancien test manuel
conservé), soit 778 passés au total contre 775 avant B-1a/B-1b-1 cumulés —
cohérent avec les tests ajoutés dans ce lot précis (+3 nets par rapport à
`eff5a0a`) et 1 échec pré-existant confirmé hors périmètre (§5.7).

## 8. Découvert mais NON corrigé
- **`PositionActionExecutor.move_stop` devient code mort en production**
  (annoncé par l'ordre §1/§4) : `execute_raise_stop_signal` était son seul
  appelant de production ; après ce lot, RAISE_STOP passe exclusivement par
  `stop_modification_service.modify_stop`. `move_stop` reste en place,
  non supprimée (interdiction explicite §8 de l'ordre), toujours exercée
  par `test_manual_move_stop_uses_same_position_manager_rules` mais plus
  par aucun chemin d'exécution réel. Dette à traiter dans un lot dédié à la
  suppression de code mort, hors périmètre ici.
- **Réserve TWS (throttle au tick)** : chaque RAISE_STOP consommé appelle
  désormais `modify_stop` du service, qui pour un stop broker actif émet un
  ordre `modify_stop_order` réel vers TWS. Un rythme de trailing très
  rapproché (ex. `RunnerBaseSetup` recalculant un nouveau stop à chaque
  tick de marché) multiplierait les appels broker un-pour-un avec les
  signaux, sans throttle ni debounce dans ce lot ni dans le service
  existant. Risque de limitation de débit TWS (`pacing violation`) sous
  forte fréquence de signal — non observé dans les tests (tous
  synchrones/isolés), signalé pour vigilance en conditions réelles, hors
  périmètre de B-1b-1 (le service lui-même, seul endroit où un throttle
  pourrait s'ajouter, est interdit à modifier ici — c'est B-1b-2).
- **B-1b-1 seul, sans B-1b-2, expose un `broker_updated=False` trompeur pour
  le cas `orderId=None`/broker déconnecté** : la dégradation explicite de ce
  sous-cas (stop manuel TWS avec seulement `broker_perm_id`, ou
  déconnexion) est le périmètre de B-1b-2 (interdit ici, §8 de l'ordre).
  Tant que B-1b-2 n'est pas déployé, un utilisateur peut voir un RAISE_STOP
  "réussir" (`ok: True`) alors que le service n'a pas pu confirmer le stop
  au broker dans ce sous-cas précis (déjà documenté par l'audit 80/rapport
  B-1a §8 comme limite `orderId=0`). **Ce lot et B-1b-2 forment une paire
  et ne doivent pas être déployés séparément en production** : B-1b-1 seul
  fait désormais atteindre le broker par le chemin RAISE_STOP, ce qui rend
  visible — pour la première fois via ce chemin — le mensonge potentiel de
  `broker_updated=False` que B-1b-2 doit corriger.

## 9. Écarts par rapport à l'ordre
Aucun.
