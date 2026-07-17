# Setup Conditions Catalog

Status: V2.4 contract
Last updated: 2026-07-16

## Purpose

Ce document est la reference du catalogue des setups et de leurs checklists de
conditions d'entree, telles qu'affichees dans la section "Ce que cherche le
setup" de la page detail d'un setup. Le code et ce document doivent rester
synchronises:

- definitions des checklists: `app/setups/setup_conditions.py`
- evaluation sequentielle + persistance: `app/engine/setup_condition_tracker.py`
- exposition API: `GET /api/setups/{setup_id}` (champ `setup_conditions`)
- rendu UI: `app/gui/static/js/setup-conditions.js` + `setup_detail.html`

Regle non negociable: chaque condition affichee correspond a un calcul
reellement effectue par le `evaluate()` du setup (`app/setups/*.py`). Aucune
checklist decorative. Les conditions cibles non calculees par le moteur sont
listees dans la section "Catalogue cible et ecarts" ci-dessous, jamais
affichees dans l'UI.

## Structure `setup_conditions` (API)

```json
{
  "setup_id": "ANET_20260713_001",
  "setup_type": "pullback_continuation",
  "setup_name": "Pullback Continuation",
  "setup_direction": "long",
  "management_only": false,
  "conditions": [
    {
      "id": "uptrend",
      "label": "Tendance haussiere confirmee",
      "description": "EMA20 au-dessus de l'EMA50 sur le flux de cotation",
      "status": "validated",
      "validated_at": "2026-07-16T14:05:12+00:00",
      "observed_value": "EMA20 50.00 / EMA50 48.00",
      "target": "EMA20 au-dessus de l'EMA50"
    }
  ],
  "current_step": 1,
  "overall_status": "watching",
  "invalidation_reason": "",
  "summary_message": "1/3 conditions validees - etape actuelle: Retour du prix sur l'EMA20",
  "updated_at": "2026-07-16T14:05:12+00:00"
}
```

- `status` par condition: `validated` | `in_progress` | `pending` | `failed`.
- `overall_status`: `watching` | `ready_to_enter` | `entered` | `invalidated`.
- `current_step`: index 0-base de la condition en cours d'observation (`null`
  si terminee ou invalidee).
- `management_only`: `true` pour les setups sans sequence d'entree
  (`runner`, `trailing_runner`, `position_management`) — `conditions` est vide.

## Regles d'evaluation sequentielle

- Les conditions sont evaluees dans l'ordre; une seule est `in_progress` a la
  fois, les suivantes restent `pending`.
- Les transitions du state machine servent de plancher: par exemple un setup
  en `WAITING_ENTRY_SIGNAL` a forcement valide sa premiere condition (c'est le
  moteur qui a decide la transition).
- `validated_at` et `observed_value` sont persistes en base
  (`setup_condition_states`) au moment de la validation et ne sont jamais
  recalcules au rafraichissement.
- `overall_status` suit le signal moteur reel: `ENTRY_READY` -> tout valide ->
  `ready_to_enter`; ordre transmis / position -> `entered`; signal
  `INVALIDATE` -> `invalidated` avec `invalidation_reason` = raison moteur et
  la condition en cause passe `failed`.
- Rearmement lifecycle (retour a `WAITING_ACTIVATION` apres un etat terminal):
  la sequence repart de zero.
- Le tracker est alimente par `StockMarketMonitor.analyze_market_snapshot` a
  chaque analyse (meme rythme que le reste de la page); il n'est jamais
  bloquant pour le trading (echec logge, jamais propage).

## Checklists reelles par setup (source de verite moteur)

### pullback_continuation — Pullback Continuation (long)

| Ordre | id | Verification moteur (app/setups/pullback_continuation.py) |
|---|---|---|
| 1 | `uptrend` | EMA20 > EMA50 (transition `WAITING_ACTIVATION` -> `WAITING_ENTRY_SIGNAL`) |
| 2 | `pullback_to_ema20` | Prix <= EMA20 |
| 3 | `bullish_rejection` | Bougie haussiere (`bullish_candle` ou cloture > ouverture) |

Invalidation moteur: prix < EMA50 ("Price lost EMA 50 trend filter") -> `uptrend` en echec.

### momentum_breakout — Momentum Breakout (long)

Les valeurs affichees proviennent de `metadata.analysis` produit par le setup
lui-meme (jamais recalculees). Ordre = ordre reel des early-returns de
`_analyze_long`:

| Ordre | id | Verification moteur (app/setups/momentum_breakout.py) |
|---|---|---|
| 1 | `market_data_ready` | Bid/ask, ATR 15m/1h, tick, spread disponibles |
| 2 | `spread_ok` | Spread <= plafond bps (15/30/60 selon cap tier) et <= 0.20 x ATR 15m |
| 3 | `price_not_extended` | Ask <= limite max + buffer anti-chase (sinon `MISSED_BREAKOUT`, cible = zone de retest) |
| 4 | `breakout_confirmed` | Un des 3 chemins: FAST (volume >= 1.5x + cloture > resistance), CONFIRMED (2 barres au-dessus + ratio >= 0.8), RETEST |
| 5 | `price_within_limit` | Ask <= `maximum_limit_price` calculee |
| 6 | `structural_stop_available` | Support structurel sous le trigger (higher low, support, retest low) |
| 7 | `risk_approved` | Risque par action > 0 et quantite max >= 1 dans le budget risque |

Pas de chemin INVALIDATE dans `evaluate()`: les etats `MISSED_BREAKOUT`/`STALE_SETUP`
sont geres par statut et lifecycle.

### breakout_retest — Breakout Retest (long)

| Ordre | id | Verification moteur (app/setups/breakout_retest.py) |
|---|---|---|
| 1 | `breakout_confirmed` | Cloture journaliere > `breakout.daily_close_above` (transition vers `WAITING_ENTRY_SIGNAL`) |
| 2 | `retest_of_level` | `retest.zone_min` <= prix <= `retest.zone_max` |
| 3 | `bullish_confirmation` | Bougie haussiere pendant le retest |

Invalidation moteur: cloture < `retest.no_close_below` (defaut `zone_min`) -> `retest_of_level` en echec.

### range_breakout — Range Breakout (long)

| Ordre | id | Verification moteur (app/setups/range_breakout.py) |
|---|---|---|
| 1 | `range_holds` | Aucune cloture sous `range.low` |
| 2 | `resistance_break` | Prix > `range.high` (declenche l'entree) |

Invalidation moteur: cloture < `range.low` ("Close below range low") -> `range_holds` en echec.

### aggressive_rebound — Aggressive Rebound / Support Bounce (long)

| Ordre | id | Verification moteur (app/setups/aggressive_rebound.py) |
|---|---|---|
| 1 | `price_at_support` | `support_zone.min` <= prix <= `support_zone.max` (transition vers `WAITING_ENTRY_SIGNAL`) |
| 2 | `bullish_rejection` | Bougie haussiere |
| 3 | `reclaim_previous_high` | Cloture > precedent haut (`previous_high`, fallback high/zone max) |

Invalidation moteur: cloture < `invalidation.close_below` (defaut `support_zone.min`) -> `price_at_support` en echec.

### runner / trailing_runner / position_management (MANAGEMENT_ONLY)

Pas de sequence d'entree: la section affiche un etat sobre
("Setup de gestion de position: pas de sequence d'entree a verifier").

## Catalogue cible et ecarts (roadmap moteur)

Cible fonctionnelle (checklists completes souhaitees). Les conditions marquees
"non calculee" n'existent pas encore dans le moteur ni dans `MarketSnapshot`;
elles ne sont PAS affichees dans l'UI tant que le moteur ne les calcule pas.

| Setup cible | Conditions cibles non calculees aujourd'hui |
|---|---|
| Pullback Continuation | `breakout_confirmed` (cassure prealable), `volume_resumption` (volume policy = WARNING_ONLY, non bloquant) |
| Breakout (momentum) | `consolidation` (detection de range prealable), `volume_contraction` (contraction pendant la consolidation) |
| Breakout-Retest | `volume_resumption` (non bloquant aujourd'hui) |
| Support Bounce / Range | `range_identified` (support/resistance testes >= 2 fois), `selling_exhaustion` (volume decroissant), `momentum_turn` (RSI/MACD absents de MarketSnapshot) |
| Bottom Reversal | Setup entier absent du moteur (divergences, base, changement de structure HH) |
| Variantes short (breakdown, pullback short) | Moteur long-only (`momentum_breakout` rejette explicitement `direction != long`) |

Checklists cibles detaillees des setups absents (pour implementation future):

- Bottom Reversal (long): 1 `downtrend_exhaustion` (divergence RSI/MACD ou plus
  bas non confirme), 2 `base_formation` (double bottom / arret des plus bas),
  3 `structure_shift` (premier HH, cassure du dernier lower high),
  4 `volume_confirmation` (volume acheteur en hausse), 5 `trend_filter_reclaim`
  (reprise EMA20/EMA50).
- Breakdown / Pullback Continuation Short: miroir des setups long
  (`downtrend`, `breakdown_confirmed`, `pullback_to_resistance`,
  `bearish_rejection`, `volume_resumption`).

Toute extension moteur doit: ajouter la detection dans le setup concerne (ou
`MarketSnapshot`), ajouter la condition dans `setup_conditions.py`, mettre a
jour ce document et les tests.

## UI (section "Ce que cherche le setup")

Emplacement: page detail setup (`setup_detail.html`), entre le panneau Graphe
et "Forecast stack summary". Etats du composant:

- `watching`: checklist + barre de progression "X/N conditions validees" +
  "Etape i/N - label" + `summary_message`.
- `ready_to_enter`: bandeau vert "Toutes les conditions sont reunies -> signal d'entree".
- `entered`: bandeau vert "Position prise".
- `invalidated`: bandeau rouge avec `invalidation_reason`; condition en cause en rouge.
- `management_only` / aucune checklist: message sobre, pas de liste.

Icones par condition: validee (verte, valeur observee + horodatage), en cours
(jaune, cible attendue + valeur courante), en attente (grisee), invalidee
(rouge).
