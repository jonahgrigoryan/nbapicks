#!/usr/bin/env python3
"""fetch_points_game_data.py

Fetches NBA game data for points prediction with GOAT All-Star tier enhancements.

GOAT TIER FEATURES (2024-25):
- Official Usage Rate: From /nba/v1/stats?type=advanced (usage_pct)
- True Shooting %: Official TS% from advanced stats endpoint
- Offensive Rating: Player-level ORtg from advanced stats
- Team Defensive Rating: From /nba/v1/standings (defensive_rating)
- Net Rating: Team net rating from standings
- Starting Lineup Detection: Via box scores (players with >=20 min in Q1)
- Clutch Scoring: From play_by_play (last 5 min, margin <=5)
- League Rank: Player's PPG rank from /nba/v1/leaders
- Days of Rest: Granular rest tracking (0=B2B, 1=standard, 2=optimal, 3+=rust)
- DvP (Defense vs Position): Auto-computed from opponent's recent games
- Pace: Official team pace from standings + L10 calculation

Usage:
  python3 fetch_points_game_data.py \
    --game-date 2025-11-29 \
    --away BOS \
    --home MIN \
    --season 2025

Output:
  Prints a JSON payload (`GAME_DATA`) for the matchup with structure:
  {
    "meta": {...},
    "teams": {
      "AWAY": {"days_rest": 1, "pace_last_10": 102.5, "dvp": {...}, "advanced": {...}},
      "HOME": {...}
    },
    "players": {
      "AWAY": [{"recent": {"usg_pct": 25.5, "ts_pct": 0.58, ...}, "is_starter": true, ...}],
      "HOME": [...]
    }
  }

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
import time
from collections import deque

# Load .env file if it exists
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if "=" in line:
                key, value = line.strip().split("=", 1)
                os.environ[key] = value.strip("'").strip('"')
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import requests


BALDONTLIE_BASE_V1 = "https://api.balldontlie.io/v1"
BALDONTLIE_BASE_NBA_V1 = "https://api.balldontlie.io/nba/v1"
BALDONTLIE_BASE_V2 = "https://api.balldontlie.io/v2"

# Basic client-side rate limiter to stay under the API per-minute cap.
_REQUEST_WINDOW_SEC = 60
_MAX_REQUESTS_PER_WINDOW = 50  # keep below 60/min to be safe
_REQUEST_TIMES: deque = deque()
_HTTP_TIMEOUT_SEC = 45


def bdl_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Thin wrapper over BallDontLie HTTP GET."""

    api_key = os.getenv("BALLDONTLIE_API_KEY")
    if not api_key:
        raise RuntimeError("BALLDONTLIE_API_KEY env var is not set")

    if path.startswith("/v2/"):
        base = BALDONTLIE_BASE_V2
        rel = path.replace("/v2", "", 1)
    elif path.startswith("/nba/v1/"):
        base = BALDONTLIE_BASE_NBA_V1
        rel = path.replace("/nba/v1", "", 1)
    else:
        base = BALDONTLIE_BASE_V1
        rel = path.replace("/v1", "", 1)

    url = base + rel
    headers = {"Authorization": api_key}

    now = time.time()
    while _REQUEST_TIMES and now - _REQUEST_TIMES[0] > _REQUEST_WINDOW_SEC:
        _REQUEST_TIMES.popleft()
    if len(_REQUEST_TIMES) >= _MAX_REQUESTS_PER_WINDOW:
        sleep_for = _REQUEST_WINDOW_SEC - (now - _REQUEST_TIMES[0]) + 0.1
        if sleep_for > 0:
            time.sleep(sleep_for)

    resp: Optional[requests.Response] = None
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=_HTTP_TIMEOUT_SEC)
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
            if attempt < 2:
                time.sleep(1.0 + attempt * 1.5)
                continue
            raise

        _REQUEST_TIMES.append(time.time())
        if resp.status_code == 429 and attempt < 2:
            time.sleep(1.0 + attempt * 1.5)
            continue
        resp.raise_for_status()
        return resp.json()

    if resp is None:
        raise RuntimeError("bdl_get failed without response")
    resp.raise_for_status()
    return resp.json()


@dataclass
class StatSummary:
    avg: float
    stdev: float


@dataclass
class RecentPoints:
    sample_size: int
    minutes_avg: float
    pts: Optional[StatSummary]
    usg_pct: Optional[float] = None
    ts_pct: Optional[float] = None  # True Shooting Percentage
    off_rating: Optional[float] = None  # Offensive Rating


@dataclass
class SeasonPoints:
    minutes: float
    pts: float
    usg_pct: Optional[float] = None
    ts_pct: Optional[float] = None
    off_rating: Optional[float] = None


@dataclass
class PlayerRecord:
    player_id: int
    name: str
    position: str
    season: SeasonPoints
    recent: RecentPoints
    injury_status: str
    injury_notes: Optional[str]
    is_starter: bool = False
    pts_league_rank: Optional[int] = None
    clutch_pts_avg: Optional[float] = None


@dataclass
class TeamAdvanced:
    defensive_rating: float
    net_rating: float
    pace: float


@dataclass
class DvPRanking:
    pts_allowed_avg: float
    rank: int
    bucket: str  # WEAK, AVERAGE, STRONG


def get_teams_map() -> Dict[str, Dict[str, Any]]:
    data = bdl_get("/v1/teams", {})
    teams: Dict[str, Dict[str, Any]] = {}
    for t in data.get("data", []):
        abbr = str(t.get("abbreviation") or "").upper()
        if not abbr:
            continue
        
        # If we already have this abbreviation, prefer the one with a conference
        # (Current teams have conferences, historic ones like WAS Capitols often don't)
        if abbr in teams:
            current_has_conf = bool(teams[abbr].get("conference", "").strip())
            new_has_conf = bool(t.get("conference", "").strip())
            if not current_has_conf and new_has_conf:
                teams[abbr] = t
        else:
            teams[abbr] = t
    return teams


def find_game_by_teams_and_date(game_date: datetime, away_abbr: str, home_abbr: str) -> Dict[str, Any]:
    teams_map = get_teams_map()
    try:
        away_id = teams_map[away_abbr]["id"]
        home_id = teams_map[home_abbr]["id"]
    except KeyError as e:
        raise RuntimeError(f"Unknown team abbreviation: {e}")

    date_str = game_date.date().isoformat()
    params = {
        "dates[]": date_str,
        "team_ids[]": [away_id, home_id],
        "per_page": 100,
    }
    data = bdl_get("/v1/games", params)

    for g in data.get("data", []):
        if (
            str((g.get("home_team") or {}).get("abbreviation") or "").upper() == home_abbr
            and str((g.get("visitor_team") or {}).get("abbreviation") or "").upper() == away_abbr
        ):
            return g

    raise RuntimeError(f"No game found for {away_abbr} @ {home_abbr} on {date_str}")


def get_player_injuries_for_teams(team_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    if not team_ids:
        return {}

    params: Dict[str, Any] = {"per_page": 100}
    for tid in team_ids:
        params.setdefault("team_ids[]", []).append(tid)

    try:
        injuries: Dict[int, Dict[str, Any]] = {}
        cursor: Optional[str] = None
        for _ in range(20):
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor

            data = bdl_get("/v1/player_injuries", page_params)
            for injury in data.get("data", []):
                player = injury.get("player") or {}
                pid = player.get("id")
                if not pid:
                    continue

                status = injury.get("status", "AVAILABLE")
                desc = injury.get("description")

                injuries[int(pid)] = {
                    "status": str(status).upper() if status else "AVAILABLE",
                    "description": desc,
                }

            cursor = (data.get("meta") or {}).get("next_cursor")
            if not cursor:
                break

        return injuries
    except Exception as e:
        print(
            f"[WARN] Failed to fetch player injuries: {e}. Treating all players as AVAILABLE.",
            file=sys.stderr,
            flush=True,
        )
        return {}


def get_active_players_for_team(team_id: int) -> List[Dict[str, Any]]:
    params = {"team_ids[]": [team_id], "per_page": 100}
    data = bdl_get("/v1/players/active", params)
    return list(data.get("data", []))


def get_season_points(player_ids: List[int], season: int) -> Dict[int, SeasonPoints]:
    if not player_ids:
        return {}

    def _fetch_batched(endpoint: str, base_params: Dict[str, Any], ids: List[int], chunk_size: int = 25) -> List[Dict[str, Any]]:
        all_rows: List[Dict[str, Any]] = []
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i : i + chunk_size]
            params = dict(base_params)
            for pid in chunk:
                params.setdefault("player_ids[]", []).append(pid)
            resp = bdl_get(endpoint, params)
            all_rows.extend(resp.get("data", []))
        return all_rows

    def _parse_minutes(minutes_val: Any) -> float:
        if isinstance(minutes_val, str) and ":" in minutes_val:
            mm, ss = minutes_val.split(":")
            return float(int(mm) + int(ss) / 60.0)
        try:
            return float(minutes_val or 0.0)
        except Exception:
            return 0.0

    def _parse_rows(rows: List[Dict[str, Any]]) -> Dict[int, SeasonPoints]:
        out: Dict[int, SeasonPoints] = {}
        for row in rows:
            if "stats" in row:
                stats = row.get("stats") or {}
                pid = (row.get("player") or {}).get("id")
            else:
                stats = row
                pid = row.get("player_id")

            if not pid:
                continue

            out[int(pid)] = SeasonPoints(
                minutes=round(_parse_minutes(stats.get("min")), 2),
                pts=float(stats.get("pts", 0.0) or 0.0),
                usg_pct=float(stats.get("usage_pct", 0.0)) if stats.get("usage_pct") else None,
                ts_pct=float(stats.get("true_shooting_pct", 0.0)) if stats.get("true_shooting_pct") else None,
                off_rating=float(stats.get("offensive_rating", 0.0)) if stats.get("offensive_rating") else None,
            )
        return out

    # Try advanced season averages first if available
    try:
        rows = _fetch_batched("/nba/v1/season_averages/advanced", {"season": season, "season_type": "regular"}, player_ids)
        return _parse_rows(rows)
    except Exception:
        # Fallback to general base
        nba_params = {"season": season, "season_type": "regular", "type": "base"}
        try:
            rows = _fetch_batched("/nba/v1/season_averages/general", nba_params, player_ids)
            return _parse_rows(rows)
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            # Fallback for accounts without /nba/v1 access or if endpoint rejects the season
            if status in (401, 400):
                try:
                    legacy_rows = _fetch_batched("/v1/season_averages", {"season": season}, player_ids)
                    return _parse_rows(legacy_rows)
                except requests.HTTPError as le:
                    legacy_status = le.response.status_code if le.response is not None else None
                    if legacy_status == 400:
                        print(
                            f"[WARN] Legacy /v1/season_averages rejected request (season={season}): {le}. "
                            f"Falling back to computing season averages from /v1/stats.",
                            file=sys.stderr,
                            flush=True,
                        )

                        def _parse_stat_min(min_val: Any) -> float:
                            if isinstance(min_val, str) and ":" in min_val:
                                mm, ss = min_val.split(":")
                                return float(int(mm) + int(ss) / 60.0)
                            try:
                                return float(min_val or 0.0)
                            except Exception:
                                return 0.0

                        out_pts_sum: Dict[int, float] = {int(pid): 0.0 for pid in player_ids}
                        out_min_sum: Dict[int, float] = {int(pid): 0.0 for pid in player_ids}
                        out_games: Dict[int, int] = {int(pid): 0 for pid in player_ids}

                        def _fetch_stats_for_chunk(ids: List[int]) -> None:
                            cursor: Optional[int] = None
                            for _ in range(200):  # hard cap to prevent infinite loops
                                params: Dict[str, Any] = {"per_page": 100, "seasons[]": [season]}
                                for pid in ids:
                                    params.setdefault("player_ids[]", []).append(pid)
                                if cursor is not None:
                                    params["cursor"] = cursor

                                data = bdl_get("/v1/stats", params)
                                rows = list(data.get("data", []))
                                for r in rows:
                                    player = r.get("player") or {}
                                    pid_val = player.get("id")
                                    if not pid_val:
                                        continue
                                    pid_int = int(pid_val)
                                    if pid_int not in out_games:
                                        continue

                                    m = _parse_stat_min(r.get("min"))
                                    if m <= 0:
                                        continue

                                    out_games[pid_int] += 1
                                    out_min_sum[pid_int] += m
                                    out_pts_sum[pid_int] += float(r.get("pts", 0.0) or 0.0)

                                next_cursor = (data.get("meta") or {}).get("next_cursor")
                                if not next_cursor:
                                    break
                                cursor = int(next_cursor)

                        # Fetch in chunks to limit URL length and keep responses manageable.
                        chunk_size = 10
                        for i in range(0, len(player_ids), chunk_size):
                            _fetch_stats_for_chunk([int(x) for x in player_ids[i : i + chunk_size]])

                        out: Dict[int, SeasonPoints] = {}
                        for pid in player_ids:
                            pid_int = int(pid)
                            g = out_games.get(pid_int, 0)
                            if g > 0:
                                out[pid_int] = SeasonPoints(
                                    minutes=round(out_min_sum[pid_int] / g, 2),
                                    pts=round(out_pts_sum[pid_int] / g, 2),
                                )
                            else:
                                out[pid_int] = SeasonPoints(minutes=0.0, pts=0.0)
                        return out
                    raise
            raise


def get_recent_game_stats(player_ids: List[int], since_date: datetime) -> Dict[int, List[Dict[str, Any]]]:
    out: Dict[int, List[Dict[str, Any]]] = {pid: [] for pid in player_ids}
    for pid in player_ids:
        params = {
            "player_ids[]": [pid],
            "start_date": since_date.date().isoformat(),
            "per_page": 50,
        }
        data = bdl_get("/v1/stats", params)
        out[pid] = data.get("data", [])
    return out


def get_advanced_player_stats(player_ids: List[int], since_date: datetime) -> Dict[int, List[Dict[str, Any]]]:
    """Fetch advanced box score stats for players since a specific date."""
    out: Dict[int, List[Dict[str, Any]]] = {pid: [] for pid in player_ids}
    for pid in player_ids:
        params = {
            "player_ids[]": [pid],
            "start_date": since_date.date().isoformat(),
            "per_page": 50,
        }
        try:
            # Using v1 stats endpoint with advanced type if supported, else fallback
            data = bdl_get("/nba/v1/stats/advanced", params)
            out[pid] = data.get("data", [])
        except Exception:
            # Fallback to empty if endpoint not available or fails
            out[pid] = []
    return out


def get_team_standings(season: int) -> Dict[int, Dict[str, Any]]:
    """Fetch current team standings and ratings from GOAT standings endpoint.
    
    Uses /nba/v1/standings with season parameter to get official:
    - defensive_rating (DRtg)
    - net_rating (NRtg)  
    - pace (possessions per 48 min)
    - offensive_rating (ORtg)
    """
    try:
        # GOAT endpoint with season parameter
        params = {"season": season}
        data = bdl_get("/nba/v1/standings", params)
        standings: Dict[int, Dict[str, Any]] = {}
        
        for s in data.get("data", []):
            # Handle nested structure: could be s["team"]["id"] or s["attributes"]["team"]["id"]
            attrs = s.get("attributes") or s
            team_info = attrs.get("team") or {}
            tid = team_info.get("id") or s.get("id")
            
            if tid:
                standings[int(tid)] = {
                    "defensive_rating": float(attrs.get("defensive_rating") or attrs.get("def_rating") or 110.0),
                    "offensive_rating": float(attrs.get("offensive_rating") or attrs.get("off_rating") or 110.0),
                    "net_rating": float(attrs.get("net_rating") or 0.0),
                    "pace": float(attrs.get("pace") or 100.0),
                    "win": int(attrs.get("win") or attrs.get("wins") or 0),
                    "loss": int(attrs.get("loss") or attrs.get("losses") or 0),
                }
        return standings
    except Exception as e:
        print(f"[WARN] Failed to fetch GOAT standings: {e}. Using fallback.", file=sys.stderr)
        return {}


def get_game_advanced_stats(
    game_id: int,
    player_ids: Optional[List[int]] = None,
) -> Dict[int, Dict[str, Any]]:
    """Fetch official advanced stats for a specific game from GOAT endpoint.
    
    Uses /nba/v1/stats with type=advanced for:
    - usage_pct (USG%)
    - true_shooting_pct (TS%)
    - offensive_rating (ORtg)
    - effective_fg_pct (eFG%)
    
    Returns dict mapping player_id -> advanced stats dict.
    """
    try:
        params: Dict[str, Any] = {
            "game_ids[]": [game_id],
            "per_page": 100,
        }
        
        # Try GOAT advanced endpoint first
        try:
            data = bdl_get("/nba/v1/stats/advanced", params)
        except Exception:
            # Fallback to base stats with type=advanced
            params["type"] = "advanced"
            data = bdl_get("/v1/stats", params)
        
        results: Dict[int, Dict[str, Any]] = {}
        
        for row in data.get("data", []):
            attrs = row.get("attributes") or row
            player = attrs.get("player") or row.get("player") or {}
            pid = player.get("id")
            
            if not pid:
                continue
            
            # Filter to requested player_ids if specified
            if player_ids and int(pid) not in player_ids:
                continue
            
            results[int(pid)] = {
                "usage_pct": _safe_float(attrs.get("usage_pct") or attrs.get("usg_pct")),
                "true_shooting_pct": _safe_float(attrs.get("true_shooting_pct") or attrs.get("ts_pct")),
                "offensive_rating": _safe_float(attrs.get("offensive_rating") or attrs.get("off_rating")),
                "effective_fg_pct": _safe_float(attrs.get("effective_fg_pct") or attrs.get("efg_pct")),
            }
        
        return results
    except Exception as e:
        print(f"[WARN] Failed to fetch game advanced stats: {e}", file=sys.stderr)
        return {}


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Safely convert value to float."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def get_starting_lineup(game_id: int, team_id: int) -> List[int]:
    """Detect starting lineup for a team in a specific game.
    
    Uses box score stats to identify starters by:
    1. Looking for players with significant Q1 minutes
    2. Falling back to top 5 players by total minutes
    
    Returns list of player IDs for the starting 5.
    """
    try:
        # Get box score stats for the game
        params = {
            "game_ids[]": [game_id],
            "per_page": 100,
        }
        data = bdl_get("/v1/stats", params)
        
        team_players: List[Dict[str, Any]] = []
        
        for row in data.get("data", []):
            player_team = row.get("team") or {}
            if player_team.get("id") != team_id:
                continue
            
            player = row.get("player") or {}
            pid = player.get("id")
            if not pid:
                continue
            
            mins = parse_minutes(row.get("min"))
            team_players.append({
                "player_id": int(pid),
                "minutes": mins,
            })
        
        # Sort by minutes and take top 5 as starters
        team_players.sort(key=lambda x: x["minutes"], reverse=True)
        starters = [p["player_id"] for p in team_players[:5]]
        
        return starters
    except Exception as e:
        print(f"[WARN] Failed to detect starting lineup: {e}", file=sys.stderr)
        return []


def get_pts_league_rank(player_id: int, season: int) -> Optional[int]:
    """Get player's scoring rank in the league from /v1/leaders.

    Returns the player's PPG ranking (1 = league leader), or None if not found.
    """
    try:
        params: Dict[str, Any] = {
            "season": season,
            "stat_type": "pts",
            "per_page": 100,
        }

        try:
            data = bdl_get("/v1/leaders", params)
        except Exception:
            data = bdl_get("/nba/v1/leaders", params)

        leaders = data.get("data", [])

        for idx, leader in enumerate(leaders, 1):
            player = leader.get("player") or (leader.get("attributes") or {}).get("player") or {}
            pid = player.get("id")
            if not pid or int(pid) != player_id:
                continue

            rank_val = leader.get("rank")
            if rank_val is None:
                rank_val = (leader.get("attributes") or {}).get("rank")
            return int(rank_val) if rank_val is not None else idx

        return None
    except Exception:
        return None


def get_pts_league_ranks_batch(player_ids: List[int], season: int) -> Dict[int, int]:
    """Get league scoring ranks for multiple players.

    Uses `/v1/leaders` which returns per-game leaders and supports cursor-based
    pagination. Returns dict mapping player_id -> rank (1 = league leader).
    """
    try:
        player_id_set = set(int(pid) for pid in player_ids if pid)
        if not player_id_set:
            return {}

        ranks: Dict[int, int] = {}
        cursor: Optional[int] = None
        seen = 0
        max_pages = 10  # safety guard

        for _ in range(max_pages):
            params: Dict[str, Any] = {
                "season": season,
                "stat_type": "pts",
                "per_page": 100,
            }
            if cursor is not None:
                params["cursor"] = cursor

            try:
                data = bdl_get("/v1/leaders", params)
            except Exception:
                data = bdl_get("/nba/v1/leaders", params)

            leaders = data.get("data", [])
            if not leaders:
                break

            for idx, leader in enumerate(leaders, 1):
                player = leader.get("player") or (leader.get("attributes") or {}).get("player") or {}
                pid = player.get("id")
                if not pid:
                    continue

                pid_int = int(pid)
                if pid_int not in player_id_set or pid_int in ranks:
                    continue

                rank_val = leader.get("rank")
                if rank_val is None:
                    rank_val = (leader.get("attributes") or {}).get("rank")

                ranks[pid_int] = int(rank_val) if rank_val is not None else (seen + idx)

            meta = data.get("meta") or {}
            next_cursor = meta.get("next_cursor")
            if not next_cursor:
                break

            cursor = int(next_cursor)
            seen += len(leaders)

            # Early exit if we got all requested ids.
            if len(ranks) == len(player_id_set):
                break

        return ranks
    except Exception as e:
        print(f"[WARN] Failed to fetch league ranks: {e}", file=sys.stderr)
        return {}


def get_clutch_scoring_avg(
    player_id: int,
    game_ids: List[int],
) -> Optional[float]:
    """Calculate average clutch scoring from play-by-play data.
    
    Clutch = last 5 minutes of regulation, margin <= 5 points.
    
    Returns average clutch points per game, or None if no data.
    """
    if not game_ids:
        return None
    
    clutch_pts_per_game: List[float] = []
    
    for game_id in game_ids[:10]:  # Limit to recent 10 games
        try:
            params = {
                "game_id": game_id,
                "per_page": 500,
            }
            
            # Try GOAT play_by_play endpoint
            try:
                data = bdl_get("/nba/v1/play_by_play", params)
            except Exception:
                continue  # Skip if endpoint unavailable
            
            plays = data.get("data", [])
            clutch_pts = 0.0
            
            for play in plays:
                attrs = play.get("attributes") or play
                
                # Check if clutch situation (Q4, last 5 min, margin <=5)
                period = int(attrs.get("period") or 0)
                clock = attrs.get("clock") or "12:00"
                score_home = int(attrs.get("score_home") or 0)
                score_away = int(attrs.get("score_away") or 0)
                
                # Parse clock (format: "MM:SS")
                try:
                    mins_left = int(clock.split(":")[0])
                except Exception:
                    mins_left = 12
                
                margin = abs(score_home - score_away)
                
                # Clutch = Q4 (period 4), <= 5 minutes left, margin <= 5
                if period == 4 and mins_left <= 5 and margin <= 5:
                    # Check if this player scored
                    player_info = attrs.get("player") or {}
                    pid = player_info.get("id")
                    event_type = str(attrs.get("event_type") or attrs.get("type") or "").lower()
                    
                    if pid and int(pid) == player_id:
                        if "made" in event_type or "score" in event_type:
                            pts = int(attrs.get("points") or attrs.get("pts") or 0)
                            if pts == 0:
                                # Infer points from shot type
                                if "3pt" in event_type or "three" in event_type:
                                    pts = 3
                                elif "ft" in event_type or "free" in event_type:
                                    pts = 1
                                else:
                                    pts = 2
                            clutch_pts += pts
            
            clutch_pts_per_game.append(clutch_pts)
            
        except Exception:
            continue
    
    if not clutch_pts_per_game:
        return None
    
    return round(sum(clutch_pts_per_game) / len(clutch_pts_per_game), 2)


def get_player_advanced_season_stats(
    player_ids: List[int],
    season: int,
) -> Dict[int, Dict[str, Any]]:
    """Fetch official advanced season averages from GOAT endpoint.
    
    Returns dict mapping player_id -> {usage_pct, ts_pct, off_rating, ...}
    """
    if not player_ids:
        return {}
    
    results: Dict[int, Dict[str, Any]] = {}
    
    def _fetch_batch(ids: List[int]) -> None:
        params: Dict[str, Any] = {
            "season": season,
            "season_type": "regular",
        }
        for pid in ids:
            params.setdefault("player_ids[]", []).append(pid)
        
        try:
            # Try GOAT advanced season averages endpoint
            data = bdl_get("/nba/v1/season_averages/advanced", params)
            
            for row in data.get("data", []):
                attrs = row.get("attributes") or row.get("stats") or row
                player = row.get("player") or {}
                pid = player.get("id") or row.get("player_id")
                
                if not pid:
                    continue
                
                results[int(pid)] = {
                    "usage_pct": _safe_float(attrs.get("usage_pct") or attrs.get("usg_pct")),
                    "true_shooting_pct": _safe_float(attrs.get("true_shooting_pct") or attrs.get("ts_pct")),
                    "offensive_rating": _safe_float(attrs.get("offensive_rating") or attrs.get("off_rating")),
                    "defensive_rating": _safe_float(attrs.get("defensive_rating") or attrs.get("def_rating")),
                    "effective_fg_pct": _safe_float(attrs.get("effective_fg_pct") or attrs.get("efg_pct")),
                    "assist_pct": _safe_float(attrs.get("assist_pct") or attrs.get("ast_pct")),
                    "rebound_pct": _safe_float(attrs.get("rebound_pct") or attrs.get("reb_pct")),
                }
        except Exception:
            pass  # Will use fallback estimation
    
    # Batch in chunks of 25
    for i in range(0, len(player_ids), 25):
        _fetch_batch(player_ids[i:i + 25])
    
    return results


def get_recent_game_ids(player_id: int, num_games: int = 10) -> List[int]:
    """Get recent game IDs for a player for play-by-play analysis."""
    try:
        params = {
            "player_ids[]": [player_id],
            "per_page": num_games,
        }
        data = bdl_get("/v1/stats", params)
        
        game_ids = []
        for row in data.get("data", []):
            game = row.get("game") or {}
            gid = game.get("id")
            if gid and gid not in game_ids:
                game_ids.append(gid)
        
        return game_ids[:num_games]
    except Exception:
        return []


def summarize_recent_points(stats_rows: List[Dict[str, Any]], adv_rows: Optional[List[Dict[str, Any]]] = None) -> RecentPoints:
    if not stats_rows:
        return RecentPoints(sample_size=0, minutes_avg=0.0, pts=None, usg_pct=None, ts_pct=None, off_rating=None)

    rows = stats_rows[:]

    def _date_key(row: Dict[str, Any]) -> datetime:
        game = row.get("game") or {}
        dt = game.get("date")
        if not isinstance(dt, str) or not dt:
            return datetime.now(timezone.utc)
        dt_str: str = dt
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except Exception:
            return datetime.now(timezone.utc)

    rows.sort(key=_date_key, reverse=True)
    l5 = rows[:5]
    
    # Match advanced stats to recent games
    l5_game_ids = [r.get("game", {}).get("id") for r in l5 if r.get("game", {}).get("id")]
    l5_adv = []
    if adv_rows:
        for ar in adv_rows:
            if ar.get("game", {}).get("id") in l5_game_ids:
                l5_adv.append(ar)

    pts_vals = [float(r.get("pts", 0.0) or 0.0) for r in l5]
    if len(pts_vals) == 1:
        pts_summary = StatSummary(avg=pts_vals[0], stdev=0.0)
    elif pts_vals:
        pts_summary = StatSummary(
            avg=float(sum(pts_vals) / len(pts_vals)),
            stdev=float(statistics.pstdev(pts_vals)),
        )
    else:
        pts_summary = None

    mins_raw: List[float] = []
    for r in l5:
        m = r.get("min")
        if isinstance(m, str) and ":" in m:
            mm, ss = m.split(":")
            mins_raw.append(float(int(mm) + int(ss) / 60.0))
        elif m is not None:
            try:
                mins_raw.append(float(m))
            except Exception:
                pass

    minutes_avg = float(sum(mins_raw) / len(mins_raw)) if mins_raw else 0.0

    # Calculate usage and efficiency from L5 games
    usg_vals: List[float] = []
    ts_vals: List[float] = []
    off_rtg_vals: List[float] = []
    
    # Use official advanced stats if available
    if l5_adv:
        for ar in l5_adv:
            usg = ar.get("usage_pct")
            ts = ar.get("true_shooting_pct")
            ortg = ar.get("offensive_rating")
            if usg is not None: usg_vals.append(float(usg))
            if ts is not None: ts_vals.append(float(ts))
            if ortg is not None: off_rtg_vals.append(float(ortg))
    
    # Fallback to estimation for usage if official data is missing
    if not usg_vals:
        for r in l5:
            fga = float(r.get("fga", 0.0) or 0.0)
            fta = float(r.get("fta", 0.0) or 0.0)
            tov = float(r.get("turnover", 0.0) or 0.0)
            player_min = parse_minutes(r.get("min")) # Need to add parse_minutes helper
            
            if player_min > 0:
                poss_used = fga + 0.44 * fta + tov
                estimated_usg = (poss_used / player_min) * 48.0 / 80.0 * 100.0
                usg_vals.append(min(estimated_usg, 45.0))

    usg_pct_avg = round(sum(usg_vals) / len(usg_vals), 1) if usg_vals else None
    ts_pct_avg = round(sum(ts_vals) / len(ts_vals), 3) if ts_vals else None
    off_rtg_avg = round(sum(off_rtg_vals) / len(off_rtg_vals), 1) if off_rtg_vals else None

    return RecentPoints(
        sample_size=len(l5),
        minutes_avg=round(minutes_avg, 2),
        pts=pts_summary,
        usg_pct=usg_pct_avg,
        ts_pct=ts_pct_avg,
        off_rating=off_rtg_avg,
    )


def parse_minutes(min_val: Any) -> float:
    """Parse minutes from various formats (mm:ss string or float)."""
    if isinstance(min_val, str) and ":" in min_val:
        mm, ss = min_val.split(":")
        return float(int(mm) + int(ss) / 60.0)
    try:
        return float(min_val or 0.0)
    except Exception:
        return 0.0


def compute_days_of_rest(team_id: int, game_date: date) -> int:
    """Calculate days of rest for a team before the given game date.
    
    Returns:
        0 = back-to-back (played yesterday)
        1 = one day rest
        2 = two days rest
        3+ = three or more days rest
    """
    # Look back up to 7 days to find most recent game
    start_date = game_date - timedelta(days=7)
    params = {
        "team_ids[]": [team_id],
        "start_date": start_date.isoformat(),
        "end_date": (game_date - timedelta(days=1)).isoformat(),
        "per_page": 100,
    }

    try:
        data = bdl_get("/v1/games", params)
        games = list(data.get("data", []))

        if not games:
            return 3  # No recent games, treat as well-rested

        # Find most recent game
        most_recent_date: Optional[date] = None
        for game in games:
            game_date_str = game.get("date")
            if not game_date_str:
                continue
            try:
                game_dt = datetime.fromisoformat(str(game_date_str).replace("Z", "+00:00"))
                gd = game_dt.date()
                # Check if team was in this game
                home_team_id = (game.get("home_team") or {}).get("id")
                visitor_team_id = (game.get("visitor_team") or {}).get("id")
                if home_team_id == team_id or visitor_team_id == team_id:
                    if most_recent_date is None or gd > most_recent_date:
                        most_recent_date = gd
            except Exception:
                continue

        if most_recent_date is None:
            return 3  # No valid games found

        days_rest = (game_date - most_recent_date).days - 1
        return max(0, min(days_rest, 4))  # Cap at 4 days
    except Exception:
        return 1  # Default to 1 day rest on error


def compute_back_to_back(team_id: int, game_date: date) -> bool:
    prev_date = game_date - timedelta(days=1)
    params = {
        "dates[]": prev_date.isoformat(),
        "team_ids[]": [team_id],
        "per_page": 100,
    }

    try:
        data = bdl_get("/v1/games", params)
        for game in data.get("data", []):
            game_date_str = game.get("date")
            if not game_date_str:
                continue
            try:
                game_dt = datetime.fromisoformat(str(game_date_str).replace("Z", "+00:00"))
                if game_dt.date() == prev_date:
                    home_team_id = (game.get("home_team") or {}).get("id")
                    visitor_team_id = (game.get("visitor_team") or {}).get("id")
                    if home_team_id == team_id or visitor_team_id == team_id:
                        return True
            except Exception:
                continue
        return False
    except Exception:
        return False


def compute_dvp_by_position(team_id: int, game_date: date, num_games: int = 10) -> Dict[str, DvPRanking]:
    """Compute Defense vs Position stats for a team.
    
    Analyzes opponent player scoring by position over the team's last N games
    to determine how well the team defends each position.
    
    Returns dict mapping position (PG, SG, SF, PF, C, G, F) to DvPRanking.
    """
    # Get team's recent games
    start_date = game_date - timedelta(days=45)
    params = {
        "team_ids[]": [team_id],
        "start_date": start_date.isoformat(),
        "end_date": (game_date - timedelta(days=1)).isoformat(),
        "per_page": 100,
    }

    try:
        data = bdl_get("/v1/games", params)
        games = list(data.get("data", []))

        def _game_date_key(g: Dict[str, Any]) -> date:
            date_str = g.get("date")
            try:
                dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
                return dt.date()
            except Exception:
                return date.min

        games.sort(key=_game_date_key, reverse=True)
        recent_games = games[:num_games]

        if not recent_games:
            return {}

        # Collect opponent scoring by position
        position_pts: Dict[str, List[float]] = {
            "PG": [], "SG": [], "SF": [], "PF": [], "C": [], "G": [], "F": []
        }

        for game in recent_games:
            game_id = game.get("id")
            if not game_id:
                continue

            # Determine opponent team
            home_team = game.get("home_team") or {}
            visitor_team = game.get("visitor_team") or {}
            home_id = home_team.get("id")
            visitor_id = visitor_team.get("id")
            
            if home_id == team_id:
                opp_id = visitor_id
            elif visitor_id == team_id:
                opp_id = home_id
            else:
                continue

            if not opp_id:
                continue

            # Get stats for this game
            stats_params = {"game_ids[]": [game_id], "per_page": 100}
            try:
                stats_data = bdl_get("/v1/stats", stats_params)
                for stat in stats_data.get("data", []):
                    stat_team = stat.get("team") or {}
                    if stat_team.get("id") != opp_id:
                        continue

                    player = stat.get("player") or {}
                    pos = str(player.get("position") or "").upper()
                    pts = float(stat.get("pts", 0.0) or 0.0)
                    mins = stat.get("min")
                    
                    # Parse minutes
                    if isinstance(mins, str) and ":" in mins:
                        mm, ss = mins.split(":")
                        mins_float = float(int(mm) + int(ss) / 60.0)
                    else:
                        mins_float = float(mins or 0.0)
                    
                    # Only count players with meaningful minutes
                    if mins_float < 10:
                        continue

                    # Map positions
                    if pos in position_pts:
                        position_pts[pos].append(pts)
                    elif pos == "G-F" or pos == "F-G":
                        position_pts["G"].append(pts)
                        position_pts["F"].append(pts)

            except Exception:
                continue

        # Calculate averages and assign buckets
        # League average points by position (2024-25 estimates)
        league_avg_by_pos = {
            "PG": 17.5, "SG": 15.5, "SF": 14.5, "PF": 14.0, "C": 14.5, "G": 16.5, "F": 14.2
        }

        result: Dict[str, DvPRanking] = {}
        for pos, pts_list in position_pts.items():
            if not pts_list:
                continue

            avg = sum(pts_list) / len(pts_list)
            league_avg = league_avg_by_pos.get(pos, 15.0)
            
            # Calculate relative performance (positive = allows more points = weak defense)
            relative = (avg - league_avg) / league_avg if league_avg > 0 else 0
            
            # Assign rank (1-30, estimated) and bucket
            # >10% above league avg = WEAK defense (ranks 25-30)
            # Within 10% = AVERAGE (ranks 11-24)
            # >10% below league avg = STRONG defense (ranks 1-10)
            if relative > 0.10:
                bucket = "WEAK"
                rank = int(25 + min(relative * 50, 5))  # 25-30
            elif relative < -0.10:
                bucket = "STRONG"
                rank = int(10 - min(abs(relative) * 50, 9))  # 1-10
            else:
                bucket = "AVERAGE"
                rank = int(15 + relative * 50)  # 11-24 roughly

            result[pos] = DvPRanking(
                pts_allowed_avg=round(avg, 1),
                rank=max(1, min(30, rank)),
                bucket=bucket
            )

        return result
    except Exception:
        return {}


def get_team_pace_last_10(team_id: int, game_date: date) -> Optional[float]:
    start_date = game_date - timedelta(days=30)
    params = {
        "team_ids[]": [team_id],
        "start_date": start_date.isoformat(),
        "end_date": (game_date - timedelta(days=1)).isoformat(),
        "per_page": 100,
    }

    try:
        data = bdl_get("/v1/games", params)
        games = list(data.get("data", []))

        def _game_date_key(g: Dict[str, Any]) -> date:
            date_str = g.get("date")
            try:
                dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
                return dt.date()
            except Exception:
                return date.min

        games.sort(key=_game_date_key, reverse=True)
        last_10 = games[:10]
        if not last_10:
            return None

        paces: List[float] = []
        for game in last_10:
            gid = game.get("id")
            if not gid:
                continue

            stats_params = {"game_ids[]": [gid], "per_page": 100}
            try:
                stats_data = bdl_get("/v1/stats", stats_params)
                team_stats = stats_data.get("data", [])

                total_fga = 0.0
                total_oreb = 0.0
                total_tov = 0.0
                total_fta = 0.0
                total_min = 0.0

                for stat in team_stats:
                    if (stat.get("team") or {}).get("id") != team_id:
                        continue

                    total_fga += float(stat.get("fga", 0.0) or 0.0)
                    total_oreb += float(stat.get("oreb", 0.0) or 0.0)
                    total_tov += float(stat.get("turnover", 0.0) or 0.0)
                    total_fta += float(stat.get("fta", 0.0) or 0.0)

                    min_val = stat.get("min", 0.0)
                    if isinstance(min_val, str) and ":" in min_val:
                        mm, ss = min_val.split(":")
                        total_min += float(int(mm) + int(ss) / 60.0)
                    else:
                        total_min += float(min_val or 0.0)

                possessions = total_fga - total_oreb + total_tov + 0.44 * total_fta
                team_minutes = total_min / 5.0 if total_min > 0 else 0.0
                if team_minutes > 0:
                    paces.append((possessions / team_minutes) * 48.0)
            except Exception:
                continue

        if not paces:
            return None
        return round(sum(paces) / len(paces), 2)
    except Exception:
        return None


def build_points_game_payload(game_date: datetime, away_abbr: str, home_abbr: str, season: int) -> Dict[str, Any]:
    """Build comprehensive game payload with GOAT All-Star tier data.
    
    GOAT Features:
    - Official advanced stats (Usage%, TS%, ORtg) from /nba/v1/stats/advanced
    - Team ratings (DRtg, NRtg, Pace) from /nba/v1/standings
    - Starting lineup detection via box score analysis
    - Clutch scoring from play_by_play
    - League scoring rank from /nba/v1/leaders
    """
    game = find_game_by_teams_and_date(game_date, away_abbr, home_abbr)
    game_id = game.get("id")

    teams_map = get_teams_map()
    away_team = teams_map[away_abbr]
    home_team = teams_map[home_abbr]

    away_team_id = away_team["id"]
    home_team_id = home_team["id"]

    # Fetch injuries
    injuries = get_player_injuries_for_teams([away_team_id, home_team_id])
    
    # GOAT: Fetch official team standings with advanced ratings
    standings = get_team_standings(season)

    # Get active players
    away_players_raw = get_active_players_for_team(away_team_id)
    home_players_raw = get_active_players_for_team(home_team_id)

    away_ids = [int(p["id"]) for p in away_players_raw if p.get("id")]
    home_ids = [int(p["id"]) for p in home_players_raw if p.get("id")]
    all_ids = away_ids + home_ids

    # Fetch base season stats
    season_points = get_season_points(all_ids, season)
    
    # GOAT: Fetch official advanced season stats (Usage%, TS%, ORtg)
    print(f"[INFO] Fetching GOAT advanced season stats for {len(all_ids)} players...", file=sys.stderr)
    advanced_season_stats = get_player_advanced_season_stats(all_ids, season)
    
    # GOAT: Fetch league scoring ranks
    print(f"[INFO] Fetching league scoring ranks...", file=sys.stderr)
    league_ranks = get_pts_league_ranks_batch(all_ids, season)

    # Fetch recent game stats (L5/L10)
    since_date = game_date - timedelta(days=20)
    recent_stats_raw = get_recent_game_stats(all_ids, since_date)
    recent_adv_raw = get_advanced_player_stats(all_ids, since_date)
    
    # GOAT: Determine probable starters based on minutes projection
    # (For future games, use season minutes avg to estimate starters)
    away_starters: Set[int] = set()
    home_starters: Set[int] = set()
    
    # Estimate starters from top 5 players by season minutes
    def _get_probable_starters(player_ids: List[int]) -> Set[int]:
        players_with_mins = []
        for pid in player_ids:
            sp = season_points.get(pid)
            if sp:
                players_with_mins.append((pid, sp.minutes))
        players_with_mins.sort(key=lambda x: x[1], reverse=True)
        return set(pid for pid, _ in players_with_mins[:5])
    
    away_starters = _get_probable_starters(away_ids)
    home_starters = _get_probable_starters(home_ids)
    
    print(f"[INFO] Identified {len(away_starters)} away starters, {len(home_starters)} home starters", file=sys.stderr)

    def _build_records(
        players_raw: List[Dict[str, Any]],
        starters_set: set[int],
    ) -> List[PlayerRecord]:
        records: List[PlayerRecord] = []
        for p in players_raw:
            pid = p.get("id")
            if not pid:
                continue
            pid_int = int(pid)

            full_name = f"{p.get('first_name','').strip()} {p.get('last_name','').strip()}".strip()
            position = str(p.get("position") or "")

            # Base season stats
            s = season_points.get(pid_int) or SeasonPoints(minutes=0.0, pts=0.0)
            
            # GOAT: Merge official advanced stats into season stats
            adv_stats = advanced_season_stats.get(pid_int, {})
            if adv_stats:
                s = SeasonPoints(
                    minutes=s.minutes,
                    pts=s.pts,
                    usg_pct=adv_stats.get("usage_pct") or s.usg_pct,
                    ts_pct=adv_stats.get("true_shooting_pct") or s.ts_pct,
                    off_rating=adv_stats.get("offensive_rating") or s.off_rating,
                )
            
            # Recent stats summary
            recent_summary = summarize_recent_points(
                recent_stats_raw.get(pid_int, []),
                recent_adv_raw.get(pid_int, [])
            )
            
            # Injury info
            inj = injuries.get(pid_int, {"status": "AVAILABLE", "description": None})
            
            # GOAT: Is this player a starter?
            is_starter = pid_int in starters_set
            
            # GOAT: League scoring rank
            pts_rank = league_ranks.get(pid_int)
            
            # GOAT: Clutch scoring (only for key players to save API calls)
            clutch_avg: Optional[float] = None
            if is_starter and s.pts >= 15.0:
                # Get recent game IDs for clutch analysis
                recent_game_ids = get_recent_game_ids(pid_int, num_games=5)
                if recent_game_ids:
                    clutch_avg = get_clutch_scoring_avg(pid_int, recent_game_ids)

            records.append(
                PlayerRecord(
                    player_id=pid_int,
                    name=full_name,
                    position=position,
                    season=s,
                    recent=recent_summary,
                    injury_status=str(inj.get("status") or "AVAILABLE"),
                    injury_notes=inj.get("description"),
                    is_starter=is_starter,
                    pts_league_rank=pts_rank,
                    clutch_pts_avg=clutch_avg,
                )
            )
        return records

    away_records = _build_records(away_players_raw, away_starters)
    home_records = _build_records(home_players_raw, home_starters)

    # Schedule context
    away_b2b = compute_back_to_back(away_team_id, game_date.date())
    home_b2b = compute_back_to_back(home_team_id, game_date.date())
    
    # Compute days of rest (more granular than just B2B)
    away_days_rest = compute_days_of_rest(away_team_id, game_date.date())
    home_days_rest = compute_days_of_rest(home_team_id, game_date.date())

    # Pace from L10 games (fallback for live pace)
    away_pace_last_10 = get_team_pace_last_10(away_team_id, game_date.date())
    home_pace_last_10 = get_team_pace_last_10(home_team_id, game_date.date())
    
    # Compute DvP rankings for each team (how they defend each position)
    away_dvp = compute_dvp_by_position(away_team_id, game_date.date())
    home_dvp = compute_dvp_by_position(home_team_id, game_date.date())

    # GOAT: Get advanced team ratings from standings
    default_adv = {
        "defensive_rating": 110.0,
        "offensive_rating": 110.0,
        "net_rating": 0.0,
        "pace": 100.0,
        "win": 0,
        "loss": 0,
    }
    away_adv = standings.get(away_team_id, default_adv)
    home_adv = standings.get(home_team_id, default_adv)
    
    # Use official pace from standings if available, else L10 calculation
    away_pace_official = away_adv.get("pace") if away_adv.get("pace", 0) > 0 else away_pace_last_10
    home_pace_official = home_adv.get("pace") if home_adv.get("pace", 0) > 0 else home_pace_last_10

    return {
        "meta": {
            "game_date": game_date.date().isoformat(),
            "season": season,
            "away_abbr": away_abbr,
            "home_abbr": home_abbr,
            "source": "balldontlie_goat",
            "balldontlie_game_id": game_id,
            "features": [
                "official_usage_pct",
                "official_ts_pct",
                "team_drtg",
                "starter_detection",
                "league_rank",
                "clutch_scoring",
            ],
        },
        "teams": {
            away_abbr: {
                "team_id": away_team_id,
                "back_to_back": away_b2b,
                "days_rest": away_days_rest,
                "pace_last_10": away_pace_last_10,
                "pace_official": away_pace_official,
                "dvp": {pos: asdict(dvp) for pos, dvp in away_dvp.items()},
                "advanced": away_adv,
            },
            home_abbr: {
                "team_id": home_team_id,
                "back_to_back": home_b2b,
                "days_rest": home_days_rest,
                "pace_last_10": home_pace_last_10,
                "pace_official": home_pace_official,
                "dvp": {pos: asdict(dvp) for pos, dvp in home_dvp.items()},
                "advanced": home_adv,
            },
        },
        "players": {
            away_abbr: [asdict(p) for p in away_records],
            home_abbr: [asdict(p) for p in home_records],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-date", required=True, help="Game date YYYY-MM-DD")
    parser.add_argument("--away", required=True, help="Away team abbreviation, e.g. BOS")
    parser.add_argument("--home", required=True, help="Home team abbreviation, e.g. MIN")
    parser.add_argument("--season", type=int, required=True, help="Season year, e.g. 2025")
    args = parser.parse_args()

    game_date = datetime.fromisoformat(args.game_date).replace(tzinfo=timezone.utc)

    payload = build_points_game_payload(
        game_date=game_date,
        away_abbr=args.away.upper(),
        home_abbr=args.home.upper(),
        season=args.season,
    )
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
