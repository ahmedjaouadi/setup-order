# Intelligence API + Semantic Persistence - Phase 1

## Objectif

Cette phase transforme la couche canonique et la validation semantique en un module d'intelligence autonome:

- persisté;
- transactionnel;
- interrogeable par API;
- decouple de l'execution TWS.

Le moteur de trading n'utilise pas directement cette couche pour placer des ordres.
Elle produit uniquement des brouillons, validations, issues, ambiguities, provenances et scenarios extraits.

## Ce qui a ete ajoute

### Tables SQLite

Ajouts dans `app/storage/database.py`:

- `semantic_analyses`
- `extracted_fields`
- `ambiguities`
- `extracted_scenarios`
- `schema_migrations`

Les enregistrements enfants sont rattaches a `semantic_analyses` via des foreign keys avec `ON DELETE CASCADE`.

### Repository transactionnel

Nouveau repository:

- `app/intelligence/repository.py`

Le bundle complet suivant est enregistre dans une seule transaction SQLite:

- analyse;
- scenarios extraits;
- champs extraits;
- ambiguities.

Si une etape echoue:

- rollback complet.

### Service d'intelligence

Nouveau service:

- `app/intelligence/service.py`

Responsabilites:

- parser l'entree `text` ou `payload`;
- produire une ou plusieurs configurations canoniques;
- separer `save_validation` et `arm_validation`;
- produire des issues structurees pour la GUI;
- construire une provenance champ par champ;
- gerer l'idempotence;
- persister les analyses;
- exposer un contrat stable pour une future couche LLM.

### Provider LLM desactive

Ajout de:

- `app/intelligence/provider.py`

avec:

- `LLMProvider`
- `DisabledLLMProvider`

Le contrat du futur provider est deja pose, mais aucun modele externe n'est active a cette phase.

### Endpoints

Ajout de:

- `POST /api/intelligence/analyze`
- `POST /api/intelligence/validate`
- `GET /api/intelligence/setups/{setup_id}/latest`
- `GET /api/intelligence/setups/{setup_id}/analyses`
- `GET /api/intelligence/analyses/{analysis_id}`
- `GET /api/intelligence/analyses/{analysis_id}/scenarios`
- `POST /api/intelligence/analyses/{analysis_id}/ambiguities/{ambiguity_id}/resolve`

## Contrat de persistance

## semantic_analyses

Chaque analyse stocke notamment:

- `analysis_id`
- `setup_id`
- `symbol`
- `request_id`
- `idempotency_key`
- `analysis_hash`
- `schema_version`
- `parser_version`
- `canonical_mapper_version`
- `prompt_version`
- `llm_model`
- `save_validation_json`
- `arm_validation_json`
- `issues_json`

## extracted_scenarios

Chaque scenario extrait est stocke separement avec:

- `scenario_id`
- `analysis_id`
- `symbol`
- `scenario_name`
- `scenario_role`
- `setup_type`
- `status`
- `selected`
- `armed`
- `canonical_config_json`

## extracted_fields

Chaque champ extrait stocke:

- `raw_key`
- `normalized_key`
- `canonical_path`
- `raw_value`
- `parsed_value`
- `source_text`
- `source_line_start`
- `source_line_end`
- `extraction_method`
- `confidence`
- `validation_status`

## ambiguities

Chaque ambiguite stocke:

- `ambiguity_id`
- `analysis_id`
- `scenario_id`
- `field_path`
- `message`
- `options_json`
- `status`
- `resolution_json`

## Save validation vs arm validation

La separation est maintenant explicite:

- `save_validation`
  - verifie si le scenario peut etre sauve comme brouillon exploitable;
- `arm_validation`
  - verifie si le scenario peut etre arme pour execution future.

Exemple attendu:

- un brouillon partiel sans stop peut etre sauve;
- il ne peut pas etre arme.

Le statut de scenario reflète cela:

- `READY_FOR_REVIEW`
- `REVIEW_REQUIRED`
- `INVALID_DRAFT`

## Idempotence

Le service calcule un `analysis_hash` base sur:

- texte/payload brut;
- symbole;
- `parser_version`;
- `schema_version`;
- `canonical_mapper_version`.

Regles:

- si `idempotency_key` identique: reutilisation de l'analyse existante;
- sinon, si meme `analysis_hash` et pas de `force_new_revision`: reutilisation;
- si `force_new_revision=true`: nouvelle analyse liee a la precedente via `previous_analysis_id`.

## Provenance

La provenance n'est plus un placeholder.

Phase 1 couvre:

- mapping direct JSON/canonique;
- alias mapping;
- extraction texte via convertisseur;
- ligne source quand elle peut etre retrouvee;
- statut de validation par champ.

## Contrat d'erreur structure

Les erreurs exposees a la GUI utilisent maintenant un format structure:

- `code`
- `field_path`
- `message`
- `severity`
- `source_line`
- `accepted_aliases`
- `scenario_id`

Cela evite les messages opaques du type:

- `Invalid setup`
- `Add a stop loss in the setup text`

## Ce que cette phase ne fait pas encore

- pas de persistence SQL du score global de confiance;
- pas de provenance multi-source avancee;
- pas de resolution intelligente d'ambiguites;
- pas de worker multi-analyses;
- pas de provider LLM reel;
- pas de GUI historique / inspection detaillee branchee sur ces endpoints.

## Prochain bloc recommande

Le prochain bloc naturel devient:

1. enrichissement des ambiguities + score de confiance;
2. exposition GUI de l'historique d'analyses;
3. provenance multi-scenarios plus fine;
4. branchement d'un vrai provider LLM sur le contrat deja pose.
