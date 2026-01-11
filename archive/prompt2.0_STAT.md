# NBA MULTI-STAT PROPS "GOAT ALL-STAR" SYSTEM (ZERO-INPUT v2.0 STAT)

> **MULTI-STAT ENGINE**: Supports **PTS**, **REB**, **AST**, **3PM**, **PRA** with stat-specific analytics, floor/ceiling modeling, and safety-first selection.

---

## 🎯 STAT TYPE SELECTION

### Input Required:
```
AWAY_ABBR: {AWAY_ABBR}
HOME_ABBR: {HOME_ABBR}
STAT_TYPE: {STAT_TYPE}  # Options: PTS, REB, AST, 3PM, PRA
```

| STAT_TYPE | Description | Key Metrics |
|-----------|-------------|-------------|
| **PTS** | Points | Usage%, TS%, Off Rating |
| **REB** | Rebounds | Reb%, Position, Opp FGA |
| **AST** | Assists | Ast%, Teammate Usage, Tempo |
| **3PM** | 3-Pointers Made | 3P%, 3PA Volume, Catch-Shoot% |
| **PRA** | Points + Rebounds + Assists | Composite of all three |

---

## 🚀 STAT-SPECIFIC DATA SYNC

```bash
#!/bin/bash
# Multi-Stat Automation Script
AWAY="{AWAY_ABBR}"
HOME="{HOME_ABBR}"
STAT="{STAT_TYPE}"  # PTS, REB, AST, 3PM, or PRA
TODAY=$(date +%Y-%m-%d)
SEASON=$(date +%Y)

# 1. Fetch comprehensive game data with stat-specific metrics
python3 fetch_master_data_v3.py \
  --date "$TODAY" \
  --season "$SEASON" > master_data.json

# 2. Fetch live Vegas lines for all stat types
python3 fetch_live_lines_v3.py \
  --date "$TODAY" > live_lines.json

# 3. Run Monte Carlo simulation (10,000 iterations per stat)
python3 simulation_engine_v3.py \
  --master-data master_data.json \
  --lines live_lines.json \
  --output simulation_results.json

echo "✅ Multi-stat sync complete. Filter for $STAT type in analysis."
```

---

## STEP 1: STAT-SPECIFIC METRICS VALIDATION

### ✅ Advanced Metrics by Stat Type

#### For REBOUNDS (REB):
| Metric | Field Path | Weight |
|--------|------------|--------|
| **Rebound %** | `players.*.season.reb_pct` | Primary |
| **Position** | `players.*.position` | C/PF +5% boost |
| **Opp Missed Shots** | `teams.*.opp_fga - opp_fgm` | Opportunity |
| **Contested Reb%** | `players.*.season.contested_reb_pct` | Effort metric |
| **Minutes Floor** | `players.*.recent.minutes_avg` | Volume guarantee |

#### For ASSISTS (AST):
| Metric | Field Path | Weight |
|--------|------------|--------|
| **Assist %** | `players.*.season.ast_pct` | Primary |
| **Potential Assists** | `players.*.season.potential_ast` | Creation volume |
| **Teammate FG%** | `teams.*.fg_pct` | Conversion rate |
| **Pace** | `teams.*.pace` | Opportunity frequency |
| **Time of Poss** | `players.*.season.time_of_possession` | Ball handling |

#### For 3-POINTERS (3PM):
| Metric | Field Path | Weight |
|--------|------------|--------|
| **3P%** | `players.*.season.fg3_pct` | Accuracy |
| **3PA Volume** | `players.*.season.fg3a` | Attempts |
| **Catch-Shoot 3P%** | `players.*.season.catch_shoot_3pct` | Role clarity |
| **Pull-Up 3P%** | `players.*.season.pullup_3pct` | Creator ability |
| **Opp 3P DRtg** | `teams.*.opp_3p_pct_allowed` | Matchup factor |

#### For PRA (Points + Rebounds + Assists):
- Combines all three stat distributions
- Correlation-adjusted variance (not simple sum)
- Higher floor stability than individual stats

---

## STEP 2: PERCENTILE-BASED FLOOR/CEILING ANALYSIS

**CRITICAL**: V3 methodology uses 10th/90th percentile floors and ceilings, NOT just mean projections.

### Safety Buffer Rules:
```
OVER BET SAFETY:
  floor_10th = 10th percentile of simulated distribution
  buffered_line = line + 0.0  # Adjust for conservatism
  
  ✅ VALID OVER: floor_10th > buffered_line
  ⚠️  MARGINAL: floor_10th within 0.5 of line AND over_prob >= 55%
  ❌ INVALID: floor_10th < line AND over_prob < 55%

UNDER BET SAFETY:
  ceiling_90th = 90th percentile of simulated distribution
  buffered_line = line - 0.0  # Adjust for conservatism
  
  ✅ VALID UNDER: ceiling_90th < buffered_line
  ⚠️  MARGINAL: ceiling_90th within 0.5 of line AND under_prob >= 55%
  ❌ INVALID: ceiling_90th > line AND under_prob < 55%
```

### Floor Margin Calculation:
```
# For Overs:
floor_margin = p10 - line
# Positive = safer pick (floor clears line)

# For Unders:
ceiling_margin = line - p90
# Positive = safer pick (ceiling stays below line)
```

---

## STEP 3: STAT-SPECIFIC GOAT ADJUSTMENTS

Compute the **GOAT Adjustment Score** using STAT-SPECIFIC factors:

### 📊 REBOUNDS ADJUSTMENTS

#### A. Rebound Percentage Modifier
```
IF season.reb_pct >= 20.0:  +2.0 (Elite rebounder - Gobert/Jokic tier)
IF season.reb_pct >= 15.0:  +1.0 (Strong rebounder)
IF season.reb_pct >= 12.0:  +0.5 (Above average)
IF season.reb_pct < 8.0:    -1.0 (Limited rebounding role)
ELSE:                        0.0
```

#### B. Position Bonus
```
IF position IN ("C", "PF"):  +1.5 (Primary rebounding positions)
IF position IN ("SF"):       +0.5 (Versatile forwards)
IF position IN ("SG", "PG"): -0.5 (Guards rarely featured)
```

#### C. Opponent Missed Shot Opportunity
```
opp_miss_rate = 1 - opp.fg_pct
opp_fga = opp.fga_per_game

IF opp_miss_rate >= 0.55 AND opp_fga >= 88:  +1.5 (High rebound opportunity)
IF opp_miss_rate >= 0.52:                    +0.5 (Above avg opportunity)
IF opp_miss_rate <= 0.45:                    -1.0 (Efficient opponent = fewer boards)
```

#### D. Minutes Stability
```
recent_min_stdev = stdev of last 5 games minutes
IF recent_min_stdev <= 3.0:   +0.5 (Stable minutes = reliable volume)
IF recent_min_stdev >= 8.0:   -1.0 (High variance = unpredictable)
```

### 🎯 ASSISTS ADJUSTMENTS

#### A. Assist Percentage Modifier
```
IF season.ast_pct >= 35.0:  +2.5 (Primary playmaker - elite)
IF season.ast_pct >= 28.0:  +1.5 (Lead ball handler)
IF season.ast_pct >= 20.0:  +0.5 (Secondary creator)
IF season.ast_pct < 12.0:   -1.5 (Off-ball role = limited assists)
ELSE:                        0.0
```

#### B. Teammate Finishing Boost
```
team_fg_pct = team.fg_pct
IF team_fg_pct >= 0.48:  +1.0 (Teammates convert well)
IF team_fg_pct >= 0.46:  +0.5 (Average conversion)
IF team_fg_pct < 0.44:   -0.5 (Poor finishers = fewer assists)
```

#### C. Pace Environment (Assists-Specific)
```
projected_pace = (team.pace + opp.pace) / 2

IF projected_pace >= 102:  +1.5 (Fast pace = more assist opps)
IF projected_pace <= 96:   -1.5 (Slow grind = fewer opps)
ELSE:                       0.0
```

#### D. Usage Cannibalization Check
```
# If multiple high-usage players (>=28%) on same team
high_usage_count = count(teammates with usg_pct >= 28)

IF high_usage_count >= 2 AND player.ast_pct >= 25:  -0.5 (Shared creation)
IF high_usage_count >= 3:                           -1.0 (Crowded backcourt)
```

### 🏀 3-POINTERS ADJUSTMENTS

#### A. 3-Point Percentage Modifier
```
IF season.fg3_pct >= 0.40:   +2.0 (Elite shooter)
IF season.fg3_pct >= 0.37:   +1.0 (Above average)
IF season.fg3_pct >= 0.35:   +0.5 (League average)
IF season.fg3_pct < 0.33:    -1.5 (Below average shooter)
```

#### B. Volume Adjustment
```
IF season.fg3a >= 8.0:   +1.5 (High volume = more opportunities)
IF season.fg3a >= 5.5:   +0.5 (Moderate volume)
IF season.fg3a < 3.0:    -1.0 (Limited role = fewer attempts)
```

#### C. Opponent 3P Defense
```
IF opp.opp_3p_pct_allowed >= 0.38:  +1.5 (Poor perimeter defense)
IF opp.opp_3p_pct_allowed >= 0.36:  +0.5 (Below avg defense)
IF opp.opp_3p_pct_allowed <= 0.34:  -1.5 (Strong perimeter D)
```

#### D. Variance Penalty (3PM is high variance)
```
recent_3pm_stdev = stdev of last 5 games 3PM
IF recent_3pm_stdev >= 1.8:   -0.5 (High variance = lower confidence)
IF recent_3pm_stdev <= 1.0:   +0.5 (Consistent volume)
```

### ⚡ PRA ADJUSTMENTS (Points + Rebounds + Assists)

#### A. Triple-Stat Usage
```
IF player regularly contributes 5+ in all three categories:  +2.0 (Versatile producer)
IF two categories show strong production:                    +1.0 (Dual-threat)
IF primarily a scorer only:                                  +0.0 (Single-dimensional)
```

#### B. Correlation Boost
```
# PRA benefits from stat correlation - high usage scorers often get boards/assists
IF usg_pct >= 25 AND reb_pct >= 10 AND ast_pct >= 15:  +1.5 (Triple-threat)
```

#### C. Floor Stability (PRA has lower variance than individuals)
```
pra_floor = p10_pts + p10_reb + p10_ast
IF pra_floor exceeds line by 3+:  +2.0 (Very safe floor)
IF pra_floor exceeds line by 1+:  +1.0 (Comfortable floor)
```

---

## STEP 4: BLOWOUT RISK ASSESSMENT

**Blowout games reduce minutes for starters and inflate garbage time stats unpredictably.**

### Blowout Risk Calculation:
```python
net_rating_diff = abs(home.net_rating - away.net_rating)

# Sigmoid-based blowout probability
blowout_risk = 1.0 / (1.0 + exp(-0.3 * (net_rating_diff - 8)))

# Interpretation:
# net_rating_diff < 5:  Low blowout risk (~20%)
# net_rating_diff 5-10: Moderate risk (30-50%)
# net_rating_diff > 10: High blowout risk (60%+)
```

### Blowout Adjustments by Stat:
```
IF blowout_risk > 0.5:
  REB:  -1.0 (Starters pulled early)
  AST:  -1.0 (Garbage time = ISO ball)
  3PM:  -0.5 (Volume may drop)
  PRA:  -1.5 (Combined hit)

IF blowout_risk > 0.35:
  All stats: -0.5 (Moderate concern)
```

---

## STEP 5: SAFETY SCORE & MATCHUP DIFFICULTY

### Safety Score Formula:
```
safety_score = (floor_margin * 10) + (win_prob / 10) - (blowout_risk * 5)

# Components:
# - floor_margin: Primary driver (scaled up)
# - win_prob: Secondary factor (scaled down)
# - blowout_risk: Penalty for uncertain game scripts
```

### Matchup Difficulty Classification:
```
score = 0

# Win probability factor
IF win_prob >= 65:  score += 2
IF win_prob >= 55:  score += 1
IF win_prob < 45:   score -= 2

# Floor margin factor
IF floor_margin >= 2.0:  score += 2
IF floor_margin >= 1.0:  score += 1
IF floor_margin < 0:     score -= 2

# Blowout factor
IF blowout_risk > 0.4:   score -= 1

# Classification:
IF score >= 3:   "EASY"     # High confidence
IF score <= -1:  "HARD"     # Proceed with caution
ELSE:            "NEUTRAL"  # Standard play
```

---

## STEP 6: OVER/UNDER DECISION LOGIC

**V3 Priority**: Prefer OVERS. Only select UNDER if ceiling clearly stays below line.

### Decision Tree:
```
1. CHECK OVER VALIDITY:
   over_floor_margin = p10 - line
   IF over_floor_margin > 0:
     → SELECT OVER (floor clears line)
   
2. IF OVER INVALID, CHECK UNDER:
   under_ceiling_margin = line - p90
   IF under_ceiling_margin > 0:
     → SELECT UNDER (ceiling stays below)

3. RELAXED CRITERIA (if neither strict check passes):
   IF over_prob >= 55%:
     → SELECT OVER (positive EV edge)
   ELIF under_prob >= 55%:
     → SELECT UNDER (positive EV edge)
   ELSE:
     → SKIP (no clear edge)
```

---

## STEP 7: SIMULATION INTERPRETATION

### Win Probability Tiers:
| Win Prob % | Tier | Action |
|------------|------|--------|
| **≥ 70%** | 🔥 ELITE LOCK | Maximum confidence + high floor margin |
| **65-69%** | ✅ STRONG VALUE | High confidence |
| **55-64%** | 📊 POSITIVE EV | Standard play |
| **50-54%** | ⚠️ COIN FLIP | Only if floor margin is positive |
| **< 50%** | ❌ NEGATIVE EV | Avoid |

### Edge Analysis (Stat-Specific):
```
edge = adjusted_projection - line

# For counting stats (REB, AST, 3PM) - lower thresholds:
IF edge >= 2.0:  "SIGNIFICANT EDGE"
IF edge >= 1.0:  "MODERATE EDGE"
IF edge >= 0.5:  "SLIGHT EDGE"
IF edge < 0.5:   "NO EDGE"

# For PRA - higher thresholds due to combined nature:
IF edge >= 4.0:  "SIGNIFICANT EDGE"
IF edge >= 2.0:  "MODERATE EDGE"
IF edge >= 1.0:  "SLIGHT EDGE"
```

---

## STEP 8: SELECTION RULES

### Starter Filter (MANDATORY):
- `is_starter == true`
- `baseline_line` exists for the STAT_TYPE
- `injury_status != "OUT"` and `injury_status != "DOUBTFUL"`

### Selection Criteria (Priority Order):
1. **Floor Margin** (10th percentile distance from line - PRIMARY)
2. **Win Probability** (simulation-based over/under prob)
3. **Safety Score** (combined metric)
4. **Matchup Difficulty** ("EASY" preferred)
5. **Consistency** (lower stdev = more reliable)

### Per-Team Selection:
- Select **TOP 2** players per team for the stat type
- Must have `win_prob_pct >= 55%` minimum
- Must have `floor_margin >= 0` OR `win_prob >= 60%`
- No duplicate players across picks

---

## STEP 9: FINAL OUTPUT FORMAT

```json
{
  "meta": {
    "matchup": "{AWAY_ABBR} @ {HOME_ABBR}",
    "stat_type": "{STAT_TYPE}",
    "game_date": "auto-detected",
    "data_source": "balldontlie_v3",
    "features_used": [
      "percentile_floor_ceiling",
      "blowout_risk",
      "stat_specific_adjustments",
      "over_under_logic",
      "safety_score"
    ]
  },
  "away_picks": [
    {
      "player": "First Last",
      "stat_type": "REB",
      "side": "Over",
      "line": 8.5,
      "10th_percentile_floor": 9.2,
      "50th_percentile_median": 11.4,
      "90th_percentile_ceiling": 14.1,
      "floor_margin": 0.7,
      "win_prob_pct": 67.8,
      "safety_score": 12.5,
      "matchup_difficulty": "EASY",
      "blowout_risk": 0.22,
      "stat_factors": {
        "reb_pct": 18.5,
        "position": "C",
        "opp_miss_rate": 0.54,
        "minutes_stability": "HIGH"
      },
      "why_summary": "Elite reb% [18.5%] + C position vs inefficient opp [54% miss rate]. Floor of 9.2 clears 8.5 line. EASY matchup."
    }
  ],
  "home_picks": [
    {
      "player": "First Last",
      "stat_type": "REB",
      "side": "Over",
      "line": 6.5,
      "10th_percentile_floor": 7.1,
      "50th_percentile_median": 9.3,
      "90th_percentile_ceiling": 11.8,
      "floor_margin": 0.6,
      "win_prob_pct": 64.2,
      "safety_score": 10.8,
      "matchup_difficulty": "NEUTRAL",
      "blowout_risk": 0.22,
      "stat_factors": {
        "reb_pct": 14.2,
        "position": "PF",
        "opp_miss_rate": 0.51,
        "minutes_stability": "MODERATE"
      },
      "why_summary": "Strong reb% [14.2%] at PF. Floor of 7.1 clears line by 0.6. Consistent minutes."
    }
  ]
}
```

---

## SUMMARY BULLETS TEMPLATE

After final output, provide stat-specific summary bullets:

- **📊 Stat Type Analyzed**: {STAT_TYPE} props with stat-specific adjustments
- **🎯 Live Lines Synced**: [X] players with Vegas lines for {STAT_TYPE}
- **🔒 Floor/Ceiling Model**: 10th/90th percentile safety checks applied
- **🏀 Top Floor Margin**: [Player Name] with +[X.X] floor margin, [XX.X]% win prob
- **⚡ Blowout Risk Level**: [Low/Moderate/High] based on net rating differential
- **⚠️ Key Cautions**: [Any concerns: high variance, blowout risk, inconsistent minutes]

---

## STAT-SPECIFIC QUICK REFERENCE

### REB Props - Key Signals:
✅ **GO**: High reb%, C/PF position, opponent misses a lot, stable minutes  
⚠️ **CAUTION**: Guard trying to hit boards, high blowout risk, inconsistent minutes  
❌ **AVOID**: Low reb%, poor position fit, efficient opponent

### AST Props - Key Signals:
✅ **GO**: High ast%, primary ball handler, fast pace, good team FG%  
⚠️ **CAUTION**: Multiple high-usage teammates, slow pace, blowout risk  
❌ **AVOID**: Off-ball player, poor teammate finishing, crowded backcourt

### 3PM Props - Key Signals:
✅ **GO**: Elite 3P% (>38%), high volume (6+ 3PA), poor opp perimeter D  
⚠️ **CAUTION**: High variance shooter, low volume, strong perimeter D  
❌ **AVOID**: Below average shooter, inconsistent attempts, no floor

### PRA Props - Key Signals:
✅ **GO**: Triple-threat player, versatile production, high floor sum  
⚠️ **CAUTION**: Single-dimensional scorer, high blowout risk  
❌ **AVOID**: One-stat wonder, unpredictable minutes

---

## INPUT TEMPLATE

```
AWAY_ABBR: ___
HOME_ABBR: ___
STAT_TYPE: ___  # Choose: PTS, REB, AST, 3PM, PRA
```

**The system handles stat-specific adjustments, floor/ceiling modeling, and safety scoring automatically.**
