# NBA Points Props GOAT System - Setup Instructions

## ⚠️ CRITICAL REQUIREMENT: API KEY NEEDED

The system requires a **BallDontLie API key** with **GOAT All-Star tier subscription** to function.

### Current Status
- ❌ `BALLDONTLIE_API_KEY` environment variable is **NOT SET**
- ❌ Cannot fetch live game data, player stats, or betting lines
- ✅ All code files are present and ready to use

---

## 🔑 How to Set Up API Key

### Option 1: Create .env File (Recommended)
```bash
# Create .env file in project root
cat > /vercel/sandbox/.env << 'EOF'
BALLDONTLIE_API_KEY=your_api_key_here
EOF
```

### Option 2: Export Environment Variable
```bash
export BALLDONTLIE_API_KEY="your_api_key_here"
```

### Option 3: Pass Inline
```bash
BALLDONTLIE_API_KEY="your_api_key_here" python3 fetch_points_game_data.py --game-date 2025-12-29 --away GSW --home BKN --season 2025
```

---

## 🚀 Once API Key is Set - Run This Command

```bash
#!/bin/bash
# Zero-Input Automation for GSW @ BKN
AWAY="GSW"
HOME="BKN"
TODAY="2025-12-29"
SEASON="2025"

# Step 1: Fetch GOAT-tier game data
python3 fetch_points_game_data.py \
  --game-date "$TODAY" \
  --away "$AWAY" \
  --home "$HOME" \
  --season "$SEASON" > game_data.json

# Step 2: Extract Game ID and fetch live Vegas lines
GAME_ID=$(cat game_data.json | python3 -c "import json,sys; print(json.load(sys.stdin)['meta']['balldontlie_game_id'])")
python3 fetch_live_lines.py --game-id "$GAME_ID" --simple > live_lines.json

# Step 3: Run Monte Carlo simulation (10,000 iterations)
python3 simulation_engine.py \
  --game-data-file game_data.json \
  --baselines-file live_lines.json \
  --starters-only > simulation_results.json

# Step 4: Display results
cat simulation_results.json

echo "✅ Analysis complete!"
```

---

## 📊 What the System Does

### GOAT All-Star Features
1. **Official Usage Rate** - From `/nba/v1/season_averages/advanced`
2. **True Shooting %** - Elite efficiency metric
3. **Team Defensive Rating** - From `/nba/v1/standings`
4. **Clutch Scoring** - Last 5 min, margin ≤5 from play-by-play
5. **League Rank** - Player's PPG rank from `/nba/v1/leaders`
6. **Starter Detection** - Auto-identifies starters via box scores
7. **Days of Rest** - B2B penalties, optimal recovery bonuses
8. **Pace Environment** - Fast/slow game adjustments
9. **Monte Carlo Simulation** - 10,000 iterations for win probability

### Output Format
```json
{
  "meta": {
    "matchup": "GSW @ BKN",
    "game_date": "2025-12-29",
    "data_source": "balldontlie_goat"
  },
  "away_picks": [
    {
      "player": "Stephen Curry",
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
      }
    }
  ],
  "home_picks": [...]
}
```

---

## 🔧 Alternative: Mock Data Demo

If you want to test the system without an API key, I can create a mock data generator that simulates the API responses for demonstration purposes.

Would you like me to:
1. **Wait for you to provide the API key** (recommended for real analysis)
2. **Create a mock data demo** (for testing the pipeline logic)

---

## 📝 Files Present
- ✅ `fetch_points_game_data.py` - Fetches GOAT-tier game data
- ✅ `fetch_live_lines.py` - Fetches live Vegas betting lines
- ✅ `simulation_engine.py` - Monte Carlo simulation engine
- ✅ `prompt2.0.md` - Complete system documentation

---

## 🆘 Getting a BallDontLie API Key

1. Visit: https://www.balldontlie.io/
2. Sign up for an account
3. Subscribe to **GOAT All-Star tier** (required for advanced endpoints)
4. Copy your API key
5. Set it as `BALLDONTLIE_API_KEY` environment variable

---

## ⚡ Quick Test (Once Key is Set)

```bash
# Test API connection
python3 -c "
import os
import requests
api_key = os.getenv('BALLDONTLIE_API_KEY')
if not api_key:
    print('❌ API key not set')
else:
    headers = {'Authorization': api_key}
    resp = requests.get('https://api.balldontlie.io/v1/teams', headers=headers)
    if resp.status_code == 200:
        print('✅ API key is valid!')
    else:
        print(f'❌ API error: {resp.status_code}')
"
```
