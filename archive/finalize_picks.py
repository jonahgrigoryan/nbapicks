
import json
import sys
from datetime import datetime

def load_json(path):
    with open(path, 'r') as f:
        return json.load(f)

def generate_why_summary(r):
    parts = []
    
    # Usage
    usg = r['goat_factors'].get('usage_pct')
    if usg:
        parts.append(f"Usage [{usg:.1f}%]")
    
    # TS%
    ts = r['goat_factors'].get('ts_pct')
    if ts:
        if ts >= 0.62:
            parts.append(f"Elite TS [{ts*100:.1f}%]")
        else:
            parts.append(f"TS [{ts*100:.1f}%]")
            
    # Opp DRtg
    drtg = r['goat_factors'].get('opp_drtg')
    if drtg:
        desc = "DEF"
        if drtg >= 114:
            desc = "weak DEF"
        elif drtg <= 112:
            desc = "strong DEF"
        parts.append(f"vs {desc} [{drtg:.1f} DRtg]")
        
    # Rank
    rank = r['goat_factors'].get('league_rank')
    if rank:
        if rank <= 10:
            parts.append(f"Top 10 scorer (#{rank})")
        elif rank <= 50:
             parts.append(f"Top 50 scorer (#{rank})")
        else:
             parts.append(f"Rank #{rank}")
             
    # Clutch
    clutch = r['goat_factors'].get('clutch_ppg')
    if clutch and clutch >= 2.5:
        parts.append(f"Clutch [{clutch:.1f} PPG]")
        
    # Edge
    edge = r.get('edge_pts')
    if edge is not None:
        parts.append(f"Simulation edge: {edge:+.1f} pts.")
        
    return " + ".join(parts[:-1]) + ". " + parts[-1] if parts else ""

def main():
    sim_data = load_json('simulation_results.json')
    try:
        game_data = load_json('game_data.json')
    except:
        game_data = {}

    results = sim_data['results']
    
    # Filter and Sort
    # Rules: Starter only, Win Prob desc, Edge desc
    candidates = [r for r in results if r['is_starter']]
    
    # Split by team
    # Need to know which team is which.
    # game_data['meta'] has away_abbr and home_abbr
    meta = game_data.get('meta', {})
    away_abbr = meta.get('away_abbr', 'AWAY')
    home_abbr = meta.get('home_abbr', 'HOME')
    
    away_cands = [r for r in candidates if r['team'] == away_abbr]
    home_cands = [r for r in candidates if r['team'] == home_abbr]
    
    # Sort
    def sort_key(r):
        return (r['win_prob_pct'], r['edge_pts'])
        
    away_cands.sort(key=sort_key, reverse=True)
    home_cands.sort(key=sort_key, reverse=True)
    
    # Select Top 3 with min 55% win prob
    final_away = [r for r in away_cands if r['win_prob_pct'] >= 55.0][:3]
    final_home = [r for r in home_cands if r['win_prob_pct'] >= 55.0][:3]
    
    # Transform to output format
    def transform(r):
        return {
            "player": r['player'],
            "line": r['baseline_line'],
            "adjusted_mean": r['adjusted_mean'],
            "win_prob_pct": r['win_prob_pct'],
            "edge_pts": r['edge_pts'],
            "confidence_0_100": int(r['win_prob_pct']),
            "goat_factors": r['goat_factors'],
            "why_summary": generate_why_summary(r)
        }
    
    output = {
        "meta": {
            "matchup": f"{away_abbr} @ {home_abbr}",
            "game_date": meta.get('game_date', "auto-detected"),
            "data_source": "balldontlie_goat",
            "features_used": [
                "official_usage_pct", "official_ts_pct", "team_drtg",
                "starter_detection", "league_rank", "clutch_scoring"
            ]
        },
        "away_picks": [transform(r) for r in final_away],
        "home_picks": [transform(r) for r in final_home]
    }
    
    # Print JSON
    print(json.dumps(output, indent=2))
    
    # Print Summary Bullets
    print("\n--- SUMMARY BULLETS ---")
    print(f"- **🎯 Live Line Sync**: {len(final_away) + len(final_home)} picks generated from {len(results)} simulated players")
    print("- **📊 GOAT Metrics Applied**: Usage%, TS%, Team DRtg from official endpoints")
    
    away_starters = len([r for r in away_cands]) # Total starters considered
    home_starters = len([r for r in home_cands])
    print(f"- **🏀 Starters Auto-Detected**: {away_starters} away, {home_starters} home via `is_starter`")
    
    # Find top edge across all picks
    all_picks = output['away_picks'] + output['home_picks']
    if all_picks:
        top_pick = max(all_picks, key=lambda x: x['edge_pts'])
        print(f"- **🔥 Top Edge Found**: {top_pick['player']} with +{top_pick['edge_pts']} pts edge, {top_pick['win_prob_pct']}% win prob")
    else:
        print("- **🔥 Top Edge Found**: None met the 55% criteria.")

    # Risk Factors
    risks = []
    if not all_picks:
        risks.append("No players met the 55% win probability threshold.")
    
    # Check for low win prob in selected picks? No, we filtered them.
    # Check for B2B?
    # Inspect one random pick's goat_factors
    if all_picks:
        rest = all_picks[0]['goat_factors'].get('days_rest')
        if rest == 0:
            risks.append("Teams on B2B (Back-to-Back).")
            
    print(f"- **⚠️ Key Risk Factors**: {', '.join(risks) if risks else 'None detected'}")

if __name__ == "__main__":
    main()
