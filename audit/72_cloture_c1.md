# Clôture C-1 — vérification git

Lot : C-1 (réparation des setups figés au démarrage, root C, S58.3)
Portée : git uniquement. Aucun code modifié, aucune suppression.

## C1 — commit C-1 en tête de sa branche

Commande :
```
git log --oneline fix/c1-repair-frozen-setups -2
```
Sortie :
```
0b89ef4 docs(audit): rapport de lot C-1 — réparation des setups figés au démarrage prouvée
effb6c6 fix(reconciliation): repair setups frozen by non-atomic fill/close at startup (root C, S58.3)
```
Constat : le commit en tête est `0b89ef4` (rapport d'audit ajouté après le fix), pas `effb6c6` comme l'attendait l'énoncé. `effb6c6` est bien présent, en 2ᵉ position. Écart mineur sans impact sur la suite (C3/C5 confirment sa présence après fusion).

## C2 — fusion ff-only

Commande :
```
git checkout feat/setup-conditions && git merge --ff-only fix/c1-repair-frozen-setups
```
Sortie :
```
D	data/setups/CODI_20260628_001.json
D	data/setups/TXN_20260630_001.json
Your branch is up to date with 'origin/feat/setup-conditions'.
Switched to branch 'feat/setup-conditions'
Updating 3caa911..0b89ef4
Fast-forward
 app/engine/reconciliation.py |  79 ++++++++++++
 audit/71_rapport_c1.md       | 289 +++++++++++++++++++++++++++++++++++++++++++
 audit/ORDRE_C1.md            | 106 ++++++++++++++++
 tests/test_reconciliation.py | 173 ++++++++++++++++++++++++++
 4 files changed, 647 insertions(+)
 create mode 100644 audit/71_rapport_c1.md
 create mode 100644 audit/ORDRE_C1.md
```
Constat : fusion fast-forward réussie, `3caa911..0b89ef4`. Les deux lignes `D data/setups/...` proviennent de l'état non indexé préexistant du dépôt (fichiers de données runtime supprimés localement, hors périmètre de la fusion), pas de la fusion elle-même.

## C3 — commit présent dans feat/setup-conditions

Commande :
```
git log --oneline feat/setup-conditions | grep effb6c6
```
Sortie :
```
effb6c6 fix(reconciliation): repair setups frozen by non-atomic fill/close at startup (root C, S58.3)
```

## C4 — push

Commande :
```
git push origin feat/setup-conditions && git push -u origin fix/c1-repair-frozen-setups
```
Sortie :
```
To https://github.com/ahmedjaouadi/setup-order.git
   3caa911..0b89ef4  feat/setup-conditions -> feat/setup-conditions
remote:
remote: Create a pull request for 'fix/c1-repair-frozen-setups' on GitHub by visiting:
remote:      https://github.com/ahmedjaouadi/setup-order/pull/new/fix/c1-repair-frozen-setups
remote:
branch 'fix/c1-repair-frozen-setups' set up to track 'origin/fix/c1-repair-frozen-setups'.
To https://github.com/ahmedjaouadi/setup-order.git
 * [new branch]      fix/c1-repair-frozen-setups -> fix/c1-repair-frozen-setups
```

## C5 — présent dans origin

Commande :
```
git log --oneline origin/feat/setup-conditions | grep effb6c6
```
Sortie :
```
effb6c6 fix(reconciliation): repair setups frozen by non-atomic fill/close at startup (root C, S58.3)
```

## C6 — rien en attente

Commande :
```
git log --oneline origin/feat/setup-conditions..feat/setup-conditions
```
Sortie :
```
(vide)
```

## C7 — app/ et tests/ propres

Commande :
```
git status --short -- app/ tests/
```
Sortie :
```
(vide)
```

## Verdict global

Tous les points sont conformes hormis un écart mineur et sans conséquence sur C1 (voir constat). Fusion ff-only propre, commit poussé, aucune arborescence app/ ou tests/ modifiée par cette clôture.
