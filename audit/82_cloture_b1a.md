# Clôture B-1a — fusion et push vérifiés

Contexte : merge de `fix/b1a-persist-adopted-stop` dans `feat/setup-conditions`, puis push des deux branches vers `origin`. Aucun code modifié, aucune suppression effectuée.

## C1 — commit B-1a en tête de sa branche

Commande :
```
git log --oneline fix/b1a-persist-adopted-stop -2
```

Sortie brute :
```
d424bf4 docs(audit): rapport de lot B-1a — stop broker adopté persisté en ordre local
aeca6cf fix(reconciliation): persist adopted broker stop as local order (root B, B-1a prereq)
```

Constat : l'ordre attendait `aeca6cf` en tête ; en réalité la tête est `d424bf4` (rapport de lot B-1a committé après `aeca6cf`, cf. `audit/81_rapport_b1a.md`). `aeca6cf` est bien présent, un cran plus bas. Écart signalé, non bloquant (le commit code recherché est présent et le rapport de lot est une conséquence normale du process).

## C2 — fusion ff-only

Commande :
```
git checkout feat/setup-conditions &&
git merge --ff-only fix/b1a-persist-adopted-stop
```

Sortie brute :
```
D	data/setups/CODI_20260628_001.json
D	data/setups/TXN_20260630_001.json
Your branch is up to date with 'origin/feat/setup-conditions'.
Switched to branch 'feat/setup-conditions'
Updating a2521f6..d424bf4
Fast-forward
 app/engine/reconciliation.py              |  66 +++++-
 audit/81_rapport_b1a.md                   | 322 ++++++++++++++++++++++++++++++
 audit/ORDRE_B1a.md                        | 100 ++++++++++
 tests/test_reconciliation_adopted_stop.py | 268 +++++++++++++++++++++++++
 4 files changed, 755 insertions(+), 1 deletion(-)
 create mode 100644 audit/81_rapport_b1a.md
 create mode 100644 audit/ORDRE_B1a.md
 create mode 100644 tests/test_reconciliation_adopted_stop.py
```

Constat : fast-forward pur, pas de conflit. Les deux lignes `D data/setups/...` sont un état de suppression préexistant dans l'arbre de travail (visible dans le git status de session, sans rapport avec le merge) — non touché par cette opération.

## C3 — présence de aeca6cf dans feat/setup-conditions

Commande :
```
git log --oneline feat/setup-conditions | grep aeca6cf
```

Sortie brute :
```
aeca6cf fix(reconciliation): persist adopted broker stop as local order (root B, B-1a prereq)
```

## C4 — push

Commande :
```
git push origin feat/setup-conditions &&
git push -u origin fix/b1a-persist-adopted-stop
```

Sortie brute :
```
To https://github.com/ahmedjaouadi/setup-order.git
   a2521f6..d424bf4  feat/setup-conditions -> feat/setup-conditions
remote:
remote: Create a pull request for 'fix/b1a-persist-adopted-stop' on GitHub by visiting:
remote:      https://github.com/ahmedjaouadi/setup-order/pull/new/fix/b1a-persist-adopted-stop
remote:
branch 'fix/b1a-persist-adopted-stop' set up to track 'origin/fix/b1a-persist-adopted-stop'.
To https://github.com/ahmedjaouadi/setup-order.git
 * [new branch]      fix/b1a-persist-adopted-stop -> fix/b1a-persist-adopted-stop
```

## C5 — présence de aeca6cf dans origin/feat/setup-conditions

Commande :
```
git fetch origin --quiet
git log --oneline origin/feat/setup-conditions | grep aeca6cf
```

Sortie brute :
```
aeca6cf fix(reconciliation): persist adopted broker stop as local order (root B, B-1a prereq)
```

## C6 — rien en attente

Commande :
```
git log --oneline origin/feat/setup-conditions..feat/setup-conditions
```

Sortie brute :
```
(vide)
```

## C7 — app/ et tests/ propres

Commande :
```
git status --short -- app/ tests/
```

Sortie brute :
```
(vide)
```

## Verdicts

| Point | Vérification | Verdict |
|---|---|---|
| C1 | commit B-1a en tête de sa branche | ÉCART (tête réelle = `d424bf4`, rapport de lot ; `aeca6cf` présent un cran dessous) |
| C2 | fusion ff-only vers feat/setup-conditions | OK |
| C3 | aeca6cf présent dans feat/setup-conditions | OK |
| C4 | push des deux branches vers origin | OK |
| C5 | aeca6cf présent dans origin/feat/setup-conditions | OK |
| C6 | rien en attente (local == origin) | OK |
| C7 | app/ et tests/ propres | OK |
