#!/usr/bin/env python3
"""Generate picks from simulation results following prompt2.0.md workflow."""

import json
import sys
from typing import Dict, List, Any, Optional


def load_data(game_data_file: str, simulation_file: str, live_lines_file: str) -> tuple:
    """Load game data, simulation results, and live lines."""
    with open(game_data_file, 'r') as f:
        game_data = json.load(f)
    with open(simulation_file, 'r') as f:
        simulation = json.load(f)
    with open(live_lines_file, 'r') as f:
        live_lines = json.load(f)
    return game_data, simulation, live_lines


def get_player_data(game_data: Dict, team: str, player_name: str) -> Optional[Dict]:
    """Get player data from game_data."""
    players = game_data.get('players', {}).get(team, [])
    for p in players:
        if p.get('name') == player_name:
            return p
    return None


def compute_goat_score(player_data: Dict, opp_team_data: Dict) -> float:
    """Compute GOAT Adjustment Score per prompt2.0.md Step 2."""
    score = 0.0
    
    # A. Usage Rate Adjustment
    usg_pct = player_data.get('recent', {}).get('usg_pct')
    if usg_pct is not None:
        if usg_pct >= 28.0:
            score += 1.2
        elif usg_pct < 20.0:
            score -= 0.8
    
    # B. Efficiency Boost (True Shooting %)
    ts_pct = player_data.get('recent', {}).get('ts_pct')
    if ts_pct is not None:
        if ts_pct >= 0.62:
            score += 1.0
        elif ts_pct >= 0.58:
            score += 0.5
        elif ts_pct < 0.52:
            score -= 0.5
    
    # C. Team Defense Matchup
    opp_drtg = opp_team_data.get('advanced', {}).get('defensive_rating')
    if opp_drtg is not None:
        if opp_drtg >= 118:
            score += 1.7
        elif opp_drtg >= 114:
            score += 0.8
        elif opp_drtg <= 108:
            score -= 2.0
        elif opp_drtg <= 112:
            score -= 1.0
    
    # D. Pace Environment (added separately)
    pace_adj = player_data.get('pace_adjustment', 0.0)
    score += pace_adj
    
    # E. Days of Rest
    days_rest = player_data.get('days_rest', 1)
    is_away = player_data.get('is_away', False)
    if days_rest == 0:  # B2B
        score += -1.8 if is_away else -1.0
    elif days_rest == 2:
        score += 0.2
    elif days_rest >= 3:
        score -= 0.3
    
    # F. Recent Form
    recent_pts = player_data.get('recent', {}).get('pts', {}).get('avg', 0)
    season_pts = player_data.get('season', {}).get('pts', 0)
    if season_pts > 0:
        form_delta = ((recent_pts - season_pts) / season_pts) * 17.0
        score += max(-5.5, min(5.5, form_delta))
    
    # G. Clutch Performance
    clutch_ppg = player_data.get('clutch_pts_avg')
    if clutch_ppg is not None:
        if clutch_ppg >= 4.0:
            score += 1.0
        elif clutch_ppg >= 2.5:
            score += 0.5
        elif clutch_ppg < 1.0:
            score -= 0.5
    
    # H. League Standing
    league_rank = player_data.get('pts_league_rank')
    if league_rank is not None:
        if league_rank <= 10:
            score += 1.4
        elif league_rank <= 25:
            score += 0.5
        elif league_rank >= 100:
            score -= 0.5
    
    # I. Minutes Stability
    proj_minutes = player_data.get('recent', {}).get('minutes_avg', 0) or player_data.get('season', {}).get('minutes', 0)
    if proj_minutes >= 34:
        score += 1.2
    elif proj_minutes >= 30:
        score += 0.6
    elif proj_minutes < 26:
        score -= 3.3
    
    # J. Scoring Consistency
    stdev = player_data.get('recent', {}).get('pts', {}).get('stdev', 0)
    if stdev <= 5.0:
        score += 1.3
    elif stdev > 7.0:
        score -= 1.7
    
    return round(score, 2)


def compute_pace_adjustment(game_data: Dict, team: str, opp_team: str) -> float:
    """Compute pace adjustment for GOAT score."""
    team_data = game_data.get('teams', {}).get(team, {})
    opp_data = game_data.get('teams', {}).get(opp_team, {})
    
    team_pace = team_data.get('pace_official') or team_data.get('pace_last_10')
    opp_pace = opp_data.get('pace_official') or opp_data.get('pace_last_10')
    
    if team_pace is None or opp_pace is None:
        return 0.0
    
    projected_pace = (team_pace + opp_pace) / 2.0
    
    if projected_pace >= 104:
        return 2.7
    elif projected_pace <= 98:
        return -2.7
    return 0.0


def select_team_picks(
    sim_results: List[Dict],
    game_data: Dict,
    team: str,
    opp_team: str,
    live_lines: Dict
) -> List[Dict]:
    """Select picks for a team using cascading threshold system."""
    # Filter to starters with lines and not injured
    candidates = []
    team_data = game_data.get('teams', {}).get(team, {})
    opp_data = game_data.get('teams', {}).get(opp_team, {})
    
    pace_adj = compute_pace_adjustment(game_data, team, opp_team)
    
    for result in sim_results:
        if result.get('team') != team:
            continue
        if not result.get('is_starter', False):
            continue
        if result.get('baseline_line') is None:
            continue
        
        player_name = result.get('player')
        player_data = get_player_data(game_data, team, player_name)
        if not player_data:
            continue
        
        # Check injury status
        injury_status = player_data.get('injury_status', '').upper()
        if injury_status in {'OUT', 'DOUBTFUL'}:
            continue
        
        # Add team context to player_data for GOAT score
        player_data['days_rest'] = team_data.get('days_rest', 1)
        player_data['is_away'] = (team == game_data.get('meta', {}).get('away_abbr'))
        player_data['pace_adjustment'] = pace_adj
        
        # Compute GOAT score
        goat_score = compute_goat_score(player_data, opp_data) + pace_adj
        
        candidates.append({
            'player': player_name,
            'line': result.get('baseline_line'),
            'adjusted_mean': result.get('adjusted_mean'),
            'win_prob_pct': result.get('win_prob_pct'),
            'edge_pts': result.get('edge_pts'),
            'goat_score': goat_score,
            'player_data': player_data,
            'result': result
        })
    
    # Sort by win_prob descending
    candidates.sort(key=lambda x: x['win_prob_pct'], reverse=True)
    
    selected = []
    
    # Tier 1: win_prob >= 60%
    tier1 = [c for c in candidates if c['win_prob_pct'] >= 60]
    selected.extend(tier1[:3])
    
    if len(selected) < 3:
        # Tier 2: win_prob >= 55%
        remaining = [c for c in candidates if c not in selected]
        tier2 = [c for c in remaining if c['win_prob_pct'] >= 55]
        selected.extend(tier2[:3 - len(selected)])
    
    if len(selected) < 3:
        # Tier 3: win_prob >= 50%
        remaining = [c for c in candidates if c not in selected]
        tier3 = [c for c in remaining if c['win_prob_pct'] >= 50]
        selected.extend(tier3[:3 - len(selected)])
    
    if len(selected) < 3:
        # Tier 4: edge_pts >= 0.5 or top by win_prob (fallback)
        remaining = [c for c in candidates if c not in selected]
        # Sort remaining by win_prob descending (they're already sorted, but ensure)
        remaining.sort(key=lambda x: x['win_prob_pct'], reverse=True)
        tier4_edge = [c for c in remaining if c['edge_pts'] >= 0.5]
        if len(selected) + len(tier4_edge) >= 3:
            # Use edge picks first, then fill with highest win_prob
            selected.extend(tier4_edge[:3 - len(selected)])
            if len(selected) < 3:
                remaining_no_edge = [c for c in remaining if c not in tier4_edge]
                selected.extend(remaining_no_edge[:3 - len(selected)])
        else:
            # Not enough edge picks, use top by win_prob
            selected.extend(remaining[:3 - len(selected)])
    
    return selected[:3]  # Ensure exactly 3


def format_pick(candidate: Dict, team: str, opp_team: str, game_data: Dict) -> Dict:
    """Format a pick according to prompt2.0.md output format."""
    player_data = candidate['player_data']
    result = candidate['result']
    goat_factors = result.get('goat_factors', {})
    
    # Determine selection tier
    win_prob = candidate['win_prob_pct']
    if win_prob >= 60:
        tier = 1
        flag = None
    elif win_prob >= 55:
        tier = 2
        flag = None
    elif win_prob >= 50:
        tier = 3
        flag = "⚠️ RELAXED THRESHOLD"
    else:
        tier = 4
        flag = "⚡ FALLBACK PICK"
    
    # Build why_summary
    why_parts = []
    usg_pct = goat_factors.get('usage_pct')
    if usg_pct:
        why_parts.append(f"Usage [{usg_pct:.1f}%]")
    
    ts_pct = goat_factors.get('ts_pct')
    if ts_pct:
        if ts_pct >= 0.62:
            why_parts.append(f"Elite TS [{ts_pct:.1%}]")
        else:
            why_parts.append(f"TS [{ts_pct:.1%}]")
    
    opp_drtg = goat_factors.get('opp_drtg')
    if opp_drtg:
        if opp_drtg >= 118:
            why_parts.append(f"vs weak DEF [{opp_drtg:.1f} DRtg]")
        elif opp_drtg <= 108:
            why_parts.append(f"vs strong DEF [{opp_drtg:.1f} DRtg]")
        else:
            why_parts.append(f"vs DEF [{opp_drtg:.1f} DRtg]")
    
    league_rank = goat_factors.get('league_rank')
    if league_rank and league_rank <= 10:
        why_parts.append(f"Top 10 scorer (#{league_rank})")
    elif league_rank and league_rank <= 25:
        why_parts.append(f"Top 25 scorer (#{league_rank})")
    
    clutch_ppg = goat_factors.get('clutch_ppg')
    if clutch_ppg and clutch_ppg >= 2.5:
        why_parts.append(f"Clutch performer [{clutch_ppg:.1f} PPG]")
    
    edge_pts = candidate['edge_pts']
    why_parts.append(f"Simulation edge: {edge_pts:+.1f} pts")
    
    why_summary = ". ".join(why_parts) + "."
    
    return {
        "player": candidate['player'],
        "line": candidate['line'],
        "adjusted_mean": round(candidate['adjusted_mean'], 2),
        "win_prob_pct": round(candidate['win_prob_pct'], 1),
        "edge_pts": round(candidate['edge_pts'], 2),
        "confidence_0_100": min(95, max(50, int(round(candidate['win_prob_pct'] * 0.8 + 40)))),  # Map win_prob to confidence range
        "selection_tier": tier,
        "selection_flag": flag,
        "goat_factors": {
            "usage_pct": goat_factors.get('usage_pct'),
            "ts_pct": goat_factors.get('ts_pct'),
            "opp_drtg": goat_factors.get('opp_drtg'),
            "clutch_ppg": goat_factors.get('clutch_ppg'),
            "league_rank": goat_factors.get('league_rank')
        },
        "why_summary": why_summary
    }


def main():
    if len(sys.argv) != 4:
        print("Usage: python3 generate_picks_from_simulation.py <game_data.json> <simulation_results.json> <live_lines.json>")
        sys.exit(1)
    
    game_data_file = sys.argv[1]
    simulation_file = sys.argv[2]
    live_lines_file = sys.argv[3]
    
    game_data, simulation, live_lines = load_data(game_data_file, simulation_file, live_lines_file)
    
    meta = game_data.get('meta', {})
    away_abbr = meta.get('away_abbr')
    home_abbr = meta.get('home_abbr')
    game_date = meta.get('game_date')
    
    sim_results = simulation.get('results', [])
    
    # Select picks for each team
    away_picks_raw = select_team_picks(sim_results, game_data, away_abbr, home_abbr, live_lines)
    home_picks_raw = select_team_picks(sim_results, game_data, home_abbr, away_abbr, live_lines)
    
    # Format picks
    away_picks = [format_pick(p, away_abbr, home_abbr, game_data) for p in away_picks_raw]
    home_picks = [format_pick(p, home_abbr, away_abbr, game_data) for p in home_picks_raw]
    
    # Count tiers
    away_tiers = {1: 0, 2: 0, 3: 0, 4: 0}
    home_tiers = {1: 0, 2: 0, 3: 0, 4: 0}
    
    for p in away_picks:
        away_tiers[p['selection_tier']] += 1
    for p in home_picks:
        home_tiers[p['selection_tier']] += 1
    
    relaxed_count = sum(1 for p in away_picks + home_picks if p['selection_tier'] == 3)
    fallback_count = sum(1 for p in away_picks + home_picks if p['selection_tier'] == 4)
    
    output = {
        "meta": {
            "matchup": f"{away_abbr} @ {home_abbr}",
            "game_date": game_date,
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
        "away_picks": away_picks,
        "home_picks": home_picks,
        "selection_summary": {
            "away_tier_breakdown": away_tiers,
            "home_tier_breakdown": home_tiers,
            "relaxed_picks_count": relaxed_count,
            "fallback_picks_count": fallback_count
        }
    }
    
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()

