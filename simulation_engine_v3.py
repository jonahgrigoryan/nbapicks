#!/usr/bin/env python3
"""simulation_engine_v3.py

Pro Parlay Syndicate v3.0 - Multi-Stat Monte Carlo Simulation Engine

Simulates player stat distributions for ALL categories:
- PTS: Points (with usage%, TS%, off_rating adjustments)
- REB: Rebounds (with reb_pct, position bonus)
- AST: Assists (with ast_pct, playmaker bonus)
- 3PM: Three-pointers made (with 3P%, volume adjustments)
- PRA: Points + Rebounds + Assists (computed from sum of distributions)

PERCENTILE OUTPUTS:
- 10th percentile: Floor (for Over bets)
- 50th percentile: Median projection
- 90th percentile: Ceiling (for Under bets)

ADVANCED FEATURES:
- Blowout risk adjustment (using net rating gaps)
- Usage cannibalization (multiple high-usage players)
- Stat-specific variance modeling
- DvP (Defense vs Position) adjustments

Usage:
  python3 simulation_engine_v3.py \
    --master-data master_data_v3.json \
    --lines live_lines_v3.json \
    --output simulation_results_v3.json

Requirements:
  - Input from fetch_master_data_v3.py
  - Input from fetch_live_lines_v3.py
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Seed for reproducibility (can be overridden)
RANDOM_SEED = 42

# Simulation parameters
NUM_SIMULATIONS = 10000
STAT_TYPES = ["pts", "reb", "ast", "fg3m", "pra"]

# Data-quality guardrails
MIN_MINUTES_PROJ = 14.0  # Skip players projected below this
MIN_SEASON_MINUTES = 8.0  # If season minutes below this, treat as low-data


@dataclass
class StatSimResult:
    """Simulation result for a single stat type."""
    mean: float
    stdev: float
    p10: float  # 10th percentile (floor)
    p25: float  # 25th percentile
    p50: float  # 50th percentile (median)
    p75: float  # 75th percentile
    p90: float  # 90th percentile (ceiling)
    line: Optional[float] = None
    over_prob: Optional[float] = None
    under_prob: Optional[float] = None
    over_ev: Optional[float] = None
    under_ev: Optional[float] = None


@dataclass
class PlayerSimResult:
    """Complete simulation result for a player."""
    player_id: int
    player_name: str
    team: str
    game_id: int
    opponent: str
    position: str
    is_starter: bool
    injury_status: str
    minutes_proj: float
    stats: Dict[str, StatSimResult] = field(default_factory=dict)
    # Advanced adjustments applied
    blowout_risk: float = 0.0  # 0-1 scale
    usage_cannibalization: float = 0.0  # Adjustment factor
    context_notes: List[str] = field(default_factory=list)


@dataclass
class SimulationContext:
    """Context for simulation adjustments."""
    game_id: int
    home_team: str
    away_team: str
    home_net_rating: float
    away_net_rating: float
    combined_pace: float
    spread_proxy: float  # Positive = home favored
    blowout_risk: float  # 0-1 scale
    home_dvp: Dict[str, Dict[str, Any]]
    away_dvp: Dict[str, Dict[str, Any]]


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Safely convert value to float."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def build_lines_index(lines: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """Build a player_id -> line data index for reliable matching."""
    lines_by_id: Dict[int, Dict[str, Any]] = {}
    for data in lines.values():
        if not isinstance(data, dict):
            continue
        pid = data.get("player_id")
        if pid is None:
            continue
        try:
            pid_int = int(pid)
        except (ValueError, TypeError):
            continue
        lines_by_id[pid_int] = data
    return lines_by_id


def compute_blowout_risk(net_rating_diff: float) -> float:
    """Compute blowout risk based on net rating differential.
    
    Higher net rating diff = higher chance of blowout.
    Returns 0-1 scale where 1 = very likely blowout.
    """
    # Net rating diff of ~10 is roughly a blowout
    abs_diff = abs(net_rating_diff)
    # Sigmoid-like mapping
    risk = 1.0 / (1.0 + math.exp(-0.3 * (abs_diff - 8)))
    return min(1.0, max(0.0, risk))


def compute_usage_cannibalization(
    team_players: List[Dict[str, Any]],
    target_player_id: int,
) -> float:
    """Compute usage cannibalization factor for a player.
    
    If multiple high-usage players are active, each gets slightly reduced projection.
    Returns adjustment factor (0.9-1.0 range).
    """
    high_usage_count = 0
    target_usage = 0.0
    
    for p in team_players:
        season = p.get("season", {})
        usg = _safe_float(season.get("usg_pct"))
        if usg >= 25.0:  # High usage threshold
            high_usage_count += 1
        if p.get("player_id") == target_player_id:
            target_usage = usg
    
    # If 3+ high usage players, reduce projections slightly
    if high_usage_count >= 3 and target_usage >= 20.0:
        return 0.95  # 5% reduction
    elif high_usage_count >= 4:
        return 0.92  # 8% reduction
    return 1.0


def get_dvp_multiplier(
    dvp_data: Dict[str, Dict[str, Any]],
    stat_type: str,
    position: str,
) -> float:
    """Get DvP multiplier for a stat type and position.
    
    Returns multiplier (0.9-1.1 range) based on opponent's defense vs position.
    """
    if not dvp_data:
        return 1.0
    
    stat_dvp = dvp_data.get(stat_type, {})
    if not stat_dvp:
        return 1.0
    
    # Try exact position, then fallback
    pos_upper = position.upper()
    pos_data = stat_dvp.get(pos_upper)
    
    # Handle combo positions
    if not pos_data:
        if pos_upper in ("G-F", "F-G"):
            pos_data = stat_dvp.get("G") or stat_dvp.get("F")
        elif pos_upper in ("PG", "SG"):
            pos_data = stat_dvp.get("G")
        elif pos_upper in ("SF", "PF"):
            pos_data = stat_dvp.get("F")
    
    if not pos_data:
        return 1.0
    
    bucket = pos_data.get("bucket", "AVERAGE")
    if bucket == "WEAK":
        return 1.08  # Opponent weak at defending this position/stat
    elif bucket == "STRONG":
        return 0.92  # Opponent strong at defending
    return 1.0


def get_rest_multiplier(days_rest: int) -> float:
    """Get rest multiplier for player performance.
    
    0 = B2B (tired): 0.96
    1 = Standard: 1.0
    2 = Well-rested: 1.02
    3+ = Potential rust: 1.0
    """
    if days_rest == 0:
        return 0.96
    elif days_rest == 1:
        return 1.0
    elif days_rest == 2:
        return 1.02
    return 1.0  # 3+ days might have rust


def simulate_stat_distribution(
    base_projection: float,
    stdev: float,
    num_sims: int = NUM_SIMULATIONS,
    min_value: float = 0.0,
) -> np.ndarray:
    """Simulate stat distribution using normal distribution with floor at min_value."""
    # Use truncated normal to avoid negative values
    samples = np.random.normal(base_projection, stdev, num_sims)
    samples = np.maximum(samples, min_value)
    return samples


def compute_base_projection(
    player_data: Dict[str, Any],
    stat_type: str,
    context: SimulationContext,
    team_players: List[Dict[str, Any]],
    line: Optional[float],
) -> Tuple[float, float, List[str]]:
    """Compute base projection and stdev for a stat type.
    
    Returns (projection, stdev, notes).
    """
    season = player_data.get("season", {})
    recent = player_data.get("recent", {})
    position = player_data.get("position", "")
    player_id = player_data.get("player_id", 0)
    team = player_data.get("team_abbr", "")
    
    notes: List[str] = []
    
    # Get season and recent averages
    season_val = _safe_float(season.get(stat_type))
    season_minutes = _safe_float(season.get("minutes"))
    recent_data = recent.get(stat_type, {})
    recent_avg = _safe_float(recent_data.get("avg") if isinstance(recent_data, dict) else 0)
    recent_stdev = _safe_float(recent_data.get("stdev") if isinstance(recent_data, dict) else 0)

    # Weight recent more if sample size is good
    sample_size = recent.get("sample_size", 0)
    has_reliable_mean = False

    if sample_size >= 3 and recent_avg > 0:
        # 60% recent, 40% season
        base = recent_avg * 0.6 + season_val * 0.4
        stdev = recent_stdev if recent_stdev > 0 else abs(base * 0.2)
        has_reliable_mean = True
    elif season_val > 0 and season_minutes >= MIN_SEASON_MINUTES:
        base = season_val
        stdev = abs(season_val * 0.25)
        has_reliable_mean = True
        notes.append("Limited recent data")
    elif line is not None and line > 0:
        # Fallback: anchor to the market line when we lack reliable means.
        base = float(line)
        stdev = max(2.0, abs(base * 0.25))
        notes.append("Baseline anchored to market line")
    else:
        base = 0.0
        stdev = 6.0
        notes.append("Missing baseline stats")

    # Market anchoring: blend toward the sportsbook line to prevent massive edges
    # when the underlying season/recent baselines are stale or incomplete.
    if line is not None and line > 0:
        market_weight = 0.25 if has_reliable_mean else 0.75
        base = (1.0 - market_weight) * base + market_weight * float(line)
        stdev = max(stdev, abs(float(line)) * 0.20)
        notes.append(f"Market anchor ({market_weight:.0%})")
    
    # Stat-specific adjustments
    if stat_type == "pts":
        # Usage and efficiency adjustments (GOAT Tuned v3.1)
        usg = _safe_float(season.get("usg_pct")) or _safe_float(recent.get("usg_pct"))
        ts = _safe_float(season.get("ts_pct")) or _safe_float(recent.get("ts_pct"))
        
        if usg and usg >= 28.0:
            base += 1.5  # Official Usage bonus
            notes.append(f"High usage ({usg:.1f}%)")
        elif usg and usg < 20.0:
            base -= 1.0
            
        if ts and ts >= 0.62:
            base += 1.0  # Elite efficiency
            notes.append(f"Elite TS% ({ts:.1%})")
        elif ts and ts >= 0.58:
            base += 0.5
        elif ts and ts < 0.52:
            base -= 0.5
        
        # Apply pace adjustment (Tuned v3.1)
        projected_pace = context.combined_pace
        if projected_pace >= 104:
            base += 2.2
            notes.append(f"Fast pace ({projected_pace:.1f})")
        elif projected_pace <= 98:
            base -= 2.2
            notes.append(f"Slow pace ({projected_pace:.1f})")
        
    elif stat_type == "reb":
        # Rebound percentage adjustment
        reb_pct = _safe_float(season.get("reb_pct")) or _safe_float(recent.get("reb_pct"))
        if reb_pct and reb_pct > 15:
            base += 1.0
            notes.append(f"High reb% ({reb_pct:.1f}%)")
        
        # Center/PF bonus
        if position.upper() in ("C", "PF"):
            base += 0.5
            
    elif stat_type == "ast":
        # Assist percentage adjustment
        ast_pct = _safe_float(season.get("ast_pct")) or _safe_float(recent.get("ast_pct"))
        if ast_pct and ast_pct > 25:
            base += 1.0
            notes.append(f"High ast% ({ast_pct:.1f}%)")
        
        # Point guard bonus
        if position.upper() in ("PG", "G"):
            base += 0.5
            
    elif stat_type == "fg3m":
        # Three-point shooting adjustments
        fg3_pct = _safe_float(season.get("fg3_pct")) or _safe_float(recent.get("fg3_pct"))
        if fg3_pct and fg3_pct > 0.38:
            base += 0.3
            notes.append(f"Elite 3P% ({fg3_pct:.1%})")
        
        # Higher variance for 3-pointers
        stdev = max(stdev, base * 0.35)
    
    # DvP adjustment (Tuned v3.1)
    is_home = team == context.home_team
    opp_dvp = context.away_dvp if is_home else context.home_dvp
    dvp_mult = get_dvp_multiplier(opp_dvp, stat_type, position)
    if dvp_mult > 1.0:
        base += 2.4
        notes.append("Favorable DvP")
    elif dvp_mult < 1.0:
        base -= 2.4
        notes.append("Tough DvP")
    
    # Team Defensive Rating matchup (Tuned v3.1)
    opp_adv = (context.away_dvp if is_home else context.home_dvp).get("advanced", {}) # Check context
    opp_drtg = _safe_float(context.away_net_rating if is_home else context.home_net_rating) # Placeholder, net rating used for blowout
    
    # Use net rating differential for blowout risk (v3.0 feature)
    # This is already handled in blowout_risk below
    
    # REST adjustment (Tuned v3.1)
    team_data = player_data.get("_team_context", {})
    days_rest = team_data.get("days_rest", 1)
    if days_rest == 0:
        base -= 2.5 if not is_home else 1.5
        notes.append("B2B fatigue")
    elif days_rest == 2:
        base += 0.5
        notes.append("Well-rested")
    elif days_rest >= 3:
        base -= 0.3
        notes.append("Rust factor")
    
    # Minutes stability adjustment (Tuned v3.1)
    season_min = _safe_float(season.get("minutes"))
    recent_min = _safe_float(recent.get("minutes_avg"))
    proj_min = recent_min if recent.get("sample_size", 0) >= 3 else season_min
    
    if proj_min >= 34:
        base += 1.7
        notes.append(f"Minutes stability (+1.7)")
    elif proj_min >= 30:
        base += 0.85
        notes.append(f"Minutes stability (+0.85)")
    elif proj_min < 26:
        base -= 2.5
        notes.append(f"Minutes stability (-2.5)")
    
    # Recent Form adjustment (Tuned v3.1)
    if season_val > 0:
        form_delta = ((recent_avg - season_val) / season_val) * 8.0
        form_adj = max(-3.2, min(3.2, form_delta))
        base += form_adj
        if abs(form_adj) > 1.0:
            notes.append(f"Recent form ({form_adj:+.1f})")
    
    # Usage cannibalization
    cannib = compute_usage_cannibalization(team_players, player_id)
    if cannib < 1.0:
        base *= cannib
        notes.append(f"Usage cannibalization ({cannib:.2f}x)")
    
    # Blowout risk - reduce minutes projection for non-stars
    is_starter = player_data.get("is_starter", False)
    if context.blowout_risk > 0.3 and not is_starter:
        minute_reduction = 1.0 - (context.blowout_risk * 0.15)
        base *= minute_reduction
        notes.append(f"Blowout risk ({context.blowout_risk:.1%})")
    
    # Ensure positive values
    base = max(0.1, base)
    stdev = max(0.5, stdev)
    
    return base, stdev, notes


def simulate_player(
    player_data: Dict[str, Any],
    lines_data: Dict[str, Any],
    context: SimulationContext,
    team_players: List[Dict[str, Any]],
    lines_by_id: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Optional[PlayerSimResult]:
    """Run full simulation for a player across all stat types."""
    player_id = player_data.get("player_id", 0)
    player_name = player_data.get("name", "")
    team = player_data.get("team_abbr", "")
    position = player_data.get("position", "")
    is_starter = player_data.get("is_starter", False)
    injury_status = player_data.get("injury_status", "AVAILABLE")
    
    # Skip injured players
    if injury_status.upper() in ("OUT", "DOUBTFUL"):
        return None
    
    # Get lines for this player
    player_lines: Dict[str, Any] = {}
    if lines_by_id:
        player_lines = lines_by_id.get(int(player_id), {})
    if not player_lines:
        player_lines = lines_data.get(player_name, {})
    if not player_lines:
        # Try alternate name matching
        for name, data in lines_data.items():
            if player_name.lower() in name.lower() or name.lower() in player_name.lower():
                player_lines = data
                break
    
    if not player_lines:
        return None  # No lines available
    
    # Get game_id and opponent from lines
    game_id = player_lines.get("game_id", context.game_id)
    opponent = player_lines.get("opponent", "")
    
    # Season minutes projection
    season = player_data.get("season", {})
    recent = player_data.get("recent", {})
    minutes_proj = _safe_float(recent.get("minutes_avg")) or _safe_float(season.get("minutes"))

    if minutes_proj < MIN_MINUTES_PROJ:
        return None

    result = PlayerSimResult(
        player_id=player_id,
        player_name=player_name,
        team=team,
        game_id=game_id,
        opponent=opponent,
        position=position,
        is_starter=is_starter,
        injury_status=injury_status,
        minutes_proj=round(minutes_proj, 1),
        blowout_risk=context.blowout_risk,
    )
    
    # Simulate each stat type that has a line
    individual_distributions: Dict[str, np.ndarray] = {}

    for stat_type in ["pts", "reb", "ast", "fg3m"]:
        line_data = player_lines.get(stat_type)
        if not line_data or not isinstance(line_data, dict):
            continue

        line = _safe_float(line_data.get("line"))
        if line <= 0:
            continue

        # Compute projection
        proj, stdev, notes = compute_base_projection(
            player_data, stat_type, context, team_players, line
        )
        result.context_notes.extend(notes)
        
        # Run simulation
        distribution = simulate_stat_distribution(proj, stdev)
        individual_distributions[stat_type] = distribution
        
        # Compute percentiles
        p10 = float(np.percentile(distribution, 10))
        p25 = float(np.percentile(distribution, 25))
        p50 = float(np.percentile(distribution, 50))
        p75 = float(np.percentile(distribution, 75))
        p90 = float(np.percentile(distribution, 90))
        
        # Compute over/under probabilities
        over_count = np.sum(distribution > line)
        over_prob = float(over_count / len(distribution))
        under_prob = 1.0 - over_prob
        
        # Compute expected value (simplified, assuming -110 standard)
        over_odds = line_data.get("over_odds", -110)
        under_odds = line_data.get("under_odds", -110)
        
        over_implied = _odds_to_implied(over_odds or -110)
        under_implied = _odds_to_implied(under_odds or -110)
        
        over_ev = (over_prob - over_implied) * 100
        under_ev = (under_prob - under_implied) * 100
        
        result.stats[stat_type] = StatSimResult(
            mean=round(float(np.mean(distribution)), 2),
            stdev=round(float(np.std(distribution)), 2),
            p10=round(p10, 2),
            p25=round(p25, 2),
            p50=round(p50, 2),
            p75=round(p75, 2),
            p90=round(p90, 2),
            line=line,
            over_prob=round(over_prob * 100, 1),
            under_prob=round(under_prob * 100, 1),
            over_ev=round(over_ev, 2),
            under_ev=round(under_ev, 2),
        )
    
    # Compute PRA if we have pts, reb, ast
    if all(k in individual_distributions for k in ["pts", "reb", "ast"]):
        pra_distribution = (
            individual_distributions["pts"] +
            individual_distributions["reb"] +
            individual_distributions["ast"]
        )
        
        # Check if there's a PRA line
        pra_line_data = player_lines.get("pra")
        pra_line = None
        if pra_line_data and isinstance(pra_line_data, dict):
            pra_line = _safe_float(pra_line_data.get("line"))
        
        p10 = float(np.percentile(pra_distribution, 10))
        p25 = float(np.percentile(pra_distribution, 25))
        p50 = float(np.percentile(pra_distribution, 50))
        p75 = float(np.percentile(pra_distribution, 75))
        p90 = float(np.percentile(pra_distribution, 90))
        
        over_prob = None
        under_prob = None
        over_ev = None
        under_ev = None
        
        if pra_line and pra_line > 0:
            over_count = np.sum(pra_distribution > pra_line)
            over_prob = float(over_count / len(pra_distribution))
            under_prob = 1.0 - over_prob
            
            pra_over_odds = pra_line_data.get("over_odds", -110) if pra_line_data else -110
            pra_under_odds = pra_line_data.get("under_odds", -110) if pra_line_data else -110
            
            over_implied = _odds_to_implied(pra_over_odds or -110)
            under_implied = _odds_to_implied(pra_under_odds or -110)
            
            over_ev = (over_prob - over_implied) * 100
            under_ev = (under_prob - under_implied) * 100
            
            over_prob = float(round(over_prob * 100, 1))
            under_prob = float(round(under_prob * 100, 1))
            over_ev = float(round(over_ev, 2))
            under_ev = float(round(under_ev, 2))
        
        result.stats["pra"] = StatSimResult(
            mean=round(float(np.mean(pra_distribution)), 2),
            stdev=round(float(np.std(pra_distribution)), 2),
            p10=round(p10, 2),
            p25=round(p25, 2),
            p50=round(p50, 2),
            p75=round(p75, 2),
            p90=round(p90, 2),
            line=pra_line,
            over_prob=over_prob,
            under_prob=under_prob,
            over_ev=over_ev,
            under_ev=under_ev,
        )
    
    return result if result.stats else None


def _odds_to_implied(odds: int) -> float:
    """Convert American odds to implied probability."""
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


def build_game_context(
    game_data: Dict[str, Any],
) -> SimulationContext:
    """Build simulation context from game data."""
    away_abbr = game_data.get("away_abbr", "")
    home_abbr = game_data.get("home_abbr", "")
    game_id = game_data.get("game_id", 0)
    
    teams = game_data.get("teams", {})
    away_team = teams.get(away_abbr, {})
    home_team = teams.get(home_abbr, {})
    
    away_advanced = away_team.get("advanced", {})
    home_advanced = home_team.get("advanced", {})
    
    away_net = _safe_float(away_advanced.get("net_rating"))
    home_net = _safe_float(home_advanced.get("net_rating"))
    
    away_pace = _safe_float(away_team.get("pace_last_10")) or _safe_float(away_advanced.get("pace")) or 100.0
    home_pace = _safe_float(home_team.get("pace_last_10")) or _safe_float(home_advanced.get("pace")) or 100.0
    combined_pace = (away_pace + home_pace) / 2.0
    
    # Spread proxy: positive = home favored
    # Home court advantage is roughly 2-3 points
    spread_proxy = home_net - away_net + 2.5
    
    # Blowout risk based on net rating diff
    blowout_risk = compute_blowout_risk(abs(home_net - away_net))
    
    return SimulationContext(
        game_id=game_id,
        home_team=home_abbr,
        away_team=away_abbr,
        home_net_rating=home_net,
        away_net_rating=away_net,
        combined_pace=combined_pace,
        spread_proxy=spread_proxy,
        blowout_risk=blowout_risk,
        home_dvp=home_team.get("dvp", {}),
        away_dvp=away_team.get("dvp", {}),
    )


def run_full_slate_simulation(
    master_data: Dict[str, Any],
    lines_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Run simulation for all players in the full slate."""
    
    games = master_data.get("games", [])
    lines = lines_data.get("lines", lines_data)  # Handle both v3 format and flat format
    lines_by_id = build_lines_index(lines) if isinstance(lines, dict) else {}
    
    all_results: List[Dict[str, Any]] = []
    games_processed = 0
    
    for game_data in games:
        game_id = game_data.get("game_id", 0)
        away_abbr = game_data.get("away_abbr", "")
        home_abbr = game_data.get("home_abbr", "")
        
        print(f"[INFO] Simulating {away_abbr} @ {home_abbr}...", file=sys.stderr)
        
        # Build context
        context = build_game_context(game_data)
        
        # Get players
        players = game_data.get("players", {})
        away_players = players.get(away_abbr, [])
        home_players = players.get(home_abbr, [])
        
        # Add team context to players
        away_team_data = game_data.get("teams", {}).get(away_abbr, {})
        home_team_data = game_data.get("teams", {}).get(home_abbr, {})
        
        for p in away_players:
            p["_team_context"] = away_team_data
        for p in home_players:
            p["_team_context"] = home_team_data
        
        all_team_players = away_players + home_players
        
        # Simulate each player
        for player_data in all_team_players:
            result = simulate_player(
                player_data,
                lines,
                context,
                all_team_players,
                lines_by_id=lines_by_id,
            )
            if result:
                # Convert to dict
                result_dict = {
                    "player_id": result.player_id,
                    "player_name": result.player_name,
                    "team": result.team,
                    "game_id": result.game_id,
                    "opponent": result.opponent,
                    "position": result.position,
                    "is_starter": result.is_starter,
                    "injury_status": result.injury_status,
                    "minutes_proj": result.minutes_proj,
                    "blowout_risk": round(result.blowout_risk, 3),
                    "context_notes": list(set(result.context_notes)),  # Dedupe
                    "stats": {
                        stat_type: asdict(stat_result)
                        for stat_type, stat_result in result.stats.items()
                    },
                }
                all_results.append(result_dict)
        
        games_processed += 1
    
    return {
        "meta": {
            "date": master_data.get("meta", {}).get("game_date"),
            "games_processed": games_processed,
            "players_simulated": len(all_results),
            "simulations_per_player": NUM_SIMULATIONS,
            "stat_types": STAT_TYPES,
        },
        "results": all_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pro Parlay Syndicate v3.0 - Multi-Stat Simulation Engine"
    )
    parser.add_argument(
        "--master-data",
        required=True,
        help="Path to master data JSON (from fetch_master_data_v3.py)",
    )
    parser.add_argument(
        "--lines",
        required=True,
        help="Path to live lines JSON (from fetch_live_lines_v3.py)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path (optional, defaults to stdout)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=RANDOM_SEED,
        help=f"Random seed for reproducibility (default: {RANDOM_SEED})",
    )
    args = parser.parse_args()
    
    # Set seed
    seed = args.seed
    
    # Load input files
    print(f"[INFO] Loading master data from {args.master_data}...", file=sys.stderr)
    with open(args.master_data) as f:
        master_data = json.load(f)
    
    print(f"[INFO] Loading lines from {args.lines}...", file=sys.stderr)
    with open(args.lines) as f:
        lines_data = json.load(f)
    
    # Run simulation
    print("[INFO] Running Monte Carlo simulations...", file=sys.stderr)
    np.random.seed(seed)
    results = run_full_slate_simulation(master_data, lines_data)
    
    # Output
    output = json.dumps(results, indent=2)
    
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"[INFO] Results written to {args.output}", file=sys.stderr)
        print(f"[INFO] Simulated {results['meta']['players_simulated']} players across {results['meta']['games_processed']} games", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
