#!/usr/bin/env python3
"""generate_mock_data.py

Generates realistic mock data for GSW @ BKN to demonstrate the GOAT system
without requiring a BallDontLie API key.

This creates:
1. game_data.json - Mock GOAT-tier game data with advanced metrics
2. live_lines.json - Mock Vegas betting lines for starters

Usage:
  python3 generate_mock_data.py
"""

import json
from datetime import datetime

def generate_mock_game_data():
    """Generate realistic mock game data for GSW @ BKN."""
    
    # GSW starters (realistic 2024-25 stats)
    gsw_players = [
        {
            "player_id": 115,
            "name": "Stephen Curry",
            "position": "PG",
            "is_starter": True,
            "injury_status": "AVAILABLE",
            "injury_notes": None,
            "pts_league_rank": 12,
            "clutch_pts_avg": 3.8,
            "season": {
                "minutes": 32.5,
                "pts": 26.8,
                "usg_pct": 31.2,
                "ts_pct": 0.638,
                "off_rating": 118.5
            },
            "recent": {
                "sample_size": 5,
                "minutes_avg": 33.2,
                "pts": {
                    "avg": 28.4,
                    "stdev": 6.2
                },
                "usg_pct": 32.1,
                "ts_pct": 0.645,
                "off_rating": 120.2
            }
        },
        {
            "player_id": 3547254,
            "name": "Andrew Wiggins",
            "position": "SF",
            "is_starter": True,
            "injury_status": "AVAILABLE",
            "injury_notes": None,
            "pts_league_rank": 78,
            "clutch_pts_avg": 1.8,
            "season": {
                "minutes": 28.3,
                "pts": 16.2,
                "usg_pct": 19.5,
                "ts_pct": 0.558,
                "off_rating": 112.3
            },
            "recent": {
                "sample_size": 5,
                "minutes_avg": 29.1,
                "pts": {
                    "avg": 17.8,
                    "stdev": 5.4
                },
                "usg_pct": 20.2,
                "ts_pct": 0.565,
                "off_rating": 114.1
            }
        },
        {
            "player_id": 3547235,
            "name": "Draymond Green",
            "position": "PF",
            "is_starter": True,
            "injury_status": "AVAILABLE",
            "injury_notes": None,
            "pts_league_rank": 245,
            "clutch_pts_avg": 0.9,
            "season": {
                "minutes": 27.8,
                "pts": 8.5,
                "usg_pct": 14.2,
                "ts_pct": 0.512,
                "off_rating": 108.7
            },
            "recent": {
                "sample_size": 5,
                "minutes_avg": 26.5,
                "pts": {
                    "avg": 7.2,
                    "stdev": 4.1
                },
                "usg_pct": 13.8,
                "ts_pct": 0.498,
                "off_rating": 106.5
            }
        },
        {
            "player_id": 666969,
            "name": "Trayce Jackson-Davis",
            "position": "C",
            "is_starter": True,
            "injury_status": "AVAILABLE",
            "injury_notes": None,
            "pts_league_rank": 156,
            "clutch_pts_avg": 1.2,
            "season": {
                "minutes": 24.6,
                "pts": 11.8,
                "usg_pct": 16.8,
                "ts_pct": 0.682,
                "off_rating": 115.2
            },
            "recent": {
                "sample_size": 5,
                "minutes_avg": 25.8,
                "pts": {
                    "avg": 13.2,
                    "stdev": 4.8
                },
                "usg_pct": 17.5,
                "ts_pct": 0.695,
                "off_rating": 117.3
            }
        },
        {
            "player_id": 3547236,
            "name": "Dennis Schroder",
            "position": "PG",
            "is_starter": True,
            "injury_status": "AVAILABLE",
            "injury_notes": None,
            "pts_league_rank": 92,
            "clutch_pts_avg": 2.1,
            "season": {
                "minutes": 29.2,
                "pts": 15.3,
                "usg_pct": 21.4,
                "ts_pct": 0.542,
                "off_rating": 110.8
            },
            "recent": {
                "sample_size": 5,
                "minutes_avg": 30.5,
                "pts": {
                    "avg": 16.8,
                    "stdev": 5.9
                },
                "usg_pct": 22.1,
                "ts_pct": 0.551,
                "off_rating": 112.4
            }
        }
    ]
    
    # BKN starters (realistic 2024-25 stats)
    bkn_players = [
        {
            "player_id": 203,
            "name": "Cam Thomas",
            "position": "SG",
            "is_starter": True,
            "injury_status": "AVAILABLE",
            "injury_notes": None,
            "pts_league_rank": 18,
            "clutch_pts_avg": 3.5,
            "season": {
                "minutes": 33.8,
                "pts": 24.6,
                "usg_pct": 29.8,
                "ts_pct": 0.578,
                "off_rating": 113.2
            },
            "recent": {
                "sample_size": 5,
                "minutes_avg": 34.5,
                "pts": {
                    "avg": 26.2,
                    "stdev": 7.1
                },
                "usg_pct": 30.5,
                "ts_pct": 0.585,
                "off_rating": 115.1
            }
        },
        {
            "player_id": 1629011,
            "name": "Cameron Johnson",
            "position": "SF",
            "is_starter": True,
            "injury_status": "AVAILABLE",
            "injury_notes": None,
            "pts_league_rank": 95,
            "clutch_pts_avg": 1.9,
            "season": {
                "minutes": 30.5,
                "pts": 15.1,
                "usg_pct": 18.7,
                "ts_pct": 0.612,
                "off_rating": 114.8
            },
            "recent": {
                "sample_size": 5,
                "minutes_avg": 31.2,
                "pts": {
                    "avg": 16.4,
                    "stdev": 5.2
                },
                "usg_pct": 19.3,
                "ts_pct": 0.625,
                "off_rating": 116.5
            }
        },
        {
            "player_id": 1630533,
            "name": "Nic Claxton",
            "position": "C",
            "is_starter": True,
            "injury_status": "AVAILABLE",
            "injury_notes": None,
            "pts_league_rank": 142,
            "clutch_pts_avg": 1.4,
            "season": {
                "minutes": 28.9,
                "pts": 12.3,
                "usg_pct": 15.6,
                "ts_pct": 0.658,
                "off_rating": 116.2
            },
            "recent": {
                "sample_size": 5,
                "minutes_avg": 29.8,
                "pts": {
                    "avg": 13.6,
                    "stdev": 4.5
                },
                "usg_pct": 16.2,
                "ts_pct": 0.672,
                "off_rating": 118.3
            }
        },
        {
            "player_id": 1630527,
            "name": "Ziaire Williams",
            "position": "SF",
            "is_starter": True,
            "injury_status": "AVAILABLE",
            "injury_notes": None,
            "pts_league_rank": 198,
            "clutch_pts_avg": 1.1,
            "season": {
                "minutes": 26.4,
                "pts": 9.8,
                "usg_pct": 16.2,
                "ts_pct": 0.528,
                "off_rating": 109.5
            },
            "recent": {
                "sample_size": 5,
                "minutes_avg": 27.1,
                "pts": {
                    "avg": 10.6,
                    "stdev": 4.9
                },
                "usg_pct": 16.8,
                "ts_pct": 0.535,
                "off_rating": 110.8
            }
        },
        {
            "player_id": 1630534,
            "name": "Ben Simmons",
            "position": "PG",
            "is_starter": True,
            "injury_status": "AVAILABLE",
            "injury_notes": None,
            "pts_league_rank": 312,
            "clutch_pts_avg": 0.6,
            "season": {
                "minutes": 24.2,
                "pts": 6.2,
                "usg_pct": 12.8,
                "ts_pct": 0.548,
                "off_rating": 107.3
            },
            "recent": {
                "sample_size": 5,
                "minutes_avg": 23.5,
                "pts": {
                    "avg": 5.8,
                    "stdev": 3.2
                },
                "usg_pct": 12.3,
                "ts_pct": 0.542,
                "off_rating": 106.1
            }
        }
    ]
    
    game_data = {
        "meta": {
            "balldontlie_game_id": 999999,
            "game_date": "2025-12-29",
            "away_abbr": "GSW",
            "home_abbr": "BKN",
            "season": 2025,
            "data_source": "mock_goat_demo",
            "generated_at": datetime.utcnow().isoformat() + "Z"
        },
        "teams": {
            "GSW": {
                "days_rest": 1,
                "pace_last_10": 101.8,
                "pace_official": 101.5,
                "dvp": {
                    "PG": {"pts_allowed_avg": 22.5, "rank": 18, "bucket": "AVERAGE"},
                    "SG": {"pts_allowed_avg": 21.8, "rank": 15, "bucket": "AVERAGE"},
                    "SF": {"pts_allowed_avg": 19.2, "rank": 12, "bucket": "AVERAGE"},
                    "PF": {"pts_allowed_avg": 17.5, "rank": 10, "bucket": "STRONG"},
                    "C": {"pts_allowed_avg": 16.8, "rank": 8, "bucket": "STRONG"}
                },
                "advanced": {
                    "defensive_rating": 115.8,
                    "net_rating": 2.3,
                    "pace": 101.5
                }
            },
            "BKN": {
                "days_rest": 2,
                "pace_last_10": 99.2,
                "pace_official": 99.5,
                "dvp": {
                    "PG": {"pts_allowed_avg": 24.2, "rank": 25, "bucket": "WEAK"},
                    "SG": {"pts_allowed_avg": 23.5, "rank": 23, "bucket": "WEAK"},
                    "SF": {"pts_allowed_avg": 20.8, "rank": 19, "bucket": "AVERAGE"},
                    "PF": {"pts_allowed_avg": 18.5, "rank": 16, "bucket": "AVERAGE"},
                    "C": {"pts_allowed_avg": 17.2, "rank": 14, "bucket": "AVERAGE"}
                },
                "advanced": {
                    "defensive_rating": 117.2,
                    "net_rating": -3.8,
                    "pace": 99.5
                }
            }
        },
        "players": {
            "GSW": gsw_players,
            "BKN": bkn_players
        }
    }
    
    return game_data


def generate_mock_live_lines(game_data):
    """Generate realistic mock Vegas lines for starters."""
    
    lines = {}
    
    # Extract starters and generate lines based on season averages
    for team_abbr, players in game_data["players"].items():
        for player in players:
            if player.get("is_starter"):
                season_pts = player["season"]["pts"]
                # Vegas lines are typically slightly below season average
                line = round(season_pts - 0.5, 1)
                lines[player["name"]] = line
    
    return lines


def main():
    print("[INFO] Generating mock GOAT-tier game data for GSW @ BKN...")
    
    # Generate game data
    game_data = generate_mock_game_data()
    with open("game_data.json", "w") as f:
        json.dump(game_data, f, indent=2)
    print("✅ Created game_data.json")
    
    # Generate live lines
    live_lines = generate_mock_live_lines(game_data)
    with open("live_lines.json", "w") as f:
        json.dump(live_lines, f, indent=2)
    print("✅ Created live_lines.json")
    
    print("\n📊 Mock Data Summary:")
    print(f"  - Game: {game_data['meta']['away_abbr']} @ {game_data['meta']['home_abbr']}")
    print(f"  - Date: {game_data['meta']['game_date']}")
    print(f"  - GSW Starters: {len([p for p in game_data['players']['GSW'] if p['is_starter']])}")
    print(f"  - BKN Starters: {len([p for p in game_data['players']['BKN'] if p['is_starter']])}")
    print(f"  - Total Lines: {len(live_lines)}")
    
    print("\n🎯 Vegas Lines Generated:")
    for player, line in sorted(live_lines.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {player}: {line}")
    
    print("\n✅ Mock data generation complete!")
    print("\nNext step: Run simulation with:")
    print("  python3 simulation_engine.py --game-data-file game_data.json --baselines-file live_lines.json --starters-only")


if __name__ == "__main__":
    main()
