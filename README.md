# Setup Order

Plateforme locale de gestion de setups de trading basee sur `program.md`.

Cette V1 fournit:

- backend FastAPI avec API JSON et WebSocket;
- stockage SQLite local;
- chargement de setups YAML;
- validation de risque avant ordre;
- connecteur broker simule par defaut;
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

## Important

Le connecteur actif par defaut est `simulated`. Aucun ordre reel n'est envoye a IBKR avec la configuration initiale. Le passage a `paper` ou `live` doit rester explicite dans `config.yaml` et dans un connecteur broker dedie.
