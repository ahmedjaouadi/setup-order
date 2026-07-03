# Canonical Normalization - Phase 1

## Objectif

Cette phase pose une base de normalisation canonique entre:

- les textes utilisateur et JSON libres;
- les alias metier;
- les validations de setup;
- le moteur principal.

Le but est de disposer d'un module autonome, reusable et extensible avant d'attaquer la couche IA/LLM, la validation semantique avancee et les scenarios multiples.

## Ce qui a ete fait

### 1. Retrait du mode utilisateur `simulation`

Le programme n'expose plus que deux modes utilisateur:

- `paper`
- `live`

Le mode legacy `simulation` reste accepte uniquement comme entree historique. Il est automatiquement promu vers `paper` pendant la normalisation, avec warning.

Impact:

- config runtime par defaut sur `paper`;
- validation des setups limitee a `paper|live`;
- GUI limitee a `paper|live`;
- runtime UI aligne sur `paper|live`.

### 2. Ajout d'un module de normalisation canonique

Nouveaux fichiers:

- `app/conversion/alias_resolver.py`
- `app/conversion/canonical_field_registry.py`
- `app/conversion/canonical_model_builder.py`
- `config/field_aliases.yaml`

Responsabilites:

- `alias_resolver.py`
  - normalise les cles et chemins;
  - resout un alias vers un champ canonique;
  - evite que la GUI, le parser texte et la future couche IA recodent chacun leurs propres synonymes.

- `canonical_field_registry.py`
  - charge le registre de champs canoniques depuis `config/field_aliases.yaml`;
  - centralise le dictionnaire de mapping.

- `canonical_model_builder.py`
  - transforme une charge utile heterogene vers un modele interne propre;
  - convertit les types simples;
  - remappe les champs plats vers la structure imbriquee attendue;
  - harmonise certains champs metier critiques.

### 3. Branchement au pipeline principal

La normalisation est executee:

- dans `app/setups/text_converter.py` apres parsing texte/JSON;
- dans `app/engine/setup_engine.py` avant validation et persistance.

Cela donne une seule porte d'entree canonique avant:

- la validation metier;
- le stockage;
- l'usage par le moteur.

## Comment le module fonctionne

## Flux

1. Une entree brute arrive depuis le texte libre, un JSON colle, un fichier setup ou plus tard une extraction IA.
2. `AliasResolver` convertit les variantes de noms vers un chemin canonique.
3. `canonical_model_builder` reconstruit un dictionnaire normalise.
4. Les champs simples sont coerces si necessaire:
   - numeriques;
   - booleens;
   - enums usuels;
   - `symbol`, `direction`, `setup_type`, `entry.order_type`.
5. Les incoherences simples sont harmonisees:
   - `simulation` devient `paper`;
   - `initial_stop_loss` et `protective_stop` sont recroises selon le contexte.
6. Le resultat normalise est envoye a la validation standard deja existante.

## Pourquoi c'est scalable

Cette brique est volontairement separee du moteur de trading:

- elle peut etre appelee par la GUI, l'API, l'IA ou les imports batch;
- elle concentre les aliases et conventions en un seul endroit;
- elle permet d'ajouter des schemas, du scoring de confiance ou de la provenance sans casser le moteur principal;
- elle facilite les traitements paralleles sur plusieurs scenarios, car la normalisation est pure et deterministe a l'echelle d'un payload.

## Choix d'architecture

Ce bloc a ete pris en premier parce qu'il debloque plusieurs gros morceaux a la fois:

- couche IA/LLM;
- analyse semantique multi-scenarios;
- gestion des ambiguites;
- API intelligence;
- rule compiler et validation semantique.

Sans modele canonique stable, ces futurs blocs risquent de dupliquer les mappings, diverger sur les noms de champs et produire des validations incoherentes.

## Compatibilite et migration

Compatibilite immediate:

- les anciens payloads avec `simulation` continuent d'etre lus;
- les alias courants comme `SL`, `budget`, `entry_order_type`, `retest_zone_min` peuvent etre ramenes vers la structure attendue.

Ce qui n'est pas encore fait:

- migration automatique des anciens fichiers sur disque vers une ecriture canonique;
- schemas JSON officiels;
- versioning du registre d'aliases;
- provenance champ par champ;
- score de confiance;
- validation semantique riche.

## Limites actuelles

Phase 1 reste volontairement simple:

- pas encore de moteur de provenance;
- pas encore de resolution d'ambiguites multi-sources;
- pas encore d'API dediee `/api/intelligence/...`;
- pas encore de persistence SQL pour analyses semantiques et champs extraits;
- pas encore de rollback/compare entre scenarios.

## Impact sur le broker interne

Le broker interne de test existe toujours, mais il n'est plus un mode utilisateur.

En pratique:

- l'utilisateur choisit `paper` ou `live`;
- le connecteur interne `simulated` reste reserve aux tests et au dev;
- l'interface n'expose plus ce choix comme option fonctionnelle normale.

Cela clarifie l'intention produit:

- `paper` = workflow de trading non reel;
- `live` = workflow reel;
- broker interne = outil de test technique.

## Prochaines etapes recommandees

1. Stabiliser cette couche avec tests supplementaires sur aliases, coercions et warnings.
2. Ajouter des JSON schemas autour du modele canonique.
3. Introduire `semantic_validation_service.py` au-dessus de la normalisation.
4. Ajouter la provenance et les ambiguites utilisateur champ par champ.
5. Exposer le tout via `app/intelligence/*` et `/api/intelligence/...`.

## Resume

Cette phase ne cherche pas encore a "rendre le systeme intelligent".
Elle construit le sol stable sur lequel l'intelligence pourra travailler proprement.

Le benefice immediat est deja concret:

- moins de logique dupliquee;
- moins d'ecarts entre GUI, parser texte et moteur;
- meilleure compatibilite legacy;
- meilleur point d'entree pour les prochains gros blocs.
