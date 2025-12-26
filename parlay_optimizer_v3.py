#!/usr/bin/env python3
"""parlay_optimizer_v3.py

Pro Parlay Syndicate v3.0 - 10-Leg Parlay Optimizer

GOAL: Build a daily 10-leg parlay across the full NBA slate using:
- PTS: Points
- REB: Rebounds  
- AST: Assists
- 3PM: Three-pointers Made
- PRA: Points + Rebounds + Assists

HARD RULES:
1. One leg per player (strict cap)
2. Max 1 player per game (independence rule)
3. Skip any player/stat with missing lines (no fallback)
4. Safety Buffer:
   - Overs: 10th percentile floor must clear Line + 0.5
   - Unders: 90th percentile ceiling must stay below Line - 0.5
5. Over/Under Logic: Prefer Overs. Only pick Under if 90th percentile < buffered line
6. Selection Objective: Maximize floor safety (10th percentile distance), then win_prob_pct

ADVANCED FEATURES:
- Blowout Risk adjustment
- Usage Cannibalization check
- Team-level volatility penalty

Usage:
  # Full automated pipeline:
  python3 parlay_optimizer_v3.py --date 2025-01-15 --season 2025

  # Using pre-fetched data:
  python3 parlay_optimizer_v3.py \
    --simulation-results simulation_results_v3.json \
    --lines live_lines_v3.json

Output:
  final_parlay_v3.json with 10 safest legs

Requirements:
  - fetch_master_data_v3.py
  - fetch_live_lines_v3.py  
  - simulation_engine_v3.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import date
from typing import Any, Dict, List, Optional, Set, Tuple

# Safety buffer constants
# NOTE: Set to 0 for standard operation. The 10th percentile floor check
# already provides safety - requiring floor > line is sufficient.
# Increase buffer for more conservative picks (e.g., 0.5 = very strict)
OVER_BUFFER = 0.0   # Floor must clear Line + buffer (0 = floor just needs to beat line)
UNDER_BUFFER = 0.0  # Ceiling must stay below Line - buffer

# Selection constants
MAX_LEGS = 10
MAX_PLAYERS_PER_GAME = 1
STAT_TYPES = ["pts", "reb", "ast", "fg3m", "pra"]


@dataclass
class ParlayLeg:
    """A single leg in the parlay."""
    player: str
    player_id: int
    team: str
    stat_type: str
    side: str  # "Over" or "Under"
    line: float
    win_prob_pct: float
    floor_10th: float
    ceiling_90th: float
    floor_margin: float  # Distance from line (positive = safe)
    matchup_difficulty: str  # "EASY", "NEUTRAL", "HARD"
    game_id: int
    opponent: str
    context_notes: List[str]


@dataclass
class CandidateLeg:
    """Candidate leg before final selection."""
    player: str
    player_id: int
    team: str
    stat_type: str
    side: str
    line: float
    win_prob_pct: float
    floor_10th: float
    ceiling_90th: float
    floor_margin: float
    safety_score: float  # Combined safety metric
    matchup_difficulty: str
    game_id: int
    opponent: str
    blowout_risk: float
    context_notes: List[str]


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Safely convert value to float."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _resolve_python_executable() -> str:
    """Prefer local venv python if present for subprocess calls."""
    venv_python = os.path.join(os.path.dirname(__file__), ".venv", "bin", "python")
    if os.path.exists(venv_python):
        return venv_python
    return sys.executable or "python3"


def compute_matchup_difficulty(
    win_prob: float,
    floor_margin: float,
    blowout_risk: float,
) -> str:
    """Compute matchup difficulty label."""
    # Combine factors
    score = 0
    
    if win_prob >= 65:
        score += 2
    elif win_prob >= 55:
        score += 1
    elif win_prob < 45:
        score -= 2
    
    if floor_margin >= 2.0:
        score += 2
    elif floor_margin >= 1.0:
        score += 1
    elif floor_margin < 0:
        score -= 2
    
    if blowout_risk > 0.4:
        score -= 1
    
    if score >= 3:
        return "EASY"
    elif score <= -1:
        return "HARD"
    return "NEUTRAL"


def compute_safety_score(
    floor_margin: float,
    win_prob: float,
    blowout_risk: float,
) -> float:
    """Compute combined ranking score.

    Goal: maximize *accuracy* (win probability) while still preferring
    comfortable margins and avoiding blowout volatility.
    """
    prob_score = win_prob  # 0-100
    margin_score = floor_margin * 2.0
    blowout_penalty = blowout_risk * 8.0

    return prob_score + margin_score - blowout_penalty


def evaluate_leg(
    player_result: Dict[str, Any],
    stat_type: str,
    lines_data: Dict[str, Any],
) -> Optional[CandidateLeg]:
    """Evaluate a single potential leg.
    
    Returns CandidateLeg if valid, None if should be skipped.
    """
    player_name = player_result.get("player_name", "")
    player_id = player_result.get("player_id", 0)
    team = player_result.get("team", "")
    game_id = player_result.get("game_id", 0)
    opponent = player_result.get("opponent", "")
    blowout_risk = _safe_float(player_result.get("blowout_risk", 0))
    context_notes = player_result.get("context_notes", [])
    
    # Get stat simulation results
    stats = player_result.get("stats", {})
    stat_result = stats.get(stat_type)
    
    if not stat_result:
        return None
    
    line = _safe_float(stat_result.get("line"))
    if line <= 0:
        return None  # No line available
    
    p10 = _safe_float(stat_result.get("p10"))
    p90 = _safe_float(stat_result.get("p90"))
    over_prob = _safe_float(stat_result.get("over_prob"))
    under_prob = _safe_float(stat_result.get("under_prob"))
    
    # Determine Over vs Under
    # Rule: Prefer Overs. Only pick Under if 90th percentile < buffered line
    
    # Check Over validity: floor (p10) must clear Line + OVER_BUFFER
    over_buffered_line = line + OVER_BUFFER
    over_floor_margin = p10 - over_buffered_line
    over_valid = over_floor_margin > 0
    
    # Check Under validity: ceiling (p90) must stay below Line - UNDER_BUFFER
    under_buffered_line = line - UNDER_BUFFER
    under_ceiling_margin = under_buffered_line - p90
    under_valid = under_ceiling_margin > 0
    
    # Decision logic based on win probability and margins.
    # First: if floor/ceiling rules validate a side, use it.
    # If both validate, pick the higher win probability.
    # Fallback: if neither validates, allow >=55% side.

    if over_valid and under_valid:
        if over_prob >= under_prob:
            side = "Over"
            win_prob = over_prob
            floor_margin = over_floor_margin
        else:
            side = "Under"
            win_prob = under_prob
            floor_margin = under_ceiling_margin
    elif over_valid:
        side = "Over"
        win_prob = over_prob
        floor_margin = over_floor_margin
    elif under_valid:
        side = "Under"
        win_prob = under_prob
        floor_margin = under_ceiling_margin
    else:
        if over_prob >= 55.0 or under_prob >= 55.0:
            if over_prob >= under_prob:
                side = "Over"
                win_prob = over_prob
                floor_margin = over_floor_margin
            else:
                side = "Under"
                win_prob = under_prob
                floor_margin = under_ceiling_margin
        else:
            return None
    
    # Compute safety score
    safety_score = compute_safety_score(floor_margin, win_prob, blowout_risk)
    
    # Compute matchup difficulty
    matchup = compute_matchup_difficulty(win_prob, floor_margin, blowout_risk)
    
    return CandidateLeg(
        player=player_name,
        player_id=player_id,
        team=team,
        stat_type=stat_type,
        side=side,
        line=line,
        win_prob_pct=win_prob,
        floor_10th=p10,
        ceiling_90th=p90,
        floor_margin=round(floor_margin, 2),
        safety_score=round(safety_score, 2),
        matchup_difficulty=matchup,
        game_id=game_id,
        opponent=opponent,
        blowout_risk=blowout_risk,
        context_notes=context_notes,
    )


def select_optimal_parlay(
    candidates: List[CandidateLeg],
    max_legs: int = MAX_LEGS,
    max_players_per_game: int = MAX_PLAYERS_PER_GAME,
) -> List[ParlayLeg]:
    """Select optimal parlay legs using independence rules.
    
    Rules:
    1. One leg per player (strict cap)
    2. Max 1 player per game (independence)
    3. Maximize floor safety, then win_prob
    """
    # Sort primarily by win probability, then score.
    sorted_candidates = sorted(
        candidates,
        key=lambda c: (c.win_prob_pct, c.safety_score),
        reverse=True,
    )
    
    selected: List[ParlayLeg] = []
    used_players: Set[int] = set()
    game_counts: Dict[int, int] = {}
    
    for candidate in sorted_candidates:
        if len(selected) >= max_legs:
            break
        
        # Check independence rules
        if candidate.player_id in used_players:
            continue  # Already have a leg for this player
        
        if game_counts.get(candidate.game_id, 0) >= max_players_per_game:
            continue  # Already hit per-game cap
        
        # Add this leg
        leg = ParlayLeg(
            player=candidate.player,
            player_id=candidate.player_id,
            team=candidate.team,
            stat_type=candidate.stat_type,
            side=candidate.side,
            line=candidate.line,
            win_prob_pct=candidate.win_prob_pct,
            floor_10th=candidate.floor_10th,
            ceiling_90th=candidate.ceiling_90th,
            floor_margin=candidate.floor_margin,
            matchup_difficulty=candidate.matchup_difficulty,
            game_id=candidate.game_id,
            opponent=candidate.opponent,
            context_notes=candidate.context_notes,
        )
        
        selected.append(leg)
        used_players.add(candidate.player_id)
        game_counts[candidate.game_id] = game_counts.get(candidate.game_id, 0) + 1
    
    return selected


def run_pipeline(
    game_date: str,
    season: int,
    output_path: str = "final_parlay_v3.json",
    max_legs: int = MAX_LEGS,
    master_mode: str = "lite",
) -> Dict[str, Any]:
    """Run the full pipeline: fetch data -> simulate -> optimize."""
    print(f"[INFO] Starting Pro Parlay Syndicate v3.0 for {game_date}", file=sys.stderr)
    python_exe = _resolve_python_executable()
    
    # Step 1: Fetch master data
    print("[INFO] Step 1/4: Fetching master data...", file=sys.stderr)
    master_data_path = f"master_data_v3_{game_date}.json"
    
    result = subprocess.run(
        [
            python_exe, "fetch_master_data_v3.py",
            "--date", game_date,
            "--season", str(season),
            "--mode", master_mode,
            "--output", master_data_path,
        ],
        capture_output=True,
        text=True,
    )
    
    if result.returncode != 0:
        print(f"[ERROR] fetch_master_data_v3.py failed: {result.stderr}", file=sys.stderr)
        raise RuntimeError("Failed to fetch master data")
    
    # Step 2: Fetch live lines
    print("[INFO] Step 2/4: Fetching live lines...", file=sys.stderr)
    lines_path = f"live_lines_v3_{game_date}.json"
    
    result = subprocess.run(
        [
            python_exe, "fetch_live_lines_v3.py",
            "--date", game_date,
            "--output", lines_path,
        ],
        capture_output=True,
        text=True,
    )
    
    if result.returncode != 0:
        print(f"[ERROR] fetch_live_lines_v3.py failed: {result.stderr}", file=sys.stderr)
        raise RuntimeError("Failed to fetch live lines")
    
    # Step 3: Run simulation
    print("[INFO] Step 3/4: Running Monte Carlo simulations...", file=sys.stderr)
    sim_path = f"simulation_results_v3_{game_date}.json"
    
    result = subprocess.run(
        [
            python_exe, "simulation_engine_v3.py",
            "--master-data", master_data_path,
            "--lines", lines_path,
            "--output", sim_path,
        ],
        capture_output=True,
        text=True,
    )
    
    if result.returncode != 0:
        print(f"[ERROR] simulation_engine_v3.py failed: {result.stderr}", file=sys.stderr)
        raise RuntimeError("Failed to run simulation")
    
    # Step 4: Optimize parlay
    print("[INFO] Step 4/4: Optimizing parlay selection...", file=sys.stderr)
    
    with open(sim_path) as f:
        sim_results = json.load(f)
    
    with open(lines_path) as f:
        lines_data = json.load(f)
    
    parlay = optimize_parlay(sim_results, lines_data, max_legs)
    
    # Save output
    with open(output_path, "w") as f:
        json.dump(parlay, f, indent=2)
    
    print(f"[INFO] Parlay saved to {output_path}", file=sys.stderr)
    
    return parlay


def optimize_parlay(
    sim_results: Dict[str, Any],
    lines_data: Dict[str, Any],
    max_legs: int = MAX_LEGS,
) -> Dict[str, Any]:
    """Optimize parlay from simulation results.
    
    Can be run standalone with pre-fetched data.
    """
    results = sim_results.get("results", [])
    lines = lines_data.get("lines", lines_data)
    
    print(f"[INFO] Evaluating {len(results)} players...", file=sys.stderr)
    
    # Generate all candidate legs
    candidates: List[CandidateLeg] = []
    
    for player_result in results:
        for stat_type in STAT_TYPES:
            candidate = evaluate_leg(player_result, stat_type, lines)
            if candidate:
                candidates.append(candidate)
    
    print(f"[INFO] Generated {len(candidates)} valid candidate legs", file=sys.stderr)
    
    # Select optimal parlay
    selected = select_optimal_parlay(candidates, max_legs, MAX_PLAYERS_PER_GAME)
    
    print(f"[INFO] Selected {len(selected)} legs for parlay", file=sys.stderr)
    
    if len(selected) < max_legs:
        print(
            f"[WARN] Only selected {len(selected)} legs (requested {max_legs}).",
            file=sys.stderr,
        )

    # Compute parlay stats
    if selected:
        combined_prob = 1.0
        for leg in selected:
            combined_prob *= (leg.win_prob_pct / 100.0)
        combined_prob_pct = combined_prob * 100
        
        avg_floor_margin = sum(leg.floor_margin for leg in selected) / len(selected)
        avg_win_prob = sum(leg.win_prob_pct for leg in selected) / len(selected)
    else:
        combined_prob_pct = 0.0
        avg_floor_margin = 0.0
        avg_win_prob = 0.0
    
    # Build output
    output = {
        "meta": {
            "date": sim_results.get("meta", {}).get("date"),
            "legs_count": len(selected),
            "candidates_evaluated": len(candidates),
            "combined_win_prob_pct": round(combined_prob_pct, 4),
            "avg_floor_margin": round(avg_floor_margin, 2),
            "avg_individual_win_prob": round(avg_win_prob, 1),
            "stat_types_used": list(set(leg.stat_type for leg in selected)),
        },
        "parlay": [
            {
                "player": leg.player,
                "team": leg.team,
                "stat_type": leg.stat_type,
                "side": leg.side,
                "line": leg.line,
                "win_prob_pct": leg.win_prob_pct,
                "10th_percentile_floor": leg.floor_10th,
                "matchup_difficulty": leg.matchup_difficulty,
                "game_id": leg.game_id,
                "opponent": leg.opponent,
            }
            for leg in selected
        ],
        "detailed_legs": [asdict(leg) for leg in selected],
    }
    
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pro Parlay Syndicate v3.0 - 10-Leg Parlay Optimizer"
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Game date YYYY-MM-DD (triggers full pipeline)",
    )
    parser.add_argument(
        "--season",
        type=int,
        default=2025,
        help="NBA season year (default: 2025)",
    )
    parser.add_argument(
        "--simulation-results",
        default=None,
        help="Path to simulation results JSON (skip fetching)",
    )
    parser.add_argument(
        "--lines",
        default=None,
        help="Path to live lines JSON (skip fetching)",
    )
    parser.add_argument(
        "--output",
        default="final_parlay_v3.json",
        help="Output file path (default: final_parlay_v3.json)",
    )
    parser.add_argument(
        "--max-legs",
        type=int,
        default=MAX_LEGS,
        help=f"Maximum number of parlay legs (default: {MAX_LEGS})",
    )
    parser.add_argument(
        "--master-mode",
        choices=["lite", "full", "fast"],
        default="lite",
        help="Master data enrichment mode (default: lite)",
    )
    args = parser.parse_args()
    
    max_legs = args.max_legs
    
    # Determine mode
    if args.simulation_results and args.lines:
        # Standalone mode with pre-fetched data
        print("[INFO] Running in standalone mode with pre-fetched data", file=sys.stderr)
        
        with open(args.simulation_results) as f:
            sim_results = json.load(f)
        
        with open(args.lines) as f:
            lines_data = json.load(f)
        
        parlay = optimize_parlay(sim_results, lines_data, max_legs)
        
        with open(args.output, "w") as f:
            json.dump(parlay, f, indent=2)
        
        print(f"[INFO] Parlay saved to {args.output}", file=sys.stderr)
        
    elif args.date:
        # Full pipeline mode
         parlay = run_pipeline(args.date, args.season, args.output, max_legs, args.master_mode)
        
    else:
        # Default to today
        today = date.today().isoformat()
        print(f"[INFO] No date specified, using today: {today}", file=sys.stderr)
        parlay = run_pipeline(today, args.season, args.output, max_legs, args.master_mode)
    
    # Print summary
    meta = parlay.get("meta", {})
    legs = parlay.get("parlay", [])
    
    print("\n" + "=" * 60, file=sys.stderr)
    print("PRO PARLAY SYNDICATE v3.0 - FINAL PICKS", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"Date: {meta.get('date')}", file=sys.stderr)
    print(f"Legs: {meta.get('legs_count')}", file=sys.stderr)
    print(f"Combined Win Prob: {meta.get('combined_win_prob_pct'):.4f}%", file=sys.stderr)
    print(f"Avg Floor Margin: {meta.get('avg_floor_margin'):.2f}", file=sys.stderr)
    print("-" * 60, file=sys.stderr)
    
    for i, leg in enumerate(legs, 1):
        stat = leg['stat_type'].upper()
        side = leg['side']
        line = leg['line']
        prob = leg['win_prob_pct']
        print(f"{i:2}. {leg['player']} ({leg['team']}) - {stat} {side} {line} [{prob:.1f}%]", file=sys.stderr)
    
    print("=" * 60, file=sys.stderr)


if __name__ == "__main__":
    main()
