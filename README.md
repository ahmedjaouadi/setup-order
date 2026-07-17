# Setup Order

Plateforme locale de gestion de setups de trading basee sur `program.md`.

Cette V1 fournit:

- backend FastAPI avec API JSON et WebSocket;
- stockage SQLite local;
- chargement de setups YAML;
- validation de risque avant ordre;
- normalisation canonique des champs de setup avant validation;
- sauvegarde de setup distincte de l'armement runtime;
- connecteur broker paper par defaut;
- dashboard HTML pour setups, ordres, positions et logs;
- tests unitaires sur les regles de securite principales.

## Demarrage

Option simple sous Windows:

```text
Double-cliquer sur start.bat
```

Ou depuis PowerShell / CMD:

```bash
start.bat
```

Le lanceur choisit automatiquement un port disponible a partir de `8000` et ouvre le navigateur.

```bash
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Puis ouvrir `http://127.0.0.1:8000`.

## Tests

```bash
python -m unittest discover -s tests
```

## Forecasting optionnel

Les providers optionnels sont detectes dynamiquement dans le `.venv` de l'application et restent strictement hors execution d'ordres :

```powershell
./install-forecasting.ps1 -Tier p1
python scripts/check_forecasting_stack.py
```

Les tiers disponibles sont `p1` (Chronos, Darts, `naive_baseline`, `atr_baseline`), `p2` (NeuralForecast, AutoGluon-TimeSeries) et `p3` (Lag-Llama, Moirai/Uni2TS). Les providers prets s'activent automatiquement seulement si leurs dependances sont presentes dans le `.venv` de l'app.

Pour installer tout le stack de recherche d'un coup :

```powershell
python -m pip install -r requirements-forecasting.txt
```

Chronos utilise le modele `amazon/chronos-2`. Pour authentifier Hugging Face, copier `.env.example` vers `.env`, definir `HF_TOKEN`, puis garder `.env` local (il est ignore par Git). L'absence du token reste non bloquante; le cache local peut etre impose avec `local_files_only: true`.

Darts reste Model Lab/benchmark seulement. Les forecasts Chronos et les ensembles sont persistants et evaluables, mais portent toujours `execution_allowed: false`; ils enrichissent uniquement les scorecards, le `setup_quality_score` et la decision trace.

## Important

Le connecteur actif par defaut est `paper`. Le programme n'expose plus de mode utilisateur `simulation`: seuls `paper` et `live` sont autorises pour les setups et la configuration runtime. Un broker interne reste disponible uniquement pour les tests automatises.

Dans la liste et le detail d'un setup, `Armer setup` appelle `POST /api/setups/{setup_id}/arm` sans ecraser la configuration. `Desarmer setup` appelle `POST /api/setups/{setup_id}/disarm` pour repasser le statut runtime a `DISABLED`. Le bouton `Sauvegarder` reste reserve a la persistance du JSON edite.

Un nouveau setup sauvegarde ou importe demarre toujours en `DISABLED`: il n'est pas arme automatiquement. Un setup existant conserve son statut runtime lors d'une sauvegarde: s'il etait arme il reste arme, s'il etait desarme il reste `DISABLED`.

`Armer setup` valide que le setup est executable, puis remet son statut runtime initial (`WAITING_ACTIVATION`, `RECONCILING_EXISTING_POSITION`, etc.) pour que le moteur puisse le surveiller. Il ne cree pas d'ordre tout seul: l'ordre reste conditionne au signal marche, au risk engine, a la connexion broker et a l'order manager.

`Desarmer setup` est bloque si le setup a un ordre actif ou une position ouverte. Dans ce cas, utiliser `Auto OFF` pour empecher de nouveaux ordres sans couper la gestion en cours.

La page detail d'un setup affiche, juste avant `Forecast stack summary`, la section `Ce que cherche le setup`: la checklist ordonnee des conditions que le moteur verifie reellement avant d'entrer (statut par condition, valeur observee, cible attendue, timestamp de validation persiste), avec barre de progression, message de synthese et bandeaux `ready_to_enter`/`invalidated`. Le catalogue des setups et de leurs conditions est documente dans `docs/21-setup-conditions-catalog.md`; seuls les calculs reellement effectues par le moteur sont affiches.

Le panneau `Forecast stack summary` du detail setup charge seulement un forecast deja en cache a l'ouverture de la page. Le calcul lourd du forecast stack est lance uniquement via `Recalculer`, ce qui evite de ralentir la navigation dans les setups.
