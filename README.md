# NBA Points Props GOAT System v2.0

**Zero-Input Automation for NBA Points Predictions**

A sophisticated Monte Carlo simulation system that uses GOAT All-Star tier advanced metrics to predict NBA player points props with high accuracy.

---

## 🚀 Quick Start

### For GSW @ BKN Analysis (Today):

```bash
# Option 1: With API Key (Live Data)
export BALLDONTLIE_API_KEY="your_key_here"
./run_analysis.sh GSW BKN

# Option 2: Without API Key (Mock Demo)
python3 generate_mock_data.py
python3 simulation_engine.py --game-data-file game_data.json --baselines-file live_lines.json --starters-only
```

### View Results:
```bash
cat RESULTS_SUMMARY.txt          # Visual summary
cat final_picks_output.json      # Structured JSON output
cat ANALYSIS_SUMMARY.md          # Detailed analysis
```

---

## 📊 System Overview

### GOAT All-Star Features
This system leverages premium NBA data endpoints to provide elite-level analysis:

1. **Official Usage Rate** - From `/nba/v1/season_averages/advanced`
2. **True Shooting %** - Elite efficiency metric
3. **Team Defensive Rating** - From `/nba/v1/standings`
4. **Clutch Scoring** - Last 5 min, margin ≤5 from play-by-play
5. **League Rank** - Player's PPG rank from `/nba/v1/leaders`
6. **Starter Detection** - Auto-identifies starters via box scores
7. **Days of Rest** - B2B penalties, optimal recovery bonuses
8. **Pace Environment** - Fast/slow game adjustments
9. **Monte Carlo Simulation** - 10,000 iterations for win probability

### Adjustment Factors (GOAT Score)
- **Usage Rate**: +1.2 if ≥28%, -1.0 if <20%
- **True Shooting**: +1.0 if ≥62%, -0.5 if <52%
- **Opponent DRtg**: +2.0 if ≥118 (weak), -2.0 if ≤108 (strong)
- **Pace**: +2.6 if ≥104 (fast), -2.6 if ≤98 (slow)
- **Rest**: -1.8 (B2B road), +0.2 (2 days), -0.3 (3+ days)
- **Form**: (L5 - Season) / Season × 11.0 (capped ±4.5)
- **Clutch**: +1.0 if ≥4.0 PPG, -0.5 if <1.0 PPG
- **League Rank**: +1.0 if ≤10, +0.5 if ≤25, -0.5 if ≥100
- **Minutes**: +1.7 if ≥34 min, -3.3 if <26 min
- **Consistency**: +1.0 if stdev ≤5, -1.0 if stdev >7

---

## 📁 Project Structure

```
/vercel/sandbox/
├── fetch_points_game_data.py    # Fetches GOAT-tier game data
├── fetch_live_lines.py           # Fetches live Vegas betting lines
├── simulation_engine.py          # Monte Carlo simulation engine
├── generate_mock_data.py         # Mock data generator (no API key needed)
├── run_analysis.sh               # One-command automation script
├── prompt2.0.md                  # Complete system documentation
├── SETUP_INSTRUCTIONS.md         # API key setup guide
├── ANALYSIS_SUMMARY.md           # Detailed analysis report
├── RESULTS_SUMMARY.txt           # Visual results summary
├── final_picks_output.json       # Structured picks output
├── game_data.json                # Generated game data
├── live_lines.json               # Generated betting lines
└── simulation_results.json       # Raw simulation output
```

---

## 🎯 Current Analysis: GSW @ BKN (2025-12-29)

### Top Picks Summary

#### 🔥 ELITE LOCKS (≥80% Win Probability)

1. **Stephen Curry (GSW)** - 86.6% Win Prob
   - Line: 26.3 → Projection: 33.0 → Edge: +6.7 pts
   - Elite usage (31.2%) + Outstanding TS (63.8%) vs weak defense

2. **Cam Thomas (BKN)** - 81.0% Win Prob
   - Line: 24.1 → Projection: 30.4 → Edge: +6.3 pts
   - High usage (29.8%) + 2-day rest advantage vs below-avg defense

#### ✅ STRONG VALUE (70-79% Win Probability)

3. **Nic Claxton (BKN)** - 79.6% Win Prob
   - Line: 11.8 → Projection: 15.5 → Edge: +3.7 pts
   - Elite efficiency (65.8% TS) + low volatility

4. **Cameron Johnson (BKN)** - 78.8% Win Prob
   - Line: 14.6 → Projection: 18.8 → Edge: +4.2 pts
   - Strong efficiency + 2-day rest advantage

5. **Dennis Schroder (GSW)** - 77.6% Win Prob
   - Line: 14.8 → Projection: 19.2 → Edge: +4.4 pts
   - Solid usage vs weak defense + clutch reliable

#### 📊 POSITIVE EV (55-69% Win Probability)

6. **Andrew Wiggins (GSW)** - 71.1% Win Prob
   - Line: 15.7 → Projection: 18.8 → Edge: +3.1 pts
   - Above avg efficiency + recent form trending up

---

## 🎲 Recommended Parlays

### High Confidence 3-Leg:
- Stephen Curry OVER 26.3 (86.6%)
- Cam Thomas OVER 24.1 (81.0%)
- Nic Claxton OVER 11.8 (79.6%)
- **Combined Probability: ~55%**

### Conservative 2-Leg:
- Stephen Curry OVER 26.3 (86.6%)
- Cam Thomas OVER 24.1 (81.0%)
- **Combined Probability: ~70%**

---

## ⚙️ Technical Details

### Monte Carlo Simulation
- **Iterations**: 10,000 per player
- **Distribution**: Normal (Gaussian) with mean = adjusted projection, stdev = L5 volatility
- **Win Probability**: % of simulations where player exceeds the line

### Projection Formula
```python
baseline = 0.55 × season_pts + 0.45 × L5_pts
minute_scale = clamp(proj_minutes / L5_minutes, 0.85, 1.15)
context_bump = clamp(GOAT_score / 10, -0.20, 0.20)
projection = baseline × minute_scale × (1 + context_bump)
```

### Selection Criteria (Priority Order)
1. **Win Probability** (highest wins)
2. **Edge Points** (tie-breaker)
3. **GOAT Score** (secondary tie-breaker)
4. **Consistency** (stdev ≤7 preferred)

---

## 🔑 API Key Setup

### Required: BallDontLie GOAT All-Star Tier

1. Visit: https://www.balldontlie.io/
2. Sign up and subscribe to **GOAT All-Star tier**
3. Copy your API key
4. Set environment variable:

```bash
# Option 1: .env file
echo 'BALLDONTLIE_API_KEY=your_key_here' > .env

# Option 2: Export
export BALLDONTLIE_API_KEY="your_key_here"

# Option 3: Inline
BALLDONTLIE_API_KEY="your_key_here" ./run_analysis.sh GSW BKN
```

### Test API Connection:
```bash
python3 -c "
import os, requests
api_key = os.getenv('BALLDONTLIE_API_KEY')
headers = {'Authorization': api_key}
resp = requests.get('https://api.balldontlie.io/v1/teams', headers=headers)
print('✅ Valid' if resp.status_code == 200 else f'❌ Error: {resp.status_code}')
"
```

---

## 📊 Output Formats

### 1. Visual Summary (RESULTS_SUMMARY.txt)
- Formatted text with boxes and emojis
- Quick overview of top picks
- Parlay recommendations

### 2. Structured JSON (final_picks_output.json)
- Machine-readable format
- Complete GOAT factors
- Confidence scores

### 3. Detailed Analysis (ANALYSIS_SUMMARY.md)
- Comprehensive markdown report
- Methodology explanation
- Risk factors and disclaimers

### 4. Raw Simulation (simulation_results.json)
- Complete Monte Carlo output
- All players (including non-starters)
- Full GOAT factor breakdown

---

## ⚠️ Important Disclaimers

### Current Demo Uses Mock Data
This demonstration uses realistic but **simulated data**. For real betting decisions:

1. ✅ Set up BallDontLie API key (GOAT tier)
2. ✅ Run with live data fetch
3. ✅ Verify current injury reports
4. ✅ Confirm starting lineups
5. ✅ Check live Vegas lines
6. ✅ Review recent team news

### Risk Warning
- Sports betting involves risk
- Past performance doesn't guarantee future results
- Always bet responsibly
- This is a tool for analysis, not financial advice

---

## 🛠️ Customization

### Adjust Simulation Parameters:
```bash
# Change iteration count
python3 simulation_engine.py --iterations 50000 ...

# Filter to starters only
python3 simulation_engine.py --starters-only ...
```

### Modify Adjustment Weights:
Edit `simulation_engine.py` function `compute_adjustment_score()` to tune:
- Usage rate thresholds
- True shooting bonuses
- Defensive rating impacts
- Rest day penalties/bonuses

### Change Selection Criteria:
Edit `prompt2.0.md` to adjust:
- Minimum win probability threshold
- Number of picks per team
- Edge point requirements

---

## 📞 Support & Documentation

- **Full System Docs**: `prompt2.0.md`
- **Setup Guide**: `SETUP_INSTRUCTIONS.md`
- **Analysis Report**: `ANALYSIS_SUMMARY.md`
- **Visual Summary**: `RESULTS_SUMMARY.txt`

---

## 🚀 Future Enhancements

Potential improvements for future versions:
- [ ] Automated injury report integration
- [ ] Live odds comparison across multiple sportsbooks
- [ ] Historical performance tracking
- [ ] Machine learning model training
- [ ] Real-time lineup change alerts
- [ ] Telegram/Discord bot integration
- [ ] Web dashboard interface

---

## 📝 Version History

**v2.0** (Current) - GOAT All-Star Enhanced
- Official Usage Rate from advanced stats
- True Shooting % efficiency metric
- Team Defensive Rating from standings
- Clutch scoring from play-by-play
- League rank integration
- Automatic starter detection
- 10,000-iteration Monte Carlo simulation

**v1.0** - Initial Release
- Basic season average projections
- Simple adjustment factors
- Manual starter selection

---

## 📄 License

This project is for educational and research purposes. Always verify data and comply with local gambling regulations.

---

**Generated**: December 29, 2025  
**System**: GOAT All-Star v2.0  
**Matchup**: GSW @ BKN  
**Status**: ✅ Fully Operational (Mock Demo Mode)
