#!/usr/bin/env python3
"""simulation_engine.py

GOAT All-Star Enhanced Monte Carlo simulation engine for NBA points predictions.

This script:
1. Reads GAME_DATA JSON from fetch_points_game_data.py (stdin or file)
2. Accepts --baselines argument with player projection lines
3. Uses GOAT advanced metrics (Usage%, TS%, DRtg) for superior adjustments
4. Runs 10,000-iteration Monte Carlo simulation for each starter
5. Calculates Win Probability % of clearing the baseline line
6. Outputs JSON with simulation results and GOAT factors

GOAT Features:
- Official Usage Rate from /nba/v1/season_averages/advanced
- True Shooting % for efficiency adjustments
- Team Defensive Rating from /nba/v1/standings
- Clutch scoring from /nba/v1/play_by_play
- League scoring rank from /nba/v1/leaders
- Automatic starter filtering via is_starter field

Usage:
  # Pipe from fetch_points_game_data.py:
  python3 fetch_points_game_data.py --game-date 2025-01-15 --away BOS --home MIN --season 2025 | \
    python3 simulation_engine.py --baselines '{"Jayson Tatum": 27.5, "Jaylen Brown": 22.5}'

  # Or use file input:
  python3 simulation_engine.py \
    --game-data-file game_data.json \
    --baselines '{"Jayson Tatum": 27.5, "Jaylen Brown": 22.5}'

  # Baselines can also be a file path:
  python3 simulation_engine.py \
    --game-data-file game_data.json \
    --baselines-file baselines.json

  # Filter to starters only:
  python3 simulation_engine.py \
    --game-data-file game_data.json \
    --baselines-file baselines.json \
    --starters-only

Requirements:
  - numpy (for efficient Monte Carlo simulation)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    import random

# Default standard deviation when L5 stdev is unavailable
DEFAULT_STDEV = 6.0

# Number of Monte Carlo iterations
DEFAULT_ITERATIONS = 10000


@dataclass
class SimulationResult:
    """Result of Monte Carlo simulation for a single player.
    
    GOAT Enhanced: Includes official advanced metrics in output.
    """
    player_name: str
    team: str
    position: str
    baseline_line: float
    adjusted_mean: float
    stdev: float
    simulated_mean: float
    win_prob_pct: float
    iterations: int
    edge_pts: float  # adjusted_mean - baseline_line
    is_starter: bool = True
    # GOAT Advanced Factors
    goat_factors: Optional[Dict[str, Any]] = None


def _safe_float(x: Any, default: float = 0.0) -> float:
    """Safely convert to float with default."""
    try:
        return float(x)
    except Exception:
        return default


def _cap(value: float, low: float, high: float) -> float:
    """Clamp value between low and high."""
    return max(low, min(high, value))


def _normalize_name_for_match(name: str) -> str:
    """Normalize player names for fuzzy matching."""
    s = name.strip().lower()
    for ch in [".", ","]:
        s = s.replace(ch, "")
    parts = [p for p in s.split() if p]
    # Remove common suffixes
    suffixes = {"jr", "sr", "ii", "iii", "iv", "v"}
    if parts and parts[-1] in suffixes:
        parts = parts[:-1]
    return " ".join(parts)


def compute_adjustment_score(
    player: Dict[str, Any],
    team_data: Dict[str, Any],
    opp_team_data: Dict[str, Any],
    is_away: bool,
    projected_game_pace: Optional[float],
) -> Tuple[float, float, float, Dict[str, Any]]:
    """
    Compute adjustment score for a player using GOAT All-Star logic.
    
    GOAT Enhanced: Prioritizes official advanced metrics (Usage%, TS%, DRtg)
    and includes clutch scoring and league rank adjustments.
    
    Returns: (adjustment_score, proj_minutes, l5_stdev, goat_factors)
    """
    season = player.get("season") or {}
    recent = player.get("recent") or {}
    
    # Extract stats
    season_pts = _safe_float(season.get("pts"), 0.0)
    season_minutes = _safe_float(season.get("minutes"), 0.0)
    
    l5_pts_avg = _safe_float((recent.get("pts") or {}).get("avg"), 0.0)
    l5_pts_stdev = _safe_float((recent.get("pts") or {}).get("stdev"), DEFAULT_STDEV)
    l5_minutes_avg = _safe_float(recent.get("minutes_avg"), 0.0)
    sample_size = int(recent.get("sample_size") or 0)
    
    # GOAT Advanced Stats - Prioritize season (official) over recent (estimated)
    usg_pct = season.get("usg_pct") or recent.get("usg_pct")
    ts_pct = season.get("ts_pct") or recent.get("ts_pct")
    off_rating = season.get("off_rating") or recent.get("off_rating")
    
    # GOAT Player-level advanced metrics
    clutch_pts_avg = player.get("clutch_pts_avg")
    pts_league_rank = player.get("pts_league_rank")
    is_starter = player.get("is_starter", False)
    
    # Team Advanced (GOAT Upgrade) - Use official standings data
    opp_adv = opp_team_data.get("advanced") or {}
    opp_drtg = _safe_float(opp_adv.get("defensive_rating"), 0.0)
    opp_nrtg = _safe_float(opp_adv.get("net_rating"), 0.0)
    opp_pace_official = opp_team_data.get("pace_official") or opp_adv.get("pace")
    
    # Project minutes
    if sample_size >= 3 and l5_minutes_avg > 0:
        proj_minutes = l5_minutes_avg
    else:
        proj_minutes = season_minutes or l5_minutes_avg
    
    # Get team context
    days_rest = int(team_data.get("days_rest", 1))
    
    # Get DvP bucket for this player's position (fallback if DRtg unavailable)
    position = str(player.get("position") or "").upper()
    dvp = opp_team_data.get("dvp") or {}
    dvp_bucket = "AVERAGE"
    
    if position in dvp:
        dvp_bucket = str(dvp[position].get("bucket", "AVERAGE"))
    elif "-" in position:
        for part in position.split("-"):
            if part in dvp:
                dvp_bucket = str(dvp[part].get("bucket", "AVERAGE"))
                break
    
    # Track GOAT factors for output
    goat_factors: Dict[str, Any] = {
        "usage_pct": _safe_float(usg_pct) if usg_pct else None,
        "ts_pct": _safe_float(ts_pct) if ts_pct else None,
        "off_rating": _safe_float(off_rating) if off_rating else None,
        "opp_drtg": opp_drtg if opp_drtg > 0 else None,
        "opp_nrtg": opp_nrtg,
        "clutch_ppg": _safe_float(clutch_pts_avg) if clutch_pts_avg else None,
        "league_rank": pts_league_rank,
        "is_starter": is_starter,
        "days_rest": days_rest,
        "dvp_bucket": dvp_bucket,
    }
    
    # Calculate adjustments (matching prompt2.0.md GOAT rules)
    adjustment_score = 0.0
    
    # A. Minutes & Role Stability (Dec 31 patch)
    if proj_minutes >= 34:
        minutes_adj = 1.2
    elif proj_minutes >= 30:
        minutes_adj = 0.6
    elif proj_minutes >= 26:
        minutes_adj = 0.0
    else:
        minutes_adj = -3.3
    
    if sample_size < 3:
        minutes_adj *= 0.5
    adjustment_score += minutes_adj
    
    # B. Recent Form (capped, Jan 4 patch)
    if season_pts > 0:
        form_raw = ((l5_pts_avg - season_pts) / season_pts) * 14.5
        form_adj = _cap(form_raw, -5.0, 5.0)
    adjustment_score += form_adj
    
    # C. Consistency / Volatility (Jan 1 patch)
    if l5_pts_stdev <= 5:
        consistency_adj = 1.3  # Reward stable scorers
    elif l5_pts_stdev <= 7:
        consistency_adj = 0.0
    else:
        consistency_adj = -1.7  # Increased penalty for volatile players
    adjustment_score += consistency_adj
    
    # D. Pace Environment (Jan 4 patch)
    if projected_game_pace is not None:
        if projected_game_pace >= 104:
            pace_adj = 2.7
        elif projected_game_pace <= 98:
            pace_adj = -2.7
        else:
            pace_adj = 0.0
    else:
        pace_adj = 0.0
    adjustment_score += pace_adj
    
    # E. Usage Rate (GOAT Official Data)
    usage_adj = 0.0
    if usg_pct is not None:
        usg_pct_val = _safe_float(usg_pct, 0.0)
        if usg_pct_val >= 28.0:
            usage_adj = 1.2  # Primary scorer (Dec 28 patch)
        elif usg_pct_val < 20.0:
            usage_adj = -1.0  # Role player
    adjustment_score += usage_adj
    
    # F. True Shooting % (GOAT Official Data)
    if ts_pct is not None:
        ts_pct_val = _safe_float(ts_pct, 0.0)
        if ts_pct_val >= 0.62:
            ts_adj = 1.0  # Elite efficiency
        elif ts_pct_val >= 0.58:
            ts_adj = 0.5  # Above average
        elif ts_pct_val < 0.52:
            ts_adj = -0.5  # Below average
        else:
            ts_adj = 0.0
        adjustment_score += ts_adj
    
    # G. Advanced Matchup (GOAT Upgrade - Defensive Rating from standings, Jan 4 patch)
    if opp_drtg > 0:
        if opp_drtg >= 118:  # Bottom 10 Defense
            adjustment_score += 1.7
        elif opp_drtg >= 114:  # Below avg defense
            adjustment_score += 0.8
        elif opp_drtg <= 108:  # Top 5 Defense
            adjustment_score -= 2.0
        elif opp_drtg <= 112:  # Above avg defense
            adjustment_score -= 1.0
    else:
        # Fallback to DvP if DRtg unavailable
        if dvp_bucket == "WEAK":
            adjustment_score += 1.7
        elif dvp_bucket == "STRONG":
            adjustment_score -= 2.0
    
    # H. Days of Rest (Jan 1 patch - reduced optimal recovery)
    if days_rest == 0:  # B2B
        rest_adj = -1.8 if is_away else -1.0
    elif days_rest == 1:
        rest_adj = 0.0
    elif days_rest == 2:
        rest_adj = 0.2  # Reduced from 0.4
    else:  # 3+
        rest_adj = -0.3
    adjustment_score += rest_adj
    
    # I. Clutch Scoring (GOAT - from play_by_play)
    if clutch_pts_avg is not None:
        clutch_val = _safe_float(clutch_pts_avg, 0.0)
        if clutch_val >= 4.0:
            clutch_adj = 1.0  # Clutch performer
        elif clutch_val >= 2.5:
            clutch_adj = 0.5  # Reliable in clutch
        elif clutch_val < 1.0:
            clutch_adj = -0.5  # Struggles in clutch
        else:
            clutch_adj = 0.0
        adjustment_score += clutch_adj
    
    # J. League Scoring Rank (GOAT - from leaders)
    if pts_league_rank is not None:
        if pts_league_rank <= 10:
            rank_adj = 1.4  # Top 10 scorer (Jan 3 patch)
        elif pts_league_rank <= 25:
            rank_adj = 0.5  # Top 25 scorer
        elif pts_league_rank >= 100:
            rank_adj = -0.5  # Low volume
        else:
            rank_adj = 0.0
        adjustment_score += rank_adj
    
    return adjustment_score, proj_minutes, l5_pts_stdev, goat_factors


def compute_adjusted_projection(
    season_pts: float,
    l5_pts_avg: float,
    adjustment_score: float,
    proj_minutes: float,
    l5_minutes_avg: float,
) -> float:
    """
    Compute adjusted projection using same logic as points_picks.py.
    """
    # Baseline: 55% season + 45% L5
    baseline = 0.55 * season_pts + 0.45 * l5_pts_avg
    
    # Minute scaling
    minute_scale = 1.0
    if l5_minutes_avg > 0:
        minute_scale = _cap(proj_minutes / l5_minutes_avg, 0.85, 1.15)
    
    # Context bump from adjustment score
    context_bump = _cap(adjustment_score / 10.0, -0.20, 0.20)
    
    proj_pts = baseline * minute_scale * (1.0 + context_bump)
    
    # Sanity constraints
    if l5_pts_avg > 0 and proj_pts > (l5_pts_avg * 1.30):
        proj_pts = l5_pts_avg * 1.30
    if proj_minutes < 24:
        proj_pts *= 0.90
    
    return round(proj_pts, 2)


def run_monte_carlo(
    mean: float,
    stdev: float,
    line: float,
    iterations: int = DEFAULT_ITERATIONS,
) -> Tuple[float, float]:
    """
    Run Monte Carlo simulation to estimate probability of exceeding a line.
    
    Returns: (win_probability_pct, simulated_mean)
    """
    # Handle edge cases
    if stdev <= 0:
        # No variance - deterministic outcome
        return (100.0 if mean > line else 0.0), mean
    
    if HAS_NUMPY:
        # Efficient numpy simulation
        samples = np.random.normal(mean, stdev, iterations)
        wins = np.sum(samples > line)
        simulated_mean = float(np.mean(samples))
    else:
        # Fallback to pure Python
        samples = [random.gauss(mean, stdev) for _ in range(iterations)]
        wins = sum(1 for s in samples if s > line)
        simulated_mean = sum(samples) / len(samples)
    
    win_prob = (wins / iterations) * 100.0
    return round(win_prob, 1), round(simulated_mean, 2)


def find_player_in_game_data(
    player_name: str,
    game_data: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Find a player in GAME_DATA by normalized name matching.
    
    Returns: (player_dict, team_abbr) or (None, None) if not found.
    """
    target_norm = _normalize_name_for_match(player_name)
    
    players_by_team = game_data.get("players") or {}
    
    for team_abbr, players in players_by_team.items():
        for player in players:
            player_name_in_data = str(player.get("name") or "")
            if _normalize_name_for_match(player_name_in_data) == target_norm:
                return player, team_abbr
    
    return None, None


def simulate_game(
    game_data: Dict[str, Any],
    baselines: Dict[str, float],
    iterations: int = DEFAULT_ITERATIONS,
    starters_only: bool = False,
) -> List[SimulationResult]:
    """
    Run Monte Carlo simulations for all players in baselines.
    
    GOAT Enhanced: Supports filtering to starters only via is_starter field.
    
    Args:
        game_data: JSON output from fetch_points_game_data.py
        baselines: Dict mapping player names to their projection lines
        iterations: Number of Monte Carlo iterations
        starters_only: If True, only simulate players with is_starter=True
    
    Returns: List of SimulationResult objects
    """
    meta = game_data.get("meta") or {}
    teams = game_data.get("teams") or {}
    
    away_abbr = str(meta.get("away_abbr") or "").upper()
    home_abbr = str(meta.get("home_abbr") or "").upper()
    
    # Compute projected game pace
    away_team = teams.get(away_abbr) or {}
    home_team = teams.get(home_abbr) or {}
    
    away_pace = away_team.get("pace_last_10")
    home_pace = home_team.get("pace_last_10")
    
    if away_pace is not None and home_pace is not None:
        projected_game_pace = (_safe_float(away_pace) + _safe_float(home_pace)) / 2.0
    elif away_pace is not None:
        projected_game_pace = _safe_float(away_pace)
    elif home_pace is not None:
        projected_game_pace = _safe_float(home_pace)
    else:
        projected_game_pace = None
    
    results: List[SimulationResult] = []
    
    for player_name, baseline_line in baselines.items():
        # Find player in game data
        player, team_abbr = find_player_in_game_data(player_name, game_data)
        
        if player is None:
            print(f"[WARN] Player '{player_name}' not found in GAME_DATA", file=sys.stderr)
            continue
        
        # GOAT: Filter to starters only if requested
        if starters_only and not player.get("is_starter", False):
            print(f"[INFO] Skipping non-starter: {player_name}", file=sys.stderr)
            continue
        
        # Determine team context
        is_away = (team_abbr == away_abbr)
        team_data = teams.get(team_abbr) or {}
        opp_abbr = home_abbr if is_away else away_abbr
        opp_team_data = teams.get(opp_abbr) or {}
        
        # Compute adjustment score with GOAT factors
        adjustment_score, proj_minutes, l5_stdev, goat_factors = compute_adjustment_score(
            player=player,
            team_data=team_data,
            opp_team_data=opp_team_data,
            is_away=is_away,
            projected_game_pace=projected_game_pace,
        )
        
        # Get season and L5 stats
        season = player.get("season") or {}
        recent = player.get("recent") or {}
        
        season_pts = _safe_float(season.get("pts"), 0.0)
        l5_pts_avg = _safe_float((recent.get("pts") or {}).get("avg"), 0.0)
        l5_minutes_avg = _safe_float(recent.get("minutes_avg"), 0.0)
        
        # Compute adjusted projection (mean for simulation)
        adjusted_mean = compute_adjusted_projection(
            season_pts=season_pts,
            l5_pts_avg=l5_pts_avg,
            adjustment_score=adjustment_score,
            proj_minutes=proj_minutes,
            l5_minutes_avg=l5_minutes_avg,
        )
        
        # Use L5 stdev, with fallback to default
        stdev = l5_stdev if l5_stdev > 0 else DEFAULT_STDEV
        
        # Run Monte Carlo simulation
        win_prob, simulated_mean = run_monte_carlo(
            mean=adjusted_mean,
            stdev=stdev,
            line=baseline_line,
            iterations=iterations,
        )
        
        results.append(SimulationResult(
            player_name=str(player.get("name") or player_name),
            team=team_abbr,
            position=str(player.get("position") or ""),
            baseline_line=baseline_line,
            adjusted_mean=adjusted_mean,
            stdev=round(stdev, 2),
            simulated_mean=simulated_mean,
            win_prob_pct=win_prob,
            iterations=iterations,
            edge_pts=round(adjusted_mean - baseline_line, 2),
            is_starter=goat_factors.get("is_starter", True),
            goat_factors=goat_factors,
        ))
    
    # Sort by win probability descending
    results.sort(key=lambda r: r.win_prob_pct, reverse=True)
    
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monte Carlo simulation for NBA points predictions."
    )
    parser.add_argument(
        "--game-data-file",
        default=None,
        help="Path to GAME_DATA JSON file (if not piping from stdin)",
    )
    parser.add_argument(
        "--baselines",
        default=None,
        help='JSON string mapping player names to projection lines, e.g. \'{"Jayson Tatum": 27.5}\'',
    )
    parser.add_argument(
        "--baselines-file",
        default=None,
        help="Path to JSON file with baselines",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help=f"Number of Monte Carlo iterations (default: {DEFAULT_ITERATIONS})",
    )
    parser.add_argument(
        "--starters-only",
        action="store_true",
        help="Only simulate players marked as starters (is_starter=True)",
    )
    args = parser.parse_args()
    
    # Load GAME_DATA
    if args.game_data_file:
        with open(args.game_data_file, "r") as f:
            game_data = json.load(f)
    else:
        # Read from stdin
        game_data = json.load(sys.stdin)
    
    # Load baselines
    if args.baselines:
        # Try to parse as JSON string
        try:
            baselines = json.loads(args.baselines)
        except json.JSONDecodeError:
            # Maybe it's a file path
            with open(args.baselines, "r") as f:
                baselines = json.load(f)
    elif args.baselines_file:
        with open(args.baselines_file, "r") as f:
            baselines = json.load(f)
    else:
        print("Error: Must provide --baselines or --baselines-file", file=sys.stderr)
        sys.exit(1)
    
    # Run simulations
    results = simulate_game(
        game_data=game_data,
        baselines=baselines,
        iterations=args.iterations,
        starters_only=args.starters_only,
    )
    
    # Output results as JSON
    output = {
        "simulation_config": {
            "iterations": args.iterations,
            "default_stdev": DEFAULT_STDEV,
        },
        "results": [
            {
                "player": r.player_name,
                "team": r.team,
                "position": r.position,
                "baseline_line": r.baseline_line,
                "adjusted_mean": r.adjusted_mean,
                "stdev": r.stdev,
                "simulated_mean": r.simulated_mean,
                "win_prob_pct": r.win_prob_pct,
                "edge_pts": r.edge_pts,
                "is_starter": r.is_starter,
                "goat_factors": r.goat_factors,
            }
            for r in results
        ],
    }
    
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
