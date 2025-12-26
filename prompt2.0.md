# NBA POINTS PROPS "GOAT ALL-STAR" SYSTEM (ZERO-INPUT v2.0)

> **ZERO-INPUT AUTOMATION**: You only provide `AWAY_ABBR` and `HOME_ABBR`. Everything else is automatically fetched, computed, and synchronized.

---

## 🚀 SINGLE-COMMAND EXECUTION

### Input Required (ONLY THESE):
```
AWAY_ABBR: {AWAY_ABBR}
HOME_ABBR: {HOME_ABBR}
```

### Auto-Computed Values (DO NOT PROVIDE):
- `GAME_DATE`: Auto-detected from today's schedule
- `SEASON_YEAR`: Auto-computed from current date
- `BASELINES`: Auto-synced from live Vegas lines
- `STARTERS`: Auto-detected from `is_starter` field
- `GAME_ID`: Auto-fetched from API

---

## STEP 0: ONE-COMMAND DATA SYNC

**Execute this single command to fetch ALL required data:**

```bash
#!/bin/bash
# Zero-Input Automation Script
AWAY="{AWAY_ABBR}"
HOME="{HOME_ABBR}"
TODAY=$(date +%Y-%m-%d)
SEASON=$(date +%Y)

# 1. Fetch GOAT-tier game data (Usage%, TS%, DRtg, Starters, Clutch, Rank)
python3 fetch_points_game_data.py \
  --game-date "$TODAY" \
  --away "$AWAY" \
  --home "$HOME" \
  --season "$SEASON" > game_data.json

# 2. Extract Game ID and fetch live Vegas lines
GAME_ID=$(cat game_data.json | python3 -c "import json,sys; print(json.load(sys.stdin)['meta']['balldontlie_game_id'])")
python3 fetch_live_lines.py --game-id "$GAME_ID" --simple > live_lines.json

# 3. Run Monte Carlo simulation (10,000 iterations)
python3 simulation_engine.py \
  --game-data-file game_data.json \
  --baselines-file live_lines.json > simulation_results.json

echo "✅ Data sync complete. Files: game_data.json, live_lines.json, simulation_results.json"
```

---

## STEP 1: GOAT DATA VALIDATION

After running the sync command, validate these GOAT features are populated:

### ✅ GOAT Advanced Metrics Checklist
| Metric | Field Path | Source |
|--------|------------|--------|
| **Usage Rate** | `players.*.season.usg_pct` | `/nba/v1/season_averages/advanced` |
| **True Shooting %** | `players.*.season.ts_pct` | `/nba/v1/season_averages/advanced` |
| **Offensive Rating** | `players.*.season.off_rating` | `/nba/v1/season_averages/advanced` |
| **Team Def Rating** | `teams.*.advanced.defensive_rating` | `/nba/v1/standings` |
| **Team Net Rating** | `teams.*.advanced.net_rating` | `/nba/v1/standings` |
| **Team Pace** | `teams.*.advanced.pace` | `/nba/v1/standings` |
| **Is Starter** | `players.*.is_starter` | Box score analysis |
| **Clutch PPG** | `players.*.clutch_pts_avg` | `/nba/v1/play_by_play` |
| **League Rank** | `players.*.pts_league_rank` | `/nba/v1/leaders` |

### ✅ Auto-Detected Starters
The system automatically identifies starters using:
1. Top 5 players by season minutes per team
2. `is_starter: true` flag in player records
3. Players with live betting lines ≥ 10.5 points

**Filter Rule**: Only analyze players where `is_starter == true`.

---

## STEP 2: GOAT ADJUSTMENT SCORING

In your `<thinking>` block, compute the **GOAT Adjustment Score** for each starter:

### A. Official Usage Rate Adjustment
```
IF season.usg_pct >= 28.0:  +1.2 (Primary scorer)
IF season.usg_pct < 20.0:   -0.8 (Role player)
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
IF opp.advanced.defensive_rating >= 118:  +2.0 (Bottom 10 defense)
IF opp.advanced.defensive_rating >= 114:  +1.0 (Below avg defense)
IF opp.advanced.defensive_rating <= 108:  -2.0 (Top 5 defense)
IF opp.advanced.defensive_rating <= 112:  -1.0 (Above avg defense)
ELSE:                                      0.0
```

### D. Pace Environment
```
projected_pace = (team.pace_official + opp.pace_official) / 2

IF projected_pace >= 104:  +2.8 (Fast pace)
IF projected_pace <= 98:   -2.8 (Slow pace)
ELSE:                       0.0
```

### E. Days of Rest
```
IF days_rest == 0 (B2B):   -2.0 (road) / -1.2 (home)
IF days_rest == 1:          0.0 (standard)
IF days_rest == 2:         +0.4 (optimal recovery)
IF days_rest >= 3:         -0.3 (rust factor)
```

### F. Recent Form (L5 vs Season)
```
form_delta = ((recent.pts.avg - season.pts) / season.pts) * 9.0
Capped at: [-3.8, +3.8]
```

### G. Clutch Performance Bonus
```
IF clutch_pts_avg >= 4.0:  +1.0 (Clutch performer)
IF clutch_pts_avg >= 2.5:  +0.5 (Reliable in clutch)
IF clutch_pts_avg < 1.0:   -0.5 (Struggles in clutch)
```

### H. League Standing Bonus
```
IF pts_league_rank <= 10:   +1.2 (Top 10 scorer)
IF pts_league_rank <= 25:   +0.6 (Top 25 scorer)
IF pts_league_rank >= 100:  -0.5 (Low volume)
```

### I. Minutes Stability Adjustment
```
IF proj_minutes >= 34:      +1.7 (High volume floor)
IF proj_minutes >= 30:      +0.8 (Reliable starter)
IF proj_minutes >= 26:       0.0
IF proj_minutes < 26:       -3.3 (High scratch/bench risk)
```

### Total GOAT Score Formula:
```
GOAT_SCORE = Usage + Efficiency + Defense_Matchup + Pace + Rest + Form + Clutch + League_Rank + Minutes
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

### Quick Reference Commands
```bash
# Fetch data only
python3 fetch_points_game_data.py --game-date $(date +%Y-%m-%d) --away {AWAY} --home {HOME} --season $(date +%Y)

# Fetch lines only  
python3 fetch_live_lines.py --date $(date +%Y-%m-%d) --away {AWAY} --home {HOME} --simple

# Run simulation only
python3 simulation_engine.py --game-data-file game_data.json --baselines-file live_lines.json

# Full picks pipeline
python3 points_picks.py --game-date $(date +%Y-%m-%d) --away {AWAY} --home {HOME} --season $(date +%Y)
```

---

## INPUT TEMPLATE

Copy and fill in ONLY these two values:

```
AWAY_ABBR: ___
HOME_ABBR: ___
```

**That's it. The system handles everything else automatically.**
