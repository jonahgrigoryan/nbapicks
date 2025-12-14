#!/usr/bin/env python3
"""fetch_points_game_data.py

Fetches NBA game data focused only on POINTS + MINUTES (plus injuries, pace, B2B).

This is a slimmed version of `fetch_nba_game_data.py` designed specifically for
points-only workflows.

Usage:
  python3 fetch_points_game_data.py \
    --game-date 2025-11-29 \
    --away BOS \
    --home MIN \
    --season 2025

Output:
  Prints a JSON payload (`GAME_DATA`) for the matchup.

Requirements:
  - env var `BALLDONTLIE_API_KEY` must be set.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from collections import deque
from dataclasses import asdict, dataclass
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


@dataclass
class StatSummary:
    avg: float
    stdev: float


@dataclass
class RecentPoints:
    sample_size: int
    minutes_avg: float
    pts: Optional[StatSummary]


@dataclass
class SeasonPoints:
    minutes: float
    pts: float


@dataclass
class PlayerRecord:
    player_id: int
    name: str
    position: str
    season: SeasonPoints
    recent: RecentPoints
    injury_status: str
    injury_notes: Optional[str]


def get_teams_map() -> Dict[str, Dict[str, Any]]:
    data = bdl_get("/v1/teams", {})
    teams: Dict[str, Dict[str, Any]] = {}
    for t in data.get("data", []):
        abbr = str(t.get("abbreviation") or "").upper()
        if abbr:
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
            )
        return out

    nba_params = {"season": season, "season_type": "regular", "type": "base"}

    try:
        rows = _fetch_batched("/nba/v1/season_averages/general", nba_params, player_ids)
        return _parse_rows(rows)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status in (401, 400):
            legacy_rows = _fetch_batched("/v1/season_averages", {"season": season}, player_ids)
            return _parse_rows(legacy_rows)
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


def summarize_recent_points(stats_rows: List[Dict[str, Any]]) -> RecentPoints:
    if not stats_rows:
        return RecentPoints(sample_size=0, minutes_avg=0.0, pts=None)

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

    return RecentPoints(
        sample_size=len(l5),
        minutes_avg=round(minutes_avg, 2),
        pts=pts_summary,
    )


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
    game = find_game_by_teams_and_date(game_date, away_abbr, home_abbr)
    game_id = game.get("id")

    teams_map = get_teams_map()
    away_team = teams_map[away_abbr]
    home_team = teams_map[home_abbr]

    away_team_id = away_team["id"]
    home_team_id = home_team["id"]

    injuries = get_player_injuries_for_teams([away_team_id, home_team_id])

    away_players_raw = get_active_players_for_team(away_team_id)
    home_players_raw = get_active_players_for_team(home_team_id)

    away_ids = [int(p["id"]) for p in away_players_raw if p.get("id")]
    home_ids = [int(p["id"]) for p in home_players_raw if p.get("id")]
    all_ids = away_ids + home_ids

    season_points = get_season_points(all_ids, season)

    since_date = game_date - timedelta(days=20)
    recent_stats_raw = get_recent_game_stats(all_ids, since_date)

    def _build_records(players_raw: List[Dict[str, Any]]) -> List[PlayerRecord]:
        records: List[PlayerRecord] = []
        for p in players_raw:
            pid = p.get("id")
            if not pid:
                continue
            pid_int = int(pid)

            full_name = f"{p.get('first_name','').strip()} {p.get('last_name','').strip()}".strip()
            position = str(p.get("position") or "")

            s = season_points.get(pid_int) or SeasonPoints(minutes=0.0, pts=0.0)
            recent_summary = summarize_recent_points(recent_stats_raw.get(pid_int, []))
            inj = injuries.get(pid_int, {"status": "AVAILABLE", "description": None})

            records.append(
                PlayerRecord(
                    player_id=pid_int,
                    name=full_name,
                    position=position,
                    season=s,
                    recent=recent_summary,
                    injury_status=str(inj.get("status") or "AVAILABLE"),
                    injury_notes=inj.get("description"),
                )
            )
        return records

    away_records = _build_records(away_players_raw)
    home_records = _build_records(home_players_raw)

    away_b2b = compute_back_to_back(away_team_id, game_date.date())
    home_b2b = compute_back_to_back(home_team_id, game_date.date())

    away_pace_last_10 = get_team_pace_last_10(away_team_id, game_date.date())
    home_pace_last_10 = get_team_pace_last_10(home_team_id, game_date.date())

    return {
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
