# Intelligence GUI History + Confidence - Phase 1

## Objectif

Cette phase branche la couche intelligence dans la page detail d'un setup afin de rendre visibles:

- la derniere analyse;
- l'historique des analyses persistées;
- les scenarios extraits;
- les ambiguities ouvertes ou resolues;
- la provenance des champs;
- le score de confiance global et par scenario.

Le but est de donner a l'utilisateur une vraie surface d'inspection avant toute logique d'activation avancee.

## Ce qui a ete ajoute

### Panneau Intelligence dans la page detail

Ajouts dans:

- `app/gui/templates/setup_detail.html`
- `app/gui/static/css/styles.css`
- `app/gui/static/js/app.js`

Le panneau expose:

- une vue d'ensemble de l'analyse selectionnee;
- la confiance globale;
- la validation `save` vs `arm`;
- les scenarios extraits;
- les ambiguities avec resolution manuelle;
- les champs extraits avec provenance;
- l'historique des analyses pour le setup courant.

### Historique consultable

L'historique n'est plus seulement une liste passive.

Chaque analyse historique peut maintenant etre affichee dans le panneau pour revoir:

- ses scenarios;
- ses ambiguities;
- ses champs;
- sa confiance;
- ses versions de parseur/schema.

La selection est maintenue dans l'etat front tant que l'analyse existe encore dans l'historique recharge.

### Confiance persistée

La confiance est maintenant stockee en base:

- `semantic_analyses.confidence_json`
- `extracted_scenarios.confidence_json`

Mise a jour dans:

- `app/storage/database.py`
- `app/intelligence/repository.py`
- `app/intelligence/service.py`

Cela permet de recharger exactement la meme lecture de confiance apres restart de l'application.

### Synchronisation GUI / JSON detail

Le JSON detail expose dans la page setup reste synchronise apres:

- une nouvelle analyse;
- la consultation d'une analyse historique;
- la resolution d'une ambiguite.

Le bloc `intelligence` du JSON inclut aussi `selected_analysis_id`.

## Comportement utilisateur

### Lancer une analyse

Depuis la page detail d'un setup:

- bouton `Analyser ce setup`;
- appel `POST /api/intelligence/analyze`;
- recharge du panneau avec la nouvelle analyse ou l'analyse reutilisee.

### Explorer l'historique

Depuis la section Historique:

- bouton `Afficher` sur une analyse;
- le panneau bascule sur cette revision;
- les scenarios, ambiguities et provenances affiches deviennent ceux de cette revision.

### Resoudre une ambiguite

Les options de resolution ne sont affichees que pour les ambiguities `OPEN`.

Une ambiguite `RESOLVED` reste visible dans l'historique mais n'expose plus d'action de resolution.

## Pourquoi cette phase compte

Avant cette phase, la couche intelligence existait en base et par API, mais elle restait difficile a auditer depuis l'interface.

Maintenant, on a:

- une boucle d'inspection humaine;
- un historique consultable;
- une lecture explicite du niveau de confiance;
- une separation lisible entre brouillon sauvegardable et scenario armable.

## Ce qui manque encore

Cette phase n'active pas encore:

- comparaison diff entre revisions de scenarios;
- rollback d'une ancienne revision;
- activation multi-scenarios avancee;
- orchestration d'une decision GUI -> scenario selectionne -> armement;
- provider LLM reel;
- rule builder GUI complet.

## Prochain bloc recommande

Le bloc le plus logique maintenant est:

`versioning + comparaison + rollback de scenarios`

Pourquoi:

- on a deja la persistance, l'historique et la selection GUI;
- la brique suivante naturelle est de comparer deux revisions d'un meme setup;
- cela prepare proprement l'activation multi-scenarios sans coupler intelligence et execution broker;
- c'est un module separable et scalable, base sur les donnees deja persistées.
