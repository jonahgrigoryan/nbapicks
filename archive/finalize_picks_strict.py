
import json
import sys

def load_json(path):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

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

def select_team_picks(candidates):
    # Tier 1: Strict (Win Prob >= 60%)
    tier1 = sorted([c for c in candidates if c['win_prob_pct'] >= 60.0], key=lambda x: x['win_prob_pct'], reverse=True)
    selected = tier1[:3]
    
    if len(selected) < 3:
        # Tier 2: Standard (Win Prob >= 55%)
        remaining = [c for c in candidates if c not in selected]
        tier2 = sorted([c for c in remaining if c['win_prob_pct'] >= 55.0], key=lambda x: x['win_prob_pct'], reverse=True)
        selected.extend(tier2[:3 - len(selected)])
        
    if len(selected) < 3:
        # Tier 3: Relaxed (Win Prob >= 50%)
        remaining = [c for c in candidates if c not in selected]
        tier3 = sorted([c for c in remaining if c['win_prob_pct'] >= 50.0], key=lambda x: x['win_prob_pct'], reverse=True)
        # Mark with warning
        for c in tier3:
            c['selection_flag'] = "⚠️ RELAXED THRESHOLD"
            c['selection_tier'] = 3
        selected.extend(tier3[:3 - len(selected)])
        
    if len(selected) < 3:
        # Tier 4: Fallback (Sort by Edge Pts)
        remaining = [c for c in candidates if c not in selected]
        tier4 = sorted(remaining, key=lambda x: x['edge_pts'], reverse=True)
        # Mark with warning
        for c in tier4:
            c['selection_flag'] = "⚡ FALLBACK PICK"
            c['selection_tier'] = 4
        selected.extend(tier4[:3 - len(selected)])
        
    # Assign tiers to Tier 1/2 if not assigned
    for c in selected:
        if 'selection_tier' not in c:
            if c['win_prob_pct'] >= 60:
                c['selection_tier'] = 1
            else:
                c['selection_tier'] = 2
        if 'selection_flag' not in c:
             c['selection_flag'] = None

    return selected

def main():
    sim_data = load_json('simulation_results.json')
    game_data = load_json('game_data.json')
    
    results = sim_data.get('results', [])
    meta = game_data.get('meta', {})
    
    away_abbr = meta.get('away_abbr', 'AWAY')
    home_abbr = meta.get('home_abbr', 'HOME')
    
    # Filter for starters
    candidates = [r for r in results if r.get('is_starter')]
    
    away_cands = [r for r in candidates if r['team'] == away_abbr]
    home_cands = [r for r in candidates if r['team'] == home_abbr]
    
    final_away = select_team_picks(away_cands)
    final_home = select_team_picks(home_cands)
    
    def transform(r):
        return {
            "player": r['player'],
            "line": r['baseline_line'],
            "adjusted_mean": r['adjusted_mean'],
            "win_prob_pct": r['win_prob_pct'],
            "edge_pts": r['edge_pts'],
            "confidence_0_100": int(r['win_prob_pct']),
            "selection_tier": r.get('selection_tier', 1),
            "selection_flag": r.get('selection_flag'),
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
        "home_picks": [transform(r) for r in final_home],
        "selection_summary": {
             "away_tier_breakdown": {
                 "tier1": len([p for p in final_away if p.get('selection_tier')==1]),
                 "tier2": len([p for p in final_away if p.get('selection_tier')==2]),
                 "tier3": len([p for p in final_away if p.get('selection_tier')==3]),
                 "tier4": len([p for p in final_away if p.get('selection_tier')==4]),
             },
             "home_tier_breakdown": {
                 "tier1": len([p for p in final_home if p.get('selection_tier')==1]),
                 "tier2": len([p for p in final_home if p.get('selection_tier')==2]),
                 "tier3": len([p for p in final_home if p.get('selection_tier')==3]),
                 "tier4": len([p for p in final_home if p.get('selection_tier')==4]),
             }
        }
    }
    
    print(json.dumps(output, indent=2))
    
    # Summary Bullets
    print("\n--- SUMMARY BULLETS ---")
    print(f"- **🎯 Live Line Sync**: {len(results)} players simulated")
    print(f"- **✅ Selection**: {len(final_away)} away + {len(final_home)} home = {len(final_away)+len(final_home)} total picks")
    print(f"- **🏀 Starters Detected**: {len(away_cands)} away, {len(home_cands)} home")
    
    all_picks = output['away_picks'] + output['home_picks']
    if all_picks:
        top_pick = max(all_picks, key=lambda x: x['edge_pts'])
        print(f"- **🔥 Top Edge Found**: {top_pick['player']} with +{top_pick['edge_pts']:.1f} pts edge, {top_pick['win_prob_pct']}% win prob")
    
    relaxed_count = len([p for p in all_picks if p.get('selection_tier') >= 3])
    if relaxed_count > 0:
        names = [p['player'] for p in all_picks if p.get('selection_tier') >= 3]
        print(f"- **⚠️ Relaxed Picks**: {', '.join(names)}")
    else:
        print("- **⚠️ Relaxed Picks**: None")

if __name__ == "__main__":
    main()

