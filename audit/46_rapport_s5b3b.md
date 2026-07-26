# Rapport de lot — S5b-3b : réparation légitime de stop, protection prouvée

## 1. Identification
- Lot / ordre de travail : `audit/ORDRE_S5b3b.md`
- Branche : `fix/s5b3b-attach-stop-repair`  | Commit : `fa4dfdd629605363e1065b792cf3153fc535a437`
- Basée sur : `fix/s5b3a-central-review-guard` + `9ed1d8bef85d6c113855deee670e7d9300d3d627`
  (empilée sur S5b-3a, PAS sur `feat/setup-conditions`, conformément à §6)
- Mergée : non  | Poussée : non

## 2. Fichiers touchés

```
$ git diff --stat fix/s5b3a-central-review-guard..HEAD
 app/engine/order_manager.py             |  28 ++++++---
 audit/ORDRE_S5b3b.md                    |  89 ++++++++++++++++++++++++++
 tests/test_active_status_write_sites.py |  19 +++---
 tests/test_review_status_sticky.py      | 107 ++++++++++++++++++++++++++------
 4 files changed, 207 insertions(+), 36 deletions(-)
```

Confronté au périmètre de l'ordre (§2) :
- `app/engine/order_manager.py` : autorisé (`attach_missing_stop` uniquement,
  vérifié ci-dessous), modifié. Conforme.
- `tests/test_review_status_sticky.py`, `tests/test_active_status_write_sites.py` :
  autorisés (tests), modifiés. Conforme.
- `audit/ORDRE_S5b3b.md` : ajout de l'ordre lui-même. Conforme.
- Aucun fichier `app/storage/repositories.py` dans le diff (garde 3a
  inchangée). Conforme à l'interdiction §2/§8.
- Aucune autre méthode d'`order_manager.py` modifiée (voir §3 : le diff ne
  touche que le corps de `attach_missing_stop`, entre `raise` de la ligne
  453 et `return stop_order`). Conforme.

## 3. Diff du code de production

Diff intégral de `app/engine/order_manager.py` (seul fichier de `app/`
touché) :

```diff
diff --git a/app/engine/order_manager.py b/app/engine/order_manager.py
index dc6220c..53ffe89 100644
--- a/app/engine/order_manager.py
+++ b/app/engine/order_manager.py
@@ -451,10 +451,24 @@ class OrderManager:
                 protection_status="STOP_REPAIR_EXCEPTION",
             )
             raise
-        if stop_order.status in {
-            OrderStatus.REJECTED.value,
-            OrderStatus.ERROR.value,
-        }:
+        stop_is_active = stop_order.status in {
+            OrderStatus.CREATED.value,
+            OrderStatus.SUBMITTED.value,
+        }
+        if stop_is_active:
+            # The allow_from_review flag below is passed ONLY because
+            # stop_order.status proves the broker actually holds this stop
+            # active -- this is not a bypass of the S5b-3a review guard, it
+            # is that guard's one documented legitimate exception: a missing
+            # protective stop has just been repaired and confirmed live, so
+            # the alarm that blocked the setup can be cleared.
+            self.repository.update_setup_status(
+                setup["setup_id"],
+                SetupStatus.ENTRY_ORDER_PLACED.value,
+                "Protective stop attached to existing entry order",
+                allow_from_review=True,
+            )
+        else:
             await self._cancel_parent_for_failed_protection(
                 setup,
                 _order_record_from_row(entry_order),
@@ -462,12 +476,6 @@ class OrderManager:
                 reason="Protective stop repair failed",
                 protection_status="STOP_REPAIR_FAILED",
             )
-        else:
-            self.repository.update_setup_status(
-                setup["setup_id"],
-                SetupStatus.ENTRY_ORDER_PLACED.value,
-                "Protective stop attached to existing entry order",
-            )
         return stop_order
```

`place_stop_order` et `_cancel_parent_for_failed_protection` ne sont ni
modifiées ni déplacées — seul le corps de `attach_missing_stop` (le
branchement après le `try/except` qui appelle `place_stop_order`) change.
`repositories.py` n'apparaît nulle part dans ce diff.

## 4. Décisions prises

- **Portée du resserrement** : l'ordre demande explicitement de traiter
  `FILLED`/`CANCELLED` comme des échecs (§3, §8 : "le resserrement
  FILLED/CANCELLED est la SEULE extension de portée autorisée"). La
  condition positive `stop_is_active = status in {CREATED, SUBMITTED}`
  route donc TOUT le reste (`REJECTED`, `ERROR`, `FILLED`, `CANCELLED`, et
  toute valeur future non encore dans `OrderStatus`) vers
  `_cancel_parent_for_failed_protection`, sans énumérer ces cas un par un —
  c'est la façon la plus directe de satisfaire "jamais sur
  FILLED/CANCELLED/REJECTED/ERROR" (§4) sans dupliquer la liste des échecs.
- **Test du resserrement (`CANCELLED`)** : l'ordre laissait le choix entre
  `CANCELLED` et `FILLED` (§5.3 : "CANCELLED (ou FILLED)"). Choisi
  `CANCELLED` car `_cancel_broker_only_order`/`cancel_order` de
  `SimulatedBrokerConnector` renvoie déjà nativement ce statut dans un cas
  réel (annulation), rendant le broker de test plus proche d'un
  comportement observable qu'un `FILLED` fabriqué de toutes pièces pour un
  stop qui vient d'être soumis.
- **Assertion finale du test de resserrement** : l'ordre dit "l'alarme est
  préservée" (§5.3) sans préciser si c'est la même valeur qu'au départ.
  `_cancel_parent_for_failed_protection` (non modifiée, hors périmètre)
  écrit TOUJOURS `ERROR_REQUIRES_MANUAL_REVIEW`, quel que soit le statut de
  départ — comportement inchangé par ce lot, déjà vrai avant S5b-3b pour le
  chemin `REJECTED`. Le test part donc de `MANUAL_REVIEW_REQUIRED` (pour
  prouver que ce n'est pas seulement le cas `ERROR_REQUIRES_MANUAL_REVIEW`
  qui est couvert) et vérifie explicitement `assertNotEqual(...,
  ENTRY_ORDER_PLACED)` PUIS l'égalité avec `ERROR_REQUIRES_MANUAL_REVIEW`
  (la valeur réelle écrite par le code non modifié), plutôt que d'affirmer
  à tort que `MANUAL_REVIEW_REQUIRED` survivrait telle quelle — ce qui
  aurait fait mentir le test sur le comportement réel de
  `_cancel_parent_for_failed_protection`, une fonction explicitement hors
  périmètre de ce lot (§2).
- **Reformulation du commentaire de code** : la première rédaction du
  commentaire au-dessus de l'appel `allow_from_review=True` contenait
  littéralement la chaîne `allow_from_review=True`, ce qui aurait fait
  apparaître DEUX occurrences dans un grep naïf de cette chaîne exacte (le
  commentaire et l'appel réel) — reformulé en "The allow_from_review flag
  below is passed..." pour que le grep de preuve (§5.4) pointe sans
  ambiguïté vers le seul site d'appel réel.

## 5. Preuves de sortie

### 5.1 — Inversion des 2 tests `AttachMissingStopReviewStickyTests` (diff des assertions)

```diff
-    async def test_error_requires_manual_review_survives_attach_missing_stop(
+    async def test_error_requires_manual_review_is_cleared_by_active_stop_repair(
...
-        await recovery_manager.attach_missing_stop(self.order.id)
+        stop_order = await recovery_manager.attach_missing_stop(self.order.id)

-        self.assertEqual(
-            self._setup_status(), SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value
-        )
+        self.assertEqual(stop_order.status, OrderStatus.SUBMITTED.value)
+        self.assertEqual(self._setup_status(), SetupStatus.ENTRY_ORDER_PLACED.value)

-    async def test_manual_review_required_survives_attach_missing_stop(self) -> None:
+    async def test_manual_review_required_is_cleared_by_active_stop_repair(self) -> None:
...
-        await recovery_manager.attach_missing_stop(self.order.id)
+        stop_order = await recovery_manager.attach_missing_stop(self.order.id)

-        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)
+        self.assertEqual(stop_order.status, OrderStatus.SUBMITTED.value)
+        self.assertEqual(self._setup_status(), SetupStatus.ENTRY_ORDER_PLACED.value)
```

Aucune fixture (`_UnprotectedEntryFixture.asyncSetUp`, `_force_manual_review_required`)
n'a été modifiée — les deux tests utilisent toujours le même setup de
départ (`ERROR_REQUIRES_MANUAL_REVIEW` par la fixture, ou
`MANUAL_REVIEW_REQUIRED` forcé), seule l'attente change : la réparation
réussit maintenant au lieu d'être bloquée.

```
$ python -m pytest tests/test_review_status_sticky.py -v -k AttachMissingStop
tests/test_review_status_sticky.py::AttachMissingStopReviewStickyTests::test_cancelled_repair_attempt_does_not_clear_alarm PASSED
tests/test_review_status_sticky.py::AttachMissingStopReviewStickyTests::test_error_requires_manual_review_is_cleared_by_active_stop_repair PASSED
tests/test_review_status_sticky.py::AttachMissingStopReviewStickyTests::test_manual_review_required_is_cleared_by_active_stop_repair PASSED
tests/test_review_status_sticky.py::AttachMissingStopReviewStickyTests::test_rejected_repair_attempt_preserves_alarm PASSED
4 passed
```

Verdict : **PASS**.

### 5.2 — Non-régression : `REJECTED` → alarme préservée

`AttachMissingStopReviewStickyTests.test_rejected_repair_attempt_preserves_alarm`
(nouveau) : utilise `_StopAlwaysRejectedBroker` (déjà existant, réutilisé
tel quel, pas de nouvelle classe) pour la tentative de réparation elle-même
→ `stop_order.status == REJECTED` → `stop_is_active = False` → route vers
`_cancel_parent_for_failed_protection` → statut final
`ERROR_REQUIRES_MANUAL_REVIEW` (voir sortie ci-dessus, test inclus dans
la commande §5.1). Comportement identique à avant ce lot pour ce cas
précis (`REJECTED` était déjà dans l'ancien ensemble d'échec
`{REJECTED, ERROR}`).

Verdict : **PASS**.

### 5.3 — Resserrement : `CANCELLED` (+ setup en alarme) → pas de `ENTRY_ORDER_PLACED`

`AttachMissingStopReviewStickyTests.test_cancelled_repair_attempt_does_not_clear_alarm`
(nouveau) : nouvelle classe de broker de test `_StopImmediatelyCancelledBroker`
(accepte la soumission SELL mais rapporte `status="CANCELLED"`) → setup
forcé `MANUAL_REVIEW_REQUIRED` → `stop_order.status == CANCELLED` →
`stop_is_active = False` (alors qu'avant ce lot, `CANCELLED ∉ {REJECTED,
ERROR}` aurait fait passer dans la branche `else` et écrit
`ENTRY_ORDER_PLACED`) → route vers `_cancel_parent_for_failed_protection`
→ `assertNotEqual(status, ENTRY_ORDER_PLACED)` et statut final
`ERROR_REQUIRES_MANUAL_REVIEW` (voir sortie §5.1, test inclus dans la même
commande). Prouve que l'angle mort de l'audit 45 est fermé.

Verdict : **PASS**.

### 5.4 — Grep : `allow_from_review=True` unique dans `app/`

```
$ grep -rn "allow_from_review=True" app/ --include="*.py"
app/engine/order_manager.py:469:                allow_from_review=True,
```

Une seule occurrence, exactement le site attendu. Grep complémentaire du
paramètre lui-même (définition + usage) :

```
$ grep -rn "allow_from_review" app/ --include="*.py"
app/engine/order_manager.py:459:            # The allow_from_review flag below is passed ONLY because
app/engine/order_manager.py:469:                allow_from_review=True,
app/storage/repositories.py:487:        allow_from_review: bool = False,
app/storage/repositories.py:489:        if not allow_from_review and status in _ACTIVE_STATUSES:
```

`repositories.py:487/489` est la définition du paramètre (S5b-3a, non
modifiée par ce lot) ; `order_manager.py:469` est l'unique appelant qui le
positionne à `True` dans tout `app/`. Conforme à l'invariant §4.

Verdict : **PASS**.

### 5.5 — Cliquets textuels S2 + S5b-2, justification `ALLOWED_ACTIVE_WRITE_SITES` à jour

La justification de `app/engine/order_manager.py` dans
`tests/test_active_status_write_sites.py` décrivait encore `attach_missing_stop`
comme un site "ne vérifiant pas le statut du setup" et documentait le
"S5b-3 debt" — obsolète après ce lot. Mise à jour pour refléter le nouveau
comportement (`allow_from_review=True` conditionné par
`stop_order.status`) :

```diff
     "app/engine/order_manager.py": (
         "Three literal writes: place_entry_order() writes ENTRY_ORDER_PLACED "
         "after a bracket order is accepted (:180); place_stop_order() writes "
         "STOP_ORDER_PLACED when called with update_setup_status=True, which "
         "only happens from fill_executor's simulated-fill path (:374); "
-        "attach_missing_stop() writes ENTRY_ORDER_PLACED after repairing a "
-        "missing protective stop (:466). See "
-        "tests/test_review_status_sticky.py for the behavioural coverage of "
-        "each (S5b-2, audit 40): place_entry_order's two callers gate on "
-        "setup status upstream (unreachable from an alarm today); "
-        "attach_missing_stop and the simulated-fill cascade through "
-        "place_stop_order do NOT check setup status and are confirmed able "
-        "to overwrite MANUAL_REVIEW_REQUIRED/ERROR_REQUIRES_MANUAL_REVIEW "
-        "(documented as S5b-3 debt, not fixed by this lot)."
+        "attach_missing_stop() writes ENTRY_ORDER_PLACED after repairing a "
+        "missing protective stop (:465-470), passing allow_from_review=True "
+        "-- the one caller in the codebase authorised to cross the S5b-3a "
+        "review guard (app/storage/repositories.py), and only in the branch "
+        "where stop_order.status proves the repaired stop is CREATED or "
+        "SUBMITTED at the broker (S5b-3b, audit 46). See "
+        "tests/test_review_status_sticky.py for the behavioural coverage of "
+        "each: place_entry_order's two callers gate on setup status upstream "
+        "(unreachable from an alarm today); the simulated-fill cascade "
+        "through place_stop_order does NOT check setup status and cannot "
+        "overwrite MANUAL_REVIEW_REQUIRED/ERROR_REQUIRES_MANUAL_REVIEW since "
+        "S5b-3a's central guard (documented as S5b-3 debt prior to S5b-3a, "
+        "now blocked); attach_missing_stop is the sole legitimate exception "
+        "to that guard, proven safe by AttachMissingStopReviewStickyTests."
     ),
```

Les entrées `app/engine/post_fill_progression.py` et
`app/engine/reconciliation.py` restent inchangées (leur libellé "S5b-3
debt" est déjà obsolète depuis S5b-3a, mais leur mise à jour n'est ni
demandée par cet ordre ni dans son périmètre — signalé en §8).

```
$ python -m pytest tests/test_active_status_write_sites.py tests/test_in_position_write_sites.py -v
tests/test_active_status_write_sites.py::ActiveStatusWriteSiteRatchetTests::test_only_known_sites_write_an_active_status PASSED
tests/test_in_position_write_sites.py::InPositionWriteSiteRatchetTests::test_only_known_sites_write_in_position PASSED
2 passed
```

Verdict : **PASS**.

### 5.6 — Suite complète

```
$ python -m pytest -q
...
1 failed, 739 passed, 4 warnings, 134 subtests passed in 279.33s
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
```

Seul échec : le même que S5b-3a, sans rapport avec ce lot (`today_pnl`
attendu `2.5`, obtenu `None`, dans `TradingEngine.snapshot()`/comptes
broker — aucun chemin ne passe par `attach_missing_stop` ou
`update_setup_status`). Conforme à l'attente de l'ordre (§5.6).

Verdict : **PASS**.

## 6. Nettoyage (obligatoire)

- Aucun fichier temporaire, leurre ou script jetable laissé dans le dépôt :
  un fichier de travail créé pendant la rédaction de ce rapport,
  `tmp/s5b3b_test_diff.txt` (diff brut extrait pour §5.1), dans `tmp/` déjà
  ignoré par le dépôt — ni ajouté ni committé.
- Aucun stash créé ou poppé pendant ce lot.
- Une seule branche créée, `fix/s5b3b-attach-stop-repair`, empilée sur
  `fix/s5b3a-central-review-guard` comme demandé (§6) — pas de worktree
  supplémentaire.
- `git status --short` après ce lot : uniquement les fichiers listés au
  §2, déjà committés, plus les éléments préexistants au début de la
  session (suppressions non committées de `data/setups/CODI_20260628_001.json`/
  `TXN_20260630_001.json`, fichiers `audit/*_preaudit.md` non committés
  d'un lot antérieur) — non touchés par ce lot.

## 7. Suite de tests

Compte avant → après pour `tests/test_review_status_sticky.py` :

```
$ git show fix/s5b3a-central-review-guard:tests/test_review_status_sticky.py | grep -c "    async def test_\|    def test_"
24
$ python -m pytest tests/test_review_status_sticky.py --collect-only -q | tail -1
26 tests collected in 0.11s
```

24 → 26 : +2 net. Détail : les 2 tests de `AttachMissingStopReviewStickyTests`
existants sont inversés sur place (renommés, mêmes emplacements, pas
d'ajout net) et 2 tests entièrement nouveaux sont ajoutés
(`test_rejected_repair_attempt_preserves_alarm`,
`test_cancelled_repair_attempt_does_not_clear_alarm`) — cohérent avec
24 + 2 = 26.

```
$ python -m pytest tests/test_review_status_sticky.py -q
..........................
26 passed in 11.33s
```

Suite complète : voir §5.6, `1 failed, 739 passed` (le même échec
pré-existant que S5b-3a, +2 tests passés au total par rapport aux 737 de
S5b-3a, cohérent avec les 2 tests nets ajoutés ici).

## 8. Découvert mais NON corrigé

**Libellés obsolètes non touchés dans `ALLOWED_ACTIVE_WRITE_SITES`** : les
entrées `app/engine/post_fill_progression.py` et
`app/engine/reconciliation.py` de
`tests/test_active_status_write_sites.py` documentent encore ces sites
comme "confirmed able to overwrite MANUAL_REVIEW_REQUIRED/
ERROR_REQUIRES_MANUAL_REVIEW (S5b-3 debt)" — obsolète depuis S5b-3a (la
garde centrale bloque déjà ces écritures depuis une alarme). Ni cet ordre
ni S5b-3a n'ont demandé leur mise à jour (le §5.5 de S5b3b ne visait que
l'entrée `order_manager.py`, directement concernée par ce lot) ; je n'ai
mis à jour QUE l'entrée `order_manager.py`, conformément au périmètre
strict de l'ordre. Signalé pour une passe de nettoyage future si souhaitée
(risque nul : ces cliquets restent fonctionnellement corrects, seul le
texte de justification est en retard sur le code).

Aucun autre écart ou chemin d'effacement supplémentaire découvert.

## 9. Écarts par rapport à l'ordre

Aucun.
