# 65 — Clôture A-1 + A-1b

Branche de travail : `fix/a1b-closed-sticky-alarm`
Cible : `feat/setup-conditions`

## C1 — empilement correct

Commande :
```
git log --oneline fix/a1b-closed-sticky-alarm -4
```

Sortie brute :
```
682875d docs(audit): rapport de lot A-1b — alarme protégée contre l'écrasement par clôture
c7da0ab fix(repository): protect review alarms from being cleared by position close
54e51bd docs(audit): rapport de lot A-1 — clôture sur fill SELL, PnL circuit-breaker prouvé
42397b6 feat(reconciliation): close position on real SELL fill (root cause A, T1)
```

Verdict : conforme. c7da0ab (A-1b) est bien au-dessus de 42397b6 (A-1), lui-même au-dessus
de 6f7eae9 (hors fenêtre des -4 mais confirmé par l'historique antérieur). 682875d est le
rapport de lot A-1b, placé au sommet.

## C2 — merge de la paire (fast-forward)

Commandes :
```
git checkout feat/setup-conditions
git merge --ff-only fix/a1b-closed-sticky-alarm
```

Sortie brute :
```
D	data/setups/CODI_20260628_001.json
D	data/setups/TXN_20260630_001.json
Your branch is up to date with 'origin/feat/setup-conditions'.
Switched to branch 'feat/setup-conditions'
```
```
Updating 6f7eae9..682875d
Fast-forward
 app/engine/reconciliation.py            | 142 ++++++++++
 app/engine/trading_engine.py            |   1 +
 app/storage/repositories.py             |   4 +
 audit/63_rapport_a1.md                  | 458 ++++++++++++++++++++++++++++++++
 audit/64_rapport_a1b.md                 | 156 +++++++++++
 audit/ORDRE_A1.md                       | 110 ++++++++
 audit/ORDRE_A1b.md                      |  63 +++++
 tests/test_active_status_write_sites.py |   5 +
 tests/test_reconciliation.py            | 266 ++++++++++++++++++-
 tests/test_review_status_sticky.py      |  45 ++++
 10 files changed, 1248 insertions(+), 2 deletions(-)
 create mode 100644 audit/63_rapport_a1.md
 create mode 100644 audit/64_rapport_a1b.md
 create mode 100644 audit/ORDRE_A1.md
 create mode 100644 audit/ORDRE_A1b.md
```

Verdict : `--ff-only` a réussi (fast-forward pur, aucun commit de merge créé).
L'empilement était bien linéaire. Note : les suppressions de
`data/setups/CODI_20260628_001.json` et `data/setups/TXN_20260630_001.json`
sont un état pré-existant non lié à ce merge (working tree, non commité).

## C3 — les deux commits présents dans feat/setup-conditions

Commande :
```
git log --oneline feat/setup-conditions | grep -E "42397b6|c7da0ab"
```

Sortie brute :
```
c7da0ab fix(repository): protect review alarms from being cleared by position close
42397b6 feat(reconciliation): close position on real SELL fill (root cause A, T1)
```

Verdict : conforme, les deux commits sont présents.

## C4 — push

Commandes :
```
git push origin feat/setup-conditions
git push -u origin fix/a1-close-on-sell
git push -u origin fix/a1b-closed-sticky-alarm
```

Sortie brute :
```
To https://github.com/ahmedjaouadi/setup-order.git
   6f7eae9..682875d  feat/setup-conditions -> feat/setup-conditions
```
```
remote:
remote: Create a pull request for 'fix/a1-close-on-sell' on GitHub by visiting:
remote:      https://github.com/ahmedjaouadi/setup-order/pull/new/fix/a1-close-on-sell
remote:
branch 'fix/a1-close-on-sell' set up to track 'origin/fix/a1-close-on-sell'.
To https://github.com/ahmedjaouadi/setup-order.git
 * [new branch]      fix/a1-close-on-sell -> fix/a1-close-on-sell
```
```
remote:
remote: Create a pull request for 'fix/a1b-closed-sticky-alarm' on GitHub by visiting:
remote:      https://github.com/ahmedjaouadi/setup-order/pull/new/fix/a1b-closed-sticky-alarm
remote:
branch 'fix/a1b-closed-sticky-alarm' set up to track 'origin/fix/a1b-closed-sticky-alarm'.
To https://github.com/ahmedjaouadi/setup-order.git
 * [new branch]      fix/a1b-closed-sticky-alarm -> fix/a1b-closed-sticky-alarm
```

Verdict : les trois push ont réussi (confirmés avant exécution par l'utilisateur).

## C5 — les deux commits présents dans origin/feat/setup-conditions

Commande :
```
git log --oneline origin/feat/setup-conditions | grep -E "42397b6|c7da0ab"
```

Sortie brute :
```
c7da0ab fix(repository): protect review alarms from being cleared by position close
42397b6 feat(reconciliation): close position on real SELL fill (root cause A, T1)
```

Verdict : conforme.

## C6 — rien en attente

Commande :
```
git log --oneline origin/feat/setup-conditions..feat/setup-conditions
```

Sortie brute :
```
(vide)
```

Verdict : conforme, local et remote sont synchronisés.

## C7 — app/ et tests/ propres

Commande :
```
git status --short -- app/ tests/
```

Sortie brute :
```
(vide)
```

Verdict : conforme, aucune modification non commitée dans `app/` ou `tests/`.

## C8 — NOTE DE SÉCURITÉ

**42397b6 (A-1, seul) ne doit jamais être déployé isolément.**

Pris seul, A-1 (clôture sur fill SELL réel) peut écraser une alarme de revue
posée sur la position (review status) au moment de la clôture, car la
protection contre cet écrasement n'a été ajoutée que dans c7da0ab (A-1b).
Sans A-1b, une clôture peut donc effacer silencieusement une alarme active.

La paire **42397b6 + c7da0ab est indissociable** et doit toujours être
déployée/mergée/livrée ensemble — exactement comme la paire **5f31f85 + fa4dfdd**
l'était pour S5b-3.

Tout cherry-pick, rebase partiel, ou déploiement qui séparerait ces deux
commits réintroduirait le bug corrigé par A-1b.

## Résumé

| Point | Vérification | Verdict |
|-------|--------------|---------|
| C1 | Empilement c7da0ab / 42397b6 / 6f7eae9 | OK |
| C2 | Merge --ff-only (fast-forward pur) | OK |
| C3 | 42397b6 + c7da0ab dans feat/setup-conditions | OK |
| C4 | Push des 3 branches | OK |
| C5 | 42397b6 + c7da0ab dans origin/feat/setup-conditions | OK |
| C6 | local == remote (diff vide) | OK |
| C7 | app/ et tests/ propres | OK |
| C8 | Note de sécurité consignée (paire indissociable) | OK |
