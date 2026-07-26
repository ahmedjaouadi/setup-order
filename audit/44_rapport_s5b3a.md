# Rapport de lot — S5b-3a : garde centrale anti-effacement d'alarme

## 1. Identification
- Lot / ordre de travail : `audit/ORDRE_S5b3a.md`
- Branche : `fix/s5b3a-central-review-guard`  | Commit : `5f31f8564ce5ab11e1f8f64a2698959632fdcfb8`
- Basée sur : `feat/setup-conditions` + `f8364798ee322dba5dada90690caf75f9a1db9d8`
- Mergée : non  | Poussée : non

## 2. Fichiers touchés

```
$ git diff --stat feat/setup-conditions..HEAD
 app/storage/repositories.py        |  41 +++++++
 audit/ORDRE_S5b3a.md               | 103 ++++++++++++++++
 tests/test_review_status_sticky.py | 244 ++++++++++++++++++++++++++++++-------
 3 files changed, 342 insertions(+), 46 deletions(-)
```

Confronté au périmètre de l'ordre (§2) :
- `app/storage/repositories.py` : autorisé, modifié. Conforme.
- `tests/test_review_status_sticky.py` : autorisé (tests), modifié. Conforme.
- `audit/ORDRE_S5b3a.md` : ajout de l'ordre lui-même (§0 de l'ordre : "écris
  d'abord ce fichier"). Conforme.
- Aucun fichier de `app/engine/*` dans le diff. Conforme à l'interdiction §2/§9.
- `data/setups/CODI_20260628_001.json` et `data/setups/TXN_20260630_001.json`
  apparaissent en statut `D` (supprimé) dans `git status` mais **hors** de ce
  diff (`feat/setup-conditions..HEAD`) : ce sont des suppressions non
  committées préexistantes à ce lot (déjà présentes dans `git status` au
  début de la session), non touchées, non committées par ce lot.

## 3. Diff du code de production

Diff intégral de `app/storage/repositories.py` (seul fichier de `app/`
touché) :

```diff
diff --git a/app/storage/repositories.py b/app/storage/repositories.py
index ef36c38..dda8437 100644
--- a/app/storage/repositories.py
+++ b/app/storage/repositories.py
@@ -1,6 +1,7 @@
 from __future__ import annotations
 
 import json
+import logging
 from math import floor
 from typing import Any
 
@@ -9,10 +10,34 @@ from app.models import (
     OrderRecord,
     PositionRecord,
     SetupRecord,
+    SetupStatus,
     utc_now_iso,
 )
 from app.storage.database import Database
 
+logger = logging.getLogger(__name__)
+
+_REVIEW_ALARM_STATUSES = frozenset(
+    {
+        SetupStatus.MANUAL_REVIEW_REQUIRED.value,
+        SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value,
+    }
+)
+
+_ACTIVE_STATUSES = frozenset(
+    {
+        SetupStatus.ENTRY_ORDER_PLACED.value,
+        SetupStatus.ENTRY_PARTIALLY_FILLED.value,
+        SetupStatus.ENTRY_FILLED.value,
+        SetupStatus.STOP_ORDER_PLACED.value,
+        SetupStatus.STOP_PLACED.value,
+        SetupStatus.IN_POSITION.value,
+        SetupStatus.MANAGING_POSITION.value,
+        SetupStatus.PARTIAL_EXIT.value,
+        SetupStatus.RECONCILING_EXISTING_POSITION.value,
+    }
+)
+
 
 def _row_to_dict(row: Any) -> dict[str, Any]:
     result = dict(row)
@@ -458,7 +483,23 @@ class TradingRepository:
         last_event: str,
         status_reason: str | None = None,
         last_revalidated_at: str | None = None,
+        *,
+        allow_from_review: bool = False,
     ) -> None:
+        if not allow_from_review and status in _ACTIVE_STATUSES:
+            row = self.database.execute(
+                "SELECT status FROM setups WHERE setup_id = ?",
+                (setup_id,),
+            ).fetchone()
+            current_status = str(row["status"]) if row else None
+            if current_status in _REVIEW_ALARM_STATUSES:
+                logger.warning(
+                    "Blocked write of active status %s over review alarm %s for setup %s",
+                    status,
+                    current_status,
+                    setup_id,
+                )
+                return
         if status_reason is None and last_revalidated_at is None:
             self.database.execute(
                 """
```

`repositories.py` n'importe toujours que `app.models` et `app.storage.database`
(plus `app.conversion` en import différé local, préexistant, hors de ce lot) —
aucun import de `app.engine.*` ajouté.

## 4. Décisions prises

- **Mécanisme de trace** : l'ordre proposait `logger.warning` si aucun
  mécanisme de log n'existait déjà dans `repositories.py`. Vérifié : ce
  fichier n'a aucun logger existant (uniquement des appels SQL et
  `event_store`/`EventRecord` ailleurs dans la codebase, jamais importés
  ici). `logging.getLogger(__name__)` + `logger.warning(...)` a donc été
  ajouté, conformément à l'option de repli explicitement prévue par l'ordre.
- **Portée de l'inversion des tests GAP** : l'ordre (§5.1) cite nommément
  deux chemins à inverser ("cascade simulate_fill → IN_POSITION ; adoption →
  IN_POSITION pour MRR"), correspondant aux trois chemins fautifs du §1.
  Comme la garde est strictement centrale (aucune discrimination par site
  d'appel), elle bloque aussi, en effet de bord, l'écriture de
  `attach_missing_stop` (`order_manager.py:466`) — un site explicitement
  mis hors périmètre (§1/§2/§9, réservé à S5b-3b). Ce comportement est
  exactement celui anticipé par l'ordre lui-même en §6 ("un site que la
  garde bloque et qui devrait légitimement passer") : j'ai donc **aussi**
  inversé les deux tests de `AttachMissingStopReviewStickyTests` (ils
  décrivaient un effacement qui n'existe plus, littéralement, après ce
  lot — les laisser inchangés aurait fait mentir la suite de tests sur le
  comportement réel), et j'ai documenté ce fait en §8 ci-dessous comme
  demandé par l'ordre, plutôt que de corriger `attach_missing_stop` (ce que
  je n'ai pas fait — aucune modification de `order_manager.py`).
- **Champ de comparaison "édition de config" pour le cliquet §5.5** :
  l'ordre demande un test "édition de config" sans préciser le champ à
  modifier. Choisi `risk.max_risk_usd` (15 → 20 dans `valid_breakout_config`)
  car c'est un changement de valeur numérique qui ne touche à aucune règle
  de validation sémantique connue, donc `validation.valid` reste `True`
  sans avoir à adapter le fixture.

## 5. Preuves de sortie

### 5.1 — Inversion des tests GAP existants (diff des assertions)

Diff complet (docstrings + assertions) pour les 4 classes concernées,
extrait de `git diff feat/setup-conditions..HEAD -- tests/test_review_status_sticky.py` :

```diff
-    async def test_manual_review_required_is_overwritten_by_existing_position_adoption(
+    async def test_manual_review_required_survives_existing_position_adoption(
...
-        self.assertEqual(self._setup_status(), SetupStatus.IN_POSITION.value)
+        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)

-    async def test_error_requires_manual_review_is_overwritten_by_attach_missing_stop(
+    async def test_error_requires_manual_review_survives_attach_missing_stop(
...
-        self.assertEqual(self._setup_status(), SetupStatus.ENTRY_ORDER_PLACED.value)
+        self.assertEqual(
+            self._setup_status(), SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value
+        )

-    async def test_manual_review_required_is_overwritten_by_attach_missing_stop(self) -> None:
+    async def test_manual_review_required_survives_attach_missing_stop(self) -> None:
...
-        self.assertEqual(self._setup_status(), SetupStatus.ENTRY_ORDER_PLACED.value)
+        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)

-    def test_error_requires_manual_review_is_overwritten_by_record_fill(self) -> None:
+    def test_error_requires_manual_review_survives_record_fill(self) -> None:
...
-        self.assertEqual(self._setup_status(), SetupStatus.ENTRY_FILLED.value)
+        self.assertEqual(
+            self._setup_status(), SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value
+        )

-    def test_manual_review_required_is_overwritten_by_record_fill(self) -> None:
+    def test_manual_review_required_survives_record_fill(self) -> None:
...
-        self.assertEqual(self._setup_status(), SetupStatus.ENTRY_FILLED.value)
+        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)

-    def test_error_requires_manual_review_is_overwritten_by_mark_in_position(self) -> None:
+    def test_error_requires_manual_review_survives_mark_in_position(self) -> None:
...
-        self.assertEqual(self._setup_status(), SetupStatus.IN_POSITION.value)
+        self.assertEqual(
+            self._setup_status(), SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value
+        )

-    def test_manual_review_required_is_overwritten_by_mark_in_position(self) -> None:
+    def test_manual_review_required_survives_mark_in_position(self) -> None:
...
-        self.assertEqual(self._setup_status(), SetupStatus.IN_POSITION.value)
+        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)

-    async def test_error_requires_manual_review_is_overwritten_by_place_stop_order(
+    async def test_error_requires_manual_review_survives_place_stop_order(
...
-        self.assertEqual(self._setup_status(), SetupStatus.STOP_ORDER_PLACED.value)
+        self.assertEqual(
+            self._setup_status(), SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value
+        )

-    async def test_manual_review_required_is_overwritten_by_place_stop_order(self) -> None:
+    async def test_manual_review_required_survives_place_stop_order(self) -> None:
...
-        self.assertEqual(self._setup_status(), SetupStatus.STOP_ORDER_PLACED.value)
+        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)

-    async def test_error_requires_manual_review_is_overwritten_by_full_cascade(self) -> None:
+    async def test_error_requires_manual_review_survives_full_cascade(self) -> None:
...
-        self.assertEqual(self._setup_status(), SetupStatus.IN_POSITION.value)
+        self.assertEqual(
+            self._setup_status(), SetupStatus.ERROR_REQUIRES_MANUAL_REVIEW.value
+        )

-    async def test_manual_review_required_is_overwritten_by_full_cascade(self) -> None:
+    async def test_manual_review_required_survives_full_cascade(self) -> None:
...
-        self.assertEqual(self._setup_status(), SetupStatus.IN_POSITION.value)
+        self.assertEqual(self._setup_status(), SetupStatus.MANUAL_REVIEW_REQUIRED.value)
```

Aucune fixture (`setUp`/`asyncSetUp`, brokers de test, configs) n'a été
modifiée — seuls les noms de test, docstrings et assertions ont changé.

Verdict : **PASS** (tous les tests ci-dessus passent avec les nouvelles
assertions, voir §5.6/§5.7).

### 5.2 — Test direct de la garde (MRR et ERMR → IN_POSITION, sans flag)

`CentralReviewGuardDirectTests.test_guard_blocks_manual_review_required_to_in_position_and_logs`
et `test_guard_blocks_error_requires_manual_review_to_in_position_and_logs`
(nouveau, dans `tests/test_review_status_sticky.py`) :

```
$ python -m pytest tests/test_review_status_sticky.py -v -k CentralReviewGuardDirectTests
tests/test_review_status_sticky.py::CentralReviewGuardDirectTests::test_allow_from_review_escapes_the_block PASSED
tests/test_review_status_sticky.py::CentralReviewGuardDirectTests::test_guard_blocks_error_requires_manual_review_to_in_position_and_logs PASSED
tests/test_review_status_sticky.py::CentralReviewGuardDirectTests::test_guard_blocks_manual_review_required_to_in_position_and_logs PASSED
tests/test_review_status_sticky.py::CentralReviewGuardDirectTests::test_non_alarm_active_write_is_unaffected PASSED
4 passed
```

Chaque test pose l'alarme, appelle `update_setup_status(..., IN_POSITION)`
sans `allow_from_review`, capture les logs (`assertLogs`) et vérifie à la
fois que le statut reste l'alarme d'origine et qu'un `logger.warning`
contenant "Blocked write" a été émis.

Verdict : **PASS**.

### 5.3 — Test de non-régression (non-alarme → actif)

`CentralReviewGuardDirectTests.test_non_alarm_active_write_is_unaffected` :
pose `ENTRY_ORDER_PLACED`, écrit `IN_POSITION` sans flag → statut devient
`IN_POSITION`, comportement identique à avant la garde (voir sortie ci-dessus).

Verdict : **PASS**.

### 5.4 — Test du flag `allow_from_review=True`

`CentralReviewGuardDirectTests.test_allow_from_review_escapes_the_block` :
pose `MANUAL_REVIEW_REQUIRED`, écrit `IN_POSITION` avec
`allow_from_review=True` → statut devient `IN_POSITION` (voir sortie
ci-dessus). Prouve que l'échappatoire fonctionne, alors qu'aucun appelant
ne l'utilise dans ce lot (conforme à §3(d)).

Verdict : **PASS**.

### 5.5 — Cliquet `upsert_setup` (audit 43 Q1)

`UpsertSetupConfigSaveRatchetTests.test_config_save_does_not_overwrite_manual_review_required` :

```
$ python -m pytest tests/test_review_status_sticky.py -v -k UpsertSetupConfigSaveRatchetTests
tests/test_review_status_sticky.py::UpsertSetupConfigSaveRatchetTests::test_config_save_does_not_overwrite_manual_review_required PASSED
1 passed
```

Pose le setup en `MANUAL_REVIEW_REQUIRED`, appelle
`SetupEngine.create_or_update_from_config` avec une config éditée
(`risk.max_risk_usd` 15→20), vérifie que la validation reste valide et que
le statut reste `MANUAL_REVIEW_REQUIRED`. La docstring de la classe
documente explicitement qu'elle protège contre une régression future de
`_status_after_config_save` (qui aujourd'hui échoue en `DISABLED` pour un
setup neuf ou recopie le statut existant, jamais ne calcule un nouveau
statut actif — audit 43 Q1).

Verdict : **PASS**.

### 5.6 — Cliquets textuels S2 et S5b-2

```
$ python -m pytest tests/test_active_status_write_sites.py tests/test_in_position_write_sites.py -v
tests/test_active_status_write_sites.py::ActiveStatusWriteSiteRatchetTests::test_only_known_sites_write_an_active_status PASSED
tests/test_in_position_write_sites.py::InPositionWriteSiteRatchetTests::test_only_known_sites_write_in_position PASSED
2 passed
```

Ces deux cliquets sont de purs scans textuels sur `app/` : comme aucun
fichier de `app/engine/*` n'a été modifié, ils passent sans changement.

Verdict : **PASS**.

### 5.7 — Suite complète

```
$ python -m pytest -q
.....F....................................... [  6%]
........................................................................... [ 18%]
...
1 failed, 737 passed, 4 warnings, 134 subtests passed in 280.46s
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
```

Seul échec : `test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty`
(`today_pnl` attendu `2.5`, obtenu `None`) — sans rapport avec ce lot
(`repositories.py`/`update_setup_status` n'intervient pas dans ce chemin ;
la classe teste `TradingEngine.snapshot()`/comptes broker). Conforme à
l'attente de l'ordre (§5.7 : "seul test_account_metrics.py en échec").

Verdict : **PASS**.

## 6. Nettoyage (obligatoire)

- Aucun fichier temporaire, leurre ou script jetable laissé dans le dépôt :
  un seul fichier de travail a été créé pendant la rédaction de ce rapport,
  `tmp/s5b3a_test_diff.txt` (diff brut extrait pour rédiger §5.1) — `tmp/`
  est déjà ignoré par le dépôt (présent en `??` dans `git status` avant ce
  lot, contenu non versionné) ; ce fichier n'est ni ajouté ni committé.
- Un incident a eu lieu pendant ce lot : en tentant d'obtenir un compte de
  tests "avant" pour comparaison, j'ai exécuté `git stash` (qui a empilé les
  deux suppressions préexistantes non committées
  `data/setups/CODI_20260628_001.json`/`TXN_20260630_001.json`, présentes
  avant le début de ce lot) suivi d'un `git checkout feat/setup-conditions -- .`
  qui a écrasé la copie de travail de mes fichiers commités
  (`repositories.py`, `tests/test_review_status_sticky.py`) avec la version
  parente. Corrigé immédiatement : `git checkout HEAD -- <les 2 fichiers>`
  a restauré l'état commité (`git diff HEAD` vide sur ces fichiers après
  coup), puis `git stash pop` a restauré les deux suppressions préexistantes
  exactement comme elles étaient (`git status --short` identique à l'état de
  début de session). Aucune perte : le commit `5f31f85` n'a jamais été
  altéré, seule la copie de travail a été temporairement désynchronisée puis
  resynchronisée. Je n'ai pas retenté d'obtenir le compte "avant" par cette
  méthode ensuite.
- Aucun stash résiduel créé par ce lot (`git stash list` ne montre plus que
  le stash préexistant `stash@{0}: On feat/setup-conditions-ui: ...`, non
  créé par ce lot, non touché).
- Aucune branche ni worktree supplémentaire créée au-delà de
  `fix/s5b3a-central-review-guard` (demandée par l'ordre).
- `git status --short` après ce lot (hors fichiers déjà présents en début de
  session, énumérés en §2) : uniquement les fichiers listés dans le diff
  §2, déjà committés.

## 7. Suite de tests

Compte avant → après pour `tests/test_review_status_sticky.py` :

```
$ git show feat/setup-conditions:tests/test_review_status_sticky.py | grep -c "    async def test_\|    def test_"
19
$ python -m pytest tests/test_review_status_sticky.py --collect-only -q | tail -1
24 tests collected in 0.16s
```

19 → 24 : +5, cohérent avec les tests ajoutés (4 dans
`CentralReviewGuardDirectTests` §5.2/5.3/5.4, 1 dans
`UpsertSetupConfigSaveRatchetTests` §5.5) ; les 11 autres tests touchés en
§5.1 sont des inversions sur place (même nombre, assertions/noms changés),
pas des ajouts.

```
$ python -m pytest tests/test_review_status_sticky.py -q
........................
24 passed in 9.98s
```

Suite complète : voir §5.7, `1 failed, 737 passed` (pré-existant, hors
lot).

## 8. Découvert mais NON corrigé

**4e chemin bloqué par la garde, anticipé par l'ordre §6** :
`OrderManager.attach_missing_stop` (`order_manager.py:466`) appelle
`self.repository.update_setup_status(setup["setup_id"], ENTRY_ORDER_PLACED, ...)`
sans `allow_from_review`. La garde centrale, n'ayant aucun moyen de
distinguer ce site des trois chemins visés par ce lot (elle ne lit que
`(setup_id, statut_courant, statut_cible, allow_from_review)`, jamais
l'appelant), bloque désormais aussi cette écriture quand le setup est en
alarme au moment de l'appel. Ce n'est pas une correction délibérée de
`attach_missing_stop` — aucune ligne de `order_manager.py` n'a été modifiée
— c'est un effet de bord inévitable du caractère central de la garde,
exactement le scénario que l'ordre demandait de signaler sans corriger
(§6). Point à trancher en S5b-3b : `attach_missing_stop` est une sortie de
réparation potentiellement légitime (audit 42/43) ; s'il doit rester
utilisable depuis une alarme, il devra passer explicitement
`allow_from_review=True` à cet appel précis. Les deux tests de
`AttachMissingStopReviewStickyTests` ont été inversés en conséquence
(voir §4/§5.1) puisqu'ils décrivent maintenant le comportement réel du
code, pas un choix de conception de ce lot.

Aucun autre chemin d'effacement supplémentaire découvert.

## 9. Écarts par rapport à l'ordre

Aucun écart sur le périmètre, le changement de code, les invariants ou
l'interdiction de toucher `app/engine/*`.

Un écart mineur assumé sur la portée exacte du §5.1 : l'ordre nomme
littéralement deux des trois chemins fautifs du §1 comme exemples de tests
à inverser ("cascade simulate_fill → IN_POSITION ; adoption → IN_POSITION
pour MRR") ; j'ai également inversé les deux tests de
`AttachMissingStopReviewStickyTests`, non nommés dans cette liste, parce
que — comme expliqué en §4 et §8 — la garde centrale les affecte aussi et
les laisser dans leur état "GAP" d'origine aurait fait mentir la suite de
tests sur le comportement réel du code après ce lot. Je considère cet écart
couvert par l'esprit du §5.1 ("les tests ... qui prouvaient AUJOURD'HUI
l'effacement ... doivent maintenant prouver la SURVIE") et par
l'anticipation explicite du §6, mais le signale ici pour transparence
puisque ce n'était pas une inversion explicitement demandée nommément.
