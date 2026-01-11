# NBA Live Win Probability Predictor

A CLI tool that analyzes live NBA games and outputs win probability predictions using multiple data sources and a multi-factor model.

## Features

- **Multi-factor win probability model**: Combines lead, pre-game spread, and live efficiency
- **Impartial analysis**: Current lead is just one factor, not the dominant signal
- **Trailing team edge detection**: Alerts when a trailing team has underlying advantages
- **Rich terminal UI**: Beautiful output with progress bars and color-coded confidence
- **Prediction logging**: JSON-lines format for accuracy tracking and model calibration

## Installation

```bash
# Clone and enter directory
cd predictor

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Configuration

### API Keys

Create a `.env` file or set environment variables:

```bash
export BALLDONTLIE_API_KEY="your_balldontlie_api_key"
export ODDS_API_KEY="your_odds_api_key"
```

### Custom Configuration

Create a `config.json` file to override default parameters:

```json
{
  "sigmoid_k": 2.5,
  "lead_weight_min": 0.20,
  "lead_weight_max": 0.35,
  "spread_base_weight": 0.40
}
```

## Usage

### List Today's Games

```bash
python -m predictor --list
```

### List Games for a Specific Date

```bash
python -m predictor --list --date 2024-01-15
```

### Analyze a Game (One-Time)

```bash
python -m predictor --game 12345
```

### Continuously Poll a Game

```bash
python -m predictor --game 12345 --poll
```

### Analyze Without Logging

```bash
python -m predictor --game 12345 --no-log
```

## Output Example

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

## Testing

### Install Test Dependencies

```bash
pip install pytest pytest-cov
```

### Run All Unit Tests

```bash
pytest tests/test_model.py -v
```

### Run Integration Tests

```bash
pytest tests/test_integration.py -v
```

### Run Smoke Tests (API Connectivity)

Requires API keys to be configured:

```bash
pytest tests/test_smoke.py -v -m smoke
```

### Run All Tests

```bash
pytest tests/ -v
```

### Run Tests with Coverage

```bash
pytest tests/ --cov=predictor --cov-report=term-missing
```

### Quick Test Commands Summary

| Command | Description |
|---------|-------------|
| `pytest tests/test_model.py -v` | Unit tests for model calculations |
| `pytest tests/test_integration.py -v` | Integration tests with mocked APIs |
| `pytest tests/test_smoke.py -v -m smoke` | API connectivity tests |
| `pytest tests/ -v` | Run all tests |
| `pytest tests/ --cov=predictor` | Tests with coverage report |

## Project Structure

```
predictor/
├── __init__.py          # Package exports
├── __main__.py          # CLI entry point
├── config.py            # Configuration management
├── model.py             # Win probability calculations
├── data_fetcher.py      # API clients (balldontlie, Odds API)
├── display.py           # Rich terminal UI
├── logger.py            # JSON-lines prediction logging
└── main.py              # Main CLI logic

tests/
├── test_model.py        # Unit tests for all calc functions
├── test_integration.py  # Integration tests with mocks
└── test_smoke.py        # API connectivity tests
```

## Model Details

### Factors

1. **Time-Adjusted Lead (20-35% weight)**
   - Adjusts raw lead by home court advantage (-2.5 pts)
   - Normalizes by sqrt(minutes_remaining + 1)
   - Weight increases as game progresses

2. **Pre-game Spread (40% weight)**
   - Team strength indicator independent of current score
   - Negative spread = home favored
   - Critical for impartial analysis

3. **Live Efficiency (10-25% weight)**
   - Compares current eFG% to season averages
   - Gated early in game (< 18 minutes or < 30 possessions)

4. **Possession Edge (5-12% weight, poll only)**
   - Extra possessions from turnovers and offensive rebounds
   - Gated until halftime and baseline possession count is met

### Confidence Levels

- **High**: All factors agree, fresh data, mid-late game, spread available
- **Medium**: Mixed signals, early 2nd half, or stale data (2-5 min)
- **Low**: Factors disagree significantly, very stale data, or early game

### Special Cases

- **Blowout Override**: 20+ point lead with < 5 min → 99%/1%
- **Overtime Dampening**: Predictions compressed toward 50%
- **Pre-game**: Only spread factor active

## Logs

Predictions are logged to `logs/predictions_YYYY-MM-DD.jsonl` in JSON-lines format.

## License

MIT
