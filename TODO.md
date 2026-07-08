# TODO - Priorite actuelle

Derniere lecture: 2026-07-07.

Ce fichier remplace l'ancien backlog qui melangeait des etapes deja acceptees,
des notes historiques et des travaux encore ouverts. La priorite immediate est de
corriger l'onglet **Ordres & Positions**, car il peut afficher une vue incoherente
de la realite TWS.

## P0 - Corriger "Ordres & Positions"

Probleme constate:

- La page `/orders` consomme `/api/dashboard`, donc `TradingEngine.snapshot()`.
- Le bandeau utilise surtout `bot_state.broker_reality`.
- Les tableaux utilisent des listes reconstruites separement:
  `positions`, `orders`, `executions`.
- Le snapshot melange encore:
  - verite TWS courante;
  - rapport broker-reality persistant;
  - historique / intentions locales.
- Dans la base locale actuelle, `broker_reality` indique 8 positions TWS, 0 ordre
  actif, 8 mismatches et 0 position locale. L'UI doit donc afficher une verite
  broker claire: 8 positions broker, 0 ordre ouvert broker, et les positions sans
  stop comme critiques.

Objectif produit:

- Quand TWS est connecte, l'onglet doit etre un miroir TWS.
- Les positions affichees doivent etre les positions TWS ouvertes.
- Les ordres affiches doivent etre les ordres TWS ouverts / prepares.
- Les executions doivent venir des fills TWS.
- Les intentions locales sans ordre broker correspondant doivent etre separees ou
  marquees comme orphelines, jamais confondues avec des ordres ouverts.

Correction technique proposee:

- Creer une projection unique pour l'onglet, par exemple
  `trading_book_snapshot`, construite en une seule passe depuis:
  `broker.positions()`, `broker.open_orders()`, `broker.recent_executions()` et
  les metadonnees locales uniquement en enrichissement.
- Calculer les compteurs du bandeau depuis les memes listes que les tableaux.
- En mode TWS connecte:
  - `positions_table = broker_positions where quantity != 0`;
  - `orders_table = broker_open_orders`;
  - `local_orphans = local active orders without broker match`.
- En mode TWS deconnecte:
  - afficher le fallback local comme tel;
  - afficher le statut `DISCONNECTED` ou `STALE`;
  - ne pas presenter le fallback comme temps reel.
- Ne plus laisser `broker_reality` persistant et les listes courantes raconter
  deux histoires differentes sur le meme ecran.

Tests requis:

- Broker connecte avec 8 positions, 0 ordre, 0 local:
  bandeau positions = 8, ordres = 0, table positions = 8, table ordres vide.
- Broker connecte avec ordre TWS manuel:
  l'ordre apparait meme sans ligne locale, identifie par `permId` si `orderId=0`.
- Ordre local actif sans match TWS:
  il est affiche comme `LOCAL_ORPHAN` ou dans une section separee, et ne compte
  pas comme ordre broker actif.
- Broker deconnecte:
  fallback local affiche avec source explicite `LOCAL_FALLBACK`, age/stale visible.
- Stops absents sur positions broker:
  positions marquees critiques, sans inventer de stop local.

Plan d'execution valide le 2026-07-07:

1. Ajouter des tests de snapshot complet dans
   `tests/test_orders_positions_broker_truth.py`:
   - un broker connecte avec 8 positions et 0 ordre doit produire une seule vue
     coherente;
   - les compteurs du bandeau doivent etre calcules depuis les memes listes que
     les tableaux;
   - un ordre local actif sans match TWS ne doit pas compter comme ordre broker.
2. Refactorer `TradingEngine._build_snapshot()` pour construire une projection
   unique "trading book" avant les metriques:
   - positions de l'onglet;
   - ordres de l'onglet;
   - ordres locaux orphelins;
   - executions;
   - compteurs derives.
3. En mode TWS connecte:
   - positions = positions broker ouvertes;
   - ordres = ordres broker ouverts/prepares;
   - local active sans match broker = `LOCAL_ORPHAN`, separe ou non compte actif.
4. En mode TWS deconnecte:
   - garder le fallback local, mais le marquer clairement `LOCAL_FALLBACK` /
     `STALE`;
   - ne pas le compter comme verite broker.
5. Ajuster le frontend seulement si necessaire pour afficher `LOCAL_ORPHAN` sans
   le confondre avec un ordre TWS actif.
6. Lancer les tests cibles:
   `python -m unittest tests.test_orders_positions_broker_truth tests.test_tws_connector_parsing`.
7. Ne pas modifier la base SQLite reelle; seulement lire l'etat si un diagnostic
   supplementaire est necessaire.

## P1 - Garde-fous autour des actions sensibles

- Ajouter une garde explicite cote route pour `/api/orders/{id}/simulate-fill`:
  autorise uniquement avec le connecteur `simulated`.
- Valider le payload de `simulate-fill` avec Pydantic.
- Verifier que `DELETE /api/orders/{id}` reste limite a l'historique local et ne
  peut jamais masquer un ordre broker actif.
- Verifier que `cancel-order` utilise toujours l'identifiant broker reel quand la
  ligne vient de TWS.

## P2 - Documentation et coherence projet

- Aligner `docs/implementation-status.md` avec l'etat reel du code.
- Archiver ou supprimer les anciennes notes qui disent que les etapes 10-13 sont
  a faire si elles sont deja acceptees ailleurs.
- Garder `docs/Lecture_des_donnees_TWS_IBKR.md` comme reference pour:
  positions != ordres != executions != valeurs de compte.
- Ajouter une courte note dans la doc architecture: l'onglet Ordres & Positions
  doit etre alimente par une projection broker-truth unique.

## P3 - Nettoyage hors chemin critique

- Revoir `scripts/populate_sectors.py` avant usage:
  - mode dry-run;
  - confirmation explicite;
  - pas d'ecriture directe silencieuse dans `data/trading_state.sqlite`.
- Continuer a reduire les overrides `mypy` sur les modules critiques:
  `engine`, `broker`, `storage`.
- Ne pas lancer de nouvelles features scanner/forecasting avant stabilisation de
  l'onglet Ordres & Positions.

## Invariants a ne pas casser

- TWS / IBKR est la source de verite pour positions, ordres ouverts, stops actifs,
  executions et P&L compte.
- Le stockage local garde l'intention, l'historique et les traces; il ne remplace
  pas la verite broker.
- Seul `OrderManager` envoie, annule ou modifie un ordre broker.
- Une opportunite, un forecast ou un scenario draft reste consultatif et ne peut
  jamais contourner setup validation, risk engine, broker reality et order manager.
- Aucun ordre d'entree ne doit partir sans stop protecteur broker-ready.
