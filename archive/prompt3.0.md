# PRO PARLAY SYNDICATE v3.0 - MULTI-STAT 10-LEG OPTIMIZER

> **UPDATE 3.0**: Full NBA slate scanning across 5 stat categories: PTS, REB, AST, 3PM, PRA.
> **GOAL**: Build a daily 10-leg parlay optimized for floor safety, not raw odds.

---

## 🚀 SINGLE-COMMAND EXECUTION

### Input Required (ONLY THIS):
```
DATE: YYYY-MM-DD
```

### What's Automated:
- Full slate scanning (all NBA games on date)
- Multi-stat props: PTS, REB, AST, 3PM, PRA
- Independence rules enforcement
- Safety buffer validation
- 10-leg optimal selection

---

## COMPLETE V3 STACK

| Script | Purpose | Input | Output |
|--------|---------|-------|--------|
| `fetch_master_data_v3.py` | GOAT data for all categories | `--date`, `--season` | `master_data_v3.json` |
| `fetch_live_lines_v3.py` | All prop types from API | `--date` | `live_lines_v3.json` |
| `simulation_engine_v3.py` | Monte Carlo for all stats | master + lines | `simulation_results_v3.json` |
| `parlay_optimizer_v3.py` | 10-leg selection | simulation results | `final_parlay_v3.json` |

---

## STEP 0: ONE-COMMAND PIPELINE

**Execute this single command to run the entire system:**

```bash
# Full automated pipeline
python3 parlay_optimizer_v3.py --date 2025-01-15 --season 2025
```

### Manual Step-by-Step (if needed):
```bash
#!/bin/bash
DATE="2025-01-15"
SEASON="2025"

# 1. Fetch master data (all games, all players)
python3 fetch_master_data_v3.py \
  --date "$DATE" \
  --season "$SEASON" \
  --output "master_data_v3_$DATE.json"

# 2. Fetch ALL prop types (pts, reb, ast, 3pm, pra)
python3 fetch_live_lines_v3.py \
  --date "$DATE" \
  --output "live_lines_v3_$DATE.json"

# 3. Run Monte Carlo simulation (10,000 iterations per player/stat)
python3 simulation_engine_v3.py \
  --master-data "master_data_v3_$DATE.json" \
  --lines "live_lines_v3_$DATE.json" \
  --output "simulation_results_v3_$DATE.json"

# 4. Optimize 10-leg parlay
python3 parlay_optimizer_v3.py \
  --simulation-results "simulation_results_v3_$DATE.json" \
  --lines "live_lines_v3_$DATE.json" \
  --output "final_parlay_v3.json"

echo "✅ Pipeline complete: final_parlay_v3.json"
```

---

## STAT CATEGORIES

| Category | API Prop Type | Simulation | Notes |
|----------|--------------|------------|-------|
| **PTS** | `points` | Individual | Usage%, TS%, off_rating adjustments |
| **REB** | `rebounds` | Individual | reb_pct, position bonus (C/PF) |
| **AST** | `assists` | Individual | ast_pct, playmaker bonus (PG) |
| **3PM** | `threes` | Individual | 3P%, higher variance modeling |
| **PRA** | `points_rebounds_assists` | Summed distributions | P+R+A from individual sims |

---

## HARD RULES (MANDATORY)

### 1. One Leg Per Player
- Strict cap: maximum 1 stat type per player in parlay
- Even if player has edges in multiple categories, only best one selected

### 2. One Player Per Game (Independence Rule)
- Maximum 1 player from each game
- Ensures legs are statistically independent
- Critical for parlay probability calculations

### 3. No Missing Lines
- Skip any player/stat combination without a betting line
- No fallback to projections or estimates
- If API returns no line, skip that leg

### 4. Safety Buffer Enforcement
```
OVERS:  10th percentile floor must clear Line + 0.5
UNDERS: 90th percentile ceiling must stay below Line - 0.5

Example (PTS Over 25.5):
  - Line = 25.5
  - Buffered threshold = 26.0
  - Player's 10th percentile = 27.2
  - Floor margin = 27.2 - 26.0 = +1.2 ✅ VALID

Example (REB Under 8.5):
  - Line = 8.5
  - Buffered threshold = 8.0
  - Player's 90th percentile = 7.5
  - Ceiling margin = 8.0 - 7.5 = +0.5 ✅ VALID
```

### 5. Over/Under Logic
- **DEFAULT**: Prefer Overs (more predictable floors)
- **EXCEPTION**: Pick Under ONLY if:
  - 90th percentile ceiling < (Line - 0.5)
  - Over doesn't meet safety buffer

### 6. Selection Objective
**PRIMARY**: Maximize floor safety (10th percentile distance from buffered line)
**SECONDARY**: Win probability percentage

---

## GOAT ADVANCED METRICS

### Player-Level Data
| Metric | Source | Usage |
|--------|--------|-------|
| `usg_pct` | `/nba/v1/season_averages/advanced` | Scoring volume adjustment |
| `ts_pct` | `/nba/v1/season_averages/advanced` | Efficiency boost |
| `off_rating` | `/nba/v1/season_averages/advanced` | Offensive impact |
| `reb_pct` | `/nba/v1/season_averages/advanced` | Rebounding rate |
| `ast_pct` | `/nba/v1/season_averages/advanced` | Playmaking rate |
| `fg3_pct` | Season/recent stats | Three-point accuracy |
| `is_starter` | Minutes analysis | Role identification |
| `pts_league_rank` | `/nba/v1/leaders` | Elite scorer bonus |

### Team-Level Data
| Metric | Source | Usage |
|--------|--------|-------|
| `defensive_rating` | `/nba/v1/standings` | Matchup difficulty |
| `net_rating` | `/nba/v1/standings` | Blowout risk calculation |
| `pace` | `/nba/v1/standings` | Tempo adjustment |
| `pace_last_10` | Computed from games | Recent pace trend |
| `days_rest` | Schedule analysis | Fatigue/rust adjustment |
| `dvp` | Computed from stats | Defense vs Position |

---

## ADVANCED FEATURES (v3.0)

### 1. Blowout Risk Adjustment
```python
# Net rating differential drives blowout probability
net_diff = abs(home_net_rating - away_net_rating)
blowout_risk = sigmoid(0.3 * (net_diff - 8))

# Non-starters get reduced projections in blowout scenarios
if blowout_risk > 0.3 and not is_starter:
    projection *= (1.0 - blowout_risk * 0.15)
```

### 2. Usage Cannibalization
```python
# Multiple high-usage players compete for possessions
high_usage_count = count(players where usg_pct >= 25%)

if high_usage_count >= 3 and player_usg >= 20%:
    projection *= 0.95  # 5% reduction
elif high_usage_count >= 4:
    projection *= 0.92  # 8% reduction
```

### 3. Defense vs Position (DvP)
```python
# Position-specific defensive matchup
dvp_bucket = opponent_dvp[position][stat_type]

if dvp_bucket == "WEAK":
    projection *= 1.08  # Favorable matchup
elif dvp_bucket == "STRONG":
    projection *= 0.92  # Tough matchup
```

---

## STEP 2: GOAT ADJUSTMENT SCORING

In your `<thinking>` block, compute the **GOAT Adjustment Score** for each starter:

### A. Official Usage Rate Adjustment
```
IF season.usg_pct >= 28.0:  +1.5 (Primary scorer)
IF season.usg_pct < 20.0:   -1.0 (Role player)
ELSE:                        0.0 (Standard)
```

### B. Efficiency Boost (True Shooting %)
```
IF season.ts_pct >= 0.62:   +1.0 (Elite efficiency)
IF season.ts_pct >= 0.58:   +0.5 (Above average)
IF season.ts_pct < 0.52:    -0.5 (Below average)
ELSE:                        0.0
```

### C. Team Defense Matchup (Opponent DRtg)
```
IF opp.advanced.defensive_rating >= 118:  +2.4 (Bottom 10 defense)
IF opp.advanced.defensive_rating >= 114:  +1.2 (Below avg defense)
IF opp.advanced.defensive_rating <= 108:  -2.4 (Top 5 defense)
IF opp.advanced.defensive_rating <= 112:  -1.2 (Above avg defense)
ELSE:                                      0.0
```

### D. Pace Environment
```
projected_pace = (team.pace_official + opp.pace_official) / 2

IF projected_pace >= 104:  +2.2 (Fast pace)
IF projected_pace <= 98:   -2.2 (Slow pace)
ELSE:                       0.0
```

### E. Minutes Stability Bonus (Tuned v3.0)
```
IF proj_minutes >= 34:  +1.7 pts
IF proj_minutes >= 30:  +0.85 pts
IF proj_minutes < 26:   -2.5 pts
ELSE:                    0.0
```

### F. Days of Rest
```
IF days_rest == 0 (B2B):   -2.5 (road) / -1.5 (home)
IF days_rest == 1:          0.0 (standard)
IF days_rest == 2:         +0.5 (optimal recovery)
IF days_rest >= 3:         -0.3 (rust factor)
```

### G. Recent Form (L5 vs Season)
```
form_delta = ((recent.pts.avg - season.pts) / season.pts) * 8.0
Capped at: [-3.2, +3.2]
```

### H. Clutch Performance Bonus
```
IF clutch_pts_avg >= 4.0:  +1.0 (Clutch performer)
IF clutch_pts_avg >= 2.5:  +0.5 (Reliable in clutch)
IF clutch_pts_avg < 1.0:   -0.5 (Struggles in clutch)
```

### I. League Standing Bonus
```
IF pts_league_rank <= 10:   +1.0 (Top 10 scorer)
IF pts_league_rank <= 25:   +0.5 (Top 25 scorer)
IF pts_league_rank >= 100:  -0.5 (Low volume)
```

### Total GOAT Score Formula:
```
GOAT_SCORE = Usage + Efficiency + Defense_Matchup + Pace + Minutes + Rest + Form + Clutch + League_Rank
```

---

## STEP 3: SIMULATION INTERPRETATION

Read `simulation_results.json` and apply these decision rules:

### Win Probability Tiers
| Win Prob % | Tier | Action |
|------------|------|--------|
| **≥ 70%** | 🔥 ELITE LOCK | Maximum confidence |
| **65-69%** | ✅ STRONG VALUE | High confidence |
| **55-64%** | 📊 POSITIVE EV | Standard play |
| **50-54%** | ⚠️ COIN FLIP | Proceed with caution |
| **< 50%** | ❌ NEGATIVE EV | Avoid |

### Edge Analysis
```
edge_pts = adjusted_mean - baseline_line

IF edge_pts >= 3.0:  "SIGNIFICANT EDGE"
IF edge_pts >= 1.5:  "MODERATE EDGE"  
IF edge_pts >= 0.5:  "SLIGHT EDGE"
IF edge_pts < 0.5:   "NO EDGE"
```

---

## STEP 4: SELECTION RULES

### Starter Filter (MANDATORY)
Only consider players where:
- `is_starter == true`
- `baseline_line` exists (has Vegas line)
- `injury_status != "OUT"` and `injury_status != "DOUBTFUL"`

### Selection Criteria (Priority Order)
1. **Win Probability** (highest wins)
2. **Edge Points** (tie-breaker)
3. **GOAT Score** (secondary tie-breaker)
4. **Consistency** (recent.pts.stdev ≤ 7 preferred)

### Per-Team Selection
- Select **TOP 3** players per team
- Must have `win_prob_pct >= 55%` minimum
- No duplicate players

---

## STEP 5: FINAL OUTPUT FORMAT

```json
{
  "meta": {
    "matchup": "{AWAY_ABBR} @ {HOME_ABBR}",
    "game_date": "auto-detected",
    "data_source": "balldontlie_goat",
    "features_used": [
      "official_usage_pct",
      "official_ts_pct", 
      "team_drtg",
      "starter_detection",
      "league_rank",
      "clutch_scoring"
    ]
  },
  "away_picks": [
    {
      "player": "First Last",
      "line": 27.5,
      "adjusted_mean": 29.2,
      "win_prob_pct": 68.5,
      "edge_pts": 1.7,
      "confidence_0_100": 82,
      "goat_factors": {
        "usage_pct": 28.5,
        "ts_pct": 0.612,
        "opp_drtg": 116.2,
        "clutch_ppg": 3.2,
        "league_rank": 8
      },
      "why_summary": "Usage [28.5%] + Elite TS [61.2%] vs weak DEF [116.2 DRtg]. Top 10 scorer (#8). Simulation edge: +1.7 pts."
    }
  ],
  "home_picks": [
    {
      "player": "First Last",
      "line": 24.5,
      "adjusted_mean": 26.8,
      "win_prob_pct": 65.2,
      "edge_pts": 2.3,
      "confidence_0_100": 78,
      "goat_factors": {
        "usage_pct": 25.1,
        "ts_pct": 0.585,
        "opp_drtg": 112.8,
        "clutch_ppg": 2.8,
        "league_rank": 22
      },
      "why_summary": "Solid usage [25.1%] + 2-day rest advantage. Clutch performer [2.8 PPG]. Simulation edge: +2.3 pts."
    }
  ]
}
```

---

## SUMMARY BULLETS TEMPLATE

After final output, provide these summary bullets:

- **🎯 Live Line Sync**: [X] players with Vegas lines fetched via `/nba/v2/player_props`
- **📊 GOAT Metrics Applied**: Usage%, TS%, Team DRtg from official endpoints
- **🏀 Starters Auto-Detected**: [X] away, [Y] home via `is_starter` field
- **🔥 Top Edge Found**: [Player Name] with +[X.X] pts edge, [XX.X]% win prob
- **⚠️ Key Risk Factors**: [List any concerning factors: tough matchups, B2B, injuries]

---

## AUTOMATION NOTES

### If Live Lines Unavailable
Fallback to `season.pts` as baseline:
```bash
# Generate fallback baselines from season averages
cat game_data.json | python3 -c "
import json, sys
data = json.load(sys.stdin)
baselines = {}
for team, players in data['players'].items():
    for p in players:
        if p.get('is_starter'):
            baselines[p['name']] = p['season']['pts']
print(json.dumps(baselines, indent=2))
" > live_lines.json
```

---

## INPUT TEMPLATE

Copy and fill in ONLY these two values:

```
AWAY_ABBR: ___
HOME_ABBR: ___
```

**That's it. The system handles everything else automatically.**

---

## CHANGELOG

### v3.0 (Pro Parlay Syndicate)
- ✅ Multi-stat support: PTS, REB, AST, 3PM, PRA
- ✅ Full slate scanning (all games on date)
- ✅ 10-leg parlay optimization
- ✅ Independence rules (1 player/game, 1 leg/player)
- ✅ Safety buffer validation (10th/90th percentile)
- ✅ Blowout risk adjustment
- ✅ Usage cannibalization check
- ✅ DvP matchup multipliers
- ✅ Automated pipeline orchestration
- ✅ **Tuned v3.0 Minutes weights** (-15% reduction)

### v2.0 (GOAT All-Star)
- Official usage%, TS%, off_rating from advanced endpoints
- Team defensive rating from standings
- Starter auto-detection
- League rank bonus
- Clutch scoring integration
