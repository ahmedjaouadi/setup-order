# Rapport de lot — C-2

## 1. Identification
- Lot / ordre de travail : audit/ORDRE_C2.md
- Branche : fix/c2-clear-adoption-alarm | Commit : d55a5349a1d54d72ceba9c1ace9ba03974fede7c
- Basée sur : feat/setup-conditions + 55dca80a05e3ada74795abe63378659de3819c5c
- Mergée : non | Poussée : non

## 2. Fichiers touchés
```
$ git diff --stat feat/setup-conditions..HEAD
 app/engine/reconciliation.py       |  59 ++++++++++++++--
 audit/ORDRE_C2.md                  | 100 +++++++++++++++++++++++++++
 tests/test_review_status_sticky.py | 138 +++++++++++++++++++++++++++++++++----
 3 files changed, 278 insertions(+), 19 deletions(-)
```
Conforme à l'ordre : seul `app/engine/reconciliation.py` (autorisé) + les
tests + le fichier d'ordre lui-même sont touchés. Aucun fichier interdit
(`repositories.py`, `order_manager.py`, `state_machine.py`,
`post_fill_progression.py`) modifié.

## 3. Diff du code de production
Diff intégral de `app/engine/reconciliation.py` (seul fichier de `app/`
touché) :

```diff
diff --git a/app/engine/reconciliation.py b/app/engine/reconciliation.py
index 96e5995..d66c071 100644
--- a/app/engine/reconciliation.py
+++ b/app/engine/reconciliation.py
@@ -16,6 +16,12 @@ from app.storage.repositories import TradingRepository
 
 logger = logging.getLogger(__name__)
 
+# Root C (audit 73, cause #3): the exact last_event message the adoption
+# loop's own missing-stop branch below writes when it raises the alarm.
+# Shared with the check further down that clears the alarm once the stop
+# reappears, so the two can never drift out of sync.
+ADOPTION_STOP_NOT_FOUND_MESSAGE = "Broker stop order not found"
+
 
 class ReconciliationResult(TypedDict):
     broker_positions: int
@@ -234,16 +240,30 @@ class ReconciliationEngine:
                 self.repository.update_setup_status(
                     setup["setup_id"],
                     SetupStatus.MANUAL_REVIEW_REQUIRED.value,
-                    "Broker stop order not found",
+                    ADOPTION_STOP_NOT_FOUND_MESSAGE,
                 )
                 self.event_store.record(
                     EventLevel.RISK,
                     "adoption_blocked_stop_not_found",
-                    "Broker stop order not found",
+                    ADOPTION_STOP_NOT_FOUND_MESSAGE,
                     setup_id=setup["setup_id"],
                     symbol=symbol,
                 )
                 continue
+            # Root C (audit 73/74, cause #3): a fresh, active stop
+            # (stop_order is not None, proven this cycle by
+            # _matching_stop_order against the broker's own open orders --
+            # never stale) found for a setup that is currently alarmed for
+            # exactly this cause is the one legitimate, verified reason to
+            # clear a MANUAL_REVIEW_REQUIRED alarm here. Any other alarm
+            # cause, or no alarm at all, leaves allow_from_review False --
+            # strictly unchanged behaviour, the S5b-3a guard keeps protecting
+            # it exactly as before this lot.
+            clearing_stop_alarm = (
+                stop_order is not None
+                and str(setup.get("status") or "") == SetupStatus.MANUAL_REVIEW_REQUIRED.value
+                and str(setup.get("last_event") or "") == ADOPTION_STOP_NOT_FOUND_MESSAGE
+            )
             current_stop = stop_order.stop_price if stop_order else protective_stop
             risk_remaining = max(
                 broker_position.current_price - float(current_stop),
@@ -266,11 +286,27 @@ class ReconciliationEngine:
                     status="OPEN",
                 )
             )
-            self.repository.update_setup_status(
-                setup["setup_id"],
-                SetupStatus.IN_POSITION.value,
-                "Existing IBKR position adopted",
-            )
+            if clearing_stop_alarm:
+                # The allow_from_review flag below is passed ONLY because
+                # stop_order is not None proves the broker's own open orders
+                # (fresh this cycle) hold an active protective stop again --
+                # this is not a bypass of the S5b-3a review guard, it is that
+                # guard's second documented legitimate exception (the first
+                # is attach_missing_stop, order_manager.py:465): cause #3's
+                # alarm ("Broker stop order not found") is verified gone, so
+                # the alarm it raised can be cleared.
+                self.repository.update_setup_status(
+                    setup["setup_id"],
+                    SetupStatus.IN_POSITION.value,
+                    "Existing IBKR position adopted",
+                    allow_from_review=True,
+                )
+            else:
+                self.repository.update_setup_status(
+                    setup["setup_id"],
+                    SetupStatus.IN_POSITION.value,
+                    "Existing IBKR position adopted",
+                )
             result["adopted_positions"] += 1
             self.event_store.record(
                 EventLevel.SYNC,
@@ -284,6 +320,15 @@ class ReconciliationEngine:
                     "current_stop": current_stop,
                 },
             )
+            if clearing_stop_alarm:
+                self.event_store.record(
+                    EventLevel.SYNC,
+                    "adoption_review_cleared_stop_restored",
+                    "Broker stop order reappeared; adoption review alarm cleared",
+                    setup_id=setup["setup_id"],
+                    symbol=symbol,
+                    data={"stop_order_id": stop_order.broker_order_id},
+                )
         self._save_broker_reality_report(
             local_setups=self.repository.list_setups(),
             local_orders=self.repository.list_orders(),
```

## 4. Décisions prises
- Le message littéral `"Broker stop order not found"` (cause #3) a été
  extrait en constante de module `ADOPTION_STOP_NOT_FOUND_MESSAGE`, réutilisée
  aux trois endroits (les deux écritures existantes du blocage + la nouvelle
  comparaison), pour éliminer tout risque de divergence de chaîne — l'ordre
  l'autorisait explicitement (§3, §8).
- Le passage de `allow_from_review=True` a été codé en `if/else` explicite
  (littéral `True`) plutôt qu'en variable passée telle quelle
  (`allow_from_review=clearing_stop_alarm`), pour que la preuve de sortie §5.4
  (« grep `allow_from_review=True` → exactement deux sites ») soit vérifiable
  textuellement par un grep brut, comme le patron `attach_missing_stop`. Un
  commentaire explicite (« second documented legitimate exception ») a été
  ajouté au site, à l'image de celui d'`attach_missing_stop`.
- L'événement `adoption_review_cleared_stop_restored` est émis en plus de
  l'événement `existing_position_adopted` déjà existant (pas à sa place), pour
  ne pas perdre l'information "adoption normale" tout en traçant distinctement
  la levée d'alarme.
- `stop_order.broker_order_id` a été choisi comme "id du stop retrouvé" dans
  les données de l'événement (c'est le seul identifiant de l'ordre broker
  disponible sur `BrokerOrderRequest` à ce point du code).

## 5. Preuves de sortie

### 5.1 Cas réel : levée de l'alarme cause #3 quand le stop est reposé
```
$ python -m pytest tests/test_review_status_sticky.py -k test_manual_review_required_is_cleared_when_stop_reappears -v
tests/test_review_status_sticky.py::PositionAdoptionReviewStickyTests::test_manual_review_required_is_cleared_when_stop_reappears PASSED [100%]
```
Setup posé en `MANUAL_REVIEW_REQUIRED` / `last_event=ADOPTION_STOP_NOT_FOUND_MESSAGE`,
broker rapportant un stop SELL actif (`_AdoptionBrokerWithRestoredStop`) →
après `reconciliation.run()` : statut `IN_POSITION`, un événement
`adoption_review_cleared_stop_restored` unique avec `setup_id`, `symbol` et
`stop_order_id` corrects. PASS.

### 5.2 LE TEST CRITIQUE : autre cause NON levée
```
$ python -m pytest tests/test_review_status_sticky.py -k test_other_cause_alarm_survives_even_with_position_and_stop -v
tests/test_review_status_sticky.py::PositionAdoptionReviewStickyTests::test_other_cause_alarm_survives_even_with_position_and_stop PASSED [100%]
```
Même géométrie (position ouverte + stop actif retrouvé) mais
`last_event="Sell filled but broker position quantity disagrees"` (une AUTRE
cause) → statut reste `MANUAL_REVIEW_REQUIRED`, `last_event` inchangé, aucun
événement `adoption_review_cleared_stop_restored` émis. PASS — prouve que le
filtre `last_event` protège, pas seulement la géométrie.

### 5.3 Stop non frais : l'alarme survit
```
$ python -m pytest tests/test_review_status_sticky.py -k test_manual_review_required_survives_existing_position_adoption_without_restored_stop -v
tests/test_review_status_sticky.py::PositionAdoptionReviewStickyTests::test_manual_review_required_survives_existing_position_adoption_without_restored_stop PASSED [100%]
```
Setup en alarme cause #3, mais `_AdoptionBroker` (sans variante) ne rapporte
aucun ordre ouvert ce cycle → `_matching_stop_order` renvoie `None` →
`clearing_stop_alarm` est faux → statut reste `MANUAL_REVIEW_REQUIRED`. PASS.
C'est l'adaptation directe de l'ancien test
`test_manual_review_required_survives_existing_position_adoption` (renommé,
`last_event` désormais fixé à la constante de cause #3 au lieu du générique
"test setup" — voir §9).

### 5.4 `allow_from_review=True` : exactement deux sites
```
$ grep -rn "allow_from_review=True" app/
app/engine/order_manager.py:469:                allow_from_review=True,
app/engine/reconciliation.py:302:                    allow_from_review=True,
```
PASS — deux sites exactement : `attach_missing_stop` (existant, inchangé) et
le nouveau site de ce lot.

### 5.5 Non-régression du test existant
Voir §9 pour l'explication du renommage/adaptation. Le test adapté
(`..._without_restored_stop`) passe (§5.3) et couvre exactement le même
comportement que l'original testait (l'alarme cause #3 survit sans preuve de
réparation), en plus de fixer `last_event` à la valeur réelle de la cause #3
au lieu d'un texte générique.

### 5.6 Suite complète
```
$ python -m pytest -q
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
1 failed, 764 passed, 4 warnings, 134 subtests passed in 203.49s (0:03:23)
```
Seul `test_account_metrics.py` en échec, comme exigé. PASS.

## 6. Nettoyage (obligatoire)
- Aucun fichier temporaire, leurre ou script jetable créé dans le dépôt
  pendant ce lot.
- Un worktree temporaire (`/tmp/c2_baseline`, détaché sur
  `feat/setup-conditions`) a été créé pour mesurer le compte de tests AVANT
  ce lot (§7) ; il a été supprimé après usage :
  ```
  $ git worktree remove /tmp/c2_baseline --force
  $ git worktree list
  C:/Users/AhmedJAOUADI/Workspace/setup-order  d55a534 [fix/c2-clear-adoption-alarm]
  ```
  (l'autre entrée de worktree listée, `wt-test` à `7bfd5fe`, est antérieure à
  ce lot et n'a pas été touchée.)
- Aucun stash créé.
- Aucune branche supprimée. Branche `fix/c2-clear-adoption-alarm` créée et
  conservée (non mergée, non poussée, conformément à l'ordre qui ne demande
  ni fusion ni push).
- Aucun artefact du lot ne subsiste hors du commit d55a534.

## 7. Suite de tests
```
$ python -m pytest -q   # AVANT (parent feat/setup-conditions, worktree détaché)
1 failed, 762 passed, 4 warnings, 134 subtests passed in 333.66s (0:05:33)

$ python -m pytest -q   # APRÈS (ce commit)
1 failed, 764 passed, 4 warnings, 134 subtests passed in 203.49s (0:03:23)
```
Avant → après : 762 → 764 passed (+2), 1 échec pré-existant inchangé
(`test_account_metrics.py`, hors périmètre). +2 correspond exactement aux
deux tests ajoutés dans ce lot
(`test_manual_review_required_is_cleared_when_stop_reappears` et
`test_other_cause_alarm_survives_even_with_position_and_stop`) ; le troisième
test du lot est un renommage/adaptation du test existant, pas un ajout net.

## 8. Découvert mais NON corrigé
Aucun.

## 9. Écarts par rapport à l'ordre
- Le test existant `test_manual_review_required_survives_existing_position_
  adoption` a été renommé
  `test_manual_review_required_survives_existing_position_adoption_
  without_restored_stop` et son `last_event` de fixture changé de
  "test setup" (générique) à `ADOPTION_STOP_NOT_FOUND_MESSAGE` (la vraie
  chaîne de la cause #3). Raison : l'ordre (§5.5) demandait explicitement que
  ce test soit adapté pour tester "la survie SANS stop reposé" plutôt que la
  survie inconditionnelle d'origine. Le scénario du fixture (`_AdoptionBroker`
  sans ordre ouvert) testait déjà "pas de stop frais", mais avec un
  `last_event` générique qui ne prouvait pas que le filtre de cause #3 est ce
  qui protège — le corriger vers la vraie chaîne de cause #3 rend le test
  fidèle à l'invariant qu'il est censé garantir (§4 de l'ordre : "Si le stop
  retrouvé n'est PAS actif ce cycle, aucune levée"). Le comportement testé
  (l'alarme survit) est inchangé ; seule la fixture est devenue plus précise.
- `allow_from_review=True` a été codé en branche `if/else` explicite plutôt
  qu'en passant une variable booléenne à l'appel unique — écart mineur de
  style non prescrit par l'ordre, justifié en §4 (rend le grep de preuve
  §5.4 valide littéralement, à l'image du patron `attach_missing_stop`).
- Aucun autre écart.
