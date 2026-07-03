# Semantic Validation - Phase 1

## Objectif

Cette phase ajoute une couche de validation semantique autonome au-dessus:

- de la normalisation canonique;
- des classes de setup existantes;
- du moteur principal.

Le but n'est pas de remplacer `setup.validate()`, mais de preparer une validation plus riche, plus explicable et plus facilement reusable par:

- la future couche IA/LLM;
- l'API;
- la GUI;
- les imports batch et scenarios multiples.

## Ce qui a ete ajoute

### Nouveau module

- `app/intelligence/semantic_validation_service.py`

Ce service:

- charge des schemas JSON depuis `config/schemas/`;
- applique une validation structurelle legere sur le modele canonique;
- applique ensuite des regles semantiques metier;
- retourne un rapport detaille avec erreurs, warnings et issues structurees.

### Schemas JSON

Nouveaux fichiers:

- `config/schemas/setup.base.schema.json`
- `config/schemas/setup.breakout_retest.schema.json`
- `config/schemas/setup.aggressive_rebound.schema.json`
- `config/schemas/setup.momentum_breakout.schema.json`
- `config/schemas/setup.pullback_continuation.schema.json`
- `config/schemas/setup.range_breakout.schema.json`
- `config/schemas/setup.runner.schema.json`
- `config/schemas/setup.position_management.schema.json`

Ces schemas couvrent pour l'instant:

- les types primitifs attendus;
- les enums critiques;
- les sections obligatoires;
- quelques invariants simples de structure.

## Comment la validation est branchee

Pipeline actuel:

1. entree brute;
2. normalisation canonique;
3. validation schema + validation semantique;
4. validation `SetupFactory` / `setup.validate()`;
5. sauvegarde / chargement / exposition API.

Point d'integration principal:

- `app/engine/setup_engine.py`

Le `ValidationResult` transporte maintenant aussi un bloc `details` qui contient:

- les champs remappes par le canonique;
- le rapport semantique detaille.

## Exemples de controles semantiques ajoutes

- `entry.maximum_limit_price >= entry.trigger_price`
- `risk.max_risk_usd <= risk.max_position_amount_usd` en warning
- zones inversees detectees:
  - `retest.zone_min > retest.zone_max`
  - `support_zone.min > support_zone.max`
  - `range.low >= range.high`
  - `missed_breakout.retest_zone_min > missed_breakout.retest_zone_max`
- warning sur incoherence probable:
  - `breakout.daily_close_above < retest.zone_max`
- warning si un `runner` n'a pas encore de progression de stop
- warning si un `position_management` n'a pas encore de regles de stop effectives

## Pourquoi cette approche est scalable

Le service est separe du moteur de trading:

- il peut tourner en parallele d'autres briques;
- il peut etre appele sans lancer le moteur;
- il produit un format d'issues exploitable par l'API ou une future UI de debug;
- il prepare naturellement l'arrivee de:
  - provenance champ par champ;
  - ambiguities utilisateur;
  - score de confiance;
  - semantic validation multi-scenarios.

## Ce que cette phase ne fait pas encore

- pas de scoring de confiance;
- pas de provenance;
- pas de resolution d'ambiguites;
- pas de persistence SQL des analyses semantiques;
- pas encore d'API dediee `/api/intelligence/...`;
- pas encore de compilation de regles utilisateur en DSL ou AST.

## Prochaines etapes recommandees

1. Exposer ce rapport via une API `intelligence`.
2. Ajouter un `semantic_validation_service` multi-scenarios.
3. Introduire les tables `semantic_analyses`, `extracted_fields`, `ambiguities`.
4. Ajouter la provenance et le score de confiance.
5. Brancher la future extraction IA sur le meme contrat canonique + semantique.
