# ORDRE DE TRAVAIL — A6-SEC : ne jamais ressusciter un setup terminal en
statut d'ordre actif ; alerter à la place.
Écris d'abord audit/ORDRE_A6SEC.md (mot pour mot), puis exécute.

═══ 1. CONTEXTE FIGÉ (audits 49/50, ne pas re-diagnostiquer) ═══
- reconciliation.py, branche SUBMITTED, lignes ~477-483 : quand un setup est
  dans _TERMINAL_SETUP_STATUSES ({CLOSED, CANCELLED, EXPIRED, INVALIDATED,
  ERROR}) et qu'un ordre broker apparaît encore ouvert, le setup est réécrit
  en ENTRY_ORDER_PLACED / STOP_ORDER_PLACED ("Open order restored from TWS").
  Ces couples sont ABSENTS d'ALLOWED_TRANSITIONS et non couverts par la garde
  S5b-3a (départ non-alarme).
- Cette branche n'a JAMAIS été exécutée sur les données de la base (audit 50
  Q3) — risque réel mais non matérialisé.
- Décision : on ne bloque pas seul (laisserait une incohérence silencieuse),
  on n'annule pas automatiquement (repose sur un statut terminal dont la
  fiabilité est en doute, audit 49). On ALERTE : réécrire en
  MANUAL_REVIEW_REQUIRED. L'ordre broker reste inchangé.
- MANUAL_REVIEW_REQUIRED est une cible LÉGALE depuis les statuts terminaux ?
  À VÉRIFIER dans ALLOWED_TRANSITIONS avant d'écrire (point 3). Si non légale
  pour certains, c'est une écriture directe comme les autres du fichier —
  cohérent avec le reste, mais note-le.

═══ 2. PÉRIMÈTRE ═══
AUTORISÉ : app/engine/reconciliation.py (la seule branche terminale de
_update_setup_after_reconciled_order) + tests.
INTERDIT : la branche _REVIEW_LOCKED (S5b-1, ne pas toucher), la branche
FILLED (3b-2), state_machine.py, setup_engine.py, order_manager.py.
Ne PAS ajouter d'appel broker (cancel_order) : l'annulation est une dette
séparée (geste humain futur), pas ce lot.

═══ 3. CHANGEMENT ═══
Dans la branche SUBMITTED, remplacer le bloc qui réécrit target_status
(ENTRY_ORDER_PLACED/STOP_ORDER_PLACED) quand setup_status ∈
_TERMINAL_SETUP_STATUSES par :
  - écrire MANUAL_REVIEW_REQUIRED (pas le statut actif),
  - avec un status_reason explicite du type "Broker shows an open <side>
    order for a terminal setup (<setup_status>) — needs manual review",
  - émettre un événement (mécanisme event_store déjà présent dans ce fichier)
    de niveau WARNING, distinct, ex. "reconciliation_terminal_setup_open_order",
    avec setup_id, symbol, broker_order_id, le statut terminal d'origine, le
    side, et le target_status qui AURAIT été écrit par l'ancien code (pour
    traçabilité).
  - NE PAS toucher à l'ordre broker ni à l'ordre local (déjà marqué SUBMITTED
    en amont, hors périmètre).
Vérifier d'abord si MANUAL_REVIEW_REQUIRED est légal depuis chaque statut
terminal dans ALLOWED_TRANSITIONS ; si oui, tu peux même envisager de passer
par can_transition (cohérent). Si non pour certains, écriture directe comme
le fait déjà le reste du fichier — documente le choix dans le rapport.

═══ 4. INVARIANTS ═══
- Un setup terminal + ordre broker ouvert → devient MANUAL_REVIEW_REQUIRED,
  JAMAIS un statut actif. Plus aucune "résurrection".
- La branche _REVIEW_LOCKED (alarmes) reste strictement inchangée.
- L'ordre broker n'est ni annulé ni modifié.
- Les statuts NON terminaux et NON alarme dans cette branche : comportement
  inchangé (le code n'écrivait rien pour eux — return silencieux ; ça reste).

═══ 5. PREUVE DE SORTIE ═══
1. Test : setup CANCELLED + ordre broker SELL ouvert → statut devient
   MANUAL_REVIEW_REQUIRED (pas STOP_ORDER_PLACED), événement WARNING émis,
   ordre broker non touché (aucun cancel_order appelé — vérifie via mock).
2. Test : setup CLOSED + ordre broker BUY ouvert → MANUAL_REVIEW_REQUIRED
   (pas ENTRY_ORDER_PLACED).
3. Test de non-régression : setup en alarme (_REVIEW_LOCKED) + ordre
   SUBMITTED → comportement S5b-1 inchangé (reste en alarme, event
   reconciliation_skipped_review_locked).
4. Preuve négative : mute l'assertion la plus spécifique du test 1 (statut
   final == MANUAL_REVIEW_REQUIRED) → échec, puis reverte, diff nul.
5. Le cliquet A6 (test_review_status... ou le futur 3e cliquet) n'est pas
   requis ici — mais si order_manager/reconciliation apparaît dans un cliquet
   existant avec une justification obsolète, mets-la à jour.
6. Suite complète : seul test_account_metrics.py en échec.

═══ 6. COMMIT ═══
Branche fix/a6sec-no-terminal-resurrection, depuis feat/setup-conditions.
Commit avant rapport. Message :
"fix(reconciliation): alert instead of resurrecting terminal setups with open broker orders"

═══ 7. RAPPORT ═══
audit/51_rapport_a6sec.md selon template + confrontation à ORDRE_A6SEC.md.

═══ 8. INTERDICTIONS ═══
Aucun appel broker ajouté, aucun refactoring, aucune suppression, aucune
modification hors la branche terminale visée.
