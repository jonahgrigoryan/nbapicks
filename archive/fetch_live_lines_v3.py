#!/usr/bin/env python3
"""fetch_live_lines_v3.py

Pro Parlay Syndicate v3.0 - Live Lines Fetcher

Fetches ALL player prop types from BallDontLie API:
- PTS: Points Over/Under
- REB: Rebounds Over/Under  
- AST: Assists Over/Under
- 3PM: Three-pointers Made Over/Under
- PRA: Points + Rebounds + Assists (if available)

Features:
- Fetch all games on a specific date (full slate mode)
- Get all prop types for all players
- Clean output format compatible with simulation_engine_v3.py
- Nested structure: {player_name: {stat_type: {line, over_odds, under_odds}}}

Usage:
  # Fetch ALL props for full slate:
  python3 fetch_live_lines_v3.py --date 2025-01-15

  # Fetch props for specific game:
  python3 fetch_live_lines_v3.py --game-id 12345

  # Fetch specific prop type only:
  python3 fetch_live_lines_v3.py --date 2025-01-15 --prop-type points

Requirements:
  - env var `BALLDONTLIE_API_KEY` must be set
  - GOAT All-Star tier subscription for /nba/v2/player_props access
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

# Load .env file if it exists
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if "=" in line:
                key, value = line.strip().split("=", 1)
                os.environ[key] = value.strip("'").strip('"')


# Stat type mapping for API
STAT_TYPE_MAP = {
    "points": "pts",
    "rebounds": "reb", 
    "assists": "ast",
    "threes": "fg3m",
    "three_pointers_made": "fg3m",
    "points_rebounds_assists": "pra",
    "pts": "pts",
    "reb": "reb",
    "ast": "ast",
    "fg3m": "fg3m",
    "pra": "pra",
}

# All prop types to fetch
# NOTE: The GOAT endpoint may include unreliable/alt combined props.
# We compute PRA as PTS+REB+AST instead of trusting the feed.
ALL_PROP_TYPES = ["points", "rebounds", "assists", "threes"]


BALDONTLIE_BASE_V1 = "https://api.balldontlie.io/v1"
BALDONTLIE_BASE_V2 = "https://api.balldontlie.io/v2"
BALDONTLIE_BASE_NBA_V1 = "https://api.balldontlie.io/nba/v1"

# Rate limiter (shared with fetch_points_game_data.py pattern)
_REQUEST_WINDOW_SEC = 60
_MAX_REQUESTS_PER_WINDOW = 50
_REQUEST_TIMES: deque = deque()
_HTTP_TIMEOUT_SEC = 45


def bdl_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Thin wrapper over BallDontLie HTTP GET with rate limiting."""
    api_key = os.getenv("BALLDONTLIE_API_KEY")
    if not api_key:
        raise RuntimeError("BALLDONTLIE_API_KEY env var is not set")

    # Determine base URL from path prefix
    if path.startswith("/v2/"):
        base = BALDONTLIE_BASE_V2
        rel = path[3:]  # Remove "/v2"
    elif path.startswith("/nba/v1/"):
        base = BALDONTLIE_BASE_NBA_V1
        rel = path[7:]  # Remove "/nba/v1"
    elif path.startswith("/v1/"):
        base = BALDONTLIE_BASE_V1
        rel = path[3:]  # Remove "/v1"
    else:
        # Assume v1 if no prefix
        base = BALDONTLIE_BASE_V1
        rel = path

    url = base + rel
    headers = {"Authorization": api_key}

    # Rate limiting
    now = time.time()
    while _REQUEST_TIMES and now - _REQUEST_TIMES[0] > _REQUEST_WINDOW_SEC:
        _REQUEST_TIMES.popleft()
    if len(_REQUEST_TIMES) >= _MAX_REQUESTS_PER_WINDOW:
        sleep_for = _REQUEST_WINDOW_SEC - (now - _REQUEST_TIMES[0]) + 0.1
        if sleep_for > 0:
            time.sleep(sleep_for)

    # Retry on 429 (and transient connection/timeout errors)
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
class PlayerProp:
    """A single player prop bet."""
    player_id: int
    player_name: str
    team_abbr: str
    prop_type: str  # "pts", "reb", "ast", "fg3m", "pra"
    line: float
    game_id: int
    opponent: str
    over_odds: Optional[int] = None
    under_odds: Optional[int] = None


@dataclass
class GameInfo:
    """Basic game information."""
    game_id: int
    game_date: str
    home_team_abbr: str
    away_team_abbr: str
    home_team_id: int
    away_team_id: int
    status: str


def fetch_games_on_date(game_date: date) -> List[GameInfo]:
    """Fetch all NBA games scheduled for a specific date.
    
    Args:
        game_date: The date to query for games.
        
    Returns:
        List of GameInfo objects for games on that date.
    """
    params = {
        "dates[]": game_date.isoformat(),
        "per_page": 100,
    }
    
    data = bdl_get("/v1/games", params)
    games: List[GameInfo] = []
    
    for g in data.get("data", []):
        home_team = g.get("home_team") or {}
        away_team = g.get("visitor_team") or {}
        
        games.append(GameInfo(
            game_id=int(g.get("id") or 0),
            game_date=str(g.get("date") or ""),
            home_team_abbr=str(home_team.get("abbreviation") or "").upper(),
            away_team_abbr=str(away_team.get("abbreviation") or "").upper(),
            home_team_id=int(home_team.get("id") or 0),
            away_team_id=int(away_team.get("id") or 0),
            status=str(g.get("status") or ""),
        ))
    
    return games


def _fetch_player_info(player_id: int) -> Optional[Dict[str, Any]]:
    """Fetch player information by ID."""
    try:
        response = bdl_get(f"/v1/players/{player_id}", {})
        # Player endpoint returns data wrapped in "data" key
        return response.get("data") or response
    except Exception:
        return None


def _odds_to_implied(odds: Optional[int]) -> Optional[float]:
    if odds is None:
        return None
    try:
        o = int(odds)
    except (ValueError, TypeError):
        return None
    if o > 0:
        return 100 / (o + 100)
    return abs(o) / (abs(o) + 100)


def _line_quality(over_odds: Optional[int], under_odds: Optional[int]) -> float:
    """Lower is better (closer to a fair 50/50 line)."""
    over_imp = _odds_to_implied(over_odds)
    under_imp = _odds_to_implied(under_odds)
    if over_imp is None or under_imp is None:
        return 10.0
    return abs(over_imp - 0.5) + abs(under_imp - 0.5)


def fetch_player_props_for_game(
    game_id: int,
    game_info: Optional[GameInfo] = None,
    prop_types: Optional[List[str]] = None,
) -> List[PlayerProp]:
    """Fetch ALL player props for a specific game from GOAT endpoint.
    
    Uses /v2/odds/player_props (GOAT All-Star tier required).
    
    Args:
        game_id: BallDontLie game ID.
        game_info: Optional game info for opponent lookup.
        prop_types: List of prop types to fetch. If None, fetches all.
        
    Returns:
        List of PlayerProp objects with betting lines for all stat types.
    """
    if prop_types is None:
        prop_types = ALL_PROP_TYPES
    
    try:
        params: Dict[str, Any] = {
            "game_id": game_id,
        }
        
        # Fetch props from GOAT endpoint
        data = bdl_get("/v2/odds/player_props", params)
        
        props: List[PlayerProp] = []
        player_cache: Dict[int, Dict[str, Any]] = {}

        for prop in data.get("data", []):
            # Get prop type and normalize
            prop_type_raw = str(prop.get("prop_type", "")).lower()

            # Check if this prop type is requested
            if prop_type_raw not in prop_types:
                continue

            # Normalize to our standard naming
            stat_type = STAT_TYPE_MAP.get(prop_type_raw)
            if not stat_type:
                continue

            # Only process over_under markets (traditional O/U lines)
            market = prop.get("market", {})
            market_type = str(market.get("type", "")).lower()
            if market_type != "over_under":
                continue

            player_id = int(prop.get("player_id") or 0)
            if not player_id:
                continue

            # Prefer using embedded player/team info in the prop payload to avoid
            # hundreds of /v1/players/{id} calls (slow under rate limits).
            player_name = str(prop.get("player_name") or "").strip()
            player_obj = prop.get("player") or {}

            if not player_name and isinstance(player_obj, dict):
                first = str(player_obj.get("first_name") or "").strip()
                last = str(player_obj.get("last_name") or "").strip()
                player_name = f"{first} {last}".strip() or str(player_obj.get("full_name") or "").strip()

            team_abbr = str(prop.get("team_abbr") or prop.get("team_abbreviation") or "").upper()
            team_id_raw = prop.get("team_id")

            team_obj = None
            if isinstance(player_obj, dict):
                team_obj = player_obj.get("team")
                if team_abbr == "" and isinstance(team_obj, dict):
                    team_abbr = str(team_obj.get("abbreviation") or team_obj.get("abbr") or "").upper()
                if team_id_raw is None and isinstance(team_obj, dict):
                    team_id_raw = team_obj.get("id")

            # Infer team from team_id vs game_info when possible.
            if team_abbr == "" and team_id_raw and game_info:
                try:
                    team_id = int(team_id_raw)
                except (ValueError, TypeError):
                    team_id = 0
                if team_id == game_info.home_team_id:
                    team_abbr = game_info.home_team_abbr
                elif team_id == game_info.away_team_id:
                    team_abbr = game_info.away_team_abbr

            if not player_name:
                player_name = f"player_{player_id}"

            # Team/opponent fields are nice-to-have; matching happens by player_id.
            
            # Get line value
            line_value = prop.get("line_value")
            if line_value is None:
                continue
            
            try:
                line = float(line_value)
            except (ValueError, TypeError):
                continue
            
            # Get odds from market
            over_odds = market.get("over_odds")
            under_odds = market.get("under_odds")
            
            if over_odds is not None:
                try:
                    over_odds = int(over_odds)
                except (ValueError, TypeError):
                    over_odds = None
            if under_odds is not None:
                try:
                    under_odds = int(under_odds)
                except (ValueError, TypeError):
                    under_odds = None
            
            # Determine opponent
            opponent = ""
            if game_info:
                if team_abbr == game_info.home_team_abbr:
                    opponent = game_info.away_team_abbr
                elif team_abbr == game_info.away_team_abbr:
                    opponent = game_info.home_team_abbr
            
            props.append(PlayerProp(
                player_id=player_id,
                player_name=player_name,
                team_abbr=team_abbr,
                prop_type=stat_type,
                line=line,
                game_id=game_id,
                opponent=opponent,
                over_odds=over_odds,
                under_odds=under_odds,
            ))
        
        return props
        
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status in (401, 403, 404):
            print(f"[WARN] GOAT player_props endpoint unavailable (HTTP {status}). ", file=sys.stderr)
            print("[WARN] Ensure you have GOAT All-Star tier subscription.", file=sys.stderr)
        raise
    except Exception as e:
        print(f"[ERROR] Failed to fetch player props: {e}", file=sys.stderr)
        raise


def fetch_all_lines_for_slate(
    game_date: date,
    prop_types: Optional[List[str]] = None,
    starters_only: bool = True,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Fetch ALL player prop lines for ALL games on a date.
    
    Returns nested dict for simulation_engine_v3.py:
    {
        "player_name": {
            "pts": {"line": 25.5, "over_odds": -110, "under_odds": -110},
            "reb": {"line": 5.5, ...},
            "ast": {"line": 4.5, ...},
            "fg3m": {"line": 2.5, ...},
            "pra": {"line": 35.5, ...},
            "player_id": 123,
            "team": "BOS",
            "game_id": 12345,
            "opponent": "MIN"
        },
        ...
    }
    
    Args:
        game_date: Date to fetch games for.
        prop_types: List of prop types to fetch. If None, fetches all.
        starters_only: If True, filter to likely starters.
        
    Returns:
        Nested dict mapping player names to their prop info by stat type.
    """
    print(f"[INFO] Fetching games for {game_date.isoformat()}...", file=sys.stderr)
    games = fetch_games_on_date(game_date)
    
    if not games:
        print(f"[WARN] No games found for {game_date.isoformat()}", file=sys.stderr)
        return {}
    
    print(f"[INFO] Found {len(games)} games, fetching ALL prop types...", file=sys.stderr)
    
    # Nested structure: player_name -> stat_type -> line_info
    all_lines: Dict[str, Dict[str, Any]] = {}
    
    for game in games:
        if not game.game_id:
            continue
        
        try:
            # Fetch ALL prop types for this game
            props = fetch_player_props_for_game(
                game.game_id,
                game_info=game,
                prop_types=prop_types,
            )
            
            for prop in props:
                # Key by player_id for uniqueness and reliable matching.
                player_key = str(prop.player_id)

                # Initialize player entry if needed
                if player_key not in all_lines:
                    all_lines[player_key] = {
                        "player_id": prop.player_id,
                        "player_name": prop.player_name,
                        "team": prop.team_abbr,
                        "game_id": prop.game_id,
                        "opponent": prop.opponent,
                    }

                # Add this stat type's line, keeping the most "main" line
                # (odds closest to a fair 50/50).
                new_line = {
                    "line": prop.line,
                    "over_odds": prop.over_odds,
                    "under_odds": prop.under_odds,
                }

                existing = all_lines[player_key].get(prop.prop_type)
                if isinstance(existing, dict):
                    if _line_quality(new_line.get("over_odds"), new_line.get("under_odds")) < _line_quality(
                        existing.get("over_odds"), existing.get("under_odds")
                    ):
                        all_lines[player_key][prop.prop_type] = new_line
                else:
                    all_lines[player_key][prop.prop_type] = new_line
                
        except Exception as e:
            print(f"[WARN] Failed to fetch props for game {game.game_id} ({game.away_team_abbr}@{game.home_team_abbr}): {e}", file=sys.stderr)
            continue
    
    # Derive PRA from component props when available.
    for data in all_lines.values():
        pts = data.get("pts") if isinstance(data.get("pts"), dict) else None
        reb = data.get("reb") if isinstance(data.get("reb"), dict) else None
        ast = data.get("ast") if isinstance(data.get("ast"), dict) else None

        if pts and reb and ast:
            try:
                pra_line = float(pts.get("line")) + float(reb.get("line")) + float(ast.get("line"))
            except (ValueError, TypeError):
                continue
            data["pra"] = {"line": round(pra_line, 1), "over_odds": None, "under_odds": None}

    # Filter to starters if requested (players with pts line >= 10.5)
    if starters_only:
        filtered_lines = {}
        for player_name, data in all_lines.items():
            pts_data = data.get("pts", {})
            if isinstance(pts_data, dict) and pts_data.get("line", 0) >= 10.5:
                filtered_lines[player_name] = data
            elif not starters_only:
                filtered_lines[player_name] = data
        all_lines = filtered_lines
    
    print(f"[INFO] Fetched lines for {len(all_lines)} players across all prop types", file=sys.stderr)
    return all_lines


def fetch_all_starter_lines(
    game_date: date,
    prop_type: str = "points",
    starters_only: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """Legacy function - fetch single prop type lines.
    
    For backwards compatibility with simulation_engine.py.
    """
    print(f"[INFO] Fetching games for {game_date.isoformat()}...", file=sys.stderr)
    games = fetch_games_on_date(game_date)
    
    if not games:
        print(f"[WARN] No games found for {game_date.isoformat()}", file=sys.stderr)
        return {}
    
    print(f"[INFO] Found {len(games)} games, fetching {prop_type} props...", file=sys.stderr)
    
    all_lines: Dict[str, Dict[str, Any]] = {}
    
    for game in games:
        if not game.game_id:
            continue
        
        try:
            props = fetch_player_props_for_game(
                game.game_id,
                game_info=game,
                prop_types=[prop_type],
            )
            
            for prop in props:
                if not prop.player_name:
                    continue
                
                # Filter to starters
                if starters_only and prop_type == "points" and prop.line < 10.5:
                    continue
                
                all_lines[prop.player_name] = {
                    "line": prop.line,
                    "team": prop.team_abbr,
                    "game_id": prop.game_id,
                    "opponent": prop.opponent,
                    "over_odds": prop.over_odds,
                    "under_odds": prop.under_odds,
                    "player_id": prop.player_id,
                }
                
        except Exception as e:
            print(f"[WARN] Failed to fetch props for game {game.game_id}: {e}", file=sys.stderr)
            continue
    
    print(f"[INFO] Fetched lines for {len(all_lines)} players", file=sys.stderr)
    return all_lines


def fetch_lines_for_matchup(
    game_date: date,
    away_abbr: str,
    home_abbr: str,
    prop_types: Optional[List[str]] = None,
) -> Tuple[Dict[str, Dict[str, Any]], Optional[int]]:
    """Fetch ALL player prop lines for a specific matchup.
    
    Args:
        game_date: Date of the game.
        away_abbr: Away team abbreviation (e.g., "BOS").
        home_abbr: Home team abbreviation (e.g., "MIN").
        prop_types: List of prop types to fetch. If None, fetches all.
        
    Returns:
        Tuple of (player_name -> nested stat dict, game_id or None)
    """
    games = fetch_games_on_date(game_date)
    
    # Find the specific matchup
    target_game: Optional[GameInfo] = None
    for game in games:
        if game.home_team_abbr == home_abbr.upper() and game.away_team_abbr == away_abbr.upper():
            target_game = game
            break
    
    if target_game is None:
        print(f"[WARN] No game found for {away_abbr} @ {home_abbr} on {game_date.isoformat()}", file=sys.stderr)
        return {}, None
    
    props = fetch_player_props_for_game(
        target_game.game_id,
        game_info=target_game,
        prop_types=prop_types,
    )
    
    # Build nested structure
    lines: Dict[str, Dict[str, Any]] = {}
    for prop in props:
        if not prop.player_name:
            continue
        
        if prop.player_name not in lines:
            lines[prop.player_name] = {
                "player_id": prop.player_id,
                "team": prop.team_abbr,
                "game_id": prop.game_id,
                "opponent": prop.opponent,
            }
        
        lines[prop.player_name][prop.prop_type] = {
            "line": prop.line,
            "over_odds": prop.over_odds,
            "under_odds": prop.under_odds,
        }
    
    return lines, target_game.game_id


def get_baselines_for_simulation(
    game_date: date,
    away_abbr: Optional[str] = None,
    home_abbr: Optional[str] = None,
    stat_type: str = "pts",
) -> Dict[str, float]:
    """Get player lines formatted for simulation_engine.py baselines parameter.
    
    If away_abbr and home_abbr are provided, fetches lines for that specific matchup.
    Otherwise, fetches all available lines for the date.
    
    Args:
        game_date: Date to fetch lines for.
        away_abbr: Optional away team abbreviation.
        home_abbr: Optional home team abbreviation.
        stat_type: Stat type to fetch ("pts", "reb", "ast", "fg3m", "pra").
        
    Returns:
        Dict mapping player names to their lines (e.g., {"Jayson Tatum": 27.5}).
    """
    # Map our stat types back to API prop types
    reverse_map = {"pts": "points", "reb": "rebounds", "ast": "assists", "fg3m": "threes", "pra": "points_rebounds_assists"}
    prop_type = reverse_map.get(stat_type, "points")
    
    if away_abbr and home_abbr:
        lines, _ = fetch_lines_for_matchup(game_date, away_abbr, home_abbr, [prop_type])
        # Extract just the specified stat type
        result = {}
        for name, data in lines.items():
            if stat_type in data and isinstance(data[stat_type], dict):
                result[name] = data[stat_type]["line"]
        return result
    else:
        all_lines = fetch_all_lines_for_slate(game_date, [prop_type], starters_only=True)
        # Extract just the specified stat type
        result = {}
        for name, data in all_lines.items():
            if stat_type in data and isinstance(data[stat_type], dict):
                result[name] = data[stat_type]["line"]
        return result


def build_full_lines_payload(game_date: date) -> Dict[str, Any]:
    """Build complete lines payload for parlay_optimizer_v3.py.
    
    Returns:
        {
            "meta": {"date": "...", "players_count": N, "stat_types": [...]},
            "lines": {
                "player_name": {
                    "player_id": 123,
                    "team": "BOS",
                    "game_id": 12345,
                    "opponent": "MIN",
                    "pts": {"line": 25.5, "over_odds": -110, "under_odds": -110},
                    "reb": {...},
                    ...
                }
            }
        }
    """
    all_lines = fetch_all_lines_for_slate(game_date, starters_only=True)
    
    # Count stat types present
    stat_types_found: set = set()
    for player_data in all_lines.values():
        for key in ["pts", "reb", "ast", "fg3m", "pra"]:
            if key in player_data:
                stat_types_found.add(key)
    
    return {
        "meta": {
            "date": game_date.isoformat(),
            "players_count": len(all_lines),
            "stat_types": sorted(list(stat_types_found)),
        },
        "lines": all_lines,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pro Parlay Syndicate v3.0 - Live Lines Fetcher"
    )
    parser.add_argument(
        "--game-id",
        type=int,
        default=None,
        help="BallDontLie Game ID to fetch props for.",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Game date (YYYY-MM-DD) to fetch all props for.",
    )
    parser.add_argument(
        "--away",
        default=None,
        help="Away team abbreviation for specific matchup.",
    )
    parser.add_argument(
        "--home",
        default=None,
        help="Home team abbreviation for specific matchup.",
    )
    parser.add_argument(
        "--prop-type",
        default=None,
        help="Specific prop type to fetch (points, rebounds, assists, threes, pra). If omitted, fetches all.",
    )
    parser.add_argument(
        "--all-players",
        action="store_true",
        help="Include all players, not just probable starters.",
    )
    parser.add_argument(
        "--simple",
        action="store_true",
        help="Output simple format (legacy compatibility).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path (optional).",
    )
    args = parser.parse_args()

    # Validate args
    if not args.game_id and not args.date:
        args.date = date.today().isoformat()
        print(f"[INFO] No date specified, using today: {args.date}", file=sys.stderr)
    
    # Determine prop types to fetch
    prop_types = None
    if args.prop_type:
        prop_types = [args.prop_type]
    
    # Fetch based on args
    if args.game_id:
        # Single game by ID
        props = fetch_player_props_for_game(args.game_id, prop_types=prop_types)
        
        if args.simple:
            # Group by player, then by stat type
            output: Any = {}
            for p in props:
                if p.player_name not in output:
                    output[p.player_name] = {}
                output[p.player_name][p.prop_type] = p.line
        else:
            output = [asdict(p) for p in props]
        
    elif args.away and args.home and args.date:
        # Specific matchup
        game_date = date.fromisoformat(args.date)
        lines, game_id = fetch_lines_for_matchup(
            game_date, args.away, args.home, prop_types
        )
        if args.simple:
            output = lines
        else:
            output = {
                "game_id": game_id,
                "matchup": f"{args.away.upper()} @ {args.home.upper()}",
                "date": args.date,
                "lines": lines,
            }
        
    elif args.date:
        # Full slate - all games, all prop types
        game_date = date.fromisoformat(args.date)
        
        if args.simple:
            # Legacy format: simple name -> line for single prop type
            all_lines = fetch_all_starter_lines(
                game_date,
                args.prop_type or "points",
                starters_only=not args.all_players,
            )
            output = {name: info["line"] for name, info in all_lines.items()}
        else:
            # Full v3 format with all prop types
            output = build_full_lines_payload(game_date)
    
    else:
        print("Error: Must provide --game-id, --date, or both --away and --home", file=sys.stderr)
        sys.exit(1)
    
    # Output
    result = json.dumps(output, indent=2)
    
    if args.output:
        with open(args.output, "w") as f:
            f.write(result)
        print(f"[INFO] Output written to {args.output}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()
