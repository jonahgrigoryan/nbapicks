#!/usr/bin/env python
"""
Fetches NBA game data (stats, injuries, player props) from the BALLDONTLIE API
and prints a JSON payload for a single game, suitable for your Composer prompt.

Usage:
  python scripts/fetch_nba_game_data.py \
      --game-date 2025-11-29 \
      --away BOS \
      --home MIN \
      --season 2025
"""

import argparse
import json
import os
import statistics
import time
from collections import deque
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

BALDONTLIE_BASE_V1 = "https://api.balldontlie.io/v1"
BALDONTLIE_BASE_NBA_V1 = "https://api.balldontlie.io/nba/v1"
BALDONTLIE_BASE_V2 = "https://api.balldontlie.io/v2"

# Basic client-side rate limiter to stay under the API per-minute cap.
_REQUEST_WINDOW_SEC = 60
_MAX_REQUESTS_PER_WINDOW = 50  # keep below 60/min to be safe
_REQUEST_TIMES: deque = deque()


# ---------- low-level HTTP helper ----------

def bdl_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Thin wrapper over BALLDONTLIE HTTP GET.
    Adjust base URLs / paths if the API changes.
    """
    api_key = os.getenv("BALLDONTLIE_API_KEY")
    if not api_key:
        raise RuntimeError("BALLDONTLIE_API_KEY env var is not set")

    # Heuristic: use the prefix in the path to decide base
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

    # simple sliding-window limiter
    now = time.time()
    while _REQUEST_TIMES and now - _REQUEST_TIMES[0] > _REQUEST_WINDOW_SEC:
        _REQUEST_TIMES.popleft()
    if len(_REQUEST_TIMES) >= _MAX_REQUESTS_PER_WINDOW:
        sleep_for = _REQUEST_WINDOW_SEC - (now - _REQUEST_TIMES[0]) + 0.1
        if sleep_for > 0:
            time.sleep(sleep_for)

    # retry a few times on 429
    for attempt in range(3):
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        _REQUEST_TIMES.append(time.time())
        if resp.status_code == 429 and attempt < 2:
            time.sleep(1.0 + attempt * 1.5)
            continue
        resp.raise_for_status()
        return resp.json()

    # if we somehow exit loop without returning, raise the last response
    resp.raise_for_status()
    return resp.json()


# ---------- dataclasses for output ----------

@dataclass
class StatSummary:
    avg: float
    stdev: float


@dataclass
class RecentStats:
    sample_size: int
    minutes_avg: float
    pts: Optional[StatSummary]
    reb: Optional[StatSummary]
    ast: Optional[StatSummary]


@dataclass
class SeasonStats:
    minutes: float
    pts: float
    reb: float
    ast: float


@dataclass
class PlayerRecord:
    player_id: int
    name: str
    position: str
    season: SeasonStats
    recent: RecentStats
    injury_status: str
    injury_notes: Optional[str]


# ---------- helpers to map teams / players / game ----------

def get_teams_map() -> Dict[str, Dict[str, Any]]:
    """Return mapping from team abbreviation -> team object."""
    data = bdl_get("/v1/teams", {})
    teams = {}
    for t in data.get("data", []):
        abbr = t["abbreviation"].upper()
        teams[abbr] = t
    return teams


def find_game_by_teams_and_date(
    game_date: datetime, away_abbr: str, home_abbr: str
) -> Dict[str, Any]:
    """
    Use BALLDONTLIE games endpoint filtered by date and team IDs.
    Adjust params as needed based on official docs.
    """
    teams_map = get_teams_map()
    try:
        away_id = teams_map[away_abbr]["id"]
        home_id = teams_map[home_abbr]["id"]
    except KeyError as e:
        raise RuntimeError(f"Unknown team abbreviation: {e}")

    date_str = game_date.date().isoformat()
    # Filter by date and team_ids; API allows arrays like team_ids[]=...
    params = {
        "dates[]": date_str,
        "team_ids[]": [away_id, home_id],
        "per_page": 100,
    }
    data = bdl_get("/v1/games", params)
    candidates: List[Dict[str, Any]] = data.get("data", [])

    # Filter down to exact away/home matchup
    for g in candidates:
        if (
            g["home_team"]["abbreviation"].upper() == home_abbr
            and g["visitor_team"]["abbreviation"].upper() == away_abbr
        ):
            return g

    raise RuntimeError(f"No game found for {away_abbr} @ {home_abbr} on {date_str}")


def get_player_injuries_for_teams(team_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """
    Returns mapping player_id -> injury dict {status, description}.

    Fetches player injuries from BALLDONTLIE /v1/player_injuries endpoint.
    Filters by team_ids to get injuries for players on specified teams.
    """
    if not team_ids:
        return {}

    # Build params with team_ids array
    params: Dict[str, Any] = {"per_page": 100}
    for tid in team_ids:
        params.setdefault("team_ids[]", []).append(tid)

    try:
        injuries: Dict[int, Dict[str, Any]] = {}
        cursor: Optional[str] = None
        # Follow pagination so we do not miss injuries if >100 rows
        for _ in range(20):  # hard stop to avoid runaway loops
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor

            data = bdl_get("/v1/player_injuries", page_params)

            for injury in data.get("data", []):
                player = injury.get("player", {})
                player_id = player.get("id")
                if not player_id:
                    continue

                status = injury.get("status", "AVAILABLE")
                description = injury.get("description")

                injuries[player_id] = {
                    "status": status.upper() if status else "AVAILABLE",
                    "description": description,
                }

            cursor = (data.get("meta") or {}).get("next_cursor")
            if not cursor:
                break
        return injuries
    except Exception as e:
        # If endpoint fails (e.g., 401, 404), return empty dict
        # This allows the pipeline to continue with all players as AVAILABLE
        print(
            f"[WARN] Failed to fetch player injuries: {e}. "
            "Treating all players as AVAILABLE.",
            flush=True,
        )
        return {}


def get_player_ids_for_team(team_id: int) -> List[int]:
    """
    Get active players for a team.
    You will still filter down to the 5 starters and any bench ≥ 20 min in Composer.
    """
    params = {"team_ids[]": [team_id], "per_page": 100}
    data = bdl_get("/v1/players/active", params)
    return [p["id"] for p in data.get("data", [])]


def get_season_averages(player_ids: List[int], season: int) -> Dict[int, SeasonStats]:
    """
    Use BALLDONTLIE season_averages general/base endpoint to get per-player season stats.
    Docs: /nba/v1/season_averages/general?season=2024&season_type=regular&type=base&player_ids[]=...
    Some accounts do not have access to /nba/v1; fall back to legacy /v1/season_averages on 401.
    Also batch requests to avoid URL-size or max-ids errors.
    """
    if not player_ids:
        return {}

    def _parse_rows(rows: List[Dict[str, Any]]) -> Dict[int, SeasonStats]:
        parsed: Dict[int, SeasonStats] = {}
        for row in rows:
            # Newer format nests stats under "stats" and player under "player"
            if "stats" in row:
                stats = row.get("stats", {})
                pid = (row.get("player") or {}).get("id")
            else:
                # Legacy /v1/season_averages shape
                stats = row
                pid = row.get("player_id")

            if not pid:
                continue

            minutes_val = stats.get("min")
            if isinstance(minutes_val, str) and ":" in minutes_val:
                mins, secs = minutes_val.split(":")
                minutes = int(mins) + int(secs) / 60.0
            else:
                minutes = float(minutes_val or 0.0)

            parsed[pid] = SeasonStats(
                minutes=round(minutes, 2),
                pts=float(stats.get("pts", 0.0)),
                reb=float(stats.get("reb", 0.0)),
                ast=float(stats.get("ast", 0.0)),
            )
        return parsed

    def _fetch_batched(
        endpoint: str, base_params: Dict[str, Any], ids: List[int], chunk_size: int = 25
    ) -> List[Dict[str, Any]]:
        all_rows: List[Dict[str, Any]] = []
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i : i + chunk_size]
            params = dict(base_params)
            for pid in chunk:
                params.setdefault("player_ids[]", []).append(pid)
            resp = bdl_get(endpoint, params)
            all_rows.extend(resp.get("data", []))
        return all_rows

    nba_params = {
        "season": season,
        "season_type": "regular",
        "type": "base",
    }

    try:
        rows = _fetch_batched("/nba/v1/season_averages/general", nba_params, player_ids)
        return _parse_rows(rows)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        # Fallback for accounts without /nba/v1 access or if endpoint rejects the season
        if status in (401, 400):
            try:
                legacy_rows = _fetch_batched(
                    "/v1/season_averages", {"season": season}, player_ids
                )
                return _parse_rows(legacy_rows)
            except requests.HTTPError as le:
                legacy_status = (
                    le.response.status_code if le.response is not None else None
                )
                if legacy_status == 400:
                    print(
                        f"[WARN] Legacy /v1/season_averages rejected request "
                        f"(season={season}): {le}. Returning empty stats.",
                        flush=True,
                    )
                    return {}
                raise
        raise


def get_recent_game_stats(
    player_ids: List[int], since_date: datetime
) -> Dict[int, List[Dict[str, Any]]]:
    """
    Pull per-game box-score stats since a given date.
    Docs: /v1/stats?player_ids[]=...&start_date=YYYY-MM-DD
    """
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


def summarize_recent(stats_rows: List[Dict[str, Any]]) -> RecentStats:
    """
    Compute L5 averages and stdevs for PTS/REB/AST + minutes.
    """
    if not stats_rows:
        return RecentStats(sample_size=0, minutes_avg=0.0,
                           pts=None, reb=None, ast=None)

    # sort newest first by game date if available
    rows = stats_rows[:]
    # some APIs will have 'game' dict with 'date'

    def _date_key(row):
        game = row.get("game") or {}
        dt = game.get("date")
        try:
            return datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            return datetime.now(timezone.utc)

    rows.sort(key=_date_key, reverse=True)
    l5 = rows[:5]

    def _series(key: str) -> Optional[StatSummary]:
        vals = [float(r.get(key, 0.0)) for r in l5]
        if not vals:
            return None
        if len(vals) == 1:
            return StatSummary(avg=vals[0], stdev=0.0)
        return StatSummary(avg=float(sum(vals) / len(vals)),
                           stdev=float(statistics.pstdev(vals)))

    mins_raw = []
    for r in l5:
        m = r.get("min")
        if isinstance(m, str) and ":" in m:
            mm, ss = m.split(":")
            mins_raw.append(int(mm) + int(ss) / 60.0)
        elif m is not None:
            mins_raw.append(float(m))
    minutes_avg = float(sum(mins_raw) / len(mins_raw)) if mins_raw else 0.0

    return RecentStats(
        sample_size=len(l5),
        minutes_avg=round(minutes_avg, 2),
        pts=_series("pts"),
        reb=_series("reb"),
        ast=_series("ast"),
    )


def get_player_props(game_id: int) -> Dict[int, List[Dict[str, Any]]]:
    """
    Pull player props for this game from BALLDONTLIE odds endpoint.
    Docs show /v2/odds/player_props?game_id=...
    """
    # Props removed for this workflow; return empty.
    return {}


# ---------- schedule / pace stubs (ready for future upgrades) ----------

def compute_back_to_back(
    team_id: int, game_date: date, api_key: str
) -> bool:
    """
    Determines if a team is playing a back-to-back game.

    Checks if the team played a game on the day before the given game_date.

    Args:
        team_id: Numeric BallDontLie team identifier.
        game_date: Date of the current game.
        api_key: API key (unused, kept for compatibility).

    Returns:
        True if team played the previous day, False otherwise.
    """
    # Calculate the previous day
    prev_date = game_date - timedelta(days=1)

    # Get games for this team on the previous day
    params = {
        "dates[]": prev_date.isoformat(),
        "team_ids[]": [team_id],
        "per_page": 100,
    }

    try:
        data = bdl_get("/v1/games", params)
        games = data.get("data", [])

        # Check if team played on previous day
        for game in games:
            game_date_str = game.get("date")
            if not game_date_str:
                continue

            try:
                # Parse game date
                game_dt = datetime.fromisoformat(
                    game_date_str.replace("Z", "+00:00")
                )
                if game_dt.date() == prev_date:
                    # Check if team was home or visitor
                    home_team_id = game.get("home_team", {}).get("id")
                    visitor_team_id = game.get("visitor_team", {}).get("id")
                    if home_team_id == team_id or visitor_team_id == team_id:
                        return True
            except Exception:
                continue

        return False
    except Exception:
        # If API call fails, default to False
        return False


def get_team_pace_last_10(
    team_id: int,
    game_date: date,
    api_key: str,
) -> Optional[float]:
    """
    Calculates team's average pace over last 10 games.

    Pace = (possessions / minutes) * 48
    Possessions = FGA - OREB + TOV + 0.44 * FTA

    Args:
        team_id: Numeric BallDontLie team identifier.
        game_date: Date to look back from (exclusive).
        api_key: API key (unused, kept for compatibility).

    Returns:
        Average pace over last 10 games, or None if insufficient data.
    """
    # Get team's games up to (but not including) game_date
    # Look back up to 30 days to find 10 games
    start_date = game_date - timedelta(days=30)
    params = {
        "team_ids[]": [team_id],
        "start_date": start_date.isoformat(),
        "end_date": (game_date - timedelta(days=1)).isoformat(),
        "per_page": 100,
    }

    try:
        data = bdl_get("/v1/games", params)
        games = data.get("data", [])

        # Sort games by date (newest first) and take last 10
        def _game_date_key(g):
            date_str = g.get("date", "")
            try:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                return dt.date()
            except Exception:
                return date.min

        games.sort(key=_game_date_key, reverse=True)
        last_10_games = games[:10]

        if not last_10_games:
            return None

        # Get stats for each game and calculate pace
        paces = []
        for game in last_10_games:
            game_id = game.get("id")
            if not game_id:
                continue

            # Get team stats for this game
            stats_params = {
                "game_ids[]": [game_id],
                "per_page": 100,
            }

            try:
                stats_data = bdl_get("/v1/stats", stats_params)
                team_stats_list = stats_data.get("data", [])

                # Aggregate team stats for the game
                # (stats endpoint returns per-player, need to sum)
                total_fga = 0.0
                total_oreb = 0.0
                total_tov = 0.0
                total_fta = 0.0
                total_min = 0.0

                for stat in team_stats_list:
                    # Only count stats for this team
                    stat_team_id = stat.get("team", {}).get("id")
                    if stat_team_id != team_id:
                        continue

                    total_fga += float(stat.get("fga", 0.0))
                    total_oreb += float(stat.get("oreb", 0.0))
                    total_tov += float(stat.get("turnover", 0.0))
                    total_fta += float(stat.get("fta", 0.0))

                    # Sum minutes (convert mm:ss to decimal if needed)
                    min_val = stat.get("min", 0.0)
                    if isinstance(min_val, str) and ":" in min_val:
                        mm, ss = min_val.split(":")
                        total_min += int(mm) + int(ss) / 60.0
                    else:
                        total_min += float(min_val or 0.0)

                # Calculate possessions
                # Possessions = FGA - OREB + TOV + 0.44 * FTA
                possessions = (
                    total_fga - total_oreb + total_tov + 0.44 * total_fta
                )

                # Convert summed player minutes to team minutes (divide by 5)
                team_minutes = total_min / 5.0 if total_min > 0 else 0.0

                # Calculate pace: (possessions / team_minutes) * 48
                if team_minutes > 0:
                    pace = (possessions / team_minutes) * 48.0
                    paces.append(pace)

            except Exception:
                # Skip this game if stats fetch fails
                continue

        if not paces:
            return None

        # Return average pace
        return round(sum(paces) / len(paces), 2)

    except Exception:
        # If API call fails, return None
        return None


# ---------- top-level orchestration ----------

def build_game_payload(
    game_date: datetime, away_abbr: str, home_abbr: str, season: int
) -> Dict[str, Any]:
    game = find_game_by_teams_and_date(game_date, away_abbr, home_abbr)
    game_id = game["id"]

    api_key = os.getenv("BALLDONTLIE_API_KEY")
    if not api_key:
        raise RuntimeError("BALLDONTLIE_API_KEY env var is not set")

    teams_map = get_teams_map()
    away_team = teams_map[away_abbr]
    home_team = teams_map[home_abbr]

    away_team_id = away_team["id"]
    home_team_id = home_team["id"]

    # injuries
    injuries = get_player_injuries_for_teams([away_team_id, home_team_id])

    # players universe = active players on both teams
    away_player_ids = get_player_ids_for_team(away_team_id)
    home_player_ids = get_player_ids_for_team(home_team_id)
    all_player_ids = away_player_ids + home_player_ids

    # season averages
    season_stats = get_season_averages(all_player_ids, season)

    # recent stats (last 10 days as a rough window)
    since_date = game_date - timedelta(days=20)
    recent_stats_raw = get_recent_game_stats(all_player_ids, since_date)

    # compose player records
    def build_players(team_ids: List[int]) -> List[PlayerRecord]:
        records: List[PlayerRecord] = []
        for pid in team_ids:
            s = season_stats.get(pid)
            if not s:
                # keep player with zeroed stats instead of dropping when season averages are missing
                s = SeasonStats(minutes=0.0, pts=0.0, reb=0.0, ast=0.0)

            # fetch player meta via players endpoint to get name + pos
            pdata = bdl_get(f"/v1/players/{pid}", {})
            pmeta = pdata.get("data", pdata)
            full_name = f"{pmeta['first_name']} {pmeta['last_name']}"
            position = pmeta.get("position") or ""

            recent_rows = recent_stats_raw.get(pid, [])
            recent_summary = summarize_recent(recent_rows)

            inj = injuries.get(pid, {"status": "AVAILABLE", "description": None})

            records.append(
                PlayerRecord(
                    player_id=pid,
                    name=full_name,
                    position=position,
                    season=s,
                    recent=recent_summary,
                    injury_status=inj.get("status") or "AVAILABLE",
                    injury_notes=inj.get("description"),
                )
            )
        return records

    away_players = build_players(away_player_ids)
    home_players = build_players(home_player_ids)

    # schedule flags (neutral for now, ready for future wiring)
    away_b2b = compute_back_to_back(
        away_team_id, game_date.date(), api_key
    )
    home_b2b = compute_back_to_back(
        home_team_id, game_date.date(), api_key
    )

    # pace placeholders (callable stub for future real pace)
    away_pace_last_10 = get_team_pace_last_10(
        away_team_id, game_date.date(), api_key
    )
    home_pace_last_10 = get_team_pace_last_10(
        home_team_id, game_date.date(), api_key
    )

    payload: Dict[str, Any] = {
        "meta": {
            "game_date": game_date.date().isoformat(),
            "season": season,
            "away_abbr": away_abbr,
            "home_abbr": home_abbr,
            "source": "balldontlie",
            "balldontlie_game_id": game_id,
        },
        "teams": {
            away_abbr: {
                "team_id": away_team_id,
                "back_to_back": away_b2b,
                "pace_last_10": away_pace_last_10,
            },
            home_abbr: {
                "team_id": home_team_id,
                "back_to_back": home_b2b,
                "pace_last_10": home_pace_last_10,
            },
        },
        "players": {
            away_abbr: [asdict(p) for p in away_players],
            home_abbr: [asdict(p) for p in home_players],
        },
        "props": {},
    }

    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-date", required=True, help="Game date YYYY-MM-DD")
    parser.add_argument("--away", required=True, help="Away team abbreviation, e.g. BOS")
    parser.add_argument("--home", required=True, help="Home team abbreviation, e.g. MIN")
    parser.add_argument("--season", type=int, required=True, help="Season year, e.g. 2025")
    args = parser.parse_args()

    game_date = datetime.fromisoformat(args.game_date).replace(tzinfo=timezone.utc)

    payload = build_game_payload(game_date, args.away.upper(), args.home.upper(), args.season)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
