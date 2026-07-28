ORDRE DE TRAVAIL — C-1 : réparer les setups figés au redémarrage (S58.3 + fenêtre A-1).
Écris d'abord audit/ORDRE_C1.md (mot pour mot), puis exécute.

═══ 1. CONTEXTE FIGÉ (audits 69/70, ne pas re-diagnostiquer) ═══
- Deux fenêtres de crash non atomiques laissent un setup figé :
  * S58.3 (entrée) : ENTRY_FILLED avec position + stop actif, resté figé car
    protection_status == POSITION_OPEN_STOP_ACTIVE, qu'A-3 ne scanne pas.
  * Fenêtre A-1 (sortie complète) : position localement soldée (quantity 0,
    CLOSED) mais setups.status resté IN_POSITION/MANAGING_POSITION/PARTIAL_EXIT.
- self.progression existe dans ReconciliationEngine (construit dans __init__).
  has_active_protection → mark_in_position(protection_verified=True) est
  appelable telle quelle ; ENTRY_FILLED → IN_POSITION est légale dans la table.
- get_position(symbol) conserve la ligne soldée (quantity 0). positions est
  indexée PAR SYMBOLE : le test position["setup_id"] == setup["setup_id"] est
  OBLIGATOIRE pour C-1b (sinon faux positif si un autre setup a réutilisé le
  symbole — démontré audit 70 Q1).
- La sortie PARTIELLE figée n'a pas de critère local sûr → HORS PÉRIMÈTRE.

═══ 2. PÉRIMÈTRE ═══
AUTORISÉ : app/engine/reconciliation.py (extension de la logique de détection
au démarrage) + tests.
INTERDIT : post_fill_progression.py, position_manager.py, repositories.py,
state_machine.py / ALLOWED_TRANSITIONS (ENTRY_FILLED→IN_POSITION déjà légale,
rien à ajouter), order_manager.py.
Ne PAS toucher les branches d'alarme existantes d'A-3.

═══ 3. CHANGEMENT ═══
Étendre la détection au démarrage (dans _detect_unprotected_entry_orphans ou
une fonction sœur appelée au même point, run(startup=True) uniquement) avec
DEUX nouvelles branches de RÉPARATION (pas d'alarme) :

(C-1) ENTRÉE FIGÉE — pour un setup dont protection_snapshot_for_setup renvoie
    POSITION_OPEN_STOP_ACTIVE :
      - appeler self.progression.has_active_protection(setup_id)
      - si True → self.progression.mark_in_position(setup_id,
        protection_verified=True). (Ne PAS passer un littéral ; passer le
        résultat réel de has_active_protection.)
      - émettre un événement de réparation UNIQUEMENT si le statut a
        réellement changé (le setup était ENTRY_FILLED, devient IN_POSITION).
        PAS d'événement si le setup est déjà IN_POSITION (réécriture
        idempotente au démarrage suivant → silencieuse).

(C-1b) SORTIE COMPLÈTE FIGÉE — pour un setup tel que :
      status ∈ {IN_POSITION, MANAGING_POSITION, PARTIAL_EXIT}
      ET position = get_position(setup.symbol) is not None
      ET position["setup_id"] == setup["setup_id"]   ← OBLIGATOIRE
      ET int(position["quantity"]) == 0
    → update_setup_status(setup_id, CLOSED, "Position closed reconciled at
      startup") en direct (patron identique à _handle_sell_fill:803).
      Émettre un événement de réparation.
    Ce cas s'auto-exclut au démarrage suivant (CLOSED est terminal, filtré
    en tête). VÉRIFIER que le filtre _TERMINAL_SETUP_STATUSES est bien testé
    AVANT cette logique.

Les deux branches restent mutuellement exclusives avec les branches d'alarme
d'A-3 (un seul protection_status par appel — audit 69 §3.2). Ne pas modifier
les branches d'alarme.

═══ 4. INVARIANTS ═══
- C-1 : un ENTRY_FILLED + stop actif → IN_POSITION. Un ENTRY_FILLED SANS stop
  actif → reste géré par la branche d'alarme A-3 existante (POSITION_OPEN_
  STOP_MISSING_CRITICAL), PAS par C-1. Mutuellement exclusifs.
- C-1b : ne clôture JAMAIS un setup dont la ligne positions porte un autre
  setup_id (test d'égalité obligatoire). Ne clôture jamais un setup jamais
  entré (position is None).
- Réparation ≠ alarme : C-1/C-1b écrivent un statut de progression normale
  (IN_POSITION/CLOSED), PAS MANUAL_REVIEW_REQUIRED.
- En marche normale (startup=False) : aucune de ces branches ne s'exécute.
- mark_in_position reçoit le résultat réel de has_active_protection, jamais
  un littéral True.
- Aucune transition ajoutée à la table. Aucun appel broker.

═══ 5. PREUVE DE SORTIE ═══
1. C-1 : setup ENTRY_FILLED + position + stop actif, run(startup=True) →
   IN_POSITION, événement de réparation émis.
2. C-1 idempotence : 2e run(startup=True) → reste IN_POSITION, AUCUN nouvel
   événement (la réécriture est silencieuse). C'est le test que l'audit 70
   signale manquant — écris-le.
3. C-1 exclusion : setup ENTRY_FILLED + position SANS stop actif →
   PAS de progression vers IN_POSITION ; c'est la branche d'alarme A-3 qui
   agit (MANUAL_REVIEW_REQUIRED). Prouve la mutuelle exclusion.
4. C-1b : setup IN_POSITION + position même setup_id + quantity 0 →
   CLOSED, événement émis.
5. C-1b FAUX POSITIF évité : setup A IN_POSITION + ligne positions du même
   symbole mais setup_id=B + quantity 0 → A n'est PAS clôturé (reste
   IN_POSITION). C'est le test critique de l'audit 70 — écris-le.
6. C-1b idempotence : après clôture, 2e run → setup CLOSED exclu par le
   filtre terminal, aucune ré-écriture.
7. C-1b jamais-entré : setup IN_POSITION mais get_position renvoie None →
   pas de clôture (ne peut pas arriver en pratique, mais prouve la garde).
8. Non-régression : les branches d'alarme A-3 et les tests
   StartupOrphanDetectionTests passent sans modification.
9. Suite complète : seul test_account_metrics.py en échec.

═══ 6. COMMIT ═══
Branche fix/c1-repair-frozen-setups, depuis feat/setup-conditions.
Commit avant rapport. Message :
"fix(reconciliation): repair setups frozen by non-atomic fill/close at startup (root C, S58.3)"

═══ 7. RAPPORT ═══
audit/71_rapport_c1.md selon template + confrontation à ORDRE_C1.md.

═══ 8. INTERDICTIONS ═══
Aucun ajout à la table, aucun appel broker, aucun refactoring, aucune
suppression. Ne pas couvrir la sortie partielle figée (hors périmètre,
documenter en dette). Doute → tu t'ARRÊTES.
