#!/usr/bin/env python3
"""Generate picks based on simulation results and prompt2.0_STAT.md methodology"""

import json
import math
from typing import Dict, List, Optional, Tuple

def calculate_blowout_risk(home_net_rating: float, away_net_rating: float) -> float:
    """Calculate blowout risk using sigmoid function"""
    net_rating_diff = abs(home_net_rating - away_net_rating)
    return 1.0 / (1.0 + math.exp(-0.3 * (net_rating_diff - 8)))

def calculate_safety_score(floor_margin: float, win_prob: float, blowout_risk: float) -> float:
    """Calculate safety score"""
    return (floor_margin * 10) + (win_prob / 10) - (blowout_risk * 5)

def classify_matchup_difficulty(win_prob: float, floor_margin: float, blowout_risk: float) -> str:
    """Classify matchup difficulty"""
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
    else:
        return "NEUTRAL"

def get_stat_specific_factors(player_data: Dict, stat_type: str, team_data: Dict, opp_data: Dict) -> Dict:
    """Get stat-specific factors for GOAT adjustments"""
    factors = {}
    
    season = player_data.get("season", {})
    recent = player_data.get("recent", {})
    position = player_data.get("position", "")
    
    if stat_type == "REB":
        reb_pct = season.get("reb_pct")
        if reb_pct:
            factors["reb_pct"] = reb_pct
        
        factors["position"] = position
        
        opp_fg_pct = opp_data.get("advanced", {}).get("offensive_rating", 110.0) / 100.0  # Approximation
        opp_miss_rate = 1 - (opp_fg_pct * 0.45)  # Rough estimate
        factors["opp_miss_rate"] = round(opp_miss_rate, 2)
        
        minutes_stdev = recent.get("reb", {}).get("stdev", 0)
        if minutes_stdev <= 3.0:
            factors["minutes_stability"] = "HIGH"
        elif minutes_stdev >= 8.0:
            factors["minutes_stability"] = "LOW"
        else:
            factors["minutes_stability"] = "MODERATE"
    
    elif stat_type == "AST":
        ast_pct = season.get("ast_pct")
        if ast_pct:
            factors["ast_pct"] = ast_pct
        
        team_fg_pct = team_data.get("advanced", {}).get("offensive_rating", 110.0) / 100.0  # Approximation
        factors["team_fg_pct"] = round(team_fg_pct * 0.45, 2)  # Rough estimate
        
        pace = team_data.get("advanced", {}).get("pace", 100.0)
        opp_pace = opp_data.get("advanced", {}).get("pace", 100.0)
        projected_pace = (pace + opp_pace) / 2
        factors["projected_pace"] = round(projected_pace, 1)
    
    elif stat_type == "PTS":
        usg_pct = recent.get("usg_pct") or season.get("usg_pct")
        if usg_pct:
            factors["usg_pct"] = usg_pct
        
        ts_pct = season.get("ts_pct")
        if ts_pct:
            factors["ts_pct"] = ts_pct
    
    return factors

def determine_side(p10: float, p90: float, line: float, over_prob: float, under_prob: float) -> Tuple[str, float]:
    """Determine over/under side based on floor/ceiling analysis"""
    over_floor_margin = p10 - line
    under_ceiling_margin = line - p90
    
    # Priority 1: Check OVER validity
    if over_floor_margin > 0:
        return "Over", over_floor_margin
    
    # Priority 2: Check UNDER validity
    if under_ceiling_margin > 0:
        return "Under", under_ceiling_margin
    
    # Priority 3: Relaxed criteria
    if over_prob >= 55:
        return "Over", over_floor_margin
    elif under_prob >= 55:
        return "Under", under_ceiling_margin
    else:
        return None, 0

def generate_why_summary(player_name: str, stat_type: str, side: str, factors: Dict, floor_margin: float, win_prob: float, matchup: str) -> str:
    """Generate why summary"""
    parts = []
    
    if stat_type == "REB":
        if factors.get("reb_pct"):
            parts.append(f"Reb% [{factors['reb_pct']:.1f}%]")
        if factors.get("position") and "C" in factors["position"]:
            parts.append("C position")
        if factors.get("opp_miss_rate") and factors["opp_miss_rate"] >= 0.52:
            parts.append(f"vs inefficient opp [{factors['opp_miss_rate']:.0%} miss rate]")
    
    elif stat_type == "AST":
        if factors.get("ast_pct"):
            parts.append(f"Ast% [{factors['ast_pct']:.1f}%]")
        if factors.get("projected_pace") and factors["projected_pace"] >= 102:
            parts.append(f"fast pace [{factors['projected_pace']:.1f}]")
    
    elif stat_type == "PTS":
        if factors.get("usg_pct"):
            parts.append(f"High usage [{factors['usg_pct']:.1f}%]")
    
    if floor_margin > 0:
        parts.append(f"Floor clears line by {floor_margin:.1f}")
    elif floor_margin < 0:
        parts.append(f"Floor {abs(floor_margin):.1f} below line")
    
    if win_prob >= 65:
        parts.append(f"{win_prob:.1f}% win prob")
    
    if matchup == "EASY":
        parts.append("EASY matchup")
    
    return ". ".join(parts) + "."

def main():
    # Load data
    with open("simulation_results.json", "r") as f:
        sim_results = json.load(f)
    
    with open("master_data.json", "r") as f:
        master_data = json.load(f)
    
    # Extract game data
    game = master_data["games"][0]
    away_abbr = game["away_abbr"]
    home_abbr = game["home_abbr"]
    game_date = game["game_date"]
    
    # Calculate blowout risk
    home_net = game["teams"][home_abbr]["advanced"]["net_rating"]
    away_net = game["teams"][away_abbr]["advanced"]["net_rating"]
    blowout_risk = calculate_blowout_risk(home_net, away_net)
    
    # Process each stat type
    stat_types = ["PTS", "REB", "AST"]
    output = {
        "meta": {
            "matchup": f"{away_abbr} @ {home_abbr}",
            "stat_types": stat_types,
            "game_date": game_date,
            "data_source": "balldontlie_v3",
            "features_used": [
                "percentile_floor_ceiling",
                "blowout_risk",
                "stat_specific_adjustments",
                "over_under_logic",
                "safety_score"
            ]
        },
        "picks": {}
    }
    
    for stat_type in stat_types:
        stat_key = stat_type.lower()
        picks_by_team = {"away": [], "home": []}
        
        # Process each player
        for result in sim_results["results"]:
            player_id = result["player_id"]
            player_name = result["player_name"]
            team = result["team"]
            is_starter = result["is_starter"]
            injury_status = result["injury_status"]
            
            # Filter: must be starter and available
            if not is_starter or injury_status not in ["AVAILABLE"]:
                continue
            
            # Get stat data
            if stat_key not in result["stats"]:
                continue
            
            stat_data = result["stats"][stat_key]
            line = stat_data["line"]
            p10 = stat_data["p10"]
            p50 = stat_data["p50"]
            p90 = stat_data["p90"]
            over_prob = stat_data["over_prob"]
            under_prob = stat_data["under_prob"]
            
            # Determine side
            side, margin = determine_side(p10, p90, line, over_prob, under_prob)
            if side is None:
                continue
            
            # Calculate floor/ceiling margin
            if side == "Over":
                floor_margin = p10 - line
                win_prob = over_prob
            else:
                floor_margin = line - p90
                win_prob = under_prob
            
            # Must meet minimum criteria
            if win_prob < 55 and floor_margin < 0:
                continue
            
            # Get player data from master_data
            team_players = game["players"][team]
            player_data = None
            for p in team_players:
                if p["player_id"] == player_id:
                    player_data = p
                    break
            
            if not player_data:
                continue
            
            # Get stat-specific factors
            team_data = game["teams"][team]
            opp_abbr = home_abbr if team == away_abbr else away_abbr
            opp_data = game["teams"][opp_abbr]
            factors = get_stat_specific_factors(player_data, stat_type, team_data, opp_data)
            
            # Calculate safety score and matchup difficulty
            safety_score = calculate_safety_score(floor_margin, win_prob, blowout_risk)
            matchup_difficulty = classify_matchup_difficulty(win_prob, floor_margin, blowout_risk)
            
            # Generate why summary
            why_summary = generate_why_summary(player_name, stat_type, side, factors, floor_margin, win_prob, matchup_difficulty)
            
            pick = {
                "player": player_name,
                "stat_type": stat_type,
                "side": side,
                "line": line,
                "10th_percentile_floor": round(p10, 1),
                "50th_percentile_median": round(p50, 1),
                "90th_percentile_ceiling": round(p90, 1),
                "floor_margin": round(floor_margin, 2),
                "win_prob_pct": round(win_prob, 1),
                "safety_score": round(safety_score, 1),
                "matchup_difficulty": matchup_difficulty,
                "blowout_risk": round(blowout_risk, 2),
                "stat_factors": factors,
                "why_summary": why_summary
            }
            
            team_key = "away" if team == away_abbr else "home"
            picks_by_team[team_key].append(pick)
        
        # Sort by floor_margin descending, then win_prob descending
        for team_key in ["away", "home"]:
            picks_by_team[team_key].sort(key=lambda x: (x["floor_margin"], x["win_prob_pct"]), reverse=True)
            # Select top 2 per team
            picks_by_team[team_key] = picks_by_team[team_key][:2]
        
        output["picks"][stat_type] = {
            "away_picks": picks_by_team["away"],
            "home_picks": picks_by_team["home"]
        }
    
    # Print output
    print(json.dumps(output, indent=2))

if __name__ == "__main__":
    main()


