# Rapport de lot — B-1a

## 1. Identification
- Lot / ordre de travail : `audit/ORDRE_B1a.md` — persister le stop broker
  adopté en ligne `orders` locale (root B, B-1a prérequis).
- Branche : `fix/b1a-persist-adopted-stop` | Commit : `aeca6cfe09d94cb6e123ab1cf46babb6b5597c13`.
- Basée sur : `feat/setup-conditions`, `a2521f6` (docs(audit): cloture B-2 —
  fusion ff-only et push verifies).
- Mergée : non | Poussée : non.

```
$ git log -1 --format="%H %s"
aeca6cfe09d94cb6e123ab1cf46babb6b5597c13 fix(reconciliation): persist adopted broker stop as local order (root B, B-1a prereq)
```

## 2. Fichiers touchés
```
$ git diff --stat a2521f6..aeca6cf
 app/engine/reconciliation.py              |  66 +++++++-
 audit/ORDRE_B1a.md                        | 100 +++++++++++
 tests/test_reconciliation_adopted_stop.py | 268 ++++++++++++++++++++++++++++++
 3 files changed, 433 insertions(+), 1 deletion(-)
```
Confrontation au périmètre autorisé (§2 de l'ordre — `app/engine/reconciliation.py`
+ tests) : **conforme**. `audit/ORDRE_B1a.md` est le fichier d'ordre lui-même
(exigé §"Écris d'abord..."), pas du code de production. Aucun fichier interdit
touché : `StopModificationService`, `order_manager.py`, `repositories.py`,
`_matching_stop_order` — tous inchangés, vérifié par le diff-stat ci-dessus
(ils n'apparaissent pas).

## 3. Diff du code de production
Diff intégral de `app/engine/reconciliation.py` (seul fichier de `app/`
touché) :

```diff
diff --git a/app/engine/reconciliation.py b/app/engine/reconciliation.py
index d66c071..5206056 100644
--- a/app/engine/reconciliation.py
+++ b/app/engine/reconciliation.py
@@ -9,10 +9,18 @@ from app.broker.tws_connector import BrokerConnector
 from app.engine.broker_reality import REPORT_STATE_KEY, build_broker_reality_report
 from app.engine.position_manager import PositionManager
 from app.engine.post_fill_progression import PostFillProgression
-from app.models import ConnectionStatus, EventLevel, OrderStatus, PositionRecord, SetupStatus
+from app.models import (
+    ConnectionStatus,
+    EventLevel,
+    OrderRecord,
+    OrderStatus,
+    PositionRecord,
+    SetupStatus,
+)
 from app.setups.setup_roles import setup_is_management_only, setup_role_from_config
 from app.storage.event_store import EventStore
 from app.storage.repositories import TradingRepository
+from app.utils.id_generator import new_id
 
 logger = logging.getLogger(__name__)
 
@@ -264,6 +272,8 @@ class ReconciliationEngine:
                 and str(setup.get("status") or "") == SetupStatus.MANUAL_REVIEW_REQUIRED.value
                 and str(setup.get("last_event") or "") == ADOPTION_STOP_NOT_FOUND_MESSAGE
             )
+            if stop_order is not None:
+                self._persist_adopted_stop_order(stop_order, setup, symbol)
             current_stop = stop_order.stop_price if stop_order else protective_stop
             risk_remaining = max(
                 broker_position.current_price - float(current_stop),
@@ -351,6 +361,60 @@ class ReconciliationEngine:
         )
         return result
 
+    def _persist_adopted_stop_order(
+        self,
+        stop_order: BrokerOrderRequest,
+        setup: dict[str, Any],
+        symbol: str,
+    ) -> None:
+        # B-1a (audit 80): the adoption loop above sees the broker's
+        # protective stop (stop_order) but, before this, never wrote it to
+        # the local orders table -- StopModificationService (root B, B-1b)
+        # can only find a broker_order_id to modify via
+        # active_stop_order_for_symbol, which reads that table. Idempotent:
+        # this loop reruns every reconciliation cycle for any non-terminal
+        # adopted setup, so reuse the existing row (upsert on its id) rather
+        # than inserting a new one each pass. Anti-theft: a row already
+        # owned by a DIFFERENT setup_id for this symbol is never reassigned
+        # -- that would silently steal order ownership between setups
+        # (audit 80 Q4) -- a fresh row is created instead and flagged.
+        setup_id = setup["setup_id"]
+        existing = self.repository.active_stop_order_for_symbol(symbol)
+        if existing is not None and str(existing.get("setup_id") or "") == str(setup_id):
+            order_id = existing["id"]
+        elif existing is not None:
+            order_id = new_id("adp")
+            self.event_store.record(
+                EventLevel.RISK,
+                "adoption_stop_order_owner_conflict",
+                "Active stop order for symbol belongs to a different setup",
+                setup_id=setup_id,
+                symbol=symbol,
+                data={
+                    "existing_setup_id": existing.get("setup_id"),
+                    "existing_order_id": existing.get("id"),
+                },
+            )
+        else:
+            order_id = new_id("adp")
+        status = _normalize_order_status(stop_order.status) or OrderStatus.SUBMITTED.value
+        self.repository.upsert_order(
+            OrderRecord(
+                id=order_id,
+                setup_id=setup_id,
+                symbol=symbol,
+                side="SELL",
+                order_type=stop_order.order_type,
+                quantity=stop_order.quantity,
+                status=status,
+                stop_price=stop_order.stop_price,
+                broker_order_id=stop_order.broker_order_id,
+                broker_perm_id=stop_order.broker_perm_id,
+                parent_id=None,
+                oca_group=stop_order.oca_group,
+            )
+        )
+
     def _detect_unprotected_entry_orphans(self, local_setups: list[dict[str, Any]]) -> None:
         # A-3 (audits 58 S58.1, 66): a crash between the entry-order upsert
         # and the stop placement in order_manager.place_entry_order leaves an
```

Point d'insertion : exactement celui prescrit par l'ordre §3 et l'audit 80 —
entre `stop_order = _matching_stop_order(...)` (`:236` avant ce lot) et
`upsert_position` (`:272` avant ce lot), gardé par
`if stop_order is not None:`. La logique elle-même est extraite dans une
méthode privée `_persist_adopted_stop_order` plutôt qu'inlinée dans la
boucle, pour garder la boucle principale lisible (voir §4, seule décision
non spécifiée par l'ordre).

## 4. Décisions prises
- **Extraction en méthode privée** `_persist_adopted_stop_order` plutôt que
  du code inliné dans la boucle `for setup in local_setups`. L'ordre §3 ne
  spécifie pas la forme exacte (inline vs méthode), seulement le point
  d'insertion et le contenu. Choix : méthode dédiée, pour isolance testable
  et lisibilité — la boucle d'adoption est déjà longue (audit 76/80 la
  décrivent comme `:169-331`).
- **Effets bénéfiques documentés (exigé §7 de l'ordre, transféré ici plutôt
  qu'au §8 car ce ne sont pas des soucis découverts mais des conséquences
  positives volontairement acceptées par l'ordre §1)** :
  1. **Corrige un faux positif CRITICAL dormant (A-3)** : avant ce lot, un
     setup `MANAGEMENT_ONLY` adopté puis relu au redémarrage suivant
     (`reconciliation.run(startup=True)`) déclenchait à tort
     `POSITION_OPEN_STOP_MISSING_CRITICAL` →
     `startup_filled_entry_without_stop` (CRITICAL), verrouillé ensuite par
     `_REVIEW_LOCKED_SETUP_STATUSES` (aucune récupération automatique
     possible). Après ce lot, la ligne `orders` persistée par le premier
     cycle d'adoption survit au redémarrage → `active_stop_order` n'est plus
     `None` → la branche `POSITION_OPEN_STOP_MISSING_CRITICAL` ne
     s'applique plus. Prouvé par
     `test_a3_false_positive_no_longer_fires_on_second_startup` (§5.4).
  2. **Commence à fermer le trou de clôture sur stop déclenché** pour un
     setup adopté : `_reconcile_local_orders` et `_handle_sell_fill`
     deviennent atteignables pour ce stop (ils ne l'étaient jamais avant, en
     l'absence de toute ligne `orders` référençant ce `broker_order_id`).
     Non testé directement dans ce lot (dépend aussi de l'appariement
     `broker_executions`, hors périmètre B-1a) mais la trajectoire du code
     change, comme anticipé par l'audit 80 §Q3/Q5.
- **Limite connue transmise à B-1b (orderId=0)** : un stop entré
  manuellement dans TWS a `broker_order_id=None` mais `broker_perm_id`
  renseigné (correctif TWS déjà appliqué, mémoire
  `tws-parsing-corrections.md`). Ce lot persiste fidèlement ce que le broker
  rapporte (`broker_order_id=stop_order.broker_order_id`, potentiellement
  `None`) — il ne peut pas inventer un `orderId` que le broker ne fournit
  pas. `StopModificationService` (`stop_modification_service.py:105`,
  `if broker_order_id and ...`) resterait `False` pour ce sous-cas précis
  même après B-1a — limite documentée dans l'audit 80 (synthèse), non
  corrigée ici (interdit §8 de l'ordre : "Ne pas modifier
  StopModificationService").

## 5. Preuves de sortie

### 5.1 — Ligne `orders` créée et trouvée par `active_stop_order_for_symbol`
Test : `test_adopted_stop_is_persisted_and_reachable_by_stop_modification_lookup`.
```
$ python -m pytest tests/test_reconciliation_adopted_stop.py::PersistAdoptedStopOrderTests::test_adopted_stop_is_persisted_and_reachable_by_stop_modification_lookup -q
.
1 passed in ...s
```
Vérifie : une ligne `orders` créée, `broker_order_id == "9002"` (celui du
stop broker simulé), `setup_id` = setup adoptant, et
`active_stop_order_for_symbol(symbol)` — l'appel exact utilisé par
`StopModificationService.modify_stop` — retourne cette même ligne.
**PASS.**

### 5.2 — Idempotence (test central)
Test : `test_two_consecutive_runs_create_a_single_order_row`.
Deux appels consécutifs à `reconciliation.run()` avec le même stop broker
actif → `len(self.repository.list_orders()) == 1` après les deux appels.
**PASS.**

### 5.3 — Anti-vol (second test critique)
Test : `test_does_not_steal_stop_order_owned_by_another_setup`.
Une ligne `orders` active préexistante appartenant à `OTHER_SETUP_001` sur
le même symbole, toujours rapportée comme ouverte côté broker (pour éviter
qu'elle soit marquée `CANCELLED` par `_reconcile_local_orders` avant que le
test n'atteigne la logique anti-vol — piège découvert en écrivant ce test,
voir note ci-dessous). Après `run()` : 2 lignes `orders` au total, la ligne
de l'autre setup inchangée (`setup_id`/`broker_order_id` intacts), une
ligne neuve créée pour le setup adoptant, événement
`adoption_stop_order_owner_conflict` présent.
**PASS.**

Note méthodologique : la première version de ce test ne faisait rapporter
que le nouveau stop (`9002`) par le broker simulé — `_reconcile_local_orders`
(qui tourne avant la boucle d'adoption, `reconciliation.py:161`) marquait
alors la ligne de l'autre setup `CANCELLED` faute de la voir dans les
ordres ouverts du broker, la rendant invisible à
`active_stop_order_for_symbol` (filtre `status IN ('CREATED','SUBMITTED')`)
— le chemin anti-vol n'était donc jamais exercé. Corrigé en faisant
rapporter les DEUX ordres par le broker simulé de ce test.

### 5.4 — Faux positif A-3 corrigé
Test : `test_a3_false_positive_no_longer_fires_on_second_startup`.
Premier cycle (`run()`) adopte la position et persiste le stop (ce lot).
Second cycle, nouvelle instance de `ReconciliationEngine` sur le même
repository (simulation de redémarrage), `run(startup=True)` : le statut du
setup n'est PAS `MANUAL_REVIEW_REQUIRED`, et
`startup_filled_entry_without_stop` n'apparaît pas dans les événements.
**PASS.** *(Ce test n'existait pas avant ce lot — l'audit 80 notait
explicitement qu'aucun test ne couvrait ce chemin.)*

### 5.5 — `setup_id` correct, jamais `"broker"`
Test : `test_setup_id_is_adopting_setup_never_the_broker_placeholder`.
Le `BrokerOrderRequest` simulé porte `setup_id="broker"` (reproduisant le
placeholder littéral réel de `tws_connector.py`), la ligne `orders` persistée
porte `setup_id == self.setup_id` (le setup adoptant) et
`assertNotEqual(..., "broker")`.
**PASS.**

### 5.6 — Non-régression reconciliation
```
$ python -m pytest tests/test_reconciliation.py tests/test_review_status_sticky.py -q
............................................................................
79 passed in ...s
```
**PASS** — les 43 tests de `test_reconciliation.py` et les 31 de
`test_review_status_sticky.py` (qui exerce spécifiquement la boucle
d'adoption et le cliquet `never_lower_stop`) passent inchangés.

### 5.7 — Suite complète
```
$ python -m pytest -q
...
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
1 failed, 775 passed, 4 warnings, 134 subtests passed in 275.37s (0:04:35)
```
Conforme à l'attendu de l'ordre §5.7 : "seul test_account_metrics.py en
échec". Vérifié pré-existant et sans rapport avec ce lot : le même test,
rejoué isolément sur `a2521f6` (parent, avant ce lot, via `git stash`),
échoue de façon identique :
```
$ git stash && python -m pytest tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty -q && git stash pop
FAILED ... AssertionError: None != 2.5
1 failed in 0.50s
```
**PASS** (conforme à l'attendu, écart pré-existant confirmé hors périmètre).

## 6. Nettoyage (obligatoire)
- Fichiers temporaires / leurres / scripts jetables créés pendant le lot :
  aucun fichier jetable commité. Des scripts Python de diagnostic ad hoc ont
  été exécutés en ligne de commande (`python -c "..."`) pour investiguer
  l'échec initial du test anti-vol (§5.3) — jamais écrits sur disque, rien à
  nettoyer.
- Stash créés/poppés : un stash temporaire pour vérifier la préexistence de
  l'échec `test_account_metrics.py` sur le parent (§5.7) — créé et poppé
  dans la même commande, `git status` après confirme le retour à l'état
  d'avant stash (modifications de ce lot toujours présentes, rien de perdu).
- Branches ou worktrees créés/supprimés : branche `fix/b1a-persist-adopted-stop`
  créée depuis `feat/setup-conditions`, conservée (pas de suppression, par
  règle générale de ne jamais supprimer sans accord explicite).
- Confirmation qu'aucun artefact du lot ne subsiste hors commit :
  ```
  $ git status --short -- app/ tests/
  (vide après le commit aeca6cf, à l'exception des fichiers non liés à ce
  lot déjà présents avant son démarrage : data/setups/*, audit/*.md
  d'autres sessions, .codex/, tmp/ — non touchés, non commités par ce lot)
  ```

## 7. Suite de tests
```
$ python -m pytest -q
...
1 failed, 775 passed, 4 warnings, 134 subtests passed in 275.37s (0:04:35)
```
Avant ce lot (sur `a2521f6`, base de la branche) : 5 nouveaux tests
n'existaient pas encore. Après ce lot : 775 passés + 1 échec pré-existant
(`test_account_metrics.py`, confirmé sans rapport avec ce lot, §5.7) — soit
5 tests ajoutés par ce lot (`tests/test_reconciliation_adopted_stop.py`,
classe `PersistAdoptedStopOrderTests`), cohérent avec les 5 méthodes de
test écrites.

## 8. Découvert mais NON corrigé
- **Limite `orderId=0` / `permId` seul** (déjà documentée par l'audit 80,
  rappelée ici comme exigé par l'ordre §7) : un stop posé manuellement dans
  TWS avant adoption a `broker_order_id=None`. Ce lot persiste fidèlement
  cette valeur (potentiellement `None`) ; `StopModificationService` ne
  matche que par `orderId`, jamais par `permId` — un correctif compagnon de
  `StopModificationService` ou un routage spécifique sera nécessaire pour
  B-1b afin de couvrir ce sous-cas. Explicitement hors périmètre de B-1a
  (interdiction §8 de l'ordre).
- **`_matching_stop_order` (reconciliation.py, boucle d'adoption) matche par
  symbole seul**, sans distinction de propriétaire, parmi TOUS les ordres
  ouverts du compte broker — comportement préexistant, inchangé par ce lot
  (interdiction §8 : "Ne pas corriger `_matching_stop_order`"). Si plusieurs
  stops actifs existaient réellement pour le même symbole côté broker
  (scénario anormal), le premier trouvé dans la liste brute serait adopté ;
  la garantie anti-vol de ce lot protège uniquement contre la
  réassignation d'une ligne `orders` locale déjà possédée par un AUTRE
  setup, pas contre ce choix de matching côté broker.

## 9. Écarts par rapport à l'ordre
Aucun.
