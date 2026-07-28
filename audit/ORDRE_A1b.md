ORDRE DE TRAVAIL — A-1b : protéger les alarmes contre l'écrasement par une clôture.
Écris d'abord audit/ORDRE_A1b.md (mot pour mot), puis exécute.

═══ 1. CONTEXTE FIGÉ (audits 61 P3, 63 §8 ; décision "l'alarme prime") ═══
- A-1 fait écrire CLOSED / PARTIAL_EXIT sur un fill SELL.
- La garde centrale S5b-3a (repositories.py, update_setup_status) bloque
  l'écriture d'un statut ACTIF par-dessus une alarme, MAIS son ensemble
  _ACTIVE_STATUSES ne contient PAS CLOSED. PARTIAL_EXIT, lui, y est déjà.
- Conséquence : aujourd'hui, une clôture CLOSED peut écraser une alarme
  MANUAL_REVIEW_REQUIRED / ERROR_REQUIRES_MANUAL_REVIEW en silence — contraire
  à la décision "l'alarme prime : une clôture ne doit pas effacer une alarme
  de sécurité".

═══ 2. PÉRIMÈTRE ═══
AUTORISÉ : app/storage/repositories.py (l'ensemble _ACTIVE_STATUSES
uniquement) + tests. Éventuellement les cliquets si l'ajout les fait échouer.
INTERDIT : la garde elle-même (sa logique ne change pas, seul l'ensemble
qu'elle consulte s'élargit), reconciliation.py (A-1, déjà fait),
tout app/setups/, order_manager.py.

═══ 3. CHANGEMENT ═══
Ajouter SetupStatus.CLOSED.value à _ACTIVE_STATUSES dans repositories.py.
Vérifier d'abord (grep) que _ACTIVE_STATUSES ne sert QU'à la garde
alarme→actif ; s'il sert ailleurs, vérifier que l'ajout de CLOSED n'y crée
pas d'effet indésirable, et si doute → ARRÊTE-TOI et signale.
Commenter : CLOSED est un statut terminal qui ne doit pas écraser une alarme
(une position fermée n'annule pas le besoin de revue humaine — décision
"l'alarme prime", audit 61 P3).

═══ 4. INVARIANTS ═══
- Un fill SELL sur un setup NON alarmé → clôture normale (CLOSED/PARTIAL_EXIT
  s'écrivent). Comportement A-1 inchangé pour ce cas.
- Un fill SELL sur un setup EN alarme → l'écriture CLOSED/PARTIAL_EXIT est
  BLOQUÉE par la garde, le setup RESTE en alarme. C'est le comportement voulu.
- Aucune autre entrée de _ACTIVE_STATUSES retirée. PARTIAL_EXIT y reste.
- La logique de la garde (update_setup_status) n'est pas modifiée.

═══ 5. PREUVE DE SORTIE ═══
1. Test : setup en MANUAL_REVIEW_REQUIRED, tentative d'écrire CLOSED via
   update_setup_status (sans allow_from_review) → BLOQUÉE, statut reste
   MANUAL_REVIEW_REQUIRED. (Miroir du test CLOSED de la garde S5b-3a.)
2. Test : setup en ENTRY_ORDER_PLACED (non alarme), écrire CLOSED → passe.
3. Test bout-en-bout (recoupe A-1) : un fill SELL total sur un setup EN
   alarme → la position se ferme au broker mais le setup reste
   MANUAL_REVIEW_REQUIRED (l'alarme prime). Un fill SELL total sur un setup
   NON alarmé → CLOSED (A-1 nominal).
4. Le cliquet test_active_status_write_sites.py (S5b-2) : s'il liste les
   statuts actifs, met CLOSED à jour dedans si nécessaire, et prouve qu'il
   passe.
5. Suite complète : seul test_account_metrics.py en échec.

═══ 6. COMMIT ═══
Branche fix/a1b-closed-sticky-alarm, EMPILÉE sur fix/a1-close-on-sell
(PAS sur feat/setup-conditions — les deux se mergent ensemble).
Commit avant rapport. Message :
"fix(repository): protect review alarms from being cleared by position close"

═══ 7. RAPPORT ═══
audit/64_rapport_a1b.md selon template + confrontation à ORDRE_A1b.md.

═══ 8. INTERDICTIONS ═══
Aucun refactoring, aucune suppression, aucune modification de la logique de
garde. Doute sur un usage de _ACTIVE_STATUSES → ARRÊTE-TOI.
