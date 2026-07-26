# Clôture S5b-3a+3b — merge groupé + push (git uniquement, aucun code)

Mode : opérations git strictement décrites par l'ordre. Aucun fichier de
`app/` ou `tests/` modifié par cette clôture. Date : 2026-07-26.

---

## C1 — 3b bien empilée sur 3a

```
$ git log --oneline fix/s5b3b-attach-stop-repair -3
8d92fd8 docs(audit): rapport de lot S5b-3b — réparation légitime de stop, protection prouvée
fa4dfdd fix(order-manager): allow stop-repair to clear review alarm only when stop is active
9ed1d8b docs(audit): rapport de lot S5b-3a — garde centrale anti-effacement d'alarme
```

Écart de forme, pas de fond : la fenêtre `-3` remonte au commit de rapport
S5b-3b (`8d92fd8`) et au commit de rapport S5b-3a (`9ed1d8b`), tous deux
ajoutés après la rédaction de l'ordre — `5f31f85` et `f836479` ne sont donc
pas visibles dans cette fenêtre de 3 lignes. Vérification étendue :

```
$ git log --oneline fix/s5b3b-attach-stop-repair -6
8d92fd8 docs(audit): rapport de lot S5b-3b — réparation légitime de stop, protection prouvée
fa4dfdd fix(order-manager): allow stop-repair to clear review alarm only when stop is active
9ed1d8b docs(audit): rapport de lot S5b-3a — garde centrale anti-effacement d'alarme
5f31f85 fix(repository): block active-status writes over review alarms centrally
f836479 docs(audit): clôture S5b-2 — merge/push fix/s5b2-review-sticky-ratchet
b99f29b docs(audit): rapport de lot S5b-2 — cliquet + preuve comportementale du collant des alarmes
```

`fa4dfdd` (3b) est bien au-dessus de `5f31f85` (3a), lui-même au-dessus de
`f836479`. Empilement confirmé.

**Verdict : PASS.**

---

## C2 — Merge groupé (fast-forward)

```
$ git checkout feat/setup-conditions
D       data/setups/CODI_20260628_001.json
D       data/setups/TXN_20260630_001.json
Your branch is up to date with 'origin/feat/setup-conditions'.
Switched to branch 'feat/setup-conditions'

$ git merge --ff-only fix/s5b3b-attach-stop-repair
Updating f836479..8d92fd8
Fast-forward
 app/engine/order_manager.py             |  28 ++-
 app/storage/repositories.py             |  41 +++
 audit/44_rapport_s5b3a.md               | 425 ++++++++++++++++++++++++++++++++
 audit/46_rapport_s5b3b.md               | 363 +++++++++++++++++++++++++++
 audit/ORDRE_S5b3a.md                    | 103 ++++++++
 audit/ORDRE_S5b3b.md                    |  89 +++++++
 tests/test_active_status_write_sites.py |  19 +-
 tests/test_review_status_sticky.py      | 315 +++++++++++++++++++----
 8 files changed, 1319 insertions(+), 64 deletions(-)
 create mode 100644 audit/44_rapport_s5b3a.md
 create mode 100644 audit/46_rapport_s5b3b.md
 create mode 100644 audit/ORDRE_S5b3a.md
 create mode 100644 audit/ORDRE_S5b3b.md
```

`--ff-only` a réussi (pas d'échec, donc pas d'arrêt nécessaire) : les deux
lignes `D data/setups/...` affichées par `git checkout` sont les
suppressions non committées préexistantes (antérieures à ce lot, présentes
dans l'arbre de travail avant même S5b-3a) — elles suivent le changement de
branche normalement, aucune n'a été perdue ni créée par cette clôture.

**Verdict : PASS.**

---

## C3 — Les deux commits dans `feat/setup-conditions`

```
$ git log --oneline feat/setup-conditions | grep -E "5f31f85|fa4dfdd"
fa4dfdd fix(order-manager): allow stop-repair to clear review alarm only when stop is active
5f31f85 fix(repository): block active-status writes over review alarms centrally
```

**Verdict : PASS.**

---

## C4 — Push

```
$ git push origin feat/setup-conditions
To https://github.com/ahmedjaouadi/setup-order.git
   f836479..8d92fd8  feat/setup-conditions -> feat/setup-conditions

$ git push -u origin fix/s5b3a-central-review-guard
remote: Create a pull request for 'fix/s5b3a-central-review-guard' on GitHub by visiting:
remote:      https://github.com/ahmedjaouadi/setup-order/pull/new/fix/s5b3a-central-review-guard
branch 'fix/s5b3a-central-review-guard' set up to track 'origin/fix/s5b3a-central-review-guard'.
To https://github.com/ahmedjaouadi/setup-order.git
 * [new branch]      fix/s5b3a-central-review-guard -> fix/s5b3a-central-review-guard

$ git push -u origin fix/s5b3b-attach-stop-repair
remote: Create a pull request for 'fix/s5b3b-attach-stop-repair' on GitHub by visiting:
remote:      https://github.com/ahmedjaouadi/setup-order/pull/new/fix/s5b3b-attach-stop-repair
branch 'fix/s5b3b-attach-stop-repair' set up to track 'origin/fix/s5b3b-attach-stop-repair'.
To https://github.com/ahmedjaouadi/setup-order.git
 * [new branch]      fix/s5b3b-attach-stop-repair -> fix/s5b3b-attach-stop-repair
```

Trois push réussis, aucune erreur.

**Verdict : PASS.**

---

## C5 — Les deux commits dans `origin/feat/setup-conditions`

```
$ git fetch origin --quiet
$ git log --oneline origin/feat/setup-conditions | grep -E "5f31f85|fa4dfdd"
fa4dfdd fix(order-manager): allow stop-repair to clear review alarm only when stop is active
5f31f85 fix(repository): block active-status writes over review alarms centrally
```

**Verdict : PASS.**

---

## C6 — Rien en attente

```
$ git log --oneline origin/feat/setup-conditions..feat/setup-conditions
(vide)
```

**Verdict : PASS.**

---

## C7 — `app/` et `tests/` propres

```
$ git status --short -- app/ tests/
(vide)
```

**Verdict : PASS.**

---

## C8 — Vérification de sécurité : `5f31f85` seul ne doit jamais être déployé

`fix/s5b3a-central-review-guard` (`5f31f85`) et
`fix/s5b3b-attach-stop-repair` (`fa4dfdd`) ont été mergés dans
`feat/setup-conditions` en un seul `git merge --ff-only`, donc en un seul
mouvement de pointeur de branche (C2 : `Updating f836479..8d92fd8`,
`Fast-forward`) — `feat/setup-conditions` n'est jamais passée par un état
intermédiaire où seul `5f31f85` était son sommet ; `git log` sur cette
branche montre directement l'historique complet f836479 → ... → 5f31f85 →
... → fa4dfdd → 8d92fd8, sans qu'aucun push ni déploiement n'ait pu cibler
`5f31f85` seul depuis `feat/setup-conditions`.

**Ceci ne protège que le chemin `feat/setup-conditions`.** Le commit
`5f31f85` reste individuellement atteignable par quiconque checkoute son
hash directement, ou pointe une branche/un déploiement dessus — c'est un
état réel de l'historique git, pas quelque chose qu'un merge peut effacer.

**Mise en garde à consigner (demandée par l'ordre) : `5f31f85`
(`fix(repository): block active-status writes over review alarms
centrally`) ne doit JAMAIS être déployé seul.** Pris isolément, ce commit
pose la garde centrale S5b-3a (`update_setup_status` bloque toute écriture
d'un statut actif par-dessus une alarme sauf `allow_from_review=True`)
SANS son exception légitime S5b-3b (`fa4dfdd`, qui donne à
`attach_missing_stop` le moyen de passer ce flag quand le stop réparé est
prouvé actif). Dans cet état intermédiaire, `attach_missing_stop` —
le chemin de réparation de sécurité pour un stop manquant — se retrouve
bloqué dès qu'un setup est en alarme, sans aucune échappatoire (voir
`audit/44_rapport_s5b3a.md` §8 : "4e chemin bloqué par la garde, anticipé
par l'ordre §6"). `5f31f85` est un commit intermédiaire d'une paire
indissociable ; la seule paire valide à déployer ensemble, ou à ne pas
déployer du tout, est `5f31f85` + `fa4dfdd`.

**Verdict : PASS (vérification effectuée, mise en garde consignée
ci-dessus).**

---

## Synthèse

| Point | Vérification | Verdict |
|---|---|---|
| C1 | 3b empilée sur 3a sur `fix/s5b3b-attach-stop-repair` | PASS |
| C2 | Merge `--ff-only` de la paire dans `feat/setup-conditions` | PASS |
| C3 | Les deux commits présents dans `feat/setup-conditions` (local) | PASS |
| C4 | Push des 3 branches vers `origin` | PASS |
| C5 | Les deux commits présents dans `origin/feat/setup-conditions` | PASS |
| C6 | Rien en attente entre local et origin | PASS |
| C7 | `app/` et `tests/` propres | PASS |
| C8 | Sécurité du merge groupé + mise en garde `5f31f85` seul | PASS |

Aucune suppression effectuée. Aucun fichier de `app/` ou `tests/` touché
par cette clôture.
