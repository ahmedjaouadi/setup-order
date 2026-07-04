# TODO — Chantiers qualité (à reprendre)

Suivi des améliorations d'ingénierie logicielle identifiées le 2026-07-03, mises à jour le 2026-07-04.

---

## 1. Figer les dépendances (reproductibilité)

**Problème :** `requirements.txt` et `requirements-forecasting*.txt` n'utilisaient que des contraintes `>=`. Deux installations à deux dates pouvaient donner des environnements différents → bugs impossibles à reproduire.

**Fait :**
- [x] `requirements.txt` épinglé en `==` à partir du `.venv` qui fonctionne (`pip freeze`).
- [x] `requirements-forecasting-p1.txt` épinglé pour les paquets réellement installés dans ce venv (`torch`, `pandas`, `huggingface-hub`, `chronos-forecasting`, `u8darts`).
- [x] Dépendances de dev séparées dans `requirements-dev.txt` (`ruff`, `black`, `mypy`).
- [x] CI (`.github/workflows/tests.yml`) installe bien ces versions figées (job `unit` installe `requirements.txt`, job `lint` installe `requirements.txt` + `requirements-dev.txt`).

**Reste à faire :**
- [ ] `timesfm[torch]` (p1) n'est pas installé dans le venv de référence → non épinglé. À pinner une fois installé/validé.
- [ ] `requirements-forecasting-p2.txt` (neuralforecast, autogluon.timeseries) et `requirements-forecasting-p3.txt` (gluonts, lag-llama, uni2ts) : aucun des deux n'est installé localement → non épinglés. Installer dans un venv dédié, valider, puis pinner.

---

## 2. Outillage qualité (lint / format / typage)

**Problème :** aucun outil de qualité n'était configuré. Les type hints existent (125/175 fichiers) mais rien ne les vérifiait.

**Fait :**
- [x] `pyproject.toml` créé avec la config de **ruff** (lint), **black** (formatage), **mypy** (typage).
- [x] Première passe effectuée :
  - `black` : 168 fichiers reformatés (mécanique, aucun changement sémantique).
  - `ruff` : 209 correctifs automatiques (imports triés, `datetime.now(timezone.utc)` → `datetime.now(UTC)`, etc.) + 6 variables mortes supprimées à la main + 1 enum simplifié en `StrEnum`.
  - `ruff` : réactivé à 100 % propre. Les règles `B904` (raise sans `from` dans un `except`), `B905` (`zip` sans `strict=`) et `B019` (`lru_cache` sur méthode d'instance) sont ignorées globalement (~60 sites au total) car les corriger demande un jugement au cas par cas, pas une réécriture mécanique.
  - `mypy` : 415 erreurs trouvées au premier passage sur un code jamais typé-vérifié. 1 corrigée (annotation manquante dans `routes_dashboard.py`), le reste (53 modules, listés dans `pyproject.toml` sous `[[tool.mypy.overrides]]`) mis en `ignore_errors = true` pour établir une base propre immédiatement exploitable en CI. **Ce n'est pas de la dette nouvelle : c'est la dette pré-existante rendue visible.**
- [x] CI (`.github/workflows/tests.yml`) : nouveau job `lint` qui lance `ruff check`, `black --check`, `mypy`.

**Reste à faire (dette de typage, à réduire module par module) :**
- [ ] Retirer les modules de la liste d'override dans `pyproject.toml` au fur et à mesure qu'ils sont corrigés (pas de nouvelle dette : tout nouveau module doit être clean dès l'écriture).
- [ ] Modules avec le plus d'erreurs à traiter en priorité : `app/broker/tws_connector.py`, `app/engine/trading_engine.py`, `app/opportunities/scanner.py`, `app/engine/reconciliation.py` (mélange int/str dans le dict `result`, cf. typage à préciser avec un `TypedDict`).
- [ ] (Optionnel) Ajouter `pre-commit` pour lancer ruff/black avant chaque commit.

---

## Idées plus tard (non prioritaires)
- [ ] Découper les fichiers « god object » (`broker/tws_connector.py` ~3160 lignes, `engine/trading_engine.py`, `intelligence/service.py`).
- [ ] Revoir les 78 `except Exception` larges (s'assurer qu'ils loggent et ne masquent pas de bugs) — probablement lié aux ~40 sites `B904` ignorés ci-dessus.
