# TODO — Chantiers qualité (à reprendre)

Suivi des améliorations d'ingénierie logicielle identifiées le 2026-07-03.

---

## 1. Figer les dépendances (reproductibilité)

**Problème :** `requirements.txt` et `requirements-forecasting*.txt` n'utilisent que des contraintes `>=` (ex. `fastapi>=0.115`). Deux installations à deux dates peuvent donner des environnements différents → bugs impossibles à reproduire.

**À faire :**
- [ ] Épingler les versions exactes (`==`) à partir de l'environnement qui fonctionne (`pip freeze`).
- [ ] Séparer les dépendances runtime des dépendances de dev (tests, lint) si pertinent.
- [ ] Vérifier que la CI (`.github/workflows/tests.yml`) installe bien ces versions figées.

---

## 2. Outillage qualité (lint / format / typage)

**Problème :** aucun outil de qualité n'est configuré. Les type hints existent (125/175 fichiers) mais **rien ne les vérifie**.

**À faire :**
- [ ] Créer un `pyproject.toml` avec la config de :
  - [ ] **ruff** (lint)
  - [ ] **black** (formatage)
  - [ ] **mypy** (vérification de typage)
- [ ] Passer une première fois sur le code et corriger / ignorer les erreurs initiales.
- [ ] Ajouter ces vérifications à la CI (`.github/workflows/tests.yml`).
- [ ] (Optionnel) Ajouter `pre-commit` pour lancer ces outils avant chaque commit.

---

## Idées plus tard (non prioritaires)
- [ ] Découper les fichiers « god object » (`broker/tws_connector.py` ~3160 lignes, `engine/trading_engine.py`, `intelligence/service.py`).
- [ ] Revoir les 78 `except Exception` larges (s'assurer qu'ils loggent et ne masquent pas de bugs).
