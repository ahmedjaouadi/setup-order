# Guide de migration — Setup Order vers un nouveau PC Windows 11

Ce document explique **comment déplacer l'application sur une nouvelle machine Windows 11
sans mauvaise surprise**. Suivez les parties dans l'ordre. Les points marqués ⚠️ sont
ceux qui cassent le plus souvent une migration : lisez-les.

---

## 0. Résumé express (TL;DR)

1. Installer **Python 3.11.x** (pas 3.12/3.13), **Git**, et **IBKR TWS / IB Gateway**.
2. `git clone` du dépôt GitHub.
3. Créer un environnement virtuel `.venv` et installer `requirements.txt`.
4. **Copier le dossier `data\`** de l'ancien PC (setups, base SQLite, watchlists) — il n'est *pas* sur GitHub. ⚠️ voir la Partie 4 (la base fait ~86 Go).
5. Pour le forecasting : installer les dépendances par tier **et** récupérer les modèles (copier le cache Hugging Face ou les laisser se re-télécharger).
6. Configurer la connexion TWS puis lancer `start.bat`.

Temps estimé : 20–30 min pour l'app de base, +30–90 min si on installe tout le stack forecasting (gros téléchargements).

---

## 1. Ce dont vous avez besoin (à installer sur le nouveau PC)

| Élément | Version conseillée | Où l'obtenir | Obligatoire ? |
|---|---|---|---|
| **Python** | **3.11.x** (ex. 3.11.9) | https://www.python.org/downloads/ | ✅ Oui |
| **Git** | récent | https://git-scm.com/download/win | ✅ Oui (pour cloner/mettre à jour) |
| **IBKR TWS** ou **IB Gateway** | à jour | https://www.interactivebrokers.com | ✅ Oui pour le trading réel/paper |
| Connexion Internet | — | — | ✅ pour pip + modèles la 1re fois |
| Un navigateur (Edge/Chrome) | — | — | ✅ l'interface est web (localhost) |

> ⚠️ **Piège n°1 — la version de Python.**
> L'environnement qui **fonctionne** aujourd'hui tourne sur **Python 3.11.9**.
> Les scripts `install.bat` / `start.bat` détectent aussi un chemin `Python313` : **ignorez-le**,
> installez du **3.11** pour rester identique à la machine actuelle et éviter des soucis de
> compatibilité (notamment `torch` et les libs de forecasting).
> À l'installation de Python, **cochez « Add python.exe to PATH »**.

---

## 2. Ce qui voyage, et comment

| Contenu | Comment le transférer | Remarque |
|---|---|---|
| **Code de l'application** | `git clone` depuis GitHub | Inclut `app\`, `config\`, `scripts\`, les scripts `.bat`, les `requirements*.txt` |
| **`config\`** (schémas de setups, alias, métadonnées) | Vient **automatiquement** avec le clone | Rien à faire |
| **`data\`** (base SQLite, setups sauvegardés, watchlists, exports, logs) | **Copie manuelle** depuis l'ancien PC | ⚠️ **N'est PAS sur GitHub** (ignoré par `.gitignore`). Voir Partie 4 |
| **Modèles de forecast** (TimesFM, Chronos, Lag-Llama, Moirai) | Copie du cache Hugging Face **ou** re-téléchargement | Voir Partie 5 |
| **`.env`** (token Hugging Face `HF_TOKEN`) | **Recréer à la main** | ⚠️ Ignoré par Git (secret). Voir Partie 5 |
| **`.venv\`** (environnement Python) | **NE PAS copier** | On le recrée proprement sur le nouveau PC |

> Règle simple : **le code vient de Git ; les données et les modèles se copient à part.**

---

## 3. Installation de l'application de base (pas à pas)

### 3.1 Récupérer le code

Ouvrir **PowerShell** et choisir un dossier de travail, par exemple `C:\Users\<vous>\Workspace` :

```powershell
cd $HOME\Workspace
git clone https://github.com/ahmedjaouadi/setup-order.git
cd setup-order
```

### 3.2 Créer l'environnement virtuel Python 3.11

```powershell
# Vérifier que c'est bien du 3.11 :
py -3.11 --version

# Créer le venv nommé .venv (les scripts .bat le détectent automatiquement) :
py -3.11 -m venv .venv

# L'activer :
.\.venv\Scripts\Activate.ps1
```

> Si `Activate.ps1` est bloqué par la politique d'exécution PowerShell, lancez une fois :
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` puis réessayez.

### 3.3 Installer les dépendances runtime (obligatoire)

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Cela installe le strict nécessaire pour faire tourner l'app :
`fastapi`, `uvicorn`, `jinja2`, `pyyaml`, `python-multipart`, `ib_async`, `tzdata`.

### 3.4 Premier lancement (test à vide)

```powershell
python run.py
```

- Le lanceur choisit automatiquement un port libre à partir de **8000** et ouvre le navigateur.
- Ou double-cliquez simplement sur **`start.bat`**.
- Ouvrez `http://127.0.0.1:8000`. Le Dashboard doit s'afficher (vide au début, c'est normal).

À ce stade l'app tourne, mais **sans vos setups ni votre historique** (base vide) et **sans modèles de forecast**. On règle ça en Parties 4 et 5.

---

## 4. Migrer vos données (setups, base, watchlists)

Tout votre état vit dans le dossier **`data\`** de l'ancien PC :

```
data\
├── trading_state.sqlite        <- LA base (setups, ordres, positions, events, historique équité)
├── trading_state.sqlite-wal    <- journal WAL (à copier AVEC la base)
├── trading_state.sqlite-shm    <- fichier partagé (à copier AVEC la base)
├── setups\                     <- setups sauvegardés en JSON
├── watchlists\                 <- default.yaml (liste de surveillance)
├── exports\                    <- exports générés
└── logs\                       <- logs runtime
```

### Procédure

1. **Arrêter l'application sur l'ancien PC** (fermer la fenêtre / Ctrl+C). Ne copiez jamais la base pendant qu'elle tourne.
2. Copier **tout le dossier `data\`** vers le même emplacement dans le projet sur le nouveau PC (`setup-order\data\`).
3. Copier bien les **trois** fichiers `trading_state.sqlite`, `-wal` et `-shm` **ensemble**.

> ⚠️ **Piège n°2 — la base fait ~86 Go.**
> Le fichier `trading_state.sqlite` est très volumineux (historique accumulé).
> Deux options :
>
> - **Option A — Repartir propre (recommandé si vous ne tenez pas à l'historique complet)** :
>   ne copiez **que** `data\setups\`, `data\watchlists\` et éventuellement `data\exports\`.
>   Laissez l'app recréer une base neuve au premier lancement. Vos setups JSON pourront être ré-importés.
>
> - **Option B — Tout garder** : copier la base entière. Prévoyez un disque/USB assez grand et du temps.
>   Vous pouvez d'abord **compacter** la base sur l'ancien PC pour la réduire fortement :
>   ```powershell
>   # App arrêtée, dans le venv :
>   python -c "import sqlite3; c=sqlite3.connect(r'data\trading_state.sqlite'); c.execute('VACUUM'); c.close()"
>   ```
>   (Le `VACUUM` fusionne le WAL et récupère l'espace ; la base résultante est souvent bien plus petite.)

4. Relancer `start.bat` : vos setups et votre historique réapparaissent.

> Note : la configuration runtime (connecteur broker, port TWS, mode paper/live, audit TWS)
> est stockée **dans la base**. Si vous repartez sur une base neuve (Option A), il faudra
> **reconfigurer la connexion TWS via l'interface** (voir Partie 6).

---

## 5. Modèles de forecast (optionnel mais recommandé)

Le forecasting est **strictement hors exécution d'ordres** (il n'envoie jamais d'ordre) : il
enrichit seulement les scores et la decision trace. Il est **optionnel** — l'app trade sans.
Si vous le voulez, il y a **deux choses distinctes** à mettre en place :

### 5.1 Les dépendances Python (les librairies)

Elles ne sont **pas** dans `requirements.txt` (trop lourdes). On les installe par « tiers » :

| Tier | Contenu | Fichier |
|---|---|---|
| **p1** | TimesFM, Chronos, Darts, baselines | `requirements-forecasting-p1.txt` |
| **p2** | NeuralForecast, AutoGluon-TimeSeries | `requirements-forecasting-p2.txt` |
| **p3** | Lag-Llama, Moirai / Uni2TS | `requirements-forecasting-p3.txt` |

Installation (venv activé), par exemple le tier 1 :

```powershell
.\install-forecasting.ps1 -Tier p1
```

Ou tout d'un coup :

```powershell
python -m pip install -r requirements-forecasting.txt
```

> ⚠️ **Piège n°3 — c'est lourd.** Le tier p1 tire `torch==2.12.1` (plusieurs centaines de Mo).
> Prévoyez une bonne connexion et de l'espace disque. Faites p1 d'abord et validez, avant p2/p3.

Vérifier ce qui est prêt :

```powershell
python scripts\check_forecasting_stack.py
```

Les providers ne s'activent **que** si leurs dépendances sont présentes dans le `.venv`.

### 5.2 Les fichiers de modèles (les poids)

Les modèles se téléchargent depuis **Hugging Face** et se rangent dans un cache **hors du projet** :

```
C:\Users\<votre_nom>\.cache\huggingface\
```

Sur la machine actuelle ce cache pèse **~1,4 Go** et contient déjà les 4 modèles :

```
models--google--timesfm-2.5-200m-pytorch
models--amazon--chronos-2
models--time-series-foundation-models--Lag-Llama
models--Salesforce--moirai-1.1-R-small
```

Vous avez **deux façons** de les avoir sur le nouveau PC :

- **Option A — Copier le cache (le plus sûr, hors-ligne).**
  Copier le dossier `C:\Users\<ancien_nom>\.cache\huggingface\`
  vers `C:\Users\<nouveau_nom>\.cache\huggingface\` sur la nouvelle machine.
  → Aucun téléchargement, tout marche immédiatement.

- **Option B — Laisser se re-télécharger.**
  Au premier appel de forecast, les modèles se téléchargent automatiquement.
  Nécessite une connexion Internet.

> ⚠️ **Piège n°4 — Chronos est en mode « fichiers locaux uniquement ».**
> Dans la config, `chronos` a `local_files_only: true`. Cela veut dire qu'il **exige le modèle
> déjà présent dans le cache** et ne le télécharge pas tout seul. Donc :
> - soit vous **copiez le cache** (Option A) — recommandé ;
> - soit vous **pré-téléchargez `amazon/chronos-2` une fois** avec une connexion, avant d'utiliser Chronos.

### 5.3 Le token Hugging Face (`.env`)

Certains modèles peuvent demander une authentification Hugging Face.

1. Copier le modèle d'exemple :
   ```powershell
   Copy-Item .env.example .env
   ```
2. Éditer `.env` et renseigner votre token :
   ```
   HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx
   ```
3. `.env` reste **local** (ignoré par Git — ne le committez jamais).

> L'absence de token n'est pas bloquante si les modèles sont déjà dans le cache (Option A).

---

## 6. Configurer la connexion IBKR (TWS / IB Gateway)

L'app se connecte à **TWS** ou **IB Gateway** en local. Valeurs par défaut de l'app :

| Paramètre | Valeur par défaut |
|---|---|
| Host | `127.0.0.1` |
| Port **paper** | `7497` |
| Port **live** | `7496` |
| Client ID | `1001` |

Côté **TWS/IB Gateway** (à faire sur le nouveau PC) :

1. Ouvrir TWS, se connecter au compte (paper ou live).
2. **File → Global Configuration → API → Settings** :
   - Cocher **« Enable ActiveX and Socket Clients »**.
   - Vérifier le **Socket port** (7497 en paper, 7496 en live).
   - Ajouter `127.0.0.1` dans **« Trusted IPs »**.
3. Dans l'app (interface web), régler le connecteur/port si besoin (bouton **Sync**, réglages runtime).

> Le connecteur par défaut est **`paper`**. Seuls `paper` et `live` existent (plus de mode « simulation »).
> Si vous êtes reparti sur une base neuve, c'est ici qu'on reconfigure la connexion.

---

## 7. Vérification finale (checklist « ça marche »)

Cochez dans l'ordre :

- [ ] `py -3.11 --version` répond bien du **3.11.x**.
- [ ] `.venv` créé et activé, `pip install -r requirements.txt` sans erreur.
- [ ] `python run.py` ouvre `http://127.0.0.1:8000`, le **Dashboard** s'affiche.
- [ ] Dossier `data\` en place → vos **setups** apparaissent dans l'onglet *Setups*.
- [ ] TWS lancé, API activée → pastille **CONNECTED** en haut de l'interface.
- [ ] (Optionnel) `python scripts\check_forecasting_stack.py` liste les providers **prêts**.
- [ ] (Optionnel) Un setup affiche un **Forecast stack** après *Recalculer*.
- [ ] Lancer les tests pour confirmer la santé du code :
  ```powershell
  python -m unittest discover -s tests
  ```

---

## 8. Les pièges à éviter (récap « pas de surprise »)

1. **Python 3.11**, pas 3.12/3.13 → même version que la machine qui fonctionne.
2. **`data\` n'est pas sur GitHub** → à copier à part, sinon vous perdez setups + historique.
3. **Base SQLite ~86 Go** → `VACUUM` avant copie, ou repartir propre (garder seulement `setups\` + `watchlists\`).
4. **Chronos `local_files_only: true`** → copiez le cache Hugging Face ou pré-téléchargez `amazon/chronos-2`.
5. **`.env` (HF_TOKEN)** → à recréer à la main, jamais committé.
6. **Ne pas copier `.venv\`** de l'ancien PC → toujours recréer le venv sur la nouvelle machine.
7. **App arrêtée avant de copier la base** → sinon corruption possible (WAL en cours d'écriture).
8. **TWS : API activée + IP de confiance `127.0.0.1`** → sinon « DISCONNECTED ».

---

## Annexe — Commandes utiles

```powershell
# Mettre à jour le code depuis GitHub (nouvelle version) :
git pull

# Réinstaller les dépendances runtime après une mise à jour :
python -m pip install -r requirements.txt

# Compacter la base avant migration (app arrêtée) :
python -c "import sqlite3; c=sqlite3.connect(r'data\trading_state.sqlite'); c.execute('VACUUM'); c.close()"

# Lancer en mode développeur (auto-reload) :
python run.py --dev

# Vérifier l'état du stack forecasting :
python scripts\check_forecasting_stack.py
```

---

*Dépôt GitHub : https://github.com/ahmedjaouadi/setup-order.git*
