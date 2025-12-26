#!/usr/bin/env python3
"""fetch_master_data_v3.py

Pro Parlay Syndicate v3.0 - Master Data Fetcher

Fetches comprehensive NBA game data for ALL stat categories across the full daily slate.

STAT CATEGORIES:
- PTS: Points (existing GOAT logic)
- REB: Rebounds (with reb_pct, position bonus)
- AST: Assists (with ast_pct, playmaker bonus)
- 3PM: Three-pointers made (with 3P%, volume)
- PRA: Computed from P+R+A distributions

MODES:
- Single game: --date YYYY-MM-DD --away XXX --home XXX
- Full slate: --date YYYY-MM-DD (scans all games)

Usage:
  # Full slate mode (for parlay optimizer)
  python3 fetch_master_data_v3.py --date 2025-01-15 --season 2025

  # Single game mode
  python3 fetch_master_data_v3.py --date 2025-01-15 --away BOS --home MIN --season 2025

Output:
  JSON with structure:
  {
    "meta": {...},
    "games": [
      {
        "game_id": 12345,
        "away_abbr": "BOS",
        "home_abbr": "MIN",
        "teams": {...},
        "players": {...}
      }
    ]
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
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

# Load .env file if it exists
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if "=" in line:
                key, value = line.strip().split("=", 1)
                os.environ[key] = value.strip("'").strip('"')


BALDONTLIE_BASE_V1 = "https://api.balldontlie.io/v1"
BALDONTLIE_BASE_NBA_V1 = "https://api.balldontlie.io/nba/v1"
BALDONTLIE_BASE_V2 = "https://api.balldontlie.io/v2"

# Rate limiter
_REQUEST_WINDOW_SEC = 60
_MAX_REQUESTS_PER_WINDOW = 50
_REQUEST_TIMES: deque = deque()


def bdl_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Thin wrapper over BallDontLie HTTP GET with rate limiting."""
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
        resp = requests.get(url, headers=headers, params=params, timeout=15)
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


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class StatSummary:
    avg: float
    stdev: float


@dataclass
class MultiStatRecent:
    """Recent stats for all categories."""
    sample_size: int
    minutes_avg: float
    pts: Optional[StatSummary]
    reb: Optional[StatSummary]
    ast: Optional[StatSummary]
    fg3m: Optional[StatSummary]
    # Advanced metrics
    usg_pct: Optional[float] = None
    ts_pct: Optional[float] = None
    reb_pct: Optional[float] = None
    ast_pct: Optional[float] = None
    fg3_pct: Optional[float] = None


@dataclass
class MultiStatSeason:
    """Season averages for all categories."""
    minutes: float
    pts: float
    reb: float
    ast: float
    fg3m: float
    # Advanced metrics
    usg_pct: Optional[float] = None
    ts_pct: Optional[float] = None
    reb_pct: Optional[float] = None
    ast_pct: Optional[float] = None
    fg3_pct: Optional[float] = None
    off_rating: Optional[float] = None


@dataclass
class PlayerRecord:
    """Complete player record with multi-stat support."""
    player_id: int
    name: str
    position: str
    team_abbr: str
    season: MultiStatSeason
    recent: MultiStatRecent
    injury_status: str
    injury_notes: Optional[str]
    is_starter: bool = False
    pts_league_rank: Optional[int] = None
    reb_league_rank: Optional[int] = None
    ast_league_rank: Optional[int] = None


@dataclass
class TeamAdvanced:
    """Team advanced stats from standings."""
    defensive_rating: float
    offensive_rating: float
    net_rating: float
    pace: float
    win: int
    loss: int
    opp_3p_pct: Optional[float] = None  # Opponent 3P% allowed


@dataclass
class DvPRanking:
    """Defense vs Position ranking."""
    stat_allowed_avg: float
    rank: int
    bucket: str  # WEAK, AVERAGE, STRONG


@dataclass
class GameData:
    """Complete data for a single game."""
    game_id: int
    game_date: str
    away_abbr: str
    home_abbr: str
    away_team_id: int
    home_team_id: int
    teams: Dict[str, Dict[str, Any]]
    players: Dict[str, List[Dict[str, Any]]]


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def parse_minutes(min_val: Any) -> float:
    """Parse minutes from various formats."""
    if isinstance(min_val, str) and ":" in min_val:
        mm, ss = min_val.split(":")
        return float(int(mm) + int(ss) / 60.0)
    try:
        return float(min_val or 0.0)
    except Exception:
        return 0.0


def get_teams_map() -> Dict[str, Dict[str, Any]]:
    """Get mapping of team abbreviations to team data."""
    data = bdl_get("/v1/teams", {})
    teams: Dict[str, Dict[str, Any]] = {}
    for t in data.get("data", []):
        abbr = str(t.get("abbreviation") or "").upper()
        if not abbr:
            continue
        if abbr in teams:
            current_has_conf = bool(teams[abbr].get("conference", "").strip())
            new_has_conf = bool(t.get("conference", "").strip())
            if not current_has_conf and new_has_conf:
                teams[abbr] = t
        else:
            teams[abbr] = t
    return teams


# ============================================================================
# GAME FETCHING
# ============================================================================

def fetch_games_on_date(game_date: date) -> List[Dict[str, Any]]:
    """Fetch all NBA games scheduled for a specific date."""
    params = {
        "dates[]": game_date.isoformat(),
        "per_page": 100,
    }
    data = bdl_get("/v1/games", params)
    return list(data.get("data", []))


def find_game_by_teams(
    games: List[Dict[str, Any]],
    away_abbr: str,
    home_abbr: str,
) -> Optional[Dict[str, Any]]:
    """Find specific game by team abbreviations."""
    for g in games:
        home_team = g.get("home_team") or {}
        away_team = g.get("visitor_team") or {}
        if (
            str(home_team.get("abbreviation") or "").upper() == home_abbr
            and str(away_team.get("abbreviation") or "").upper() == away_abbr
        ):
            return g
    return None


# ============================================================================
# PLAYER DATA FETCHING
# ============================================================================

def get_active_players_for_team(team_id: int) -> List[Dict[str, Any]]:
    """Get active players for a team."""
    params = {"team_ids[]": [team_id], "per_page": 100}
    data = bdl_get("/v1/players/active", params)
    return list(data.get("data", []))


def get_player_injuries_for_teams(team_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """Fetch player injuries for specified teams."""
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
        print(f"[WARN] Failed to fetch injuries: {e}", file=sys.stderr)
        return {}


def get_season_multi_stats(
    player_ids: List[int],
    season: int,
) -> Dict[int, MultiStatSeason]:
    """Fetch season averages for all stat categories."""
    if not player_ids:
        return {}

    def _fetch_batched(
        endpoint: str,
        base_params: Dict[str, Any],
        ids: List[int],
        chunk_size: int = 25,
    ) -> List[Dict[str, Any]]:
        all_rows: List[Dict[str, Any]] = []
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i : i + chunk_size]
            params = dict(base_params)
            for pid in chunk:
                params.setdefault("player_ids[]", []).append(pid)
            try:
                resp = bdl_get(endpoint, params)
                all_rows.extend(resp.get("data", []))
            except Exception as e:
                print(f"[WARN] Batch fetch failed: {e}", file=sys.stderr)
        return all_rows

    def _parse_rows(rows: List[Dict[str, Any]]) -> Dict[int, MultiStatSeason]:
        out: Dict[int, MultiStatSeason] = {}
        for row in rows:
            if "stats" in row:
                stats = row.get("stats") or {}
                pid = (row.get("player") or {}).get("id")
            else:
                stats = row
                pid = row.get("player_id")
            if not pid:
                continue

            out[int(pid)] = MultiStatSeason(
                minutes=round(parse_minutes(stats.get("min")), 2),
                pts=_safe_float(stats.get("pts")),
                reb=_safe_float(stats.get("reb")),
                ast=_safe_float(stats.get("ast")),
                fg3m=_safe_float(stats.get("fg3m")),
                usg_pct=_safe_float(stats.get("usage_pct")) if stats.get("usage_pct") else None,
                ts_pct=_safe_float(stats.get("true_shooting_pct")) if stats.get("true_shooting_pct") else None,
                reb_pct=_safe_float(stats.get("reb_pct")) if stats.get("reb_pct") else None,
                ast_pct=_safe_float(stats.get("ast_pct")) if stats.get("ast_pct") else None,
                fg3_pct=_safe_float(stats.get("fg3_pct")) if stats.get("fg3_pct") else None,
                off_rating=_safe_float(stats.get("offensive_rating")) if stats.get("offensive_rating") else None,
            )
        return out

    # Try advanced season averages first
    try:
        rows = _fetch_batched(
            "/nba/v1/season_averages/advanced",
            {"season": season, "season_type": "regular"},
            player_ids,
        )
        if rows:
            return _parse_rows(rows)
    except Exception:
        pass

    # Fallback to general
    try:
        rows = _fetch_batched(
            "/nba/v1/season_averages/general",
            {"season": season, "season_type": "regular", "type": "base"},
            player_ids,
        )
        return _parse_rows(rows)
    except Exception:
        pass

    # Fallback to legacy
    try:
        rows = _fetch_batched("/v1/season_averages", {"season": season}, player_ids)
        return _parse_rows(rows)
    except Exception as e:
        print(f"[WARN] All season stats endpoints failed: {e}", file=sys.stderr)
        return {}


def get_recent_game_stats(
    player_ids: List[int],
    since_date: datetime,
) -> Dict[int, List[Dict[str, Any]]]:
    """Fetch recent game-by-game stats for players.

    Uses batched requests (many player_ids per call) to avoid taking
    10+ minutes on a full slate.
    """
    out: Dict[int, List[Dict[str, Any]]] = {pid: [] for pid in player_ids}
    if not player_ids:
        return out

    start_date = since_date.date().isoformat()

    chunk_size = 25
    per_page = 100
    max_pages = 6

    for i in range(0, len(player_ids), chunk_size):
        chunk = player_ids[i : i + chunk_size]

        # Paginate because 25 players x ~5 games can exceed 100 rows.
        for page in range(1, max_pages + 1):
            params: Dict[str, Any] = {
                "start_date": start_date,
                "per_page": per_page,
                "page": page,
            }
            params["player_ids[]"] = list(chunk)

            try:
                data = bdl_get("/v1/stats", params)
            except Exception:
                break

            rows = list(data.get("data", []) or [])
            if not rows:
                break

            for row in rows:
                player = row.get("player") or {}
                pid = player.get("id") or row.get("player_id")
                if not pid:
                    continue
                pid_int = int(pid)
                if pid_int in out:
                    out[pid_int].append(row)

            if len(rows) < per_page:
                break

    return out


def summarize_recent_multi_stats(stats_rows: List[Dict[str, Any]]) -> MultiStatRecent:
    """Summarize recent stats for all categories."""
    if not stats_rows:
        return MultiStatRecent(
            sample_size=0, minutes_avg=0.0,
            pts=None, reb=None, ast=None, fg3m=None,
        )

    # Sort by date, newest first
    def _date_key(row: Dict[str, Any]) -> datetime:
        game = row.get("game") or {}
        dt = game.get("date")
        if not isinstance(dt, str) or not dt:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            return datetime.now(timezone.utc)

    rows = sorted(stats_rows, key=_date_key, reverse=True)[:5]

    def _compute_summary(key: str) -> Optional[StatSummary]:
        vals = [_safe_float(r.get(key)) for r in rows]
        if not vals:
            return None
        if len(vals) == 1:
            return StatSummary(avg=vals[0], stdev=0.0)
        return StatSummary(
            avg=round(sum(vals) / len(vals), 2),
            stdev=round(statistics.pstdev(vals), 2),
        )

    # Parse minutes
    mins_raw = [parse_minutes(r.get("min")) for r in rows]
    minutes_avg = round(sum(mins_raw) / len(mins_raw), 2) if mins_raw else 0.0

    # Compute usage estimate
    usg_vals: List[float] = []
    fg3_pcts: List[float] = []
    
    for r in rows:
        fga = _safe_float(r.get("fga"))
        fta = _safe_float(r.get("fta"))
        tov = _safe_float(r.get("turnover"))
        player_min = parse_minutes(r.get("min"))
        fg3a = _safe_float(r.get("fg3a"))
        fg3m = _safe_float(r.get("fg3m"))
        
        if player_min > 0:
            poss_used = fga + 0.44 * fta + tov
            estimated_usg = (poss_used / player_min) * 48.0 / 80.0 * 100.0
            usg_vals.append(min(estimated_usg, 45.0))
        
        if fg3a > 0:
            fg3_pcts.append(fg3m / fg3a)

    return MultiStatRecent(
        sample_size=len(rows),
        minutes_avg=minutes_avg,
        pts=_compute_summary("pts"),
        reb=_compute_summary("reb"),
        ast=_compute_summary("ast"),
        fg3m=_compute_summary("fg3m"),
        usg_pct=round(sum(usg_vals) / len(usg_vals), 1) if usg_vals else None,
        fg3_pct=round(sum(fg3_pcts) / len(fg3_pcts), 3) if fg3_pcts else None,
    )


# ============================================================================
# TEAM DATA FETCHING
# ============================================================================

def get_team_standings(season: int) -> Dict[int, Dict[str, Any]]:
    """Fetch team standings with advanced ratings."""
    try:
        params = {"season": season}
        data = bdl_get("/nba/v1/standings", params)
        standings: Dict[int, Dict[str, Any]] = {}
        
        for s in data.get("data", []):
            attrs = s.get("attributes") or s
            team_info = attrs.get("team") or {}
            tid = team_info.get("id") or s.get("id")
            
            if tid:
                standings[int(tid)] = {
                    "defensive_rating": _safe_float(attrs.get("defensive_rating") or attrs.get("def_rating"), 110.0),
                    "offensive_rating": _safe_float(attrs.get("offensive_rating") or attrs.get("off_rating"), 110.0),
                    "net_rating": _safe_float(attrs.get("net_rating"), 0.0),
                    "pace": _safe_float(attrs.get("pace"), 100.0),
                    "win": int(attrs.get("win") or attrs.get("wins") or 0),
                    "loss": int(attrs.get("loss") or attrs.get("losses") or 0),
                }
        return standings
    except Exception as e:
        print(f"[WARN] Failed to fetch standings: {e}", file=sys.stderr)
        return {}


def compute_days_of_rest(team_id: int, game_date: date) -> int:
    """Calculate days of rest for a team."""
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
            return 3

        most_recent_date: Optional[date] = None
        for game in games:
            game_date_str = game.get("date")
            if not game_date_str:
                continue
            try:
                game_dt = datetime.fromisoformat(str(game_date_str).replace("Z", "+00:00"))
                gd = game_dt.date()
                home_team_id = (game.get("home_team") or {}).get("id")
                visitor_team_id = (game.get("visitor_team") or {}).get("id")
                if home_team_id == team_id or visitor_team_id == team_id:
                    if most_recent_date is None or gd > most_recent_date:
                        most_recent_date = gd
            except Exception:
                continue

        if most_recent_date is None:
            return 3
        days_rest = (game_date - most_recent_date).days - 1
        return max(0, min(days_rest, 4))
    except Exception:
        return 1


def get_team_pace_last_10(team_id: int, game_date: date) -> Optional[float]:
    """Compute team's average pace over last 10 games."""
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
                total_fga = 0.0
                total_oreb = 0.0
                total_tov = 0.0
                total_fta = 0.0
                total_min = 0.0

                for stat in stats_data.get("data", []):
                    if (stat.get("team") or {}).get("id") != team_id:
                        continue
                    total_fga += _safe_float(stat.get("fga"))
                    total_oreb += _safe_float(stat.get("oreb"))
                    total_tov += _safe_float(stat.get("turnover"))
                    total_fta += _safe_float(stat.get("fta"))
                    total_min += parse_minutes(stat.get("min"))

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


def compute_dvp_multi_stat(
    team_id: int,
    game_date: date,
    num_games: int = 10,
) -> Dict[str, Dict[str, DvPRanking]]:
    """Compute Defense vs Position for all stat categories."""
    start_date = game_date - timedelta(days=45)
    params = {
        "team_ids[]": [team_id],
        "start_date": start_date.isoformat(),
        "end_date": (game_date - timedelta(days=1)).isoformat(),
        "per_page": 100,
    }

    result: Dict[str, Dict[str, DvPRanking]] = {
        "pts": {}, "reb": {}, "ast": {}, "fg3m": {}
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
            return result

        # Collect stats by position for each category
        position_stats: Dict[str, Dict[str, List[float]]] = {
            "pts": {"PG": [], "SG": [], "SF": [], "PF": [], "C": [], "G": [], "F": []},
            "reb": {"PG": [], "SG": [], "SF": [], "PF": [], "C": [], "G": [], "F": []},
            "ast": {"PG": [], "SG": [], "SF": [], "PF": [], "C": [], "G": [], "F": []},
            "fg3m": {"PG": [], "SG": [], "SF": [], "PF": [], "C": [], "G": [], "F": []},
        }

        for game in recent_games:
            game_id = game.get("id")
            if not game_id:
                continue

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

            stats_params = {"game_ids[]": [game_id], "per_page": 100}
            try:
                stats_data = bdl_get("/v1/stats", stats_params)
                for stat in stats_data.get("data", []):
                    stat_team = stat.get("team") or {}
                    if stat_team.get("id") != opp_id:
                        continue

                    player = stat.get("player") or {}
                    pos = str(player.get("position") or "").upper()
                    mins = parse_minutes(stat.get("min"))

                    if mins < 10:
                        continue

                    pts = _safe_float(stat.get("pts"))
                    reb = _safe_float(stat.get("reb"))
                    ast = _safe_float(stat.get("ast"))
                    fg3m = _safe_float(stat.get("fg3m"))

                    for stat_type, stat_val in [("pts", pts), ("reb", reb), ("ast", ast), ("fg3m", fg3m)]:
                        if pos in position_stats[stat_type]:
                            position_stats[stat_type][pos].append(stat_val)
                        elif pos in ("G-F", "F-G"):
                            position_stats[stat_type]["G"].append(stat_val)
                            position_stats[stat_type]["F"].append(stat_val)

            except Exception:
                continue

        # League averages by position and stat (2024-25 estimates)
        league_avg = {
            "pts": {"PG": 17.5, "SG": 15.5, "SF": 14.5, "PF": 14.0, "C": 14.5, "G": 16.5, "F": 14.2},
            "reb": {"PG": 4.0, "SG": 3.5, "SF": 5.0, "PF": 6.5, "C": 9.0, "G": 3.8, "F": 5.8},
            "ast": {"PG": 6.5, "SG": 3.5, "SF": 2.5, "PF": 2.5, "C": 2.0, "G": 5.0, "F": 2.5},
            "fg3m": {"PG": 2.0, "SG": 2.2, "SF": 1.5, "PF": 1.0, "C": 0.5, "G": 2.1, "F": 1.2},
        }

        for stat_type in position_stats:
            for pos, vals in position_stats[stat_type].items():
                if not vals:
                    continue

                avg = sum(vals) / len(vals)
                lg_avg = league_avg[stat_type].get(pos, avg)

                relative = (avg - lg_avg) / lg_avg if lg_avg > 0 else 0

                if relative > 0.10:
                    bucket = "WEAK"
                    rank = int(25 + min(relative * 50, 5))
                elif relative < -0.10:
                    bucket = "STRONG"
                    rank = int(10 - min(abs(relative) * 50, 9))
                else:
                    bucket = "AVERAGE"
                    rank = int(15 + relative * 50)

                result[stat_type][pos] = DvPRanking(
                    stat_allowed_avg=round(avg, 1),
                    rank=max(1, min(30, rank)),
                    bucket=bucket,
                )

        return result
    except Exception:
        return result


# ============================================================================
# LEAGUE RANKS
# ============================================================================

def get_league_ranks_batch(
    player_ids: List[int],
    season: int,
    stat: str = "pts",
) -> Dict[int, int]:
    """Get league ranks for multiple players for a given stat.

    Uses `/v1/leaders` (cursor-based pagination). Returns dict mapping
    player_id -> rank (1 = league leader).
    """
    try:
        player_id_set = set(int(pid) for pid in player_ids if pid)
        if not player_id_set:
            return {}

        ranks: Dict[int, int] = {}
        cursor: Optional[int] = None
        seen = 0
        max_pages = 10

        for _ in range(max_pages):
            params: Dict[str, Any] = {
                "season": season,
                "stat_type": stat,
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

            if len(ranks) == len(player_id_set):
                break

        return ranks
    except Exception as e:
        print(f"[WARN] Failed to fetch {stat} league ranks: {e}", file=sys.stderr)
        return {}


# ============================================================================
# MAIN BUILD FUNCTIONS
# ============================================================================

def build_game_data(
    game: Dict[str, Any],
    game_date: datetime,
    season: int,
    teams_map: Dict[str, Dict[str, Any]],
    standings: Dict[int, Dict[str, Any]],
    mode: str = "lite",
) -> Optional[GameData]:
    """Build complete data for a single game."""
    game_id = game.get("id")
    if not game_id:
        return None

    home_team = game.get("home_team") or {}
    away_team = game.get("visitor_team") or {}

    home_abbr = str(home_team.get("abbreviation") or "").upper()
    away_abbr = str(away_team.get("abbreviation") or "").upper()
    home_team_id = int(home_team.get("id") or 0)
    away_team_id = int(away_team.get("id") or 0)

    if not all([home_abbr, away_abbr]) or home_team_id <= 0 or away_team_id <= 0:
        return None

    print(f"[INFO] Processing {away_abbr} @ {home_abbr}...", file=sys.stderr)

    if mode not in ("lite", "full", "fast"):
        raise ValueError(f"Invalid mode: {mode}")

    skip_injuries_and_recent = mode == "fast"
    skip_dvp_and_pace = mode in ("fast", "lite")

    injuries: Dict[int, Dict[str, Any]] = {}
    if not skip_injuries_and_recent:
        injuries = get_player_injuries_for_teams([home_team_id, away_team_id])

    # Get active players
    home_players_raw = get_active_players_for_team(home_team_id)
    away_players_raw = get_active_players_for_team(away_team_id)

    home_ids = [int(p["id"]) for p in home_players_raw if p.get("id")]
    away_ids = [int(p["id"]) for p in away_players_raw if p.get("id")]
    all_ids = home_ids + away_ids

    # Fetch season stats
    season_stats = get_season_multi_stats(all_ids, season)

    recent_stats_raw: Dict[int, List[Dict[str, Any]]] = {pid: [] for pid in all_ids}
    pts_ranks: Dict[int, int] = {}
    reb_ranks: Dict[int, int] = {}
    ast_ranks: Dict[int, int] = {}

    if not skip_injuries_and_recent:
        # Fetch recent stats
        since_date = game_date - timedelta(days=20)
        recent_stats_raw = get_recent_game_stats(all_ids, since_date)

        # Fetch league ranks
        pts_ranks = get_league_ranks_batch(all_ids, season, "pts")
        reb_ranks = get_league_ranks_batch(all_ids, season, "reb")
        ast_ranks = get_league_ranks_batch(all_ids, season, "ast")

    # Determine starters (top 5 by season minutes)
    def _get_probable_starters(player_ids: List[int]) -> Set[int]:
        players_with_mins = []
        for pid in player_ids:
            sp = season_stats.get(pid)
            if sp:
                players_with_mins.append((pid, sp.minutes))
        players_with_mins.sort(key=lambda x: x[1], reverse=True)
        return set(pid for pid, _ in players_with_mins[:5])

    home_starters = _get_probable_starters(home_ids)
    away_starters = _get_probable_starters(away_ids)

    def _build_player_records(
        players_raw: List[Dict[str, Any]],
        team_abbr: str,
        starters_set: Set[int],
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for p in players_raw:
            pid = p.get("id")
            if not pid:
                continue
            pid_int = int(pid)

            full_name = f"{p.get('first_name', '').strip()} {p.get('last_name', '').strip()}".strip()
            position = str(p.get("position") or "")

            # Season stats
            s = season_stats.get(pid_int) or MultiStatSeason(
                minutes=0.0, pts=0.0, reb=0.0, ast=0.0, fg3m=0.0
            )

            # Recent stats
            recent_summary = summarize_recent_multi_stats(recent_stats_raw.get(pid_int, []))

            # Injury info
            inj = injuries.get(pid_int, {"status": "AVAILABLE", "description": None})

            records.append({
                "player_id": pid_int,
                "name": full_name,
                "position": position,
                "team_abbr": team_abbr,
                "season": asdict(s) if hasattr(s, '__dataclass_fields__') else {
                    "minutes": s.minutes, "pts": s.pts, "reb": s.reb, "ast": s.ast, "fg3m": s.fg3m,
                    "usg_pct": s.usg_pct, "ts_pct": s.ts_pct, "reb_pct": s.reb_pct,
                    "ast_pct": s.ast_pct, "fg3_pct": s.fg3_pct, "off_rating": s.off_rating,
                },
                "recent": {
                    "sample_size": recent_summary.sample_size,
                    "minutes_avg": recent_summary.minutes_avg,
                    "pts": asdict(recent_summary.pts) if recent_summary.pts else None,
                    "reb": asdict(recent_summary.reb) if recent_summary.reb else None,
                    "ast": asdict(recent_summary.ast) if recent_summary.ast else None,
                    "fg3m": asdict(recent_summary.fg3m) if recent_summary.fg3m else None,
                    "usg_pct": recent_summary.usg_pct,
                    "fg3_pct": recent_summary.fg3_pct,
                },
                "injury_status": str(inj.get("status") or "AVAILABLE"),
                "injury_notes": inj.get("description"),
                "is_starter": pid_int in starters_set,
                "pts_league_rank": pts_ranks.get(pid_int),
                "reb_league_rank": reb_ranks.get(pid_int),
                "ast_league_rank": ast_ranks.get(pid_int),
            })
        return records

    home_records = _build_player_records(home_players_raw, home_abbr, home_starters)
    away_records = _build_player_records(away_players_raw, away_abbr, away_starters)

    # Team context
    home_days_rest = compute_days_of_rest(home_team_id, game_date.date())
    away_days_rest = compute_days_of_rest(away_team_id, game_date.date())

    home_pace_l10 = None if skip_dvp_and_pace else get_team_pace_last_10(home_team_id, game_date.date())
    away_pace_l10 = None if skip_dvp_and_pace else get_team_pace_last_10(away_team_id, game_date.date())

    # DvP for both teams
    home_dvp = {} if skip_dvp_and_pace else compute_dvp_multi_stat(home_team_id, game_date.date())
    away_dvp = {} if skip_dvp_and_pace else compute_dvp_multi_stat(away_team_id, game_date.date())

    # Advanced standings
    default_adv = {
        "defensive_rating": 110.0, "offensive_rating": 110.0,
        "net_rating": 0.0, "pace": 100.0, "win": 0, "loss": 0,
    }
    home_adv = standings.get(home_team_id, default_adv)
    away_adv = standings.get(away_team_id, default_adv)

    # Convert DvP to serializable format
    def _dvp_to_dict(dvp: Dict[str, Dict[str, DvPRanking]]) -> Dict[str, Dict[str, Dict[str, Any]]]:
        return {
            stat_type: {
                pos: asdict(ranking) for pos, ranking in positions.items()
            } for stat_type, positions in dvp.items()
        }

    return GameData(
        game_id=game_id,
        game_date=game_date.date().isoformat(),
        away_abbr=away_abbr,
        home_abbr=home_abbr,
        away_team_id=away_team_id,
        home_team_id=home_team_id,
        teams={
            away_abbr: {
                "team_id": away_team_id,
                "days_rest": away_days_rest,
                "pace_last_10": away_pace_l10,
                "dvp": _dvp_to_dict(away_dvp),
                "advanced": away_adv,
            },
            home_abbr: {
                "team_id": home_team_id,
                "days_rest": home_days_rest,
                "pace_last_10": home_pace_l10,
                "dvp": _dvp_to_dict(home_dvp),
                "advanced": home_adv,
            },
        },
        players={
            away_abbr: away_records,
            home_abbr: home_records,
        },
    )


def build_full_slate_payload(
    game_date: datetime,
    season: int,
    away_abbr: Optional[str] = None,
    home_abbr: Optional[str] = None,
    mode: str = "lite",
) -> Dict[str, Any]:
    """Build complete payload for full slate or single game."""
    print(f"[INFO] Fetching games for {game_date.date().isoformat()}...", file=sys.stderr)
    if mode == "fast":
        print("[INFO] Mode=fast (skip injuries/recent/DvP/pace)", file=sys.stderr)
    elif mode == "lite":
        print("[INFO] Mode=lite (skip DvP/pace)", file=sys.stderr)
    elif mode == "full":
        print("[INFO] Mode=full (compute all context)", file=sys.stderr)
    
    # Get teams map
    teams_map = get_teams_map()
    
    # Get standings
    standings = get_team_standings(season)
    
    # Fetch games
    games = fetch_games_on_date(game_date.date())
    
    if not games:
        print(f"[WARN] No games found on {game_date.date().isoformat()}", file=sys.stderr)
        return {
            "meta": {
                "game_date": game_date.date().isoformat(),
                "season": season,
                "source": "balldontlie_goat_v3",
                "games_count": 0,
            },
            "games": [],
        }
    
    # Filter to specific game if requested
    if away_abbr and home_abbr:
        target_game = find_game_by_teams(games, away_abbr.upper(), home_abbr.upper())
        if target_game:
            games = [target_game]
        else:
            print(f"[WARN] Game {away_abbr} @ {home_abbr} not found", file=sys.stderr)
            games = []
    
    print(f"[INFO] Processing {len(games)} games...", file=sys.stderr)
    
    # Build data for each game
    games_data: List[Dict[str, Any]] = []
    for game in games:
        game_data = build_game_data(game, game_date, season, teams_map, standings, mode=mode)
        if game_data:
            games_data.append({
                "game_id": game_data.game_id,
                "game_date": game_data.game_date,
                "away_abbr": game_data.away_abbr,
                "home_abbr": game_data.home_abbr,
                "away_team_id": game_data.away_team_id,
                "home_team_id": game_data.home_team_id,
                "teams": game_data.teams,
                "players": game_data.players,
            })
    
    return {
        "meta": {
            "game_date": game_date.date().isoformat(),
            "season": season,
            "source": "balldontlie_goat_v3",
            "games_count": len(games_data),
            "stat_categories": ["pts", "reb", "ast", "fg3m", "pra"],
        },
        "games": games_data,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pro Parlay Syndicate v3.0 - Master Data Fetcher"
    )
    parser.add_argument("--date", required=True, help="Game date YYYY-MM-DD")
    parser.add_argument("--season", type=int, required=True, help="Season year, e.g. 2025")
    parser.add_argument("--away", default=None, help="Away team abbreviation (optional)")
    parser.add_argument("--home", default=None, help="Home team abbreviation (optional)")
    parser.add_argument(
        "--mode",
        choices=["lite", "full", "fast"],
        default="lite",
        help="Data enrichment mode: lite (default), full, fast",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="DEPRECATED: alias for --mode fast",
    )
    parser.add_argument("--output", default=None, help="Output file path (optional)")
    args = parser.parse_args()

    game_date = datetime.fromisoformat(args.date).replace(tzinfo=timezone.utc)

    mode = args.mode
    if args.fast and mode == "lite":
        mode = "fast"

    payload = build_full_slate_payload(
        game_date=game_date,
        season=args.season,
        away_abbr=args.away,
        home_abbr=args.home,
        mode=mode,
    )

    output = json.dumps(payload, indent=2)
    
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"[INFO] Output written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
