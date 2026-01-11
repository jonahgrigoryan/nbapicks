# NBA Live Win Probability Predictor (MVP v1) — Final Implementation Spec

## Configuration
All tunable parameters externalized for calibration:

```python
CONFIG = {
    # Factor scaling
    "home_court_adjustment": 2.5,      # Points to subtract from home lead
    "lead_scale": 0.15,                # tanh scaling for lead advantage
    "spread_scale": 0.08,              # tanh scaling for spread advantage
    "efficiency_scale": 5.0,           # tanh scaling for efficiency delta
    "sigmoid_k": 2.5,                  # Steepness of final probability curve
    
    # Weight bounds
    "lead_weight_min": 0.20,           # Lead weight at game start
    "lead_weight_max": 0.35,           # Lead weight at game end
    "spread_base_weight": 0.40,        # Weight when spread available
    "efficiency_weight_full": 0.25,    # Efficiency weight when not gated
    "efficiency_weight_gated": 0.10,   # Efficiency weight when gated
    "possession_edge_weight_full": 0.12,  # Possession edge weight when ungated
    "possession_edge_weight_gated": 0.05, # Possession edge weight when gated
    
    # Gating thresholds
    "efficiency_gate_minutes": 18,     # Min minutes for full efficiency weight
    "efficiency_gate_poss": 30,        # Min possessions for full efficiency weight
    "possession_edge_gate_minutes": 24,  # Min minutes for full possession edge weight
    "possession_edge_gate_poss": 40,     # Min possessions for full possession edge weight
    
    # Trailing edge detection
    "trailing_edge_min_margin": 3,     # Min point margin to trigger alert
    "trailing_edge_factor_threshold": 0.15,  # Min advantage to count as "favoring"
    
    # Garbage time
    "blowout_lead_threshold": 20,      # Points for blowout detection (raw, not adjusted)
    "blowout_minutes_threshold": 5,    # Minutes remaining for blowout
    
    # Overtime
    "ot_dampen_factor": 0.8,           # Compress combined score toward 50% in OT

    # Possession edge
    "possession_edge_scale": 4.0,      # tanh scaling for possession edge
    
    # Data freshness
    "stale_warning_sec": 120,          # 2 min → Medium confidence
    "stale_critical_sec": 300,         # 5 min → Low confidence
    
    # API settings
    "poll_interval_sec": 30,
    "api_max_retries": 3,
    "api_retry_backoff_ms": 500,
}
```

────────────────────────────────────────────────────────────────────────────────
## Data Sources

| Source       | Data                                 | Rate Limit | Caching Strategy         |
| ------------ | ------------------------------------ | ---------- | ------------------------ |
| balldontlie  | Box scores, game state, season stats | 60 req/min | Season stats: daily      |
| The Odds API | Pre-game spread (fetch once)         | 30 req/sec | Per-game: fetch at start |

**Error Handling:**
- Retry with exponential backoff (max 3 attempts, 500ms base)
- On persistent failure: freeze last known state, mark confidence "Low"
- Cache season stats locally; refresh daily or on first request of day

────────────────────────────────────────────────────────────────────────────────
## Sign Convention

All advantage values are **positive for home team**, **negative for away team**.

### Spread Sign Convention (Odds API)

The Odds API returns spreads from the **home team's perspective**:
- **Negative spread** = home team is favored (e.g., `-3.5` means home favored by 3.5)
- **Positive spread** = home team is underdog (e.g., `+5.0` means home is 5-point underdog)

The formula `tanh(spread * -CONFIG["spread_scale"])` converts this to our advantage convention:
- Home favored (`spread = -3.5`) → `tanh(-3.5 * -0.08) = tanh(0.28)` → **positive** (home advantage)
- Home underdog (`spread = +5.0`) → `tanh(5.0 * -0.08) = tanh(-0.40)` → **negative** (away advantage)

────────────────────────────────────────────────────────────────────────────────
## Game Status Handling

```python
def get_game_status(game_data):
    """
    Returns: 'pre_game' | 'in_progress' | 'halftime' | 'between_quarters' | 'final'
    """
    if game_data.status == 'Final':
        return 'final'
    if game_data.period == 0 or game_data.clock is None:
        return 'pre_game'
    if game_data.clock == '0:00' and game_data.period in [1, 3]:
        return 'between_quarters'
    if game_data.clock == '0:00' and game_data.period == 2:
        return 'halftime'
    return 'in_progress'
```

### Pre-game Mode Weight Override

When `game_status == 'pre_game'`, explicitly zero non-spread weights before normalization:

```python
if game_status == 'pre_game':
    lead_base_weight = 0
    efficiency_base_weight = 0
    # spread_base_weight remains CONFIG["spread_base_weight"] if spread available
    # After normalization: spread gets 100% weight
    # If spread also missing: cannot produce probability, display "Awaiting tipoff"
```

### Halftime / Between Quarters

Freeze display with `[Halftime]` or `[Q1/Q3 Break]` badge. Keep showing last computed probabilities.
Mark `data_freshness_sec` as time elapsed since last in-progress update. Do not recalculate factors until clock resumes.

### Final

Display final result, stop polling this game.

────────────────────────────────────────────────────────────────────────────────
## Time Calculations

### Clock Parsing (with guards)
```python
def parse_clock(clock_str):
    """Parse 'M:SS' or 'MM:SS' or 'MM:SS.s' format. Returns (minutes, seconds) or None."""
    if clock_str is None or clock_str == '':
        return None
    try:
        parts = clock_str.split(':')
        minutes = int(parts[0])
        # Handle tenths of seconds (e.g., "1:23.4" → truncate to "23")
        sec_part = parts[1].split('.')[0] if len(parts) > 1 else '0'
        seconds = int(sec_part)
        return (minutes, seconds)
    except (ValueError, IndexError):
        return None
```

### Clock Parsing Fallback

When `parse_clock()` returns `None` during an in-progress game:

```python
parsed = parse_clock(clock_str)
if parsed is None:
    if last_known_clock is not None:
        # Use last known clock, mark data as stale
        clock_minutes, clock_seconds = last_known_clock
        clock_stale = True
    else:
        # No prior state: treat as halftime/between quarters
        # Use period to estimate: end of quarter = 0:00
        clock_minutes, clock_seconds = 0, 0
        clock_stale = True
else:
    clock_minutes, clock_seconds = parsed
    last_known_clock = parsed  # Update cache
    clock_stale = False

# If clock_stale, add to data_age_sec for confidence calculation
```

### Minutes Remaining
```python
if current_quarter <= 4:  # Regulation
    minutes_remaining = max(0, (4 - current_quarter) * 12 + clock_minutes + clock_seconds / 60)
    minutes_played = 48 - minutes_remaining
else:  # Overtime
    ot_period = current_quarter - 4
    ot_clock_elapsed = 5 - (clock_minutes + clock_seconds / 60)
    # Note: For multiple OTs, each re-poll resets; we only track current OT period.
    # minutes_remaining reflects time left in THIS OT, not total possible game time.
    minutes_remaining = max(0, 5 - ot_clock_elapsed)
    minutes_played = 48 + (ot_period - 1) * 5 + ot_clock_elapsed
```

────────────────────────────────────────────────────────────────────────────────
## Factor 1: Time-Adjusted Lead

```python
raw_lead = home_score - away_score
adjusted_lead = raw_lead - CONFIG["home_court_adjustment"]
lead_score = adjusted_lead / sqrt(minutes_remaining + 1)
lead_advantage = tanh(lead_score * CONFIG["lead_scale"])  # Range: -1.0 to +1.0
```

### Dynamic Base Weight
```python
game_progress = min(1.0, minutes_played / 48)
lead_base_weight = CONFIG["lead_weight_min"] + (
    (CONFIG["lead_weight_max"] - CONFIG["lead_weight_min"]) * game_progress
)  # 0.20 → 0.35
```

────────────────────────────────────────────────────────────────────────────────
## Factor 2: Pre-game Spread

```python
if spread is not None:
    spread_advantage = tanh(spread * -CONFIG["spread_scale"])
    spread_base_weight = CONFIG["spread_base_weight"]
    spread_missing = False
else:
    spread_advantage = None  # Inactive, not 0
    spread_base_weight = 0
    spread_missing = True
    # Confidence capped at Medium (see calc_confidence)
```

────────────────────────────────────────────────────────────────────────────────
## Factor 3: Live Efficiency

### Per-team Possessions (zero-guarded)
```python
def calc_possessions(fga, fta, tov, orb):
    poss = fga + 0.44 * fta + tov - orb
    return max(poss, 1)
```

### Efficiency Metrics (zero-guarded)
```python
def calc_efg(fgm, fg3m, fga):
    if fga == 0:
        return 0.0
    return (fgm + 0.5 * fg3m) / fga
```

### Efficiency Advantage
```python
home_eff_delta = home_eFG - home_season_eFG
away_eff_delta = away_eFG - away_season_eFG
efficiency_advantage = tanh((home_eff_delta - away_eff_delta) * CONFIG["efficiency_scale"])
```

### Gating
```python
min_poss = min(home_poss, away_poss)

if minutes_played < CONFIG["efficiency_gate_minutes"] or min_poss < CONFIG["efficiency_gate_poss"]:
    efficiency_base_weight = CONFIG["efficiency_weight_gated"]
else:
    efficiency_base_weight = CONFIG["efficiency_weight_full"]
```

────────────────────────────────────────────────────────────────────────────────
## Factor 4: Possession Edge (poll mode only)

### Extra Possessions (turnovers + offensive rebounds)
```python
extra_poss = (away_tov - home_tov) + (home_orb - away_orb)
poss_edge_rate = extra_poss / max(home_poss + away_poss, 1)
possession_edge_advantage = tanh(poss_edge_rate * CONFIG["possession_edge_scale"])
```

### Gating
```python
if minutes_played < CONFIG["possession_edge_gate_minutes"] or min_poss < CONFIG["possession_edge_gate_poss"]:
    possession_edge_weight = CONFIG["possession_edge_weight_gated"]
else:
    possession_edge_weight = CONFIG["possession_edge_weight_full"]
```

────────────────────────────────────────────────────────────────────────────────
## Season Stats Calculation

### Source
balldontlie `/season_averages` endpoint (per-team aggregation):
```
GET /season_averages?season=2024&team_ids[]={team_id}
```

### Calculate from Response
```python
# Team season eFG%
team_season_eFG = (team.fgm + 0.5 * team.fg3m) / team.fga

# Team season TOV rate
team_season_poss = calc_possessions(team.fga, team.fta, team.turnovers, team.oreb)
team_season_tov_rate = team.turnovers / team_season_poss
```

### Caching Strategy
```python
# File: data/season_stats_{YYYY-MM-DD}.json
# Structure:
{
    "fetched_at": "2024-01-15T08:00:00Z",
    "teams": {
        "1": {"eFG": 0.542, "tov_rate": 0.128},
        "2": {"eFG": 0.518, "tov_rate": 0.142},
        ...
    }
}

# Refresh: once daily, or on first request if file missing/stale (>24h)
```

### Fallback (API Failure)
```python
# If balldontlie fails to return season stats, use league averages:
LEAGUE_AVG_EFG = 0.52
LEAGUE_AVG_TOV_RATE = 0.13

if team_season_eFG is None:
    team_season_eFG = LEAGUE_AVG_EFG
if team_season_tov_rate is None:
    team_season_tov_rate = LEAGUE_AVG_TOV_RATE
```

────────────────────────────────────────────────────────────────────────────────
## Weight Normalization

Always normalize active factors to sum to 1.0:

```python
raw_weights = {
    'lead': lead_base_weight,
    'spread': spread_base_weight,
    'efficiency': efficiency_base_weight
}
total = sum(raw_weights.values())

# Guard: no active factors (pre-game + spread missing)
if total == 0:
    return {
        'status': 'awaiting_tipoff',
        'win_prob': None,
        'confidence': None,
        'message': 'Awaiting tipoff — no spread available'
    }

lead_weight = raw_weights['lead'] / total
spread_weight = raw_weights['spread'] / total
efficiency_weight = raw_weights['efficiency'] / total
```

────────────────────────────────────────────────────────────────────────────────
## Combined Model

```python
combined = lead_weight * lead_advantage

if spread_advantage is not None:
    combined += spread_weight * spread_advantage

combined += efficiency_weight * efficiency_advantage

# Overtime dampening: compress toward 50% due to higher variance
if current_quarter > 4:
    combined = combined * CONFIG["ot_dampen_factor"]

k = CONFIG["sigmoid_k"]
win_prob_home = 1 / (1 + exp(-k * combined))
win_prob_away = 1 - win_prob_home
```

### Garbage Time Override
```python
# Blowout detection: near-certain outcomes in late blowouts
# Use raw_lead (not adjusted) so a true 20-point lead isn't softened by home-court normalization
raw_lead = home_score - away_score
if (abs(raw_lead) >= CONFIG["blowout_lead_threshold"] and 
    minutes_remaining <= CONFIG["blowout_minutes_threshold"]):
    if raw_lead > 0:
        win_prob_home = 0.99
        win_prob_away = 0.01
    else:
        win_prob_home = 0.01
        win_prob_away = 0.99
```

────────────────────────────────────────────────────────────────────────────────
## Active Factors List

Used for confidence and trailing-edge calculations:

```python
active_factors = []
if lead_base_weight > 0:
    active_factors.append(('lead', lead_advantage))
if spread_base_weight > 0:
    active_factors.append(('spread', spread_advantage))
if efficiency_base_weight > 0:
    active_factors.append(('efficiency', efficiency_advantage))

active_advantages = [adv for (_, adv) in active_factors]
```

────────────────────────────────────────────────────────────────────────────────
## Confidence Levels

```python
# Guard: no active factors
if len(active_advantages) == 0:
    return 'Low'

factor_spread = max(active_advantages) - min(active_advantages)
all_same_sign = all(a >= 0 for a in active_advantages) or all(a <= 0 for a in active_advantages)
data_age_sec = time.time() - last_fetch_timestamp
```

| Level  | Criteria                                                                                     |
| ------ | -------------------------------------------------------------------------------------------- |
| High   | `all_same_sign` AND `data_age_sec < 120` AND `minutes_played >= 24` AND spread available     |
| Medium | spread missing OR `factor_spread <= 0.20` OR `120 <= data_age_sec < 300` OR `12 <= minutes_played < 24` |
| Low    | `factor_spread > 0.20` OR `data_age_sec >= 300` OR `minutes_played < 12`                     |

```python
def calc_confidence(all_same_sign, factor_spread, data_age_sec, minutes_played, spread_available):
    # Hard failures → Low
    if data_age_sec >= CONFIG["stale_critical_sec"]:
        return 'Low'
    if factor_spread > 0.20:
        return 'Low'
    if minutes_played < 12:
        return 'Low'
    
    # Spread missing caps at Medium (1-tier reduction)
    max_confidence = 'High' if spread_available else 'Medium'
    
    # Other Medium conditions
    if not all_same_sign:
        return 'Medium'
    if data_age_sec >= CONFIG["stale_warning_sec"]:
        return 'Medium'
    if minutes_played < 24:
        return 'Medium'
    
    return max_confidence
```

────────────────────────────────────────────────────────────────────────────────
## Trailing Team Edge Alert

Handle ties explicitly:

```python
if home_score == away_score:
    trailing_team = None
    show_trailing_edge = False
elif home_score > away_score:
    trailing_team = 'away'
    trailing_sign = -1
else:
    trailing_team = 'home'
    trailing_sign = 1

lead_margin = abs(home_score - away_score)
```

Count factors favoring trailing team (active factors only):

```python
# Guard: no active factors or tie game
if trailing_team is None or len(active_factors) == 0:
    show_trailing_edge = False
else:
    factors_favoring_trailing = 0
    for (name, advantage) in active_factors:
        if (advantage * trailing_sign) >= CONFIG["trailing_edge_factor_threshold"]:
            factors_favoring_trailing += 1
    
    show_trailing_edge = (
        lead_margin >= CONFIG["trailing_edge_min_margin"] and 
        factors_favoring_trailing >= 2
    )
```

────────────────────────────────────────────────────────────────────────────────
## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           main.py                                           │
│  - Entry point, CLI args                                                    │
│  - Game selection / auto-detect live games                                  │
│  - Main polling loop (CONFIG["poll_interval_sec"])                          │
├─────────────────────────────────────────────────────────────────────────────┤
│         data_fetcher.py              │           model.py                   │
│  - BallDontLieClient                 │  - calc_lead_advantage()             │
│  - OddsAPIClient                     │  - calc_spread_advantage()           │
│  - Rate limiting / retry logic       │  - calc_efficiency_advantage()       │
│  - Response caching                  │  - calc_win_probability()            │
│  - Season stats cache (daily)        │  - calc_confidence()                 │
│                                      │  - check_trailing_edge()             │
├─────────────────────────────────────────────────────────────────────────────┤
│         display.py                   │           logger.py                  │
│  - Terminal UI (Rich library)        │  - JSON-lines append                 │
│  - Progress bars for probability     │  - Daily log rotation                │
│  - Color-coded confidence            │  - Prediction logging schema         │
│  - Trailing edge alerts              │                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                           config.py                                         │
│  - CONFIG dict (all tunable parameters)                                     │
│  - Load from config.json if present: MERGE with defaults                    │
│    (missing keys use defaults, provided keys override)                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

────────────────────────────────────────────────────────────────────────────────
## Terminal Output Example

Scenario: Q3 4:32, LAL (home) 87 - BOS 82, spread available, efficiency not suppressed

```
minutes_remaining = (4 - 3) * 12 + 4.53 = 16.53
minutes_played = 48 - 16.53 = 31.47
game_progress = 31.47 / 48 = 0.656

lead_base_weight = 0.20 + 0.15 * 0.656 = 0.298
spread_base_weight = 0.40
efficiency_base_weight = 0.25

total = 0.298 + 0.40 + 0.25 = 0.948

lead_weight = 0.298 / 0.948 = 31.4%
spread_weight = 0.40 / 0.948 = 42.2%
efficiency_weight = 0.25 / 0.948 = 26.4%
```

```
┌─────────────────────────────────────────────────┐
│  LAL 87 - 82 BOS   Q3 4:32   [Data: 45s ago]   │
├─────────────────────────────────────────────────┤
│  WIN PROBABILITY                                │
│  LAL ████████████░░░░░░ 62%                    │
│  BOS ░░░░░░░░░░░░██████ 38%                    │
├─────────────────────────────────────────────────┤
│  FACTOR BREAKDOWN              Favors   Margin │
│  Current Lead (31%)            LAL      +0.18  │
│  Pre-game Spread (42%)         BOS      -0.12  │
│  Live Efficiency (26%)         LAL      +0.22  │
├─────────────────────────────────────────────────┤
│  Confidence: HIGH                               │
└─────────────────────────────────────────────────┘
```

────────────────────────────────────────────────────────────────────────────────
## Prediction Logging Schema

```json
{
  "model_version": "1.0.0",
  "config_hash": "sha256_first8",
  "timestamp": "ISO8601",
  "game_id": "string",
  "home_team": "string",
  "away_team": "string",
  "game_status": "in_progress",
  "score": {"home": 87, "away": 82},
  "quarter": 3,
  "clock": "4:32",
  "minutes_remaining": 16.53,
  "minutes_played": 31.47,
  "is_overtime": false,
  "is_blowout": false,
  "factors": {
    "lead": {"advantage": 0.18, "weight": 0.314, "active": true, "raw_lead": 5},
    "spread": {"advantage": -0.12, "weight": 0.422, "active": true, "raw_spread": -3.5},
    "efficiency": {"advantage": 0.22, "weight": 0.264, "active": true, "gated": false}
  },
  "win_prob": {"home": 0.62, "away": 0.38},
  "confidence": "High",
  "data_freshness_sec": 45,
  "trailing_team": "away",
  "trailing_edge_alert": false,
  "api_errors": []
}
```

### API Error Object Shape
```json
{
  "source": "balldontlie" | "odds_api",
  "code": 429,
  "message": "Rate limit exceeded",
  "timestamp": "2024-01-15T20:30:00Z"
}
```

────────────────────────────────────────────────────────────────────────────────
## Calibration Notes (Post-MVP)

Before trusting absolute probability values, validate with historical data:

1. **Collect holdout data**: Run model on completed games without acting on predictions
2. **Check calibration**: Group predictions by bucket (50-55%, 55-60%, etc.) and verify actual win rates match
3. **Tune sigmoid k**: If predictions are too extreme, reduce `k`; if too conservative, increase `k`
4. **Tune factor scales**: If one factor dominates, reduce its scale; if under-contributing, increase

Target: Brier score < 0.20 on holdout set before deploying for any decision-making.

────────────────────────────────────────────────────────────────────────────────
## Future Enhancements (v1.1+)

| Feature                  | Description                                              |
| ------------------------ | -------------------------------------------------------- |
| Momentum factor          | Last 5 minutes scoring differential as 4th factor        |
| Foul trouble             | Key player (top 3 min) with 4+ fouls → adjust efficiency |
| Offensive rebound rate   | Add ORB% to efficiency calculation                       |
| Team-specific home court | Replace fixed 2.5 with per-team historical HCA           |
| Live odds integration    | Compare model prob to live lines for edge detection      |
