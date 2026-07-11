# Mission : Restructuration de l'architecture du projet (TOUS les fichiers)

## Contexte et objectif

Le fichier `app/gui/static/js/app.js` dépasse 5000 lignes, et d'autres fichiers du projet (backend inclus) peuvent souffrir du même problème. Conséquences : chaque modification demande une longue exploration (temps + tokens), les fonctions sont difficiles à localiser, et les bugs de régression sont probables. Objectif : rendre **l'ensemble du projet** navigable et modulaire **sans changer aucun comportement**.

**Portée : tout le code du projet** (frontend JS, backend Python, templates), pas seulement app.js. Le fichier app.js est traité en premier car c'est le pire cas, puis la même méthode est appliquée aux autres fichiers dépassant les seuils ci-dessous.

## Seuils de taille (guides, pas dogmes)

| Élément | Cible | Seuil de découpage |
|---|---|---|
| Fichier | 100–300 lignes | envisager un découpage au-delà de ~500 lignes |
| Fonction | 5–30 lignes | signaler au-delà de ~80 lignes (mais NE PAS réécrire pendant cette mission — noter dans `NOTES-BUGS.md`) |
| Classe | < 200 lignes | envisager un découpage au-delà de ~500 lignes |

Règles d'application :
- On découpe par **domaine fonctionnel** (une responsabilité par fichier), jamais uniquement pour atteindre un chiffre.
- Ne JAMAIS découper un fichier de moins de 300 lignes sans raison fonctionnelle claire.
- Un module cohérent de 450 lignes est acceptable : ne pas le fragmenter artificiellement.
- Le sur-découpage (dizaines de micro-fichiers) est une erreur au même titre que le monolithe.

## Règles absolues (à respecter pendant TOUTE la mission)

1. **Refactoring pur uniquement.** Interdiction de corriger, améliorer, renommer ou "nettoyer" une fonction pendant le découpage. On DÉPLACE du code, on ne le RÉÉCRIT pas. Si tu repères un bug, note-le dans `NOTES-BUGS.md` et continue.
2. **Une étape = un commit.** Jamais plus d'un module extrait par commit. Message de commit format : `refactor: extract <nom-module> from app.js (no behavior change)`.
3. **Vérification après chaque étape.** Après chaque extraction : lancer l'app, ouvrir la page principale, vérifier dans la console navigateur qu'il n'y a aucune erreur `ReferenceError` / `is not defined`, et tester manuellement (ou via Playwright) au moins une fonctionnalité du module extrait.
4. **Point d'arrêt obligatoire.** À la fin de chaque phase, STOP : présente un résumé et attends ma validation avant de passer à la phase suivante. Ne jamais enchaîner deux phases sans mon accord.
5. **Économie de contexte.** Ne jamais lire app.js en entier. Utiliser `grep`/recherche ciblée pour localiser les fonctions, et ne lire que les plages de lignes nécessaires.
6. **Aucune dépendance nouvelle.** Pas de bundler (webpack, vite...), pas de framework, pas de bibliothèque ajoutée sans mon accord explicite.

---

## Phase 0 — Sécurisation (avant de toucher au code)

1. Vérifier `git status`. S'il y a des changements non commités, me les lister et me demander si on les commit ou les stash.
2. Créer une branche dédiée : `git checkout -b refactor/split-app-js`.
3. Commit de départ propre pour pouvoir revenir en arrière à tout moment.

**STOP — attendre ma validation.**

---

## Phase 1 — Générer CLAUDE.md (la carte du projet)

Créer un fichier `CLAUDE.md` à la racine du projet contenant :

1. **Description du projet** en 2-3 phrases (quoi, pour qui).
2. **Structure des dossiers** : arborescence commentée (backend, frontend, static, templates, tests...). Une ligne d'explication par dossier important.
3. **Où se trouve quoi** : tableau `Fonctionnalité → Fichier(s) → Fonctions principales`. Exemples de lignes attendues :
   - Détail d'un setup → `app.js` (provisoire) → `renderSetupDetail`, `wireSetupDetailJsonButton`
   - Copie presse-papiers → ... → `copySetupTemplateToClipboard`, `copySetupDetailInfoToClipboard`
   - Routes API backend → ...
4. **Commandes** : comment lancer l'app, comment lancer les tests, URL locale d'accès.
5. **Conventions** : nommage des fonctions, gestion des erreurs (toast ?), style de code observé dans le projet.
6. **Pièges connus** : y documenter notamment le piège clipboard (l'écriture presse-papiers doit rester dans la fenêtre d'activation utilisateur ; ne jamais mettre d'`await` d'appels réseau avant `navigator.clipboard.*` ou `execCommand`).

Contraintes : max ~150 lignes, factuel, pas de blabla. Ce fichier sera lu automatiquement à chaque session, chaque ligne doit être utile.

**STOP — me montrer le CLAUDE.md et attendre ma validation.**

---

## Phase 2 — Audit et cartographie de tout le projet

### 2a. Audit global des tailles

Sans rien modifier, lister TOUS les fichiers de code du projet avec leur nombre de lignes (ex. `find . -name "*.js" -o -name "*.py" | xargs wc -l | sort -rn`, en excluant node_modules/venv/libs tierces). Produire dans `docs/refactoring-audit.md` la liste des fichiers dépassant ~500 lignes, classés du plus gros au plus petit. C'est la liste des candidats au découpage, app.js en tête.

### 2b. Cartographie du fichier en cours de traitement

Pour chaque fichier candidat (en commençant par app.js), produire un fichier `docs/<nom-fichier>-map.md` contenant :

1. La liste de **toutes les fonctions top-level** du fichier avec : nom, ligne de début, taille approximative, rôle en une phrase.
   - Méthode recommandée : `grep -n` sur les définitions de fonctions puis compléter, plutôt que lire le fichier entier.
2. Les **variables globales / état partagé** (ex. `currentSetupDetailInfo`) : qui les écrit, qui les lit.
3. Un **regroupement par domaine fonctionnel** proposé, par exemple :
   - `api-client` : tous les fetch vers le backend
   - `setup-detail` : rendu et interactions de la page détail
   - `clipboard` : copie presse-papiers
   - `charts` : graphiques
   - `ui-helpers` : toast, onClick, helpers DOM
   - `state` : variables globales partagées
   (Adapter selon ce qui existe réellement.)
4. Une **matrice de dépendances** simple entre groupes : quel groupe appelle quel groupe. Signaler les dépendances circulaires si détectées.

**STOP — me montrer la carte et attendre ma validation du regroupement.**

---

## Phase 3 — Plan de découpage détaillé

À partir de la carte validée, produire le plan dans `docs/refactoring-plan.md` :

1. **Choix technique de modularisation**, à justifier selon la façon dont app.js est chargé actuellement (vérifier le template HTML) :
   - Option A (préférée si possible) : ES modules (`<script type="module">`, `import`/`export`).
   - Option B (si contraintes) : plusieurs `<script>` classiques chargés dans le bon ordre, chaque fichier exposant ses fonctions via un namespace (ex. `window.App.clipboard = {...}`).
   - Me présenter l'option recommandée avec ses risques AVANT de l'appliquer.
2. **Ordre d'extraction** : commencer par les modules SANS dépendances (ui-helpers, api-client), finir par ceux qui dépendent de tout (setup-detail, init). Un module par étape.
3. Pour chaque étape : fichier créé, fonctions déplacées (liste exacte), variables globales concernées, comment le reste du code y accédera, test de non-régression à exécuter.
4. **Gestion de l'état global** : les variables comme `currentSetupDetailInfo` vont dans un module `state.js` avec des accesseurs (`getCurrentSetupDetailInfo()` / `setCurrentSetupDetailInfo()`), extrait en premier ou parmi les premiers.

**STOP — attendre ma validation du plan complet avant d'écrire la moindre ligne.**

---

## Phase 4 — Exécution incrémentale

Pour CHAQUE étape du plan validé, dans l'ordre :

1. Créer le nouveau fichier module.
2. Déplacer les fonctions (copier-coller exact, zéro réécriture).
3. Supprimer les fonctions de app.js et brancher les imports/références.
4. Mettre à jour le template HTML si nécessaire (ordre des scripts / type=module).
5. **Vérifier** : app démarre, aucune erreur console, la fonctionnalité du module marche (test Playwright si disponible, sinon me demander de tester manuellement les cas non automatisables comme le presse-papiers réel).
6. Mettre à jour le tableau "Où se trouve quoi" de `CLAUDE.md`.
7. Commit.
8. Me faire un résumé d'UNE ligne et passer à l'étape suivante (pas besoin de validation entre chaque module de cette phase, SAUF si un problème apparaît — dans ce cas STOP immédiat et diagnostic avant de continuer).

Interdictions spécifiques pendant cette phase :
- Ne pas réordonner le contenu des fonctions.
- Ne pas fusionner deux fonctions "qui se ressemblent".
- Ne pas changer les noms des IDs HTML ni des routes API.
- Si une fonction est trop enchevêtrée pour être déplacée proprement : la laisser dans app.js, le noter dans `docs/refactoring-plan.md`, et continuer.

---

## Phase 5 — Fichier suivant, puis vérification finale

1. Une fois app.js traité, reprendre les Phases 2b → 4 pour le fichier suivant de la liste d'audit (`docs/refactoring-audit.md`), un fichier à la fois, toujours avec validation du plan avant exécution. Pour les fichiers Python backend : même méthode (découpage par domaine en modules, imports explicites, zéro changement de comportement, tests après chaque extraction).
2. Quand tous les fichiers > 500 lignes sont traités (ou explicitement laissés tels quels avec justification), passer en revue toutes les fonctionnalités principales de l'app (liste à établir depuis CLAUDE.md) et les tester une par une.
3. Vérifier que chaque fichier résiduel respecte les seuils, ou que son dépassement est justifié dans l'audit.
4. Me produire un bilan : taille avant/après par fichier, liste des modules, bugs notés dans `NOTES-BUGS.md` à traiter séparément.
5. Me proposer le merge de la branche — ne PAS merger sans mon accord.

---

## Après cette mission — règles permanentes pour les prochaines sessions

À ajouter à la fin de `CLAUDE.md` :

```
## Règles de travail
- Avant toute modification : consulter le tableau "Où se trouve quoi" ci-dessus pour aller directement au bon fichier. Ne pas explorer à l'aveugle.
- Lire uniquement le module concerné, jamais l'ensemble des fichiers.
- Toute nouvelle fonction doit être placée dans le module de son domaine (jamais dans un fichier fourre-tout) et ajoutée au tableau "Où se trouve quoi".
- Tailles : viser des fichiers de 100-300 lignes et des fonctions de moins de 50 lignes. Si un fichier approche 500 lignes, proposer un découpage AVANT d'y ajouter du code.
- Un changement de comportement = tester la fonctionnalité avant de conclure. Ne jamais dire "corrigé" sans vérification.
- Piège clipboard : aucune écriture presse-papiers après un await d'appel réseau.
- Refactoring et corrections de bugs : toujours dans des commits séparés.
```
