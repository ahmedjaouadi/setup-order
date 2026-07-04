# Guide de migration — Setup Order vers un nouveau PC Windows 11

Ce document explique **comment déplacer l'application sur une nouvelle machine Windows 11
sans mauvaise surprise**. Suivez les parties dans l'ordre. Les points marqués ⚠️ sont
ceux qui cassent le plus souvent une migration : lisez-les.

---

## 0. Résumé express (TL;DR)

1. Installer **Python 3.11.x** (pas 3.12/3.13), **Git**, et **IBKR TWS / IB Gateway**.
2. `git clone` du dépôt GitHub — **vos 59 setups et votre watchlist sont dedans**, aucune
   copie manuelle de fichiers n'est nécessaire pour ça.
3. Créer un environnement virtuel `.venv` et installer `requirements.txt`.
4. **Ne rien copier depuis l'ancien PC pour `data\`** : on laisse l'app créer une base neuve.
   (La base `trading_state.sqlite` de l'ancien PC ne contient que des journaux internes
   régénérables — voir Partie 4.)
5. Pour le forecasting : installer les dépendances par tier **et** récupérer les modèles (copier le cache Hugging Face ou les laisser se re-télécharger).
6. Configurer la connexion TWS puis lancer `start.bat`.
7. ⚠️ **Au premier démarrage, vos setups reviennent tous en statut `DISABLED`** — il faut les réarmer un par un dans l'interface (voir Partie 4.3). C'est normal, pas un bug.

Temps estimé : 15–20 min pour l'app de base, +30–90 min si on installe tout le stack forecasting (gros téléchargements), + quelques minutes pour réarmer les setups.

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
| **`data\setups\`** (vos 59 setups JSON) | Vient **automatiquement** avec le clone | ✅ Suivi par Git (repo **privé**). Revient en statut `DISABLED` — voir 4.3 |
| **`data\watchlists\`** (`default.yaml`) | Vient **automatiquement** avec le clone | ✅ Suivi par Git |
| **`data\trading_state.sqlite`** (base, ~80-90 Go) | **NE PAS transporter** | Ignoré par Git (`.gitignore`). Journaux internes régénérables uniquement — voir Partie 4.1 |
| **`data\exports\`, `data\logs\`** | **NE PAS transporter** | Ignorés par Git, régénérables |
| **Modèles de forecast** (TimesFM, Chronos, Lag-Llama, Moirai) | Copie du cache Hugging Face **ou** re-téléchargement | Voir Partie 5 |
| **`.env`** (token Hugging Face `HF_TOKEN`) | **Recréer à la main** | ⚠️ Ignoré par Git (secret). Voir Partie 5 |
| **`.venv\`** (environnement Python) | **NE PAS copier** | On le recrée proprement sur le nouveau PC |

> Règle simple : **`git clone` ramène le code ET vos setups/watchlist. Seuls le cache de
> modèles et le `.env` restent à gérer à part. La base ne voyage jamais.**
>
> ⚠️ Le dépôt GitHub doit rester **privé** : `data\setups\` contient vos stratégies de
> trading (prix, stops, règles). Pas de secret technique dedans, mais c'est votre logique
> de trading — ne rendez pas le dépôt public sans y penser.

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
- Ouvrez `http://127.0.0.1:8000`. Le Dashboard doit s'afficher (vide au début pour l'historique, c'est normal).

À ce lancement, l'app crée une base `trading_state.sqlite` **neuve** et **recharge automatiquement
vos 59 setups** depuis `data\setups\*.json` (déjà présents grâce au clone) — voir Partie 4 pour
le détail de ce qui se passe et le point d'attention sur leur statut.

---

## 4. Vos setups au démarrage — ce qui se passe et le point d'attention

### 4.1 D'où viennent les setups, et pourquoi on ne copie pas la base

Avant, il fallait copier manuellement le dossier `data\` de l'ancien PC. Ce n'est plus le cas :
`data\setups\` (59 fichiers JSON, ~569 Ko) et `data\watchlists\` sont maintenant **suivis par
Git** et arrivent automatiquement avec le `git clone` de la Partie 3.1.

La seule chose qui **ne vient jamais** avec le code, et qu'on ne cherche pas non plus à copier
depuis l'ancien PC, c'est la base **`trading_state.sqlite`** (souvent 80–90 Go sur une
installation qui tourne depuis un moment). Elle reste `.gitignore`e, et c'est volontaire :

- Les infos de compte (net liquidation, cash, P&L, positions) viennent **en direct de TWS**
  à chaque rafraîchissement — elles ne dépendent pas de la base.
- L'essentiel de son volume (>99 %), ce sont des **journaux internes** que le moteur écrit en
  continu : `runtime_events`, `events`, `decision_traces`, `setup_scores`,
  `feature_snapshots`, les tables `forecast_*`. Rien de tout ça n'est nécessaire pour que
  l'app reparte.

**Conclusion : ne transportez pas la base.** Laissez l'app en créer une neuve sur le nouveau PC ;
vos setups (la seule chose qui compte et ne se régénère pas) sont déjà là grâce au clone.

> Si malgré tout vous voulez garder l'historique complet de l'ancien PC (compliance, debug),
> copiez sa base **à part** de la migration standard, après l'avoir compactée avec `VACUUM`
> (app arrêtée) :
> ```powershell
> python -c "import sqlite3; c=sqlite3.connect(r'data\trading_state.sqlite'); c.execute('VACUUM'); c.close()"
> ```
> Ce n'est **pas nécessaire** pour que l'app fonctionne normalement sur le nouveau PC.

### 4.2 Garder les setups synchronisés entre les deux PC

Comme `data\setups\` est maintenant dans Git, traitez-le comme du code :

- Après avoir créé/modifié un setup sur une machine, pensez à `git add data\setups\ && git commit && git push`
  si vous voulez le retrouver sur l'autre machine.
- Sur l'autre machine, `git pull` avant de démarrer l'app pour récupérer les derniers setups.
- Le rechargement en base se fait **automatiquement à chaque démarrage** de l'app
  (`setup_engine.load_all()`) — pas besoin de bouton "importer".

### 4.3 ⚠️ Point d'attention — vos setups reviennent tous en statut `DISABLED`

C'est le point le plus important à connaître **avant** de migrer, pour ne pas croire que
"quelque chose ne fonctionne plus" :

- Sur une base neuve, chaque setup rechargé démarre **volontairement** en statut `DISABLED`,
  même si son fichier JSON contient `"enabled": true`. C'est un comportement intentionnel de
  l'app (même règle que pour un setup nouvellement importé).
- **Concrètement** : vos 59 setups seront bien visibles dans l'onglet *Setups*, avec leur
  configuration intacte — mais le moteur ne les surveille pas tant qu'ils ne sont pas réarmés.
- **Il n'y a pas de bouton "tout réarmer"** : le bouton *Auto ON pour tous les stocks* ne
  change que le flag d'auto-exécution, il ne réarme pas le statut runtime. Il faut ouvrir
  **chaque setup** et cliquer **`Armer setup`** individuellement.
- Pour 59 setups, prévoyez quelques minutes de clics. Vérifiez avant de réarmer que le
  contexte marché de chacun est toujours valable (un setup conçu pour une configuration de
  marché passée n'a pas forcément de sens à réarmer tel quel).

> La configuration runtime globale (connecteur broker, port TWS, mode paper/live, audit TWS)
> est, elle, stockée dans la base — donc réinitialisée par défaut sur une base neuve. Il faut
> la **reconfigurer une fois via l'interface** (voir Partie 6).

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
- [ ] Vos **59 setups** apparaissent dans l'onglet *Setups* (venus automatiquement du clone).
- [ ] ⚠️ Réarmer les setups voulus un par un (`Armer setup`) — ils démarrent en `DISABLED`.
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
2. **Vos setups reviennent en `DISABLED`** après le clone → à réarmer un par un, pas de bouton "tout réarmer".
3. **La base SQLite (~86-90 Go) ne se copie jamais** → elle ne contient que des journaux internes régénérables ; vos vraies données (setups) sont dans Git.
4. **Chronos `local_files_only: true`** → copiez le cache Hugging Face ou pré-téléchargez `amazon/chronos-2`.
5. **`.env` (HF_TOKEN)** → à recréer à la main, jamais committé.
6. **Ne pas copier `.venv\`** de l'ancien PC → toujours recréer le venv sur la nouvelle machine.
7. **Dépôt GitHub privé obligatoire** → `data\setups\` contient vos stratégies de trading.
8. **TWS : API activée + IP de confiance `127.0.0.1`** → sinon « DISCONNECTED ».
9. **Setups modifiés sur une machine ?** → `git push` là-bas, `git pull` ici avant de démarrer, sinon désynchro entre les deux PC.

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
