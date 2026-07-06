# skills.md — Base de compétences Trading pour Module Intelligent

> **Version : 2.0 — Actualisation du 2026-07-05**
>
> Objectif : définir les connaissances, techniques, fondamentaux, setups, règles de décision et garde-fous nécessaires pour construire un module intelligent capable de détecter, qualifier, filtrer et gérer des opportunités de trading.
>
> Ce document est conçu pour un système de trading assisté par IA, en priorité en **mode paper/simulation**, avec analyse multi-timeframe, gestion du risque stricte, détection de setups, et génération de configurations exploitables par un moteur d’ordres.

## Changelog v2.0

```text
[HARMONISÉ] Vocabulaire de décision unifié : modèle status + reason_code (sections 2.5, 29, 40).
[AJOUT] Section 6bis  — RVOL (Relative Volume) comme mesure de référence.
[AJOUT] Section 24bis — Frais, slippage et coûts de transaction.
[AJOUT] Section 25bis — Sessions et horaires de trading (open, lunch, power hour).
[AJOUT] Section 25ter — Halts (LULD), SSR, PDT et contraintes réglementaires US.
[AJOUT] Section 27bis — Gestion des ordres : bracket/OCA, TIF, fills partiels, ordres orphelins.
[AJOUT] Section 28bis — Contrôle qualité des données (data quality gate).
[AJOUT] Section 34.4  — Circuit breakers journaliers (kill switch).
[AJOUT] Section 34.5  — Limites d’exposition et de corrélation multi-positions.
[AJOUT] Section 30bis — Versioning des configurations de setup.
[ENRICHI] Section 9   — Scoring détaillé avec sous-critères par composant.
[ENRICHI] Section 32  — Learning engine : tags de contexte et boucle de calibration.
[CORRIGÉ] Numérotation et cohérence interne.
```

---

## 1. Mission du module intelligent de trading

Le module intelligent de trading doit agir comme un **analyste technique + risk manager + validateur de setup**. Il ne doit pas seulement repérer un signal, mais vérifier si le trade est réellement exploitable selon un cadre précis.

Ses responsabilités principales :

1. Détecter les opportunités de trading.
2. Classer le type de setup.
3. Identifier les niveaux clés : résistance, support, breakout, retest, entry, stop.
4. Vérifier le contexte de marché.
5. Évaluer le volume, la volatilité et le momentum.
6. Calculer le risque par action et la taille de position.
7. Refuser les trades trop tardifs, trop éloignés ou incohérents.
8. Produire une configuration claire, testable et exécutable.
9. Suivre la position après entrée.
10. Apprendre des trades gagnants, perdants, ratés et invalidés.

Le module ne doit jamais chercher à prédire parfaitement le marché. Il doit plutôt répondre à cette question :

> Est-ce que ce setup offre une entrée propre, un risque contrôlé, une invalidation claire et un avantage statistique suffisant pour être tenté ?

---

## 2. Principes fondamentaux

### 2.1 Le prix est prioritaire

Le prix reste la donnée principale. Les indicateurs ne doivent jamais remplacer la structure du prix.

Le module doit d’abord analyser :

- La tendance.
- Les supports.
- Les résistances.
- Les cassures.
- Les rejets.
- Les consolidations.
- Les mèches.
- Les clôtures.
- Les gaps.
- Les zones de liquidité.

Les indicateurs servent uniquement à confirmer ou filtrer.

---

### 2.2 Le risque vient avant le gain

Un setup est acceptable uniquement si :

- Le stop-loss est logique.
- Le risque par action est calculable.
- La taille de position respecte le risque maximum.
- L’entrée n’est pas trop éloignée du stop.
- L’entrée n’est pas trop loin du niveau de breakout.
- Le setup peut être invalidé clairement.

Formule de base :

```text
risk_per_share = entry_price - stop_loss
position_size = max_risk_usd / risk_per_share
position_value = position_size * entry_price
```

Exemple :

```text
entry_price = 20.50
stop_loss = 19.70
risk_per_share = 0.80
max_risk_usd = 15
position_size = 15 / 0.80 = 18 actions
```

---

### 2.3 Une bonne entrée doit être proche d’une invalidation

Une bonne entrée n’est pas seulement un prix qui monte. C’est une entrée où le trader sait exactement où il a tort.

Une mauvaise entrée typique :

- Acheter après une forte extension verticale.
- Acheter loin du support.
- Acheter loin du breakout.
- Acheter sans volume.
- Acheter avec un stop trop large.
- Acheter sans niveau d’invalidation.

Une bonne entrée typique :

- Breakout confirmé.
- Retest propre.
- Pullback contrôlé.
- Stop sous structure.
- Volume cohérent.
- Distance entrée/stop acceptable.

---

### 2.4 Le marché doit confirmer

Le module ne doit pas forcer un trade. Il doit attendre que le marché confirme.

Confirmations possibles :

- Clôture au-dessus de la résistance.
- Volume supérieur à la moyenne.
- Retest tenu.
- Rebond sur support.
- Reclaim d’un niveau perdu.
- Higher low confirmé.
- Breakout sur plusieurs bougies.
- Absence de rejet violent.

---

### 2.5 Refuser un trade est une décision valide

Le module doit produire des décisions selon un modèle unifié **status + reason_code** (référentiel canonique utilisé dans tout ce document) :

Statuts autorisés (enum fermé) :

```text
GO                  = entrée immédiate validée
ARMED               = ordre conditionnel placé, en attente de trigger
WAIT                = setup détecté mais non confirmé
NO_GO               = trade refusé
INVALIDATED         = setup annulé par le marché
EXPIRED             = fenêtre de validité dépassée
PAUSED              = système en pause (données, halt, circuit breaker)
```

Reason codes (extensibles, toujours joints au status) :

```text
TOO_LATE                    PRICE_TOO_EXTENDED
SPREAD_TOO_WIDE             MISSING_MARKET_DATA
STALE_DATA                  MARKET_CONTEXT_BAD
STOP_INVALID                RISK_TOO_HIGH
POSITION_SIZE_ZERO          VOLUME_INSUFFICIENT
SUPPORT_BROKEN              BREAKOUT_REJECTED
EARNINGS_IMMINENT           HALT_ACTIVE
DAILY_LOSS_LIMIT            MAX_TRADES_REACHED
EXPOSURE_LIMIT              CONFLICT_WITH_OPEN_POSITION
SETUP_NOT_CONFIRMED         WAITING_FOR_RETEST
```

Exemple :

```text
status = NO_GO
reason_code = SPREAD_TOO_WIDE
```

Cette séparation évite l’explosion combinatoire de statuts (`NO_GO_SPREAD`, `NO_GO_RISK`, etc.) et rend les logs exploitables statistiquement.

Un bon système refuse beaucoup plus de trades qu’il n’en accepte.

---

## 3. Données nécessaires

### 3.1 Données de prix

Le module doit utiliser au minimum :

- Open.
- High.
- Low.
- Close.
- Volume.
- Bid.
- Ask.
- Spread.
- VWAP si disponible.
- ATR.
- Données daily.
- Données intraday, notamment 5m, 15m, 1h.

Timeframes recommandés :

```text
1d  = contexte principal
1h  = structure intermédiaire
15m = signal principal
5m  = exécution fine, optionnel
```

---

### 3.2 Données de marché global

Le module doit aussi vérifier :

- SPY / S&P 500.
- QQQ / Nasdaq.
- IWM / small caps.
- VIX.
- Secteur du ticker.
- Pré-market et after-hours si disponibles.
- Calendrier earnings.
- News majeures.

Un setup long sur une action momentum est moins fiable si QQQ et SPY chutent fortement.

---

### 3.3 Données fondamentales utiles

Même pour du trading technique, certains fondamentaux aident à filtrer les setups.

À surveiller :

- Croissance du chiffre d’affaires.
- Croissance des bénéfices.
- Marges.
- Cash-flow.
- Dette.
- Dilution.
- Guidance.
- Earnings surprise.
- Insider buying/selling.
- Short interest.
- Float.
- Market cap.
- Secteur porteur.
- Catalyseur récent.

Ces données ne déclenchent pas l’entrée, mais elles aident à classer la qualité du setup.

---

## 4. Analyse de la tendance

### 4.1 Tendance haussière

Une tendance est considérée haussière si :

- Le prix fait des higher highs et higher lows.
- Le prix est au-dessus des moyennes mobiles clés.
- Les pullbacks sont achetés.
- Le volume augmente pendant les hausses.
- Le prix respecte les supports.

Critères possibles :

```text
trend_direction = bullish
price_above_20ema = true
price_above_50sma = true
higher_lows_count >= 2
```

---

### 4.2 Tendance baissière

Une tendance est baissière si :

- Le prix fait des lower highs et lower lows.
- Les rebonds sont vendus.
- Le prix reste sous les moyennes mobiles.
- Les supports cassent.

Pour un module orienté long, une tendance baissière doit souvent produire :

```text
NO_GO
```

ou :

```text
WAIT_FOR_RECLAIM
```

---

### 4.3 Range / consolidation

Un range est une zone où le prix oscille entre support et résistance.

Le module doit identifier :

- La borne haute.
- La borne basse.
- La compression.
- Le volume en baisse.
- Le potentiel de breakout.

Un range propre peut devenir un excellent setup de breakout.

---

## 5. Supports et résistances

### 5.1 Support

Un support est une zone où les acheteurs défendent le prix.

Types de supports :

- Support horizontal.
- Support daily.
- Support intraday.
- Ancienne résistance devenue support.
- VWAP.
- EMA 20 / SMA 50.
- Low de consolidation.
- Gap support.

Validation d’un support :

```text
support_touches >= 2
close_below_support_allowed = false
wick_below_support_allowed = true ou false selon setup
```

---

### 5.2 Résistance

Une résistance est une zone où les vendeurs apparaissent.

Types de résistances :

- Résistance horizontale.
- High daily.
- High pré-market.
- High de range.
- Gap resistance.
- Niveau psychologique.
- Ancien support cassé.

Validation d’une résistance :

```text
resistance_touches >= 2
breakout_requires_close_above = true
```

---

### 5.3 Zones plutôt que niveaux exacts

Le module doit traiter les niveaux comme des zones, pas comme des prix exacts.

Exemple :

```text
resistance_zone_min = 20.40
resistance_zone_max = 20.50
```

Cela évite les faux refus causés par quelques cents de différence.

---

## 6. Volume

### 6.1 Rôle du volume

Le volume mesure la participation. Un breakout sans volume est plus fragile.

Le module doit comparer le volume actuel avec :

- Le volume moyen de la même bougie.
- Le volume moyen des dernières bougies.
- Le volume daily moyen.
- Le volume du breakout précédent.

---

### 6.2 Règles de volume recommandées

Mode flexible :

```text
fast_breakout_volume_ratio_min = 1.5
confirmed_breakout_volume_ratio_min = 0.8
confirmed_breakout_hold_bars = 2
```

Interprétation :

- Si le breakout est rapide, il doit avoir un volume fort.
- Si le volume est moyen, le prix doit tenir au-dessus du niveau pendant plusieurs bougies.
- Un breakout faible en volume et immédiatement rejeté doit être refusé.

---

### 6.2bis RVOL — Relative Volume (mesure de référence)

Le ratio de volume brut est trompeur car le volume intraday suit une courbe en U (fort à l’ouverture, faible à midi, fort en clôture). Le module doit privilégier le **RVOL ajusté à l’heure de la journée** :

```text
rvol = volume_cumulé_du_jour_à_l_instant_t / volume_cumulé_moyen_au_même_instant_t (N jours)
lookback_days = 20
```

Interprétation :

```text
rvol >= 2.0  : intérêt anormal fort (idéal pour momentum)
rvol 1.2-2.0 : participation au-dessus de la normale
rvol 0.8-1.2 : normal
rvol < 0.8   : participation faible, breakouts fragiles
```

C’est la raison du `comparison_mode = SAME_TIME_OF_DAY` dans les configurations JSON : comparer une bougie 15m de 12h30 à la moyenne des bougies 15m de 12h30, jamais à la moyenne globale.

---

### 6.3 Volume climax

Attention au volume extrêmement élevé après une forte extension. Cela peut signaler :

- Euphorie.
- Short squeeze tardif.
- Distribution.
- Fin de mouvement court terme.

Règle possible :

```text
if price_extended_above_vwap > 6% and volume_climax = true:
    decision = WAIT_FOR_PULLBACK
```

---

## 7. Volatilité et ATR

### 7.1 ATR

L’ATR mesure l’amplitude moyenne du mouvement.

Usages :

- Calculer un buffer de stop.
- Évaluer si le stop est trop serré.
- Évaluer si l’entrée est trop loin.
- Adapter les zones d’entrée.

Exemple :

```text
stop_buffer = max(0.10, 0.25 * ATR_15m)
```

---

### 7.2 Stop trop serré

Un stop est trop serré si le bruit normal du marché peut le toucher facilement.

Signaux :

- Stop sous une mèche récente mais trop proche.
- Spread large.
- ATR élevé.
- Action très volatile.

---

### 7.3 Stop trop large

Un stop est trop large si la position devient trop petite ou le ratio risque/opportunité devient mauvais.

Règle possible :

```text
if risk_per_share / entry_price > 0.08:
    setup_quality = LOW
```

---

## 8. Gestion du risque

### 8.1 Risque fixe par trade

Le module doit respecter un risque maximum par trade.

Exemple :

```text
max_risk_usd = 15
```

La taille de position est calculée automatiquement.

---

### 8.2 Risque par action

```text
risk_per_share = entry_price - initial_stop
```

Pour un long, le stop doit toujours être inférieur à l’entrée.

Validation obligatoire :

```text
initial_stop < entry_price
risk_per_share > 0
```

---

### 8.3 Position sizing

```text
qty = floor(max_risk_usd / risk_per_share)
```

Le module doit aussi vérifier :

```text
position_value <= max_position_value_usd
qty >= 1
```

Si la taille devient 0, le trade est refusé.

---

### 8.4 Interdiction de baisser le stop

Règle essentielle :

```text
never_lower_stop = true
```

Une fois la position ouverte, le stop peut :

- Rester inchangé.
- Monter.
- Passer à breakeven.
- Suivre un trailing stop.

Mais il ne doit jamais descendre.

---

### 8.5 Pas de take-profit fixe obligatoire

Si la stratégie est orientée momentum, le module peut fonctionner sans TP fixe.

Gestion possible :

- Stop initial.
- Break-even après confirmation.
- Trailing stop progressif.
- Sortie si cassure de structure.
- Sortie si volume de distribution.
- Sortie si clôture sous VWAP / EMA / support.

---

## 9. Qualité d’un setup

### 9.1 Score global

Le module peut produire un score de 0 à 100.

Exemple de pondération :

```text
trend_quality:        20 points
structure_quality:    20 points
volume_quality:       15 points
risk_quality:         20 points
market_context:       10 points
fundamental_context:  10 points
execution_quality:     5 points
```

Détail des sous-critères par composant :

```text
trend_quality (20):
  daily_trend_bullish            +8
  intraday_aligned_with_daily    +6
  higher_lows_count >= 2         +4
  price_above_20ema_and_50sma    +2

structure_quality (20):
  resistance_touches >= 2        +5
  consolidation_before_breakout  +5
  clean_retest_or_higher_low     +5
  no_overhead_supply_nearby      +5

volume_quality (15):
  rvol >= 1.5 au signal          +7
  volume_sèche_en_consolidation  +4
  pas_de_volume_climax_récent    +4

risk_quality (20):
  stop_sous_structure_claire     +6
  risk_per_share/entry <= 4%     +6
  distance_entry_stop_vs_ATR ok  +4
  R_potentiel_structurel >= 2    +4

market_context (10):
  SPY/QQQ au-dessus VWAP         +4
  VIX stable ou en baisse        +3
  secteur_fort                   +3

fundamental_context (10):
  catalyseur_positif_récent      +4
  pas_d_earnings < 5 jours       +3
  pas_de_dilution_récente        +3

execution_quality (5):
  spread_pct <= 0.3%             +3
  liquidité_suffisante           +2
```

Interprétation :

```text
score >= 80: excellent
score 65-79: acceptable
score 50-64: faible / attendre confirmation
score < 50: no go
```

Règle importante : le score ne remplace jamais les critères de refus automatique (9.2). Un setup à 90 points avec un stop invalide reste `NO_GO`. Le score classe la qualité **parmi les setups déjà valides**.

---

### 9.2 Critères de refus automatique

Le setup doit être refusé si :

- Prix actuel trop loin de l’entrée.
- Entrée au-dessus de la zone maximale autorisée.
- Stop au-dessus de l’entrée pour un long.
- Données market data manquantes.
- Spread trop large.
- Volume insuffisant.
- Support cassé.
- Breakout rejeté.
- Gap trop violent sans consolidation.
- Earnings imminents non pris en compte.
- Position size inférieure à 1 action.
- Risque par trade supérieur au maximum.

---

## 10. Setups principaux

---

# SETUP 1 — Momentum Breakout

## 10.1 Définition

Le momentum breakout consiste à entrer lorsqu’un titre casse une résistance importante avec force.

Ce setup cherche à capturer une accélération.

---

## 10.2 Conditions idéales

- Tendance daily haussière ou en retournement fort.
- Résistance claire.
- Consolidation avant breakout.
- Volume supérieur à la moyenne.
- Clôture au-dessus de la résistance.
- Marché global favorable.
- Pas d’extension excessive.

---

## 10.3 Déclenchement

```text
breakout_close_above = resistance
breakout_timeframe = 15m
close_above_resistance_required = true
```

Entrée possible :

```text
entry_order_type = STP_LMT
entry_trigger = resistance + buffer
entry_limit = entry_trigger + max_slippage
```

---

## 10.4 Stop-loss

Le stop doit être sous :

- La résistance cassée.
- Le dernier higher low.
- La base de consolidation.
- VWAP si pertinent.

Exemple :

```text
initial_stop = breakout_level - structure_buffer
```

---

## 10.5 Invalidation

Le setup est invalidé si :

- Clôture sous la résistance cassée.
- Retest échoué.
- Volume de vente fort.
- Prix revient dans le range.
- Support de consolidation cassé.

---

## 10.6 Exemple JSON

```json
{
  "setup_type": "momentum_breakout",
  "direction": "long",
  "timeframes": {
    "signal": "15m",
    "confirmation": "1d"
  },
  "breakout": {
    "resistance": 20.50,
    "volume_rule_mode": "FLEXIBLE_CONFIRMATION",
    "fast_breakout_volume_ratio_min": 1.5,
    "confirmed_breakout_volume_ratio_min": 0.8,
    "confirmed_breakout_hold_bars": 2,
    "confirmed_breakout_timeframe": "15m",
    "close_above_resistance_required": true
  },
  "entry": {
    "order_type": "STP_LMT",
    "entry_price": 20.55,
    "limit_price": 20.65
  },
  "risk": {
    "initial_stop": 19.90,
    "max_risk_usd": 15,
    "never_lower_stop": true
  }
}
```

---

# SETUP 2 — Breakout Retest

## 11.1 Définition

Le breakout retest attend que le prix casse une résistance, puis revienne tester l’ancien niveau avant d’entrer.

Ce setup est souvent plus prudent que le breakout direct.

---

## 11.2 Conditions idéales

- Breakout confirmé.
- Ancienne résistance devient support.
- Retest propre.
- Mèches basses acceptables mais clôture sous support refusée.
- Rebond avec volume.

---

## 11.3 Déclenchement

```text
breakout_close_above = resistance
retest_zone_min = resistance - buffer
retest_zone_max = resistance + buffer
retest_required_bars = 1 ou 2
```

---

## 11.4 Entrée

Entrée après :

- Rebond depuis la zone.
- Bougie 15m verte.
- Higher low intraday.
- Close au-dessus du niveau retesté.

---

## 11.5 Stop-loss

Stop sous :

- Zone de retest.
- Mèche du retest.
- Dernier higher low.

---

## 11.6 Invalidation

Invalidation si :

```text
close_below_retest_zone = true
```

ou :

```text
failed_reclaim_after_retest = true
```

---

## 11.7 Exemple format texte

```text
SETUP_TRADING

symbol: TICKER
direction: LONG
mode: SIMULATION
setup_type: BREAKOUT_RETEST
scenario_name: Breakout_retest_prudent
scenario_role: PRIMARY
enabled: YES
selected: YES
armed: YES
conflict_policy: FIRST_TRIGGER_WINS

wait_after_open_minutes: 30
wait_closed_bars_after_open: 2
wait_bars_timeframe: 15m

support_level: 20.50
support_control_timeframe: 15m
wick_below_support_allowed: YES
close_below_support_allowed: NO

breakout_close_above: 20.50
breakout_timeframe: 15m

retest_zone_min: 20.35
retest_zone_max: 20.55
retest_required_bars: 1
retest_timeframe: 15m

entry_order_type: STP_LMT
entry_zone_min: 20.50
entry_zone_max: 20.70
entry_trigger: 20.62
entry_limit: 20.70

initial_stop: 20.15
never_lower_stop: YES
max_risk_usd: 15
```

---

# SETUP 3 — Reclaim

## 12.1 Définition

Le reclaim setup apparaît lorsqu’un titre récupère un niveau important précédemment perdu.

Exemple :

- Le prix casse sous 50.00.
- Il revient au-dessus.
- Il clôture au-dessus.
- Il confirme que les vendeurs ont échoué.

---

## 12.2 Conditions idéales

- Niveau important perdu récemment.
- Reprise rapide du niveau.
- Clôture au-dessus.
- Volume de reprise.
- Stop clair sous le niveau reclaimed.

---

## 12.3 Déclenchement

```text
reclaim_level = 50.00
close_above_reclaim_level_required = true
hold_bars = 1 ou 2
```

---

## 12.4 Entrée

Entrée possible :

- Après clôture au-dessus du reclaim.
- Sur pullback vers le niveau reclaimed.
- Sur cassure du high de la bougie de reclaim.

---

## 12.5 Stop-loss

Stop sous :

- Le reclaim level.
- La mèche basse de la bougie de reclaim.
- Le dernier higher low.

---

## 12.6 Invalidation

Invalidation si :

- Clôture sous le reclaim level.
- Rejet fort après reclaim.
- Volume vendeur supérieur au volume acheteur.

---

# SETUP 4 — Pullback sur tendance

## 13.1 Définition

Ce setup consiste à acheter un repli contrôlé dans une tendance haussière existante.

---

## 13.2 Conditions idéales

- Tendance daily haussière.
- Pullback vers EMA 20, VWAP ou support.
- Volume faible pendant la baisse.
- Rebond avec volume.
- Higher low confirmé.

---

## 13.3 Entrée

Entrée après :

- Bougie de retournement.
- Cassure du high de la bougie précédente.
- Reprise de VWAP.
- Clôture au-dessus de l’EMA intraday.

---

## 13.4 Stop-loss

Stop sous :

- Low du pullback.
- Support.
- EMA/VWAP selon structure.

---

# SETUP 5 — Opening Range Breakout

## 14.1 Définition

L’Opening Range Breakout utilise le range formé après l’ouverture du marché.

Timeframes fréquents :

```text
5m opening range
15m opening range
30m opening range
```

---

## 14.2 Conditions idéales

- Gap ou catalyseur.
- Forte activité à l’ouverture.
- Consolidation claire après l’ouverture.
- Breakout du high de l’opening range.
- Volume supérieur à la moyenne.

---

## 14.3 Règles prudentes

Pour éviter les faux signaux :

```text
wait_after_open_minutes = 30
wait_closed_bars_after_open = 2
wait_bars_timeframe = 15m
```

---

## 14.4 Invalidation

Invalidation si :

- Retour dans l’opening range.
- Clôture sous VWAP.
- Cassure du low de l’opening range.

---

# SETUP 6 — High Tight Flag

## 15.1 Définition

Le high tight flag apparaît après une forte hausse rapide, suivie d’une consolidation serrée.

---

## 15.2 Conditions idéales

- Hausse explosive récente.
- Consolidation latérale serrée.
- Volume qui sèche pendant la consolidation.
- Breakout de la borne haute.
- Stop sous la borne basse.

---

## 15.3 Risques

Ce setup est puissant mais dangereux si :

- Le prix est déjà trop étendu.
- Le volume climax a déjà eu lieu.
- Le stop est trop éloigné.
- Le marché global est faible.

---

# SETUP 7 — Gap and Go

## 16.1 Définition

Le Gap and Go cherche à profiter d’un gap haussier qui continue après l’ouverture.

---

## 16.2 Conditions idéales

- Gap causé par news ou earnings.
- Volume pré-market élevé.
- Float relativement faible ou demande forte.
- Pas de rejet immédiat.
- Prix tient au-dessus de VWAP.

---

## 16.3 Règles de prudence

Le module doit éviter :

- Acheter la première minute sans confirmation.
- Acheter après extension extrême.
- Acheter si le spread est trop large.
- Acheter si le prix casse VWAP.

---

# SETUP 8 — Failed Breakdown / Bear Trap

## 17.1 Définition

Un failed breakdown se produit lorsque le prix casse un support, attire des vendeurs, puis réintègre rapidement le range.

---

## 17.2 Conditions idéales

- Cassure sous support.
- Réintégration rapide.
- Clôture au-dessus du support.
- Volume de reprise.
- Short squeeze potentiel.

---

## 17.3 Entrée

Entrée après reclaim du support cassé.

---

## 17.4 Stop

Stop sous le low du faux breakdown.

---

# SETUP 9 — Base Breakout

## 18.1 Définition

Le base breakout correspond à une cassure d’une longue base de consolidation.

---

## 18.2 Conditions idéales

- Base construite sur plusieurs jours ou semaines.
- Volatilité qui diminue.
- Volume qui sèche.
- Résistance claire.
- Breakout avec volume.

---

## 18.3 Avantage

Ce setup offre souvent :

- Stop plus propre.
- Moins de bruit.
- Meilleure structure daily.
- Potentiel de continuation plus durable.

---

# SETUP 10 — VWAP Reclaim

## 19.1 Définition

Le VWAP reclaim consiste à entrer lorsque le prix repasse au-dessus du VWAP après l’avoir perdu.

---

## 19.2 Conditions idéales

- Prix faible au début.
- Reclaim VWAP.
- Pullback vers VWAP tenu.
- Volume acheteur.
- Marché global en amélioration.

---

## 19.3 Stop

Stop sous VWAP ou sous le higher low du reclaim.

---

# SETUP 11 — Support Bounce

## 20.1 Définition

Le support bounce consiste à acheter un rebond sur support identifié.

---

## 20.2 Conditions idéales

- Support daily ou intraday clair.
- Rejet des vendeurs.
- Mèche basse.
- Clôture au-dessus du support.
- Volume de défense.

---

## 20.3 Risques

Ce setup est moins fort qu’un breakout si la tendance générale est faible.

Le module doit exiger une confirmation plus stricte si le titre est en tendance baissière.

---

# SETUP 12 — Short Squeeze Momentum

## 21.1 Définition

Un short squeeze se produit lorsque les vendeurs à découvert sont forcés de racheter leurs positions, ce qui accélère la hausse.

---

## 21.2 Conditions utiles

- Short interest élevé.
- Float faible ou moyen.
- News positive.
- Breakout d’une résistance clé.
- Volume très élevé.
- Prix qui ne retombe pas malgré l’extension.

---

## 21.3 Risques

Ce setup est très volatil.

Règles recommandées :

```text
position_size_reduction = true
max_risk_usd = lower_than_normal
entry_requires_pullback = true
```

---

## 22. Anti-chase

### 22.1 Objectif

L’anti-chase empêche le module d’acheter trop haut après le signal.

---

### 22.2 Règle simple

```text
max_entry_extension_pct = 1.5%
```

Exemple :

```text
planned_entry = 20.00
max_allowed_entry = 20.00 * 1.015 = 20.30
```

Si le prix actuel est supérieur à 20.30 :

```text
decision = TOO_LATE
```

---

### 22.3 Règles avancées

Le module peut adapter la tolérance selon :

- ATR.
- Spread.
- Volume.
- Type de setup.
- Volatilité du ticker.

Exemple :

```text
max_chase_pct = min(1.5%, 0.35 * ATR_pct)
```

---

## 23. Retest et missed breakout

### 23.1 Problème

Quand le breakout est déjà parti sans entrée, il ne faut pas courir après le prix.

---

### 23.2 Solution

Activer un scénario de missed breakout.

```text
missed_breakout_policy = WAIT_FOR_RETEST
```

Le module attend :

- Retour vers ancienne résistance.
- Stabilisation.
- Rebond.
- Confirmation 15m.

---

### 23.3 Exemple

```json
{
  "missed_breakout": {
    "enabled": true,
    "reason": "PRICE_TOO_FAR_ABOVE_ENTRY",
    "policy": "WAIT_FOR_RETEST",
    "retest_zone_min": 20.30,
    "retest_zone_max": 20.50,
    "rearm_on_new_local_resistance": true
  }
}
```

---

## 24. Spread et liquidité

### 24.1 Spread

Le spread doit être contrôlé avant tout ordre.

```text
spread = ask - bid
spread_pct = spread / mid_price
```

Refus possible :

```text
if spread_pct > 0.5%:
    decision = NO_GO_SPREAD_TOO_WIDE
```

Pour les actions très liquides, le seuil doit être plus strict.

---

### 24.2 Liquidité

Le module doit éviter les titres avec :

- Volume trop faible.
- Spread élevé.
- Carnet vide.
- Slippage fréquent.
- Halts fréquents.

---

## 24bis. Frais, slippage et coûts de transaction

### 24bis.1 Pourquoi c’est critique

Sur des trades à risque faible (ex. `max_risk_usd = 15`), les coûts peuvent représenter 10 à 30 % du risque. Un système rentable brut peut être perdant net.

Coûts à modéliser, même en paper :

```text
commission_per_share ou commission_per_order
frais réglementaires (SEC/TAF pour actions US)
slippage_entrée = fill_price - trigger_price
slippage_sortie = stop_price - fill_price (souvent défavorable)
spread payé (traversée du bid/ask)
```

---

### 24bis.2 Règles recommandées

```text
estimated_total_cost = commissions + expected_slippage + spread/2 (x2 pour aller-retour)
cost_to_risk_ratio = estimated_total_cost / max_risk_usd

if cost_to_risk_ratio > 0.15:
    setup_quality -= pénalité
if cost_to_risk_ratio > 0.30:
    status = NO_GO, reason_code = RISK_TOO_HIGH
```

Le paper trading doit appliquer un slippage simulé réaliste (jamais de fill parfait au trigger) :

```text
simulated_fill = trigger_price + max(0.01, 0.5 * spread)
```

---

## 25. Market regime

### 25.1 Régime favorable

Un régime favorable au long momentum :

- SPY au-dessus VWAP.
- QQQ au-dessus VWAP.
- Breadth positive.
- VIX stable ou en baisse.
- Secteur du titre fort.

---

### 25.2 Régime défavorable

Un régime défavorable :

- SPY/QQQ sous VWAP.
- VIX en forte hausse.
- Forte rotation risk-off.
- News macro négative.
- Faiblesse sectorielle.

Dans ce cas, le module peut :

```text
reduce_position_size = true
require_stronger_confirmation = true
or decision = NO_GO
```

---

## 25bis. Sessions et horaires de trading

### 25bis.1 Structure d’une journée (actions US, heure de New York)

```text
04:00 - 09:30  Pré-market       : spread large, liquidité faible, niveaux clés se forment
09:30 - 10:00  Ouverture        : volatilité maximale, faux signaux fréquents
10:00 - 11:30  Matinée          : fenêtre la plus fiable pour breakouts confirmés
11:30 - 14:00  Lunch chop       : volume faible, ranges, breakouts peu fiables
14:00 - 15:00  Après-midi       : reprise progressive
15:00 - 16:00  Power hour       : volume et directionnel reviennent, attention aux reversals
16:00 - 20:00  After-hours      : réservé à l’analyse, pas d’exécution par défaut
```

---

### 25bis.2 Règles horaires recommandées

```text
no_entry_before = 10:00 (sauf setup ORB explicitement conçu pour l’ouverture)
lunch_penalty : entre 11:30 et 14:00, exiger rvol >= 1.5 et score >= 75
no_new_entry_after = 15:30 (temps insuffisant pour que le trade travaille)
force_review_before_close = 15:45 (décider : tenir overnight ou sortir, selon politique)
premarket_execution = false par défaut (spread et liquidité non maîtrisés)
```

Le module doit stocker l’heure du signal et l’heure d’entrée dans les logs pour permettre l’analyse de performance par tranche horaire.

---

## 25ter. Halts, SSR et contraintes réglementaires (actions US)

### 25ter.1 Halts de volatilité (LULD)

Les actions US sont soumises aux bandes Limit Up / Limit Down. Un titre qui bouge trop vite est halté (généralement 5 minutes, extensible).

Règles pour le module :

```text
if halt_active:
    status = PAUSED, reason_code = HALT_ACTIVE
    aucun ordre envoyé ou modifié
    à la reprise : attendre au moins 1 bougie 5m complète avant toute décision
    re-valider spread, prix vs entrée prévue, et anti-chase (les reprises gap souvent)
```

Un titre multi-halts dans la journée = volatilité extrême : réduire la taille ou refuser.

---

### 25ter.2 SSR (Short Sale Restriction)

Le SSR se déclenche quand un titre baisse de 10 % ou plus vs la clôture précédente et reste actif jusqu’au lendemain. Pour un module long-only, le SSR est surtout un **signal de contexte** : le titre a connu une pression vendeuse violente. Exiger une confirmation plus stricte (reclaim tenu, pas seulement une mèche).

---

### 25ter.3 PDT (Pattern Day Trader)

Sur un compte sur marge US de moins de 25 000 USD, la règle PDT limite à 3 day trades par 5 jours ouvrés glissants.

```text
if account_pdt_constrained:
    day_trades_remaining doit être suivi en temps réel
    if day_trades_remaining == 0:
        status = NO_GO, reason_code = MAX_TRADES_REACHED
    prioriser uniquement les setups score >= 80
```

Même en paper, simuler cette contrainte si le compte réel cible y sera soumis.

---

## 26. Catalyseurs

### 26.1 Catalyseurs positifs

- Earnings beat.
- Guidance relevée.
- Contrat majeur.
- Upgrade analyste.
- Approbation réglementaire.
- Nouveau produit.
- Partenariat stratégique.
- Short squeeze.
- Secteur en momentum.

---

### 26.2 Catalyseurs négatifs

- Offering / dilution.
- Earnings miss.
- Guidance abaissée.
- Downgrade.
- Investigation.
- Dette inquiétante.
- Insider selling massif.

Un setup technique peut être refusé si le catalyseur fondamental est trop négatif.

---

## 27. Gestion après entrée

### 27.1 États possibles

```text
POSITION_OPEN
STOP_ACTIVE
BREAKEVEN_READY
TRAILING_READY
PARTIAL_EXIT_OPTIONAL
EXIT_REQUIRED
```

---

### 27.2 Passage à breakeven

Conditions possibles :

```text
if unrealized_R >= 1.0 and structure_confirmed:
    move_stop_to_breakeven = true
```

Mais attention : passer trop vite à breakeven peut sortir prématurément.

---

### 27.3 Trailing stop

Options :

- Sous le dernier higher low.
- Sous EMA 9/20 intraday.
- Sous VWAP.
- ATR trailing.
- Structure trailing.

Règle :

```text
new_stop = max(current_stop, proposed_stop)
```

---

### 27.4 Sortie obligatoire

Sortie si :

- Stop touché.
- Clôture sous support critique.
- Breakdown avec volume.
- Rejet violent après breakout.
- Données market invalides.
- Halt/news extrême selon politique de risque.

---

## 27bis. Gestion des ordres

### 27bis.1 Bracket orders et OCA

Toute entrée doit être protégée dès le fill. La méthode la plus sûre est le **bracket order** : l’ordre d’entrée porte un stop enfant (et un TP optionnel) en groupe OCA (One-Cancels-All).

```text
règle absolue : aucune position ne doit exister sans stop actif côté broker
le stop ne doit pas vivre uniquement dans la logique du module (risque de crash/déconnexion)
```

---

### 27bis.2 Time-in-force (TIF)

```text
entrée conditionnelle (STP_LMT) : DAY par défaut — un setup intraday ne survit pas à la nuit
stop de protection : GTC tant que la position est ouverte
jamais d’ordre MKT en pré/after-market
```

---

### 27bis.3 Fills partiels

```text
if fill_qty < position_size:
    recalculer le risque réel = fill_qty * risk_per_share
    ajuster le stop enfant à fill_qty (jamais laisser un stop sur une quantité fausse)
    ne pas courir après le reste si le prix a dépassé la zone anti-chase
```

---

### 27bis.4 Ordres orphelins et réconciliation

À chaque cycle, le module doit réconcilier son état interne avec l’état broker :

```text
position broker sans setup interne     → alerte + adoption ou fermeture selon politique
ordre actif sans position ni setup     → annulation (ordre orphelin)
stop absent sur position ouverte       → recréation immédiate du stop
divergence quantité interne vs broker  → PAUSED jusqu’à résolution
```

---

## 28. Architecture recommandée

### 28.1 Modules

```text
MarketDataProvider
TechnicalAnalyzer
FundamentalAnalyzer
SetupDetector
RiskManager
ExecutionValidator
TradeManager
LearningEngine
SetupSerializer
Backtester
```

---

### 28.2 Pipeline de décision

```text
1. Load market data
2. Validate data quality
3. Detect market regime
4. Analyze ticker trend
5. Detect support/resistance
6. Detect setup candidates
7. Score setup quality
8. Calculate entry/stop/risk
9. Validate anti-chase/spread/liquidity
10. Generate setup config
11. Monitor trigger
12. Manage position
13. Record outcome
14. Learn from result
```

---

## 28bis. Contrôle qualité des données (data quality gate)

Aucune analyse n’est fiable sur des données défectueuses. Avant toute détection de setup, valider :

```text
[ ] Dernière bougie reçue < staleness_max (ex. 2x la durée du timeframe)
[ ] Pas de trous de bougies sur la fenêtre d’analyse
[ ] high >= low, high >= open/close, low <= open/close (cohérence OHLC)
[ ] Pas de prix aberrant (variation > seuil sans halt ni news = suspect)
[ ] Volume non nul sur les bougies de session régulière
[ ] Bid < Ask, spread positif
[ ] Données ajustées des splits/dividendes pour l’analyse daily
[ ] Horodatage en timezone cohérente (America/New_York recommandé en interne)
```

En cas d’échec :

```text
status = PAUSED
reason_code = STALE_DATA ou MISSING_MARKET_DATA
```

Le module ne doit jamais « boucher les trous » silencieusement : toute donnée reconstruite doit être marquée comme telle dans les logs.

---

## 29. Schéma de décision

```text
if data_quality_failed:
    return (PAUSED, MISSING_MARKET_DATA | STALE_DATA)

if halt_active:
    return (PAUSED, HALT_ACTIVE)

if daily_loss_limit_hit or max_trades_reached:
    return (PAUSED, DAILY_LOSS_LIMIT | MAX_TRADES_REACHED)

if spread_too_wide:
    return (NO_GO, SPREAD_TOO_WIDE)

if market_regime_bad and setup_quality < 85:
    return (NO_GO, MARKET_CONTEXT_BAD)

if price_too_far_above_entry:
    return (WAIT, WAITING_FOR_RETEST)

if stop_invalid:
    return (NO_GO, STOP_INVALID)

if risk_too_high or cost_to_risk_ratio > 0.30:
    return (NO_GO, RISK_TOO_HIGH)

if exposure_limit_exceeded:
    return (NO_GO, EXPOSURE_LIMIT)

if breakout_confirmed and risk_valid:
    return (ARMED, none)

return (WAIT, SETUP_NOT_CONFIRMED)
```

L’ordre des vérifications compte : les gates système (données, halt, circuit breakers) passent avant les gates de setup.

---

## 30. Format setup JSON recommandé

```json
{
  "setup_id": "TICKER_YYYYMMDD_001",
  "symbol": "TICKER",
  "enabled": true,
  "mode": "paper",
  "setup_type": "momentum_breakout",
  "setup_role": "ENTRY_AND_MANAGEMENT",
  "direction": "long",
  "timeframes": {
    "signal": "15m",
    "confirmation": "1d"
  },
  "market_context": {
    "spy_trend": "neutral",
    "qqq_trend": "neutral",
    "sector_trend": "unknown",
    "risk_regime": "normal"
  },
  "technical_context": {
    "daily_trend": "bullish",
    "intraday_trend": "bullish",
    "support": null,
    "resistance": null,
    "vwap_status": "unknown",
    "atr": null
  },
  "breakout": {
    "resistance": null,
    "volume_rule_mode": "FLEXIBLE_CONFIRMATION",
    "fast_breakout_volume_ratio_min": 1.5,
    "confirmed_breakout_volume_ratio_min": 0.8,
    "confirmed_breakout_hold_bars": 2,
    "confirmed_breakout_timeframe": "15m",
    "close_above_resistance_required": true
  },
  "volume_confirmation": {
    "enabled": true,
    "signal_timeframe": "15m",
    "comparison_mode": "SAME_TIME_OF_DAY",
    "minimum_volume_ratio": 1.0
  },
  "entry": {
    "order_type": "STP_LMT",
    "entry_price": null,
    "stop_trigger": null,
    "limit_price": null,
    "entry_zone_min": null,
    "entry_zone_max": null,
    "anti_chase_max_pct": 1.5
  },
  "risk": {
    "initial_stop": null,
    "max_risk_usd": 15,
    "risk_per_share": null,
    "position_size": null,
    "max_position_value_usd": 200,
    "never_lower_stop": true
  },
  "management": {
    "take_profit_mode": "NONE",
    "breakeven_enabled": true,
    "breakeven_after_R": 1.0,
    "trailing_stop_enabled": true,
    "trailing_method": "STRUCTURE"
  },
  "invalidation": {
    "close_below_support_invalidates": true,
    "failed_breakout_invalidates": true,
    "max_wait_minutes": 180
  },
  "decision": {
    "status": "WAIT",
    "reason_code": "SETUP_NOT_CONFIRMED",
    "quality_score": null
  },
  "meta": {
    "config_version": "2.0",
    "created_at": null,
    "updated_at": null,
    "expires_at": null,
    "created_by": "setup_detector",
    "revision": 1
  }
}
```

---

## 30bis. Versioning des configurations

Chaque configuration de setup doit être versionnée et immuable après armement :

```text
config_version : version du schéma (permet la migration des anciens setups)
revision       : incrémentée à chaque modification avant armement
expires_at     : timestamp au-delà duquel le setup passe à EXPIRED
```

Règles :

```text
- Après ARMED, seuls les champs de management (stop up, trailing) peuvent changer.
- Toute modification génère une nouvelle revision journalisée (avant/après).
- Le learning engine doit pouvoir rejouer la décision avec la config exacte de l’époque.
```

---

## 31. Champs critiques à valider

Le programme doit toujours vérifier :

```text
entry.order_type in ["MKT", "LMT", "STP", "STP_LMT", "TRAIL"]
entry.entry_price is number
risk.initial_stop is number
risk.initial_stop < entry.entry_price for long
risk.max_risk_usd > 0
risk.risk_per_share > 0
risk.position_size >= 1
```

Erreurs fréquentes à éviter :

```text
entry_price = null
initial_stop > entry_price
order_type non supporté
entry trop loin du prix actuel
stop non basé sur structure
position_size impossible
```

---

## 32. Learning Engine

### 32.1 Ce que le module doit apprendre

Le module doit enregistrer chaque setup avec :

- Prix au moment de création.
- Prix au moment du trigger.
- Entry prévue.
- Fill réel ou simulé.
- Stop initial.
- Taille de position.
- Setup type.
- Score initial.
- Décision initiale.
- Résultat final.
- Max favorable excursion.
- Max adverse excursion.
- R multiple final.
- Cause de perte ou de gain.

---

### 32.2 Classes de résultat

```text
WIN
LOSS
BREAKEVEN
MISSED_WINNER
MISSED_LOSER
INVALIDATED_BEFORE_ENTRY
TOO_LATE
BAD_DATA
```

---

### 32.2bis Tags de contexte à enregistrer

Pour rendre les statistiques exploitables, chaque trade doit être taggé :

```text
time_bucket        : OPEN / MORNING / LUNCH / AFTERNOON / POWER_HOUR
market_regime      : FAVORABLE / NEUTRAL / DEFAVORABLE
rvol_bucket        : <0.8 / 0.8-1.2 / 1.2-2.0 / >2.0
setup_type         : (voir setups 1 à 12)
entry_mode         : DIRECT_BREAKOUT / RETEST / PULLBACK
score_bucket       : 50-64 / 65-79 / 80+
day_of_week
had_catalyst       : true/false
spread_bucket      : tight / normal / wide
```

Cela permet de répondre à des questions comme : « les breakouts pris pendant le lunch avec rvol < 1.2 sont-ils rentables ? » — et d’ajuster les règles en conséquence.

---

### 32.2ter Boucle de calibration

Le learning engine doit vérifier périodiquement (ex. tous les 30 trades) :

```text
1. Le score prédit-il le résultat ? (win rate et average_R par score_bucket)
2. Quels reason_codes de refus ont raté des winners ? (MISSED_WINNER par code)
3. Quels filtres n’éliminent que du bruit ? (candidats à l’assouplissement)
4. Quels filtres laissent passer des losers ? (candidats au durcissement)
```

Règle de prudence : ne modifier qu’**un paramètre à la fois**, documenter le changement, et observer sur un échantillon suffisant avant le suivant. Sinon, impossible d’attribuer l’effet.

---

### 32.3 Questions d’apprentissage

Après chaque trade, le module doit répondre :

1. Le setup était-il correctement classé ?
2. Le niveau d’entrée était-il trop agressif ?
3. Le stop était-il logique ?
4. L’anti-chase a-t-il protégé le système ?
5. Le volume était-il vraiment confirmant ?
6. Le marché global aidait-il ou bloquait-il ?
7. Le trade aurait-il mieux fonctionné avec retest ?
8. Le trailing stop était-il trop serré ou trop large ?

---

## 33. Backtesting et validation

### 33.1 Backtest minimum

Pour chaque setup :

- Tester sur plusieurs tickers.
- Tester plusieurs périodes.
- Inclure marchés haussiers et baissiers.
- Inclure slippage et spread.
- Tester entrée directe vs retest.
- Tester différents stops.

---

### 33.2 Métriques importantes

```text
win_rate
average_R
median_R
profit_factor
max_drawdown
expectancy
false_breakout_rate
missed_winner_rate
stop_out_before_move_rate
```

Formule expectancy :

```text
expectancy = (win_rate * average_win_R) - (loss_rate * average_loss_R)
```

---

### 33.3 Éviter l’overfitting

Le module ne doit pas optimiser trop finement sur le passé.

Signaux d’overfitting :

- Trop de paramètres.
- Règles trop spécifiques à un ticker.
- Résultats excellents en backtest mais mauvais en paper.
- Sensibilité excessive à quelques cents.

---

## 34. Règles de sécurité

### 34.1 Toujours commencer en paper

Tout nouveau setup doit passer par :

```text
mode = paper
```

avant le réel.

---

### 34.2 Pas d’ordre sans validation complète

Le module ne doit pas envoyer d’ordre si :

```text
can_send_order = false
```

Causes possibles :

- Market data absente.
- Stop invalide.
- Taille impossible.
- Spread trop large.
- Setup non armé.
- Trailing stop non prêt si exigé.

---

### 34.3 Circuit breakers journaliers (kill switch)

Le module doit s’arrêter seul avant que les pertes ne s’accumulent. Paramètres recommandés :

```text
max_daily_loss_R = 3          # arrêt après -3R de pertes réalisées dans la journée
max_consecutive_losses = 3    # 3 stops consécutifs = pause obligatoire
max_trades_per_day = 5        # limite anti-overtrading
max_daily_loss_usd = 3 * max_risk_usd
cooldown_after_stop_minutes = 30   # pas de re-entry immédiate sur le même ticker
```

Comportement :

```text
if circuit_breaker_hit:
    status = PAUSED
    reason_code = DAILY_LOSS_LIMIT ou MAX_TRADES_REACHED
    annuler tous les ordres d’entrée non déclenchés
    conserver les stops des positions ouvertes
    reprise uniquement le jour suivant (jamais d’auto-reset intraday)
```

Un circuit breaker déclenché n’est pas un échec : c’est le système qui fonctionne.

---

### 34.4 Limites d’exposition et de corrélation

Le risque ne se gère pas trade par trade uniquement, mais au niveau du portefeuille :

```text
max_open_positions = 3
max_total_open_risk_R = 2.0        # somme des risques ouverts (distance aux stops)
max_positions_same_sector = 2
max_exposure_pct_of_account = 50%  # valeur totale des positions / capital
correlated_tickers_count_as_one = true  # ex. deux small caps biotech en squeeze
```

Trois breakouts simultanés sur des titres corrélés = un seul trade avec trois fois le risque.

```text
if exposure_limit_exceeded:
    status = NO_GO
    reason_code = EXPOSURE_LIMIT ou CONFLICT_WITH_OPEN_POSITION
```

---

### 34.5 Logs obligatoires

Chaque décision doit être journalisée :

```text
timestamp
symbol
price_snapshot
setup_type
decision
reason
entry
stop
risk
score
market_context
```

---

## 35. Exemple de décision finale

```json
{
  "symbol": "TICKER",
  "decision": "ARMED",
  "setup_type": "BREAKOUT_RETEST",
  "reason": "Breakout confirmed above resistance and retest zone held on 15m close.",
  "entry": {
    "order_type": "STP_LMT",
    "entry_price": 20.62,
    "limit_price": 20.70
  },
  "risk": {
    "initial_stop": 20.15,
    "risk_per_share": 0.47,
    "max_risk_usd": 15,
    "position_size": 31
  },
  "management": {
    "never_lower_stop": true,
    "take_profit_mode": "NONE",
    "trailing_stop_enabled": true
  },
  "quality_score": 78,
  "status": "READY_FOR_PAPER_EXECUTION"
}
```

---

## 36. Checklist opérationnelle

Avant d’armer un setup :

```text
[ ] Données OHLCV disponibles
[ ] Bid/ask disponibles
[ ] Spread acceptable
[ ] Tendance daily analysée
[ ] Tendance intraday analysée
[ ] Support identifié
[ ] Résistance identifiée
[ ] Type de setup classé
[ ] Volume confirmé ou mode flexible validé
[ ] Entry numérique définie
[ ] Limit price numérique définie si STP_LMT
[ ] Stop numérique défini
[ ] Stop sous entrée pour long
[ ] Risque par action calculé
[ ] Position size calculée
[ ] Anti-chase validé
[ ] Marché global non bloquant
[ ] Qualité des données validée (staleness, OHLC cohérent)
[ ] Pas de halt actif
[ ] Fenêtre horaire autorisée (pas de lunch sans conditions renforcées, pas après 15:30)
[ ] Circuit breakers journaliers non déclenchés
[ ] Limites d'exposition et corrélation respectées
[ ] Coûts estimés <= 15% du risque
[ ] Bracket/stop prêt à être attaché au fill
[ ] Setup non expiré
[ ] Décision explicite générée (status + reason_code)
```

---

## 37. Erreurs classiques à éviter

1. Confondre prix actuel et prix d’entrée.
2. Générer une entrée trop loin du marché.
3. Mettre un stop arbitraire sans structure.
4. Ignorer le spread.
5. Ignorer les earnings.
6. Acheter un breakout déjà trop étendu.
7. Ne pas attendre la clôture 15m.
8. Utiliser un volume non comparé au bon contexte.
9. Forcer un trade en marché faible.
10. Modifier le stop à la baisse.
11. Confondre setup alternatif et setup principal.
12. Utiliser un order_type non supporté.
13. Laisser `entry_price` à null.
14. Générer une configuration non exécutable.
15. Comparer le volume à la moyenne globale au lieu du même moment de journée (RVOL).
16. Trader pendant le lunch chop sans conditions renforcées.
17. Laisser une position ouverte sans stop actif côté broker.
18. Ignorer les fills partiels (stop sur une quantité fausse).
19. Empiler des positions corrélées comme si elles étaient indépendantes.
20. Continuer à trader après le déclenchement d'un circuit breaker.
21. Ignorer les frais et le slippage en paper, puis découvrir leur poids en réel.
22. Reprendre le trading immédiatement après un halt sans re-validation.

---

## 38. Glossaire rapide

```text
Breakout: cassure d’une résistance.
Retest: retour tester le niveau cassé.
Reclaim: récupération d’un niveau perdu.
Support: zone défendue par les acheteurs.
Resistance: zone défendue par les vendeurs.
VWAP: prix moyen pondéré par le volume.
ATR: mesure de volatilité.
R: unité de risque du trade.
Anti-chase: règle empêchant d’acheter trop haut.
False breakout: cassure qui échoue.
Higher low: creux plus haut que le précédent.
Lower high: sommet plus bas que le précédent.
Slippage: différence entre prix attendu et prix exécuté.
Spread: différence bid/ask.
```

---

## 39. Philosophie du système

Le module intelligent ne doit pas chercher à avoir raison sur chaque trade.

Il doit chercher à :

- Identifier les setups propres.
- Éviter les mauvais trades.
- Contrôler le risque.
- Refuser les entrées tardives.
- Respecter les invalidations.
- Apprendre des résultats.
- Générer des setups exécutables.

La meilleure décision est parfois :

```text
WAIT
```

ou :

```text
NO_GO
```

Un bon système de trading est un système qui survit assez longtemps pour laisser son avantage statistique s’exprimer.

---

## 40. Résumé exécutable pour le module

Le module doit toujours répondre avec :

```text
1. Quel est le setup ?
2. Quel est le contexte daily ?
3. Quel est le niveau clé ?
4. Où est l’entrée ?
5. Où est le stop ?
6. Quel est le risque par action ?
7. Quelle est la taille de position ?
8. Le prix est-il trop loin ?
9. Le volume confirme-t-il ?
10. Le marché global aide-t-il ?
11. Quelle est la décision finale ?
```

Décisions autorisées (référentiel canonique de la section 2.5) :

```text
status ∈ { GO, ARMED, WAIT, NO_GO, INVALIDATED, EXPIRED, PAUSED }
reason_code : toujours joint quand status ≠ GO/ARMED
```

Exemples :

```text
(WAIT, WAITING_FOR_RETEST)
(NO_GO, TOO_LATE)
(PAUSED, MISSING_MARKET_DATA)
(PAUSED, DAILY_LOSS_LIMIT)
```

---

Fin du fichier.
