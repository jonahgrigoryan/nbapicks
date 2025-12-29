#!/bin/bash
# NBA Points Props GOAT System - Zero-Input Automation
# Usage: ./run_analysis.sh AWAY_ABBR HOME_ABBR [DATE] [SEASON]
#
# Examples:
#   ./run_analysis.sh GSW BKN
#   ./run_analysis.sh BOS LAL 2025-12-30 2025

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check arguments
if [ $# -lt 2 ]; then
    echo -e "${RED}Error: Missing required arguments${NC}"
    echo "Usage: $0 AWAY_ABBR HOME_ABBR [DATE] [SEASON]"
    echo ""
    echo "Examples:"
    echo "  $0 GSW BKN"
    echo "  $0 BOS LAL 2025-12-30 2025"
    exit 1
fi

AWAY="$1"
HOME="$2"
TODAY="${3:-$(date +%Y-%m-%d)}"
SEASON="${4:-$(date +%Y)}"

echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  NBA POINTS PROPS GOAT SYSTEM - ZERO-INPUT AUTOMATION     ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Matchup:${NC} $AWAY @ $HOME"
echo -e "${YELLOW}Date:${NC} $TODAY"
echo -e "${YELLOW}Season:${NC} $SEASON"
echo ""

# Check for API key
if [ -z "$BALLDONTLIE_API_KEY" ]; then
    echo -e "${RED}⚠️  WARNING: BALLDONTLIE_API_KEY not set${NC}"
    echo -e "${YELLOW}Using mock data generator instead...${NC}"
    echo ""
    
    # Generate mock data
    echo -e "${BLUE}[1/4]${NC} Generating mock game data..."
    python3 generate_mock_data.py
    echo ""
else
    echo -e "${GREEN}✓ API key detected${NC}"
    echo ""
    
    # Step 1: Fetch GOAT-tier game data
    echo -e "${BLUE}[1/4]${NC} Fetching GOAT-tier game data..."
    python3 fetch_points_game_data.py \
        --game-date "$TODAY" \
        --away "$AWAY" \
        --home "$HOME" \
        --season "$SEASON" > game_data.json 2>&1
    
    if [ $? -ne 0 ]; then
        echo -e "${RED}✗ Failed to fetch game data${NC}"
        cat game_data.json
        exit 1
    fi
    echo -e "${GREEN}✓ Game data fetched${NC}"
    echo ""
    
    # Step 2: Extract Game ID and fetch live Vegas lines
    echo -e "${BLUE}[2/4]${NC} Fetching live Vegas lines..."
    GAME_ID=$(cat game_data.json | python3 -c "import json,sys; print(json.load(sys.stdin)['meta']['balldontlie_game_id'])" 2>/dev/null)
    
    if [ -z "$GAME_ID" ]; then
        echo -e "${YELLOW}⚠️  Could not extract game ID, using season averages as baselines${NC}"
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
    else
        python3 fetch_live_lines.py --game-id "$GAME_ID" --simple > live_lines.json 2>&1
        
        if [ $? -ne 0 ]; then
            echo -e "${YELLOW}⚠️  Failed to fetch live lines, using season averages${NC}"
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
        fi
    fi
    echo -e "${GREEN}✓ Lines fetched${NC}"
    echo ""
fi

# Step 3: Run Monte Carlo simulation
echo -e "${BLUE}[3/4]${NC} Running Monte Carlo simulation (10,000 iterations)..."
python3 simulation_engine.py \
    --game-data-file game_data.json \
    --baselines-file live_lines.json \
    --starters-only > simulation_results.json 2>&1

if [ $? -ne 0 ]; then
    echo -e "${RED}✗ Simulation failed${NC}"
    cat simulation_results.json
    exit 1
fi
echo -e "${GREEN}✓ Simulation complete${NC}"
echo ""

# Step 4: Display results
echo -e "${BLUE}[4/4]${NC} Generating final picks..."
echo ""

# Parse and display top picks
python3 << 'PYTHON_SCRIPT'
import json
import sys

# Load simulation results
with open('simulation_results.json', 'r') as f:
    data = json.load(f)

results = data.get('results', [])

# Filter to high-confidence picks (≥55% win prob)
picks = [r for r in results if r['win_prob_pct'] >= 55.0]

if not picks:
    print("❌ No picks found with ≥55% win probability")
    sys.exit(0)

print("╔════════════════════════════════════════════════════════════╗")
print("║                    TOP PICKS SUMMARY                       ║")
print("╚════════════════════════════════════════════════════════════╝")
print()

# Group by tier
elite = [p for p in picks if p['win_prob_pct'] >= 80.0]
strong = [p for p in picks if 70.0 <= p['win_prob_pct'] < 80.0]
positive = [p for p in picks if 55.0 <= p['win_prob_pct'] < 70.0]

if elite:
    print("🔥 ELITE LOCKS (≥80% Win Probability)")
    print("─" * 60)
    for p in elite:
        print(f"  {p['player']} ({p['team']})")
        print(f"    Line: {p['baseline_line']} | Projection: {p['adjusted_mean']} | Edge: +{p['edge_pts']}")
        print(f"    Win Prob: {p['win_prob_pct']}% | Usage: {p['goat_factors'].get('usage_pct', 'N/A')}%")
        print()

if strong:
    print("✅ STRONG VALUE (70-79% Win Probability)")
    print("─" * 60)
    for p in strong:
        print(f"  {p['player']} ({p['team']})")
        print(f"    Line: {p['baseline_line']} | Projection: {p['adjusted_mean']} | Edge: +{p['edge_pts']}")
        print(f"    Win Prob: {p['win_prob_pct']}% | Usage: {p['goat_factors'].get('usage_pct', 'N/A')}%")
        print()

if positive:
    print("📊 POSITIVE EV (55-69% Win Probability)")
    print("─" * 60)
    for p in positive:
        print(f"  {p['player']} ({p['team']})")
        print(f"    Line: {p['baseline_line']} | Projection: {p['adjusted_mean']} | Edge: +{p['edge_pts']}")
        print(f"    Win Prob: {p['win_prob_pct']}% | Usage: {p['goat_factors'].get('usage_pct', 'N/A')}%")
        print()

print("═" * 60)
print(f"Total Picks: {len(picks)} | Elite: {len(elite)} | Strong: {len(strong)} | Positive: {len(positive)}")
print("═" * 60)
PYTHON_SCRIPT

echo ""
echo -e "${GREEN}✅ Analysis complete!${NC}"
echo ""
echo -e "${YELLOW}Files generated:${NC}"
echo "  - game_data.json (GOAT-tier game data)"
echo "  - live_lines.json (Vegas betting lines)"
echo "  - simulation_results.json (Monte Carlo results)"
echo ""
echo -e "${YELLOW}View detailed results:${NC}"
echo "  cat simulation_results.json | python3 -m json.tool"
echo ""
