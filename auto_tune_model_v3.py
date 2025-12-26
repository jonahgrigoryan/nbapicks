#!/usr/bin/env python3
"""auto_tune_model_v2.py

GOAT All-Star Enhanced: Analyzes historical NBA games to tune prediction model weights.

This script:
1. Fetches actual points scored for all starters on a given date
2. Fetches the corresponding GAME_DATA context (Usage, DvP, Rest, Pace)
3. GOAT: Fetches official advanced stats (Usage%, TS%, ORtg) from /nba/v1/stats
4. GOAT: Fetches team standings (DRtg, NRtg, Pace) from /nba/v1/standings
5. GOAT: Fetches league scoring ranks from /nba/v1/leaders
6. GOAT: Fetches clutch scoring from /nba/v1/play_by_play
7. Compares Actual vs. Projected (using Season Averages as baseline)
8. Identifies which adjustment factor had the highest correlation with error
9. Outputs suggested weight changes for prompt2.0.md (GOAT-enhanced)
10. Appends summary to model_tuning.log

GOAT Factors Analyzed:
- ts_pct: True Shooting % (efficiency)
- off_rating: Offensive Rating (player impact)
- opp_drtg: Opponent Defensive Rating (matchup quality)
- clutch: Clutch scoring (last 5 min, margin <=5)
- league_rank: League PPG ranking

Usage:
  python3 auto_tune_model_v2.py --date 2025-01-15 --season 2025

Requirements:
  - env var `BALLDONTLIE_API_KEY` must be set.
  - GOAT All-Star tier subscription for advanced endpoints.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from dataclasses import dataclass, asdict

# Load .env file if it exists
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if "=" in line:
                key, value = line.strip().split("=", 1)
                os.environ[key] = value.strip("'").strip('"')
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# Reuse helpers from fetch_points_game_data
from fetch_points_game_data import (
    bdl_get,
    get_teams_map,
    compute_days_of_rest,
    compute_dvp_by_position,
    get_team_pace_last_10,
    get_team_standings,
    get_player_advanced_season_stats,
    get_pts_league_ranks_batch,
    get_clutch_scoring_avg,
    get_recent_game_ids,
    DvPRanking,
)


@dataclass
class PlayerGameRecord:
    """Record of a player's actual performance and pre-game context."""
    player_id: int
    name: str
    team_abbr: str
    opponent_abbr: str
    position: str
    is_home: bool
    
    # Actual performance
    actual_pts: float
    actual_minutes: float
    
    # Pre-game context (what we knew before the game)
    season_pts_avg: float
    season_minutes_avg: float
    l5_pts_avg: float
    l5_pts_stdev: float
    l5_minutes_avg: float
    
    # Adjustment factors
    usg_pct: Optional[float]
    days_rest: int
    dvp_bucket: str  # WEAK, AVERAGE, STRONG
    dvp_rank: int
    pace_env: float  # projected game pace
    
    # GOAT Advanced Metrics
    ts_pct: Optional[float]  # True Shooting %
    off_rating: Optional[float]  # Offensive Rating
    team_drtg: Optional[float]  # Team Defensive Rating (opponent's)
    team_nrtg: Optional[float]  # Team Net Rating (opponent's)
    opp_pace: Optional[float]  # Opponent's official pace
    clutch_pts_avg: Optional[float]  # Clutch scoring average
    pts_league_rank: Optional[int]  # League scoring rank
    
    # Computed values
    baseline_proj: float  # season average projection
    prediction_error: float  # actual - baseline_proj


def get_games_on_date(game_date: date) -> List[Dict[str, Any]]:
    """Fetch all completed NBA games on a specific date."""
    params = {
        "dates[]": game_date.isoformat(),
        "per_page": 100,
    }
    data = bdl_get("/v1/games", params)
    games = list(data.get("data", []))
    
    # Filter to only completed games (status="Final")
    completed_games = [
        g for g in games
        if str(g.get("status") or "").lower() == "final"
    ]
    
    return completed_games


def get_game_box_scores(game_id: int) -> List[Dict[str, Any]]:
    """Fetch box score stats for all players in a game."""
    params = {
        "game_ids[]": [game_id],
        "per_page": 100,
    }
    
    all_stats: List[Dict[str, Any]] = []
    cursor: Optional[int] = None
    
    for _ in range(10):  # Pagination safety limit
        page_params = dict(params)
        if cursor is not None:
            page_params["cursor"] = cursor
        
        data = bdl_get("/v1/stats", page_params)
        all_stats.extend(data.get("data", []))
        
        next_cursor = (data.get("meta") or {}).get("next_cursor")
        if not next_cursor:
            break
        cursor = int(next_cursor)
    
    return all_stats


def parse_minutes(min_val: Any) -> float:
    """Parse minutes from various formats (mm:ss string or float)."""
    if isinstance(min_val, str) and ":" in min_val:
        mm, ss = min_val.split(":")
        return float(int(mm) + int(ss) / 60.0)
    try:
        return float(min_val or 0.0)
    except Exception:
        return 0.0


def get_player_season_stats_before_date(
    player_ids: List[int],
    before_date: date,
    season: int,
) -> Dict[int, Dict[str, float]]:
    """
    Get season averages for players using only games BEFORE the specified date.
    This ensures we're measuring what we would have known pre-game.
    """
    if not player_ids:
        return {}
    
    # Calculate season start date (roughly October 1 of the previous year for typical NBA season)
    season_start = date(season - 1, 10, 1)
    
    # Fetch stats from season start to day before target date
    end_date = before_date - timedelta(days=1)
    
    results: Dict[int, Dict[str, float]] = {}
    
    for pid in player_ids:
        params = {
            "player_ids[]": [pid],
            "start_date": season_start.isoformat(),
            "end_date": end_date.isoformat(),
            "per_page": 100,
        }
        
        try:
            all_rows: List[Dict[str, Any]] = []
            cursor: Optional[int] = None
            
            for _ in range(20):
                page_params = dict(params)
                if cursor is not None:
                    page_params["cursor"] = cursor
                
                data = bdl_get("/v1/stats", page_params)
                all_rows.extend(data.get("data", []))
                
                next_cursor = (data.get("meta") or {}).get("next_cursor")
                if not next_cursor:
                    break
                cursor = int(next_cursor)
            
            # Calculate averages
            pts_sum = 0.0
            min_sum = 0.0
            games = 0
            
            for row in all_rows:
                mins = parse_minutes(row.get("min"))
                if mins <= 0:
                    continue
                games += 1
                pts_sum += float(row.get("pts", 0.0) or 0.0)
                min_sum += mins
            
            if games > 0:
                results[pid] = {
                    "pts_avg": pts_sum / games,
                    "min_avg": min_sum / games,
                    "games": games,
                }
            else:
                results[pid] = {"pts_avg": 0.0, "min_avg": 0.0, "games": 0}
                
        except Exception as e:
            print(f"[WARN] Failed to get season stats for player {pid}: {e}", file=sys.stderr)
            results[pid] = {"pts_avg": 0.0, "min_avg": 0.0, "games": 0}
    
    return results


def get_player_l5_stats_before_date(
    player_ids: List[int],
    before_date: date,
) -> Dict[int, Dict[str, Any]]:
    """
    Get L5 (last 5 games) stats for players using only games BEFORE the specified date.
    """
    if not player_ids:
        return {}
    
    results: Dict[int, Dict[str, Any]] = {}
    start_date = before_date - timedelta(days=30)  # Look back 30 days to find 5 games
    end_date = before_date - timedelta(days=1)
    
    for pid in player_ids:
        params = {
            "player_ids[]": [pid],
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "per_page": 50,
        }
        
        try:
            data = bdl_get("/v1/stats", params)
            rows = list(data.get("data", []))
            
            # Sort by date descending
            def _date_key(row: Dict[str, Any]) -> datetime:
                game = row.get("game") or {}
                dt = game.get("date")
                if not isinstance(dt, str) or not dt:
                    return datetime.min.replace(tzinfo=timezone.utc)
                try:
                    return datetime.fromisoformat(dt.replace("Z", "+00:00"))
                except Exception:
                    return datetime.min.replace(tzinfo=timezone.utc)
            
            rows.sort(key=_date_key, reverse=True)
            l5 = rows[:5]
            
            if not l5:
                results[pid] = {
                    "pts_avg": 0.0,
                    "pts_stdev": 0.0,
                    "min_avg": 0.0,
                    "usg_pct": None,
                    "sample_size": 0,
                }
                continue
            
            # Calculate L5 stats
            pts_vals = [float(r.get("pts", 0.0) or 0.0) for r in l5]
            min_vals = [parse_minutes(r.get("min")) for r in l5]
            
            pts_avg = sum(pts_vals) / len(pts_vals) if pts_vals else 0.0
            pts_stdev = statistics.pstdev(pts_vals) if len(pts_vals) > 1 else 0.0
            min_avg = sum(min_vals) / len(min_vals) if min_vals else 0.0
            
            # Calculate usage rate
            usg_vals: List[float] = []
            for r in l5:
                fga = float(r.get("fga", 0.0) or 0.0)
                fta = float(r.get("fta", 0.0) or 0.0)
                tov = float(r.get("turnover", 0.0) or 0.0)
                player_min = parse_minutes(r.get("min"))
                
                if player_min > 0:
                    poss_used = fga + 0.44 * fta + tov
                    estimated_usg = (poss_used / player_min) * 48.0 / 80.0 * 100.0
                    usg_vals.append(min(estimated_usg, 45.0))
            
            usg_pct = round(sum(usg_vals) / len(usg_vals), 1) if usg_vals else None
            
            results[pid] = {
                "pts_avg": pts_avg,
                "pts_stdev": pts_stdev,
                "min_avg": min_avg,
                "usg_pct": usg_pct,
                "sample_size": len(l5),
            }
            
        except Exception as e:
            print(f"[WARN] Failed to get L5 stats for player {pid}: {e}", file=sys.stderr)
            results[pid] = {
                "pts_avg": 0.0,
                "pts_stdev": 0.0,
                "min_avg": 0.0,
                "usg_pct": None,
                "sample_size": 0,
            }
    
    return results


def compute_projected_pace(
    home_team_id: int,
    away_team_id: int,
    game_date: date,
) -> Optional[float]:
    """Compute projected game pace from both teams' L10 pace."""
    home_pace = get_team_pace_last_10(home_team_id, game_date)
    away_pace = get_team_pace_last_10(away_team_id, game_date)
    
    if home_pace is None and away_pace is None:
        return None
    if home_pace is None:
        return away_pace
    if away_pace is None:
        return home_pace
    return (home_pace + away_pace) / 2.0


def get_dvp_bucket_for_position(
    dvp_data: Dict[str, DvPRanking],
    position: str,
) -> Tuple[str, int]:
    """Get DvP bucket and rank for a player's position."""
    pos_upper = position.upper()
    
    if pos_upper in dvp_data:
        return dvp_data[pos_upper].bucket, dvp_data[pos_upper].rank
    
    # Handle combo positions
    if "-" in pos_upper:
        parts = pos_upper.split("-")
        for part in parts:
            if part in dvp_data:
                return dvp_data[part].bucket, dvp_data[part].rank
    
    # Fallback mapping
    position_map = {"G": ["PG", "SG"], "F": ["SF", "PF"]}
    for key, candidates in position_map.items():
        if key in pos_upper:
            for cand in candidates:
                if cand in dvp_data:
                    return dvp_data[cand].bucket, dvp_data[cand].rank
    
    return "AVERAGE", 15


def process_game(
    game: Dict[str, Any],
    season: int,
    teams_map: Dict[str, Dict[str, Any]],
) -> List[PlayerGameRecord]:
    """Process a single game and return player records with actual vs projected.
    
    GOAT Enhanced: Fetches advanced stats (Usage%, TS%, ORtg), team standings
    (DRtg, NRtg, Pace), clutch scoring, and league ranks for backtesting.
    """
    game_id = game.get("id")
    if not game_id:
        return []
    
    home_team = game.get("home_team") or {}
    visitor_team = game.get("visitor_team") or {}
    
    home_abbr = str(home_team.get("abbreviation") or "").upper()
    away_abbr = str(visitor_team.get("abbreviation") or "").upper()
    home_team_id = home_team.get("id")
    away_team_id = visitor_team.get("id")
    
    if not all([home_abbr, away_abbr, home_team_id, away_team_id]):
        return []
    
    # Parse game date
    game_date_str = game.get("date")
    try:
        game_dt = datetime.fromisoformat(str(game_date_str).replace("Z", "+00:00"))
        game_date = game_dt.date()
    except Exception:
        return []
    
    print(f"  Processing {away_abbr} @ {home_abbr} on {game_date}...", file=sys.stderr)
    
    # Get box scores
    box_scores = get_game_box_scores(game_id)
    if not box_scores:
        print(f"    [WARN] No box scores found for game {game_id}", file=sys.stderr)
        return []
    
    # Filter to starters (players with >= 20 minutes)
    starters = [
        bs for bs in box_scores
        if parse_minutes(bs.get("min")) >= 20
    ]
    
    if not starters:
        print(f"    [WARN] No starters found (>=20 min) for game {game_id}", file=sys.stderr)
        return []
    
    # Collect player IDs
    player_ids = []
    for bs in starters:
        player = bs.get("player") or {}
        pid = player.get("id")
        if pid:
            player_ids.append(int(pid))
    
    # Get pre-game context
    season_stats = get_player_season_stats_before_date(player_ids, game_date, season)
    l5_stats = get_player_l5_stats_before_date(player_ids, game_date)
    
    # GOAT: Fetch official advanced season stats (Usage%, TS%, ORtg)
    advanced_stats = get_player_advanced_season_stats(player_ids, season)
    
    # GOAT: Fetch league scoring ranks
    league_ranks = get_pts_league_ranks_batch(player_ids, season)
    
    # GOAT: Fetch team standings for DRtg, NRtg, Pace
    standings = get_team_standings(season)
    home_standings = standings.get(home_team_id, {})
    away_standings = standings.get(away_team_id, {})
    
    # Get team context
    home_days_rest = compute_days_of_rest(home_team_id, game_date)
    away_days_rest = compute_days_of_rest(away_team_id, game_date)
    
    # Compute DvP for both teams (how they defend each position)
    home_dvp = compute_dvp_by_position(home_team_id, game_date)
    away_dvp = compute_dvp_by_position(away_team_id, game_date)
    
    # Compute projected game pace
    projected_pace = compute_projected_pace(home_team_id, away_team_id, game_date)
    
    records: List[PlayerGameRecord] = []
    
    for bs in starters:
        player = bs.get("player") or {}
        pid = player.get("id")
        if not pid:
            continue
        pid = int(pid)
        
        team = bs.get("team") or {}
        team_id = team.get("id")
        is_home = (team_id == home_team_id)
        
        player_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
        position = str(player.get("position") or "")
        
        if is_home:
            team_abbr = home_abbr
            opponent_abbr = away_abbr
            days_rest = home_days_rest
            # Home team faces away team's defense
            dvp_bucket, dvp_rank = get_dvp_bucket_for_position(away_dvp, position)
            # GOAT: Opponent stats (away team's ratings)
            opp_drtg = away_standings.get("defensive_rating")
            opp_nrtg = away_standings.get("net_rating")
            opp_pace = away_standings.get("pace")
        else:
            team_abbr = away_abbr
            opponent_abbr = home_abbr
            days_rest = away_days_rest
            # Away team faces home team's defense
            dvp_bucket, dvp_rank = get_dvp_bucket_for_position(home_dvp, position)
            # GOAT: Opponent stats (home team's ratings)
            opp_drtg = home_standings.get("defensive_rating")
            opp_nrtg = home_standings.get("net_rating")
            opp_pace = home_standings.get("pace")
        
        # Actual performance
        actual_pts = float(bs.get("pts", 0.0) or 0.0)
        actual_minutes = parse_minutes(bs.get("min"))
        
        # Pre-game context
        player_season = season_stats.get(pid, {})
        l5 = l5_stats.get(pid, {})
        
        season_pts_avg = player_season.get("pts_avg", 0.0)
        season_min_avg = player_season.get("min_avg", 0.0)
        l5_pts_avg = l5.get("pts_avg", 0.0)
        l5_pts_stdev = l5.get("pts_stdev", 0.0)
        l5_min_avg = l5.get("min_avg", 0.0)
        usg_pct = l5.get("usg_pct")
        
        # GOAT: Get official advanced stats
        player_adv = advanced_stats.get(pid, {})
        ts_pct = player_adv.get("true_shooting_pct")
        off_rating = player_adv.get("offensive_rating")
        
        # GOAT: Override usg_pct with official if available
        if player_adv.get("usage_pct"):
            usg_pct = player_adv.get("usage_pct")
        
        # GOAT: Get league scoring rank
        pts_rank = league_ranks.get(pid)
        
        # GOAT: Get clutch scoring (only for high-minute players to save API calls)
        clutch_avg: Optional[float] = None
        if season_pts_avg >= 15.0:
            recent_game_ids = get_recent_game_ids(pid, num_games=5)
            if recent_game_ids:
                clutch_avg = get_clutch_scoring_avg(pid, recent_game_ids)
        
        # Baseline projection = season average
        baseline_proj = season_pts_avg
        
        # Calculate prediction error
        prediction_error = actual_pts - baseline_proj
        
        records.append(PlayerGameRecord(
            player_id=pid,
            name=player_name,
            team_abbr=team_abbr,
            opponent_abbr=opponent_abbr,
            position=position,
            is_home=is_home,
            actual_pts=actual_pts,
            actual_minutes=actual_minutes,
            season_pts_avg=season_pts_avg,
            season_minutes_avg=season_min_avg,
            l5_pts_avg=l5_pts_avg,
            l5_pts_stdev=l5_pts_stdev,
            l5_minutes_avg=l5_min_avg,
            usg_pct=usg_pct,
            days_rest=days_rest,
            dvp_bucket=dvp_bucket,
            dvp_rank=dvp_rank,
            pace_env=projected_pace or 100.0,
            ts_pct=ts_pct,
            off_rating=off_rating,
            team_drtg=opp_drtg,
            team_nrtg=opp_nrtg,
            opp_pace=opp_pace,
            clutch_pts_avg=clutch_avg,
            pts_league_rank=pts_rank,
            baseline_proj=baseline_proj,
            prediction_error=prediction_error,
        ))
    
    print(f"    Processed {len(records)} starters", file=sys.stderr)
    return records


def compute_adjustment_values(record: PlayerGameRecord) -> Dict[str, float]:
    """
    Compute the adjustment values for each factor based on current weight rules.
    These represent the adjustment that WOULD have been applied.
    
    GOAT Enhanced: Includes True Shooting %, Offensive Rating, Team Defensive Rating,
    Clutch Scoring, and League Rank adjustments.
    """
    adjustments: Dict[str, float] = {}
    
    # Usage adjustment
    if record.usg_pct is not None:
        if record.usg_pct >= 28.0:
            adjustments["usage"] = 1.5
        elif record.usg_pct < 20.0:
            adjustments["usage"] = -1.0
        else:
            adjustments["usage"] = 0.0
    else:
        adjustments["usage"] = 0.0
    
    # DvP adjustment
    if record.dvp_bucket == "WEAK":
        adjustments["dvp"] = 2.4
    elif record.dvp_bucket == "STRONG":
        adjustments["dvp"] = -2.4
    else:
        adjustments["dvp"] = 0.0
    
    # Rest adjustment
    if record.days_rest == 0:  # B2B
        adjustments["rest"] = -2.5 if not record.is_home else -1.5
    elif record.days_rest == 1:
        adjustments["rest"] = 0.0
    elif record.days_rest == 2:
        adjustments["rest"] = 0.5
    else:  # 3+
        adjustments["rest"] = -0.3
    
    # Pace adjustment
    if record.pace_env > 104:
        adjustments["pace"] = 2.2
    elif record.pace_env < 99:
        adjustments["pace"] = -2.2
    else:
        adjustments["pace"] = 0.0
    
    # Minutes stability
    proj_minutes = record.l5_minutes_avg if record.l5_minutes_avg > 0 else record.season_minutes_avg
    if proj_minutes >= 34:
        adjustments["minutes"] = 1.7
    elif proj_minutes >= 30:
        adjustments["minutes"] = 0.85
    elif proj_minutes >= 26:
        adjustments["minutes"] = 0.0
    else:
        adjustments["minutes"] = -2.5
    
    # Form adjustment
    if record.season_pts_avg > 0:
        form_raw = ((record.l5_pts_avg - record.season_pts_avg) / record.season_pts_avg) * 8.0
        adjustments["form"] = max(-3.2, min(3.2, form_raw))
    else:
        adjustments["form"] = 0.0
    
    # Consistency adjustment
    if record.l5_pts_stdev <= 5:
        adjustments["consistency"] = 1.5
    elif record.l5_pts_stdev <= 7:
        adjustments["consistency"] = 0.0
    else:
        adjustments["consistency"] = -1.5
    
    # ========== GOAT ADVANCED ADJUSTMENTS ==========
    
    # True Shooting % adjustment (efficiency)
    if record.ts_pct is not None:
        if record.ts_pct >= 0.62:
            adjustments["ts_pct"] = 1.0  # Elite efficiency
        elif record.ts_pct >= 0.58:
            adjustments["ts_pct"] = 0.5  # Above average
        elif record.ts_pct < 0.52:
            adjustments["ts_pct"] = -0.5  # Below average
        else:
            adjustments["ts_pct"] = 0.0
    else:
        adjustments["ts_pct"] = 0.0
    
    # Offensive Rating adjustment
    if record.off_rating is not None:
        if record.off_rating >= 120:
            adjustments["off_rating"] = 1.0  # Elite offensive impact
        elif record.off_rating >= 115:
            adjustments["off_rating"] = 0.5
        elif record.off_rating < 105:
            adjustments["off_rating"] = -0.5  # Poor offensive impact
        else:
            adjustments["off_rating"] = 0.0
    else:
        adjustments["off_rating"] = 0.0
    
    # Team Defensive Rating matchup (opponent's DRtg)
    if record.team_drtg is not None:
        if record.team_drtg >= 118:
            adjustments["opp_drtg"] = 2.0  # Bottom 10 defense (good for scoring)
        elif record.team_drtg >= 114:
            adjustments["opp_drtg"] = 1.0  # Below avg defense
        elif record.team_drtg <= 108:
            adjustments["opp_drtg"] = -2.0  # Top 5 defense (bad for scoring)
        elif record.team_drtg <= 112:
            adjustments["opp_drtg"] = -1.0  # Above avg defense
        else:
            adjustments["opp_drtg"] = 0.0
    else:
        adjustments["opp_drtg"] = 0.0
    
    # Clutch scoring adjustment
    if record.clutch_pts_avg is not None:
        if record.clutch_pts_avg >= 4.0:
            adjustments["clutch"] = 1.0  # Clutch performer
        elif record.clutch_pts_avg >= 2.5:
            adjustments["clutch"] = 0.5  # Reliable in clutch
        elif record.clutch_pts_avg < 1.0:
            adjustments["clutch"] = -0.5  # Struggles in clutch
        else:
            adjustments["clutch"] = 0.0
    else:
        adjustments["clutch"] = 0.0
    
    # League scoring rank adjustment
    if record.pts_league_rank is not None:
        if record.pts_league_rank <= 10:
            adjustments["league_rank"] = 1.0  # Top 10 scorer
        elif record.pts_league_rank <= 25:
            adjustments["league_rank"] = 0.5  # Top 25 scorer
        elif record.pts_league_rank >= 100:
            adjustments["league_rank"] = -0.5  # Low volume scorer
        else:
            adjustments["league_rank"] = 0.0
    else:
        adjustments["league_rank"] = 0.0
    
    return adjustments


def pearson_correlation(x: List[float], y: List[float]) -> float:
    """Compute Pearson correlation coefficient between two lists."""
    n = len(x)
    if n < 3 or len(y) != n:
        return 0.0
    
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    
    numerator = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    
    var_x = sum((xi - mean_x) ** 2 for xi in x)
    var_y = sum((yi - mean_y) ** 2 for yi in y)
    
    denominator = (var_x * var_y) ** 0.5
    
    if denominator == 0:
        return 0.0
    
    return numerator / denominator


def analyze_correlations(
    records: List[PlayerGameRecord],
) -> Dict[str, Dict[str, Any]]:
    """
    Analyze correlations between adjustment factors and prediction error.
    
    GOAT Enhanced: Includes correlations for True Shooting %, Offensive Rating,
    Team Defensive Rating, Clutch Scoring, and League Rank.
    
    Returns a dict mapping factor name to correlation analysis results.
    """
    if not records:
        return {}
    
    # Compute adjustment values for all records
    # GOAT: Added new factors
    factor_names = [
        "usage", "dvp", "rest", "pace", "minutes", "form", "consistency",
        "ts_pct", "off_rating", "opp_drtg", "clutch", "league_rank"
    ]
    factor_values: Dict[str, List[float]] = {name: [] for name in factor_names}
    errors: List[float] = []
    
    for record in records:
        adjustments = compute_adjustment_values(record)
        for name in factor_names:
            factor_values[name].append(adjustments.get(name, 0.0))
        errors.append(record.prediction_error)
    
    # Also compute "raw" continuous values for better correlation analysis
    raw_values: Dict[str, List[float]] = {
        "usage_raw": [],
        "dvp_rank_raw": [],
        "rest_raw": [],
        "pace_raw": [],
        "minutes_raw": [],
        "form_raw": [],
        "consistency_raw": [],
        # GOAT raw values
        "ts_pct_raw": [],
        "off_rating_raw": [],
        "opp_drtg_raw": [],
        "opp_nrtg_raw": [],
        "opp_pace_raw": [],
        "clutch_raw": [],
        "league_rank_raw": [],
    }
    
    for record in records:
        raw_values["usage_raw"].append(record.usg_pct if record.usg_pct else 22.0)
        raw_values["dvp_rank_raw"].append(float(record.dvp_rank))
        raw_values["rest_raw"].append(float(record.days_rest))
        raw_values["pace_raw"].append(record.pace_env)
        
        proj_min = record.l5_minutes_avg if record.l5_minutes_avg > 0 else record.season_minutes_avg
        raw_values["minutes_raw"].append(proj_min)
        
        if record.season_pts_avg > 0:
            form_pct = (record.l5_pts_avg - record.season_pts_avg) / record.season_pts_avg
        else:
            form_pct = 0.0
        raw_values["form_raw"].append(form_pct)
        raw_values["consistency_raw"].append(record.l5_pts_stdev)
        
        # GOAT raw values
        raw_values["ts_pct_raw"].append(record.ts_pct if record.ts_pct else 0.55)
        raw_values["off_rating_raw"].append(record.off_rating if record.off_rating else 110.0)
        raw_values["opp_drtg_raw"].append(record.team_drtg if record.team_drtg else 110.0)
        raw_values["opp_nrtg_raw"].append(record.team_nrtg if record.team_nrtg else 0.0)
        raw_values["opp_pace_raw"].append(record.opp_pace if record.opp_pace else 100.0)
        raw_values["clutch_raw"].append(record.clutch_pts_avg if record.clutch_pts_avg else 0.0)
        raw_values["league_rank_raw"].append(float(record.pts_league_rank) if record.pts_league_rank else 150.0)
    
    results: Dict[str, Dict[str, Any]] = {}
    
    # Analyze bucketed adjustments
    for name in factor_names:
        corr = pearson_correlation(factor_values[name], errors)
        non_zero_count = sum(1 for v in factor_values[name] if v != 0)
        results[name] = {
            "correlation": round(corr, 4),
            "n_samples": len(errors),
            "n_non_zero": non_zero_count,
            "avg_adjustment": round(sum(factor_values[name]) / len(factor_values[name]), 3),
        }
    
    # Analyze raw continuous values
    for name, values in raw_values.items():
        corr = pearson_correlation(values, errors)
        results[name] = {
            "correlation": round(corr, 4),
            "n_samples": len(errors),
            "avg_value": round(sum(values) / len(values), 2),
        }
    
    return results


def generate_weight_suggestions(
    correlations: Dict[str, Dict[str, Any]],
    records: List[PlayerGameRecord],
) -> List[Dict[str, Any]]:
    """
    Generate suggested weight changes based on correlation analysis.
    
    GOAT Enhanced: Includes recommendations for True Shooting %, Offensive Rating,
    Team Defensive Rating, Clutch Scoring, and League Rank.
    
    Positive correlation with error means: when adjustment is positive, 
    actual was higher than predicted. This suggests the adjustment is 
    directionally correct but possibly too small.
    
    Negative correlation means: when adjustment is positive, actual was 
    lower than predicted. This suggests the adjustment direction may be wrong.
    """
    suggestions: List[Dict[str, Any]] = []
    
    # Current weights from points_prompt.md (including GOAT weights)
    current_weights = {
        "usage": {"high": 1.5, "low": -1.0},
        "dvp": {"weak": 2.4, "strong": -2.4},
        "rest": {"b2b_road": -2.5, "b2b_home": -1.5, "optimal": 0.5, "rust": -0.3},
        "pace": {"fast": 2.2, "slow": -2.2},
        "minutes": {"high": 1.7, "med": 0.85, "low": -2.5},
        "form": {"cap": 3.2},
        "consistency": {"stable": 1.5, "volatile": -1.5},
        # GOAT weights
        "ts_pct": {"elite": 1.0, "above_avg": 0.5, "below_avg": -0.5},
        "off_rating": {"elite": 1.0, "above_avg": 0.5, "below_avg": -0.5},
        "opp_drtg": {"bottom_10": 2.4, "below_avg": 1.2, "top_5": -2.4, "above_avg": -1.2},
        "clutch": {"clutch_performer": 1.0, "reliable": 0.5, "struggles": -0.5},
        "league_rank": {"top_10": 1.0, "top_25": 0.5, "low_volume": -0.5},
    }
    
    factor_analysis = [
        ("usage", "High usage (>=28%) players"),
        ("dvp", "DvP matchup advantage"),
        ("rest", "Days of rest impact"),
        ("pace", "Pace environment effect"),
        ("minutes", "Minutes stability bonus"),
        ("form", "Recent form adjustment"),
        ("consistency", "Scoring consistency"),
        # GOAT factors
        ("ts_pct", "True Shooting % (efficiency)"),
        ("off_rating", "Offensive Rating (player impact)"),
        ("opp_drtg", "Opponent Defensive Rating (matchup)"),
        ("clutch", "Clutch scoring performance"),
        ("league_rank", "League scoring rank (volume)"),
    ]
    
    for factor, description in factor_analysis:
        corr_data = correlations.get(factor, {})
        corr = corr_data.get("correlation", 0.0)
        n_non_zero = corr_data.get("n_non_zero", 0)
        
        # Only suggest changes for factors with meaningful correlation
        if abs(corr) < 0.05 or n_non_zero < 10:
            continue
        
        suggestion = {
            "factor": factor,
            "description": description,
            "correlation": corr,
            "current_weights": current_weights.get(factor, {}),
            "n_samples_with_adjustment": n_non_zero,
        }
        
        # Determine suggestion direction
        if corr > 0.10:
            # Positive correlation: adjustment direction is correct, possibly too small
            suggestion["recommendation"] = "INCREASE weights (adjustment under-predicts when conditions favor)"
            suggestion["suggested_change"] = f"+10-20% to current weights"
        elif corr < -0.10:
            # Negative correlation: adjustment direction may be wrong
            suggestion["recommendation"] = "DECREASE weights or REVIEW logic (adjustment over-predicts)"
            suggestion["suggested_change"] = f"-10-20% from current weights"
        else:
            suggestion["recommendation"] = "MAINTAIN current weights (weak correlation)"
            suggestion["suggested_change"] = "No change recommended"
        
        suggestions.append(suggestion)
    
    # Sort by absolute correlation (strongest signal first)
    suggestions.sort(key=lambda s: abs(s["correlation"]), reverse=True)
    
    return suggestions


def compute_accuracy_metrics(records: List[PlayerGameRecord]) -> Dict[str, Any]:
    """Compute overall accuracy metrics for the baseline projection."""
    if not records:
        return {}
    
    errors = [r.prediction_error for r in records]
    abs_errors = [abs(e) for e in errors]
    
    mae = sum(abs_errors) / len(abs_errors)
    mse = sum(e ** 2 for e in errors) / len(errors)
    rmse = mse ** 0.5
    
    # Bias (average error, positive = under-predicting)
    bias = sum(errors) / len(errors)
    
    # Percentage of predictions within thresholds
    within_5 = sum(1 for e in abs_errors if e <= 5) / len(abs_errors) * 100
    within_10 = sum(1 for e in abs_errors if e <= 10) / len(abs_errors) * 100
    
    return {
        "n_players": len(records),
        "mae": round(mae, 2),
        "rmse": round(rmse, 2),
        "bias": round(bias, 2),
        "within_5_pts_pct": round(within_5, 1),
        "within_10_pts_pct": round(within_10, 1),
    }


def append_to_log(
    log_path: str,
    game_date: date,
    season: int,
    metrics: Dict[str, Any],
    correlations: Dict[str, Dict[str, Any]],
    suggestions: List[Dict[str, Any]],
) -> None:
    """Append tuning session summary to log file."""
    timestamp = datetime.now(timezone.utc).isoformat()
    
    entry = {
        "timestamp": timestamp,
        "game_date": game_date.isoformat(),
        "season": season,
        "metrics": metrics,
        "top_correlations": {
            k: v for k, v in sorted(
                correlations.items(),
                key=lambda x: abs(x[1].get("correlation", 0)),
                reverse=True
            )[:5]
        },
        "suggestions_count": len(suggestions),
        "top_suggestion": suggestions[0] if suggestions else None,
    }
    
    with open(log_path, "a") as f:
        f.write("\n" + "=" * 60 + "\n")
        f.write(f"TUNING SESSION: {timestamp}\n")
        f.write(f"Game Date: {game_date.isoformat()} | Season: {season}\n")
        f.write("-" * 60 + "\n")
        f.write(f"Players Analyzed: {metrics.get('n_players', 0)}\n")
        f.write(f"MAE: {metrics.get('mae', 'N/A')} pts | RMSE: {metrics.get('rmse', 'N/A')} pts\n")
        f.write(f"Bias: {metrics.get('bias', 'N/A')} pts (positive = under-predicting)\n")
        f.write(f"Within 5 pts: {metrics.get('within_5_pts_pct', 'N/A')}% | Within 10 pts: {metrics.get('within_10_pts_pct', 'N/A')}%\n")
        f.write("-" * 60 + "\n")
        f.write("TOP CORRELATIONS (factor → error):\n")
        for name, data in list(entry["top_correlations"].items())[:5]:
            f.write(f"  {name}: r={data.get('correlation', 0):.4f}\n")
        f.write("-" * 60 + "\n")
        if suggestions:
            f.write("TOP SUGGESTION:\n")
            top = suggestions[0]
            f.write(f"  Factor: {top['factor']} ({top['description']})\n")
            f.write(f"  Correlation: {top['correlation']:.4f}\n")
            f.write(f"  Recommendation: {top['recommendation']}\n")
            f.write(f"  Suggested Change: {top['suggested_change']}\n")
        else:
            f.write("No significant weight change suggestions.\n")
        f.write("=" * 60 + "\n")
    
    print(f"Appended summary to {log_path}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze historical games to tune prediction model weights."
    )
    parser.add_argument(
        "--date",
        required=True,
        help="Game date to analyze (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--season",
        type=int,
        required=True,
        help="Season year (e.g., 2025 for 2024-25 season)",
    )
    parser.add_argument(
        "--log-file",
        default="model_tuning_v3.log",
        help="Path to tuning log file (default: model_tuning_v3.log)",
    )
    args = parser.parse_args()
    
    # Parse date
    try:
        game_date = date.fromisoformat(args.date)
    except ValueError:
        print(f"Error: Invalid date format '{args.date}'. Use YYYY-MM-DD.", file=sys.stderr)
        sys.exit(1)
    
    print(f"=" * 60, file=sys.stderr)
    print(f"AUTO-TUNE MODEL: Analyzing games from {game_date}", file=sys.stderr)
    print(f"Season: {args.season}", file=sys.stderr)
    print(f"=" * 60, file=sys.stderr)
    
    # Get teams map
    print("\nFetching teams data...", file=sys.stderr)
    teams_map = get_teams_map()
    
    # Get all games on the date
    print(f"\nFetching games on {game_date}...", file=sys.stderr)
    games = get_games_on_date(game_date)
    
    if not games:
        print(f"No games found on {game_date}.", file=sys.stderr)
        sys.exit(0)
    
    print(f"Found {len(games)} games", file=sys.stderr)
    
    # Process each game
    all_records: List[PlayerGameRecord] = []
    
    for game in games:
        try:
            records = process_game(game, args.season, teams_map)
            all_records.extend(records)
        except Exception as e:
            game_id = game.get("id", "unknown")
            print(f"  [ERROR] Failed to process game {game_id}: {e}", file=sys.stderr)
            continue
    
    if not all_records:
        print("\nNo player records collected. Cannot perform analysis.", file=sys.stderr)
        sys.exit(1)
    
    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"ANALYSIS: {len(all_records)} player records collected", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)
    
    # Compute accuracy metrics
    metrics = compute_accuracy_metrics(all_records)
    
    print("\nACCURACY METRICS (Baseline = Season Average):", file=sys.stderr)
    print(f"  Players Analyzed: {metrics['n_players']}", file=sys.stderr)
    print(f"  Mean Absolute Error: {metrics['mae']:.2f} pts", file=sys.stderr)
    print(f"  Root Mean Square Error: {metrics['rmse']:.2f} pts", file=sys.stderr)
    print(f"  Bias: {metrics['bias']:+.2f} pts (positive = under-predicting)", file=sys.stderr)
    print(f"  Within 5 pts: {metrics['within_5_pts_pct']:.1f}%", file=sys.stderr)
    print(f"  Within 10 pts: {metrics['within_10_pts_pct']:.1f}%", file=sys.stderr)
    
    # Analyze correlations
    print("\n" + "-" * 60, file=sys.stderr)
    print("CORRELATION ANALYSIS (factor → prediction error):", file=sys.stderr)
    
    correlations = analyze_correlations(all_records)
    
    # Sort and display bucketed factors (including GOAT factors)
    bucketed_factors = [
        "usage", "dvp", "rest", "pace", "minutes", "form", "consistency",
        "ts_pct", "off_rating", "opp_drtg", "clutch", "league_rank"
    ]
    sorted_factors = sorted(
        [(f, correlations.get(f, {})) for f in bucketed_factors],
        key=lambda x: abs(x[1].get("correlation", 0)),
        reverse=True
    )
    
    print("\n  === BASE FACTORS ===", file=sys.stderr)
    base_factors = ["usage", "dvp", "rest", "pace", "minutes", "form", "consistency"]
    for factor, data in sorted_factors:
        if factor not in base_factors:
            continue
        corr = data.get("correlation", 0)
        n_non_zero = data.get("n_non_zero", 0)
        direction = "↑" if corr > 0 else "↓" if corr < 0 else "→"
        strength = "STRONG" if abs(corr) > 0.2 else "MODERATE" if abs(corr) > 0.1 else "WEAK"
        print(f"  {factor:12s}: r={corr:+.4f} {direction} [{strength}] (n={n_non_zero} non-zero)", file=sys.stderr)
    
    print("\n  === GOAT ADVANCED FACTORS ===", file=sys.stderr)
    goat_factors = ["ts_pct", "off_rating", "opp_drtg", "clutch", "league_rank"]
    for factor, data in sorted_factors:
        if factor not in goat_factors:
            continue
        corr = data.get("correlation", 0)
        n_non_zero = data.get("n_non_zero", 0)
        direction = "↑" if corr > 0 else "↓" if corr < 0 else "→"
        strength = "STRONG" if abs(corr) > 0.2 else "MODERATE" if abs(corr) > 0.1 else "WEAK"
        print(f"  {factor:12s}: r={corr:+.4f} {direction} [{strength}] (n={n_non_zero} non-zero)", file=sys.stderr)
    
    # Generate suggestions
    suggestions = generate_weight_suggestions(correlations, all_records)
    
    print("\n" + "-" * 60, file=sys.stderr)
    print("SUGGESTED WEIGHT CHANGES:", file=sys.stderr)
    
    if not suggestions:
        print("  No significant weight changes recommended.", file=sys.stderr)
    else:
        for i, sug in enumerate(suggestions[:5], 1):
            print(f"\n  {i}. {sug['factor'].upper()} ({sug['description']})", file=sys.stderr)
            print(f"     Correlation: {sug['correlation']:+.4f}", file=sys.stderr)
            print(f"     Recommendation: {sug['recommendation']}", file=sys.stderr)
            print(f"     Suggested: {sug['suggested_change']}", file=sys.stderr)
    
    # Append to log
    append_to_log(
        args.log_file,
        game_date,
        args.season,
        metrics,
        correlations,
        suggestions,
    )
    
    # Output JSON summary to stdout
    output = {
        "game_date": game_date.isoformat(),
        "season": args.season,
        "n_games": len(games),
        "n_players": len(all_records),
        "metrics": metrics,
        "correlations": {
            k: v for k, v in correlations.items()
            if not k.endswith("_raw")  # Exclude raw correlations from main output
        },
        "suggestions": suggestions,
    }
    
    print("\n" + "=" * 60, file=sys.stderr)
    print("JSON OUTPUT:", file=sys.stderr)
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
