# Rapport de lot — S2

## 1. Identification
- Lot / ordre de travail : S2 — ajout de `ENTRY_FILLED → IN_POSITION` à
  `ALLOWED_TRANSITIONS` (suite du pré-audit `audit/28_pre_s2.md`,
  recommandation B). L'ordre lui-même a été transmis en chat, pas commité
  dans un fichier ; ce rapport ne peut donc confronter le contenu du lot
  qu'aux traces disponibles (pré-audit 28, commentaires du code et des
  tests, et les 3 points de vérification explicitement redemandés).
- Branche : `fix/s2-table-in-position`  | Commit : `3605232dc5b4c8ec753c69ee041ce21a10f2a3ed`
- Basée sur : `fix/s2-table-in-position` @ `5f47dbf` (« docs(audit): update C6 verdict to PASS after audit/ push »)
- Mergée : non  | Poussée : non (absente de tout remote — `git branch --contains 3605232` ne liste que la branche locale)

## 2. Fichiers touchés

Commande : `git diff --stat 5f47dbf..3605232`

```
 app/engine/state_machine.py           |  6 +++
 tests/test_in_position_write_sites.py | 83 +++++++++++++++++++++++++++++++++++
 tests/test_state_machine.py           | 20 +++++++++
 3 files changed, 109 insertions(+)
```

Conforme au périmètre attendu : un seul fichier de production
(`app/engine/state_machine.py`), deux fichiers de tests. Aucun fichier hors
`app/`/`tests/` touché, aucun fichier de données ou de config modifié.

## 3. Diff du code de production

Commande : `git show 3605232 -- app/engine/state_machine.py`

```diff
diff --git a/app/engine/state_machine.py b/app/engine/state_machine.py
index 6abed88..563a08a 100644
--- a/app/engine/state_machine.py
+++ b/app/engine/state_machine.py
@@ -145,6 +145,12 @@ ALLOWED_TRANSITIONS: dict[SetupStatus, set[SetupStatus]] = {
     SetupStatus.ENTRY_FILLED: {
         SetupStatus.STOP_ORDER_PLACED,
         SetupStatus.STOP_PLACED,
+        # On the real (bracket) path the protective stop is transmitted
+        # together with the entry, so it is already active by the time the
+        # fill lands: there is no post-fill moment where STOP_ORDER_PLACED
+        # applies. That status only describes the simulated path, where the
+        # stop is placed after the fill.
+        SetupStatus.IN_POSITION,
         SetupStatus.ERROR,
         SetupStatus.MANUAL_REVIEW_REQUIRED,
     },
```

6 lignes ajoutées, aucune ligne supprimée ni modifiée ailleurs dans le
fichier.

## 4. Décisions prises

L'ordre initial n'étant pas disponible sous forme écrite pour ce rapport
(cf. §1), seules les décisions visibles dans le diff/tests peuvent être
recensées :
- Algorithme du test-cliquet (`test_in_position_write_sites.py`) : scan de
  tout `app/**/*.py` par regex + comptage de parenthèses pour isoler chaque
  appel `update_setup_status(...)` contenant `"IN_POSITION"`, plutôt qu'une
  liste blanche figée de numéros de ligne — rend le test robuste aux
  déplacements de code à l'intérieur des fichiers déjà listés.
- Formulation exacte des deux justifications dans `ALLOWED_WRITE_SITES`
  (texte libre, voir §5.2).

## 5. Preuves de sortie

### 5.1 Nettoyage du leurre `_ratchet_decoy.py`

Commande : `git show --stat 3605232`

```
 app/engine/state_machine.py           |  6 +++
 tests/test_in_position_write_sites.py | 83 +++++++++++++++++++++++++++++++++++
 tests/test_state_machine.py           | 20 +++++++++
 3 files changed, 109 insertions(+)
```
→ Aucun fichier `_ratchet_decoy.py` dans le commit.

Commande : `ls app/engine/ | grep ratchet` → aucune sortie (exit code 1,
aucune correspondance).

Commande : `git status --short` → aucune ligne concernant `ratchet` ou
`_ratchet_decoy`.

Recherche complémentaire (historique complet, pas seulement l'index) :
`git log --all --oneline -- app/engine/_ratchet_decoy.py` → aucune sortie ;
`git ls-files | grep -i ratchet` → aucune sortie.

**Verdict : PASS.** Le fichier leurre n'existe ni sur disque, ni dans
l'index, ni dans aucun commit de l'historique (`--all`).

### 5.2 Liste d'exceptions du cliquet

Fichier : `tests/test_in_position_write_sites.py:16-27`

```python
ALLOWED_WRITE_SITES: dict[str, str] = {
    "app/engine/post_fill_progression.py": (
        "mark_in_position(): the only writer gated on protection_verified=True, "
        "shared by both the real bracket path (reconciliation.py) and the "
        "simulated path (fill_executor.py)."
    ),
    "app/engine/reconciliation.py": (
        "Existing IBKR position adoption: RECONCILING_EXISTING_POSITION -> "
        "IN_POSITION for a position discovered already open at startup. A "
        "distinct transition from the post-fill path this ratchet guards."
    ),
}
```

Deux sites whitelistés, chacun avec une justification en commentaire :
- `app/engine/post_fill_progression.py` — seul writer réel, gardé par
  `protection_verified=True`.
- `app/engine/reconciliation.py` — **oui, le chemin d'adoption d'une
  position IBKR préexistante en fait partie** : justifié comme une
  transition distincte (`RECONCILING_EXISTING_POSITION -> IN_POSITION`,
  position déjà ouverte détectée au démarrage), différente du chemin
  post-fill que le cliquet surveille.

Le test échoue (`assertFalse(unexpected, ...)`) si un site non listé écrit
`IN_POSITION`, et échoue aussi si un des deux sites listés disparaît
(`assertIn` en boucle sur `ALLOWED_WRITE_SITES`).

**Verdict : PASS.** Les deux sites sont listés en dur avec justification,
`reconciliation.py` y figure explicitement.

### 5.3 Commentaire dans la table

Fichier : `app/engine/state_machine.py:148-153` (voir diff §3) :

```python
# On the real (bracket) path the protective stop is transmitted
# together with the entry, so it is already active by the time the
# fill lands: there is no post-fill moment where STOP_ORDER_PLACED
# applies. That status only describes the simulated path, where the
# stop is placed after the fill.
SetupStatus.IN_POSITION,
```

**Verdict : PASS.** Le commentaire explique le pourquoi (chemin bracket :
stop déjà actif au fill, `STOP_ORDER_PLACED` ne s'applique qu'au chemin
simulé) et précède directement la cible `IN_POSITION` qu'il justifie.

## 6. Nettoyage (obligatoire)

- Aucun fichier temporaire, leurre ou script jetable créé pendant ce lot
  (voir §5.1 — recherche exhaustive disque + index + historique complet,
  aucune trace).
- Aucun stash créé/poppé pour ce lot.
- Aucune branche ni worktree créé/supprimé pour ce lot (audit en lecture
  seule, aucune commande git d'écriture exécutée hors la lecture).
- `git status --short` à la rédaction de ce rapport montre uniquement des
  éléments préexistants et sans rapport avec S2 (fichiers `data/setups/*`
  non suivis, `.codex/`, `tmp/`, `audit/28_pre_s2.md`, deux suppressions
  dans `data/setups/`) — aucun d'eux n'a été créé ni touché par le commit
  `3605232` ni par la rédaction de ce rapport.

## 7. Suite de tests

Commande : `python -m pytest -q` (suite complète, run après le commit S2) —
5 dernières lignes :

```
FAILED tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty
1 failed, 707 passed, 4 warnings, 98 subtests passed in 310.89s (0:05:10)
```

Comptage ciblé du lot (avant `5f47dbf` → après `3605232`, par comptage de
`def test_` dans les fichiers touchés, sans checkout du parent) :
- `tests/test_state_machine.py` : 6 → 8 tests (+2 : transition directe
  `ENTRY_FILLED → IN_POSITION`, et flux bracket réel bout-en-bout).
- `tests/test_in_position_write_sites.py` : fichier inexistant avant
  (`git show 5f47dbf:tests/test_in_position_write_sites.py` échoue) → 1
  test après.
- Total ajouté par le lot : 3 tests, cohérent avec les 109 lignes du diff
  §2 et les 3 verdicts PASS de §5.

Exécution ciblée des seuls fichiers touchés par le lot :
`python -m pytest -q tests/test_state_machine.py tests/test_in_position_write_sites.py`
→ `9 passed in 3.91s`.

## 8. Découvert mais NON corrigé

`tests/test_account_metrics.py::AccountMetricsTests::test_snapshot_uses_broker_positions_when_local_positions_are_empty`
échoue sur la suite complète. Ce fichier n'est touché ni par le diff S2
(§2) ni par aucune ligne du commit `3605232` : la panne est préexistante
et sans rapport avec ce lot. Non corrigée ici — lot en lecture seule,
signalée uniquement, hors périmètre de S2.

## 9. Écarts par rapport à l'ordre

Aucun sur les 3 points explicitement redemandés (leurre absent, liste
d'exceptions présente et justifiée pour `reconciliation.py` inclus,
commentaire de la table présent) — voir §5 pour le détail et les preuves.

Limite méthodologique de ce rapport (pas un écart du lot lui-même) : le
texte exact de l'ordre S2 n'étant pas disponible en fichier, la conformité
de §2 a été jugée par cohérence avec `audit/28_pre_s2.md` et les 3 points de
vérification demandés, pas par comparaison littérale à un ordre écrit.
