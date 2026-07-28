# Clôture A-3 — git uniquement

Hash réel A-3 (tête de `fix/a3-detect-orphan-entry`) : `2a31d2c`

## C1 — commit A-3 en tête de sa branche

Commande :
```
git log --oneline fix/a3-detect-orphan-entry -2
```
Sortie :
```
2a31d2c docs(audit): rapport de lot A-3 — détection orpheline au démarrage prouvée
5565f9d feat(reconciliation): detect and flag unprotected entry orphans at startup (root A, S58.1)
```
Verdict : OK — `2a31d2c` est en tête.

## C2 — merger (fast-forward uniquement)

Commande :
```
git checkout feat/setup-conditions && git merge --ff-only fix/a3-detect-orphan-entry
```
Sortie :
```
D	data/setups/CODI_20260628_001.json
D	data/setups/TXN_20260630_001.json
Your branch is up to date with 'origin/feat/setup-conditions'.
Already on 'feat/setup-conditions'
Already up to date.
```
Verdict : OK — ff-only n'a pas échoué (branche déjà à jour, A-3 déjà intégré).

## C3 — commit A-3 présent dans feat/setup-conditions

Commande :
```
git log --oneline feat/setup-conditions | grep 2a31d2c
```
Sortie :
```
2a31d2c docs(audit): rapport de lot A-3 — détection orpheline au démarrage prouvée
```
Verdict : OK.

## C4 — pousser

Commande :
```
git push origin feat/setup-conditions && git push -u origin fix/a3-detect-orphan-entry
```
Sortie :
```
Everything up-to-date
branch 'fix/a3-detect-orphan-entry' set up to track 'origin/fix/a3-detect-orphan-entry'.
Everything up-to-date
```
Verdict : OK.

## C5 — présent dans origin

Commande :
```
git log --oneline origin/feat/setup-conditions | grep 2a31d2c
```
Sortie :
```
2a31d2c docs(audit): rapport de lot A-3 — détection orpheline au démarrage prouvée
```
Verdict : OK.

## C6 — rien en attente

Commande :
```
git log --oneline origin/feat/setup-conditions..feat/setup-conditions
```
Sortie :
```
(vide)
```
Verdict : OK.

## C7 — app/ et tests/ propres

Commande :
```
git status --short -- app/ tests/
```
Sortie :
```
(vide)
```
Verdict : OK.

## Note hors périmètre

`git status` (hors app/ et tests/) montre des fichiers non suivis (`.codex/`, `audit/`, `data/setups/*.json`, `tmp/`) et deux suppressions (`data/setups/CODI_20260628_001.json`, `data/setups/TXN_20260630_001.json`) déjà présentes avant cet ordre. Non touché, hors scope de cette clôture git-only.
