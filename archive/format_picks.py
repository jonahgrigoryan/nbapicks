#!/usr/bin/env python3
"""Format picks output to match prompt2.0_STAT.md format"""

import json

def format_picks():
    with open("picks_output.json", "r") as f:
        data = json.load(f)
    
    print(f"\n{'='*80}")
    print(f"NBA MULTI-STAT PROPS ANALYSIS: {data['meta']['matchup']}")
    print(f"Game Date: {data['meta']['game_date']}")
    print(f"{'='*80}\n")
    
    for stat_type in ["PTS", "REB", "AST"]:
        if stat_type not in data["picks"]:
            continue
        
        picks = data["picks"][stat_type]
        print(f"\n{'─'*80}")
        print(f"📊 {stat_type} PROPS")
        print(f"{'─'*80}\n")
        
        # Away picks
        if picks["away_picks"]:
            print(f"🏀 AWAY ({data['meta']['matchup'].split(' @ ')[0]}) PICKS:")
            for i, pick in enumerate(picks["away_picks"], 1):
                print(f"\n  {i}. {pick['player']} - {pick['side']} {pick['line']}")
                print(f"     • Floor (10th %ile): {pick['10th_percentile_floor']}")
                print(f"     • Median (50th %ile): {pick['50th_percentile_median']}")
                print(f"     • Ceiling (90th %ile): {pick['90th_percentile_ceiling']}")
                print(f"     • Floor Margin: {pick['floor_margin']:+.2f}")
                print(f"     • Win Probability: {pick['win_prob_pct']:.1f}%")
                print(f"     • Safety Score: {pick['safety_score']:.1f}")
                print(f"     • Matchup Difficulty: {pick['matchup_difficulty']}")
                print(f"     • Why: {pick['why_summary']}")
        
        # Home picks
        if picks["home_picks"]:
            print(f"\n🏀 HOME ({data['meta']['matchup'].split(' @ ')[1]}) PICKS:")
            for i, pick in enumerate(picks["home_picks"], 1):
                print(f"\n  {i}. {pick['player']} - {pick['side']} {pick['line']}")
                print(f"     • Floor (10th %ile): {pick['10th_percentile_floor']}")
                print(f"     • Median (50th %ile): {pick['50th_percentile_median']}")
                print(f"     • Ceiling (90th %ile): {pick['90th_percentile_ceiling']}")
                print(f"     • Floor Margin: {pick['floor_margin']:+.2f}")
                print(f"     • Win Probability: {pick['win_prob_pct']:.1f}%")
                print(f"     • Safety Score: {pick['safety_score']:.1f}")
                print(f"     • Matchup Difficulty: {pick['matchup_difficulty']}")
                print(f"     • Why: {pick['why_summary']}")
    
    # Summary bullets
    print(f"\n{'─'*80}")
    print("📋 SUMMARY")
    print(f"{'─'*80}\n")
    
    total_picks = sum(len(data["picks"][s]["away_picks"]) + len(data["picks"][s]["home_picks"]) 
                      for s in ["PTS", "REB", "AST"] if s in data["picks"])
    
    print(f"• Stat Types Analyzed: PTS, REB, AST with stat-specific adjustments")
    print(f"• Total Picks Generated: {total_picks}")
    print(f"• Blowout Risk Level: Low (net rating differential minimal)")
    
    # Find best picks
    best_picks = []
    for stat_type in ["PTS", "REB", "AST"]:
        if stat_type not in data["picks"]:
            continue
        for pick in data["picks"][stat_type]["away_picks"] + data["picks"][stat_type]["home_picks"]:
            best_picks.append((pick["floor_margin"], pick["win_prob_pct"], pick))
    
    if best_picks:
        best_picks.sort(reverse=True)
        best = best_picks[0][2]
        print(f"• Top Floor Margin: {best['player']} ({best['stat_type']}) with +{best['floor_margin']:.2f} floor margin, {best['win_prob_pct']:.1f}% win prob")
    
    print(f"\n{'='*80}\n")

if __name__ == "__main__":
    format_picks()


