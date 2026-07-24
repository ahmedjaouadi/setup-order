ORDRE DE TRAVAIL — S4 : verrouiller la protection du chemin manuel.
Lot de TESTS + commentaire. Aucune logique de production modifiée.

Écris d'abord cet ordre dans audit/ORDRE_S4.md (mot pour mot), puis exécute.

═══ 1. CONTEXTE FIGÉ (audit 32, ne pas re-diagnostiquer) ═══
Le chemin manuel BUY est protégé par trade_guards._exposure_verdict
(trade_guards.py:438-467), règle block_if_position_on_same_symbol, première
condition évaluée, opérant par SYMBOLE. Active dans la config chargée
(DEFAULT_CONFIG settings.py:76-83 ; config.yaml ne surcharge pas
trade_guards). Zéro ordre manuel jamais soumis en production.
Deux écarts subsistent : aucun test bout-en-bout de la combinaison exacte,
et la protection dépend d'un booléen de configuration non verrouillé.

═══ 2. PÉRIMÈTRE ═══
AUTORISÉ :
 - tests/test_manual_orders.py (test bout-en-bout)
 - un fichier de test pour le cliquet de configuration
 - app/engine/manual_order_service.py : COMMENTAIRE UNIQUEMENT, aucune
   ligne de code exécutable ajoutée, modifiée ou supprimée
INTERDIT :
 - toute logique dans app/ (y compris _assess_buy, _submit_buy)
 - trade_guards.py, settings.py, config.yaml
 - toute modification de la configuration elle-même
Si tu penses qu'une logique de production doit changer, ARRÊTE-TOI.

═══ 3. CHANGEMENT ═══
(a) TEST BOUT-EN-BOUT dans tests/test_manual_orders.py, en réutilisant le
    harness existant (celui de test_guard_block_returns_422) :
    seed d'une position ouverte sur un symbole → soumission d'un BUY manuel
    sur CE MÊME symbole → assertions :
      - la soumission est bloquée avec reason_code
        CONFLICT_WITH_OPEN_POSITION
      - AUCUN ordre n'est transmis (list_orders vide pour le setup_id généré)
      - une decision_trace MANUAL_ORDER est bien écrite avec le motif de
        blocage
(b) CLIQUET DE CONFIGURATION : un test qui asserte que, dans la
    configuration EFFECTIVEMENT chargée par load_settings() :
      - trade_guards.enabled est True
      - trade_guards.exposure.enabled est True
      - trade_guards.exposure.block_if_position_on_same_symbol est True
    Le test doit échouer si l'un de ces trois passe à False.
    Docstring obligatoire expliquant POURQUOI ce test existe : la protection
    du chemin manuel BUY contre l'empilement sur un titre déjà détenu repose
    entièrement sur ce paramètre ; le désactiver retire la seule barrière.
(c) COMMENTAIRE dans manual_order_service.py, à l'endroit de l'appel à
    trade_guards.evaluate_entry (:203) : indiquer que la protection par
    symbole du chemin manuel provient de ce garde, qu'elle dépend de
    trade_guards.exposure.block_if_position_on_same_symbol, et renvoyer au
    test-cliquet. COMMENTAIRE SEULEMENT.

═══ 4. INVARIANTS ═══
 - git diff sur app/ ne montre QUE des lignes de commentaire ajoutées.
   Zéro ligne de code exécutable touchée. Prouve-le.
 - aucune assertion existante modifiée
 - aucune modification de config.yaml ni de DEFAULT_CONFIG

═══ 5. PREUVE DE SORTIE ═══
 1. Le test bout-en-bout passe. Sortie brute.
 2. PREUVE NÉGATIVE du test bout-en-bout : mute l'assertion la plus
    SPÉCIFIQUE (le reason_code CONFLICT_WITH_OPEN_POSITION, pas l'absence
    d'ordre) et montre qu'il échoue. Reverte, montre le diff nul.
 3. PREUVE NÉGATIVE du cliquet : par monkeypatch temporaire dans un run
    jetable, force block_if_position_on_same_symbol à False et montre que
    le cliquet échoue. Ne modifie JAMAIS config.yaml ni settings.py.
    Reverte, montre le diff nul.
 4. git diff feat/setup-conditions..HEAD -- app/ : uniquement des lignes
    de commentaire. Sortie brute.
 5. Suite complète : seul test_account_metrics.py en échec.

═══ 6. COMMIT ═══
Branche fix/s4-manual-path-guard, depuis feat/setup-conditions.
Commit AVANT rapport. Message :
"test: lock symbol-level guard protecting the manual buy path"

═══ 7. RAPPORT ═══
audit/33_rapport_s4.md selon le template, avec confrontation littérale
point par point à audit/ORDRE_S4.md.

═══ 8. INTERDICTIONS ═══
Aucun refactoring, aucune suppression de branche/stash/fichier, aucune
correction hors périmètre. Doute → tu t'arrêtes.
