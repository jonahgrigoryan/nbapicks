#!/usr/bin/env python3
"""fetch_live_lines.py

GOAT All-Star tier player props fetcher for NBA points predictions.

Fetches live betting lines (Points Over/Under) from the BallDontLie API
using the /nba/v2/player_props endpoint (requires GOAT subscription).

Features:
- Fetch all games on a specific date
- Get player props for a specific game
- Batch fetch all starter lines for a slate
- Output format compatible with simulation_engine.py

Usage:
  # Fetch props for a specific game:
  python3 fetch_live_lines.py --game-id 12345

  # Fetch all props for today's games:
  python3 fetch_live_lines.py --date 2025-01-15

  # Fetch props for specific matchup:
  python3 fetch_live_lines.py --date 2025-01-15 --away BOS --home MIN

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


BALDONTLIE_BASE_V1 = "https://api.balldontlie.io/v1"
BALDONTLIE_BASE_V2 = "https://api.balldontlie.io/v2"
BALDONTLIE_BASE_NBA_V1 = "https://api.balldontlie.io/nba/v1"

# Rate limiter (shared with fetch_points_game_data.py pattern)
_REQUEST_WINDOW_SEC = 60
_MAX_REQUESTS_PER_WINDOW = 50
_REQUEST_TIMES: deque = deque()


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

    # Retry on 429
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
class PlayerProp:
    """A single player prop bet."""
    player_id: int
    player_name: str
    team_abbr: str
    prop_type: str  # "points", "rebounds", "assists", etc.
    line: float
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


def fetch_player_props_for_game(game_id: int, prop_type: str = "points") -> List[PlayerProp]:
    """Fetch player props for a specific game from GOAT endpoint.
    
    Uses /v2/odds/player_props (GOAT All-Star tier required).
    
    Args:
        game_id: BallDontLie game ID.
        prop_type: Type of prop to fetch ("points", "rebounds", "assists", etc.)
        
    Returns:
        List of PlayerProp objects with betting lines.
    """
    try:
        params: Dict[str, Any] = {
            "game_id": game_id,
        }
        
        # Fetch props from GOAT endpoint
        data = bdl_get("/v2/odds/player_props", params)
        
        props: List[PlayerProp] = []
        player_cache: Dict[int, Dict[str, Any]] = {}
        
        # Filter for over_under market type (traditional O/U lines)
        # and filter by prop_type
        for prop in data.get("data", []):
            # Filter by prop type - use exact match to avoid combo props
            # (e.g., "points_assists" should not match when looking for "assists")
            prop_type_actual = str(prop.get("prop_type", "")).lower()
            if prop_type_actual != prop_type.lower():
                continue
            
            # Only process over_under markets (traditional O/U lines)
            market = prop.get("market", {})
            market_type = str(market.get("type", "")).lower()
            if market_type != "over_under":
                continue
            
            player_id = int(prop.get("player_id") or 0)
            if not player_id:
                continue
            
            # Fetch player info if not cached
            if player_id not in player_cache:
                player_data = _fetch_player_info(player_id)
                if player_data:
                    player_cache[player_id] = player_data
                else:
                    continue
            
            player = player_cache[player_id]
            team = player.get("team") or {}
            
            # Build player name
            player_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
            if not player_name:
                player_name = str(player.get("full_name") or "")
            
            # Get line value
            line_value = prop.get("line_value")
            if line_value is None:
                continue
            
            try:
                line = float(line_value)
            except (ValueError, TypeError):
                continue
            
            # Get odds from market (over_under markets have over_odds and under_odds)
            over_odds = market.get("over_odds")
            under_odds = market.get("under_odds")
            
            # Convert to int if they exist
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
            
            props.append(PlayerProp(
                player_id=player_id,
                player_name=player_name,
                team_abbr=str(team.get("abbreviation") or "").upper(),
                prop_type=prop_type,
                line=line,
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


def fetch_all_starter_lines(
    game_date: date,
    prop_type: str = "points",
    starters_only: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """Fetch player prop lines for all games on a date.
    
    Returns a dict suitable for use with simulation_engine.py:
    {
        "player_name": {
            "line": 25.5,
            "team": "BOS",
            "game_id": 12345,
            "over_odds": -110,
            "under_odds": -110
        },
        ...
    }
    
    Args:
        game_date: Date to fetch games for.
        prop_type: Type of prop ("points", "rebounds", "assists").
        starters_only: If True, filter to likely starters (lines >= 10.5 for points).
        
    Returns:
        Dict mapping player names to their prop info.
    """
    print(f"[INFO] Fetching games for {game_date.isoformat()}...", file=sys.stderr)
    games = fetch_games_on_date(game_date)
    
    if not games:
        print(f"[WARN] No games found for {game_date.isoformat()}", file=sys.stderr)
        return {}
    
    print(f"[INFO] Found {len(games)} games, fetching props...", file=sys.stderr)
    
    all_lines: Dict[str, Dict[str, Any]] = {}
    
    for game in games:
        if not game.game_id:
            continue
        
        try:
            props = fetch_player_props_for_game(game.game_id, prop_type)
            
            for prop in props:
                if not prop.player_name:
                    continue
                
                # Filter to starters (typically have higher lines)
                if starters_only and prop_type == "points" and prop.line < 10.5:
                    continue
                
                all_lines[prop.player_name] = {
                    "line": prop.line,
                    "team": prop.team_abbr,
                    "game_id": game.game_id,
                    "opponent": game.away_team_abbr if prop.team_abbr == game.home_team_abbr else game.home_team_abbr,
                    "over_odds": prop.over_odds,
                    "under_odds": prop.under_odds,
                    "player_id": prop.player_id,
                }
                
        except Exception as e:
            print(f"[WARN] Failed to fetch props for game {game.game_id} ({game.away_team_abbr}@{game.home_team_abbr}): {e}", file=sys.stderr)
            continue
    
    print(f"[INFO] Fetched lines for {len(all_lines)} players", file=sys.stderr)
    return all_lines


def fetch_lines_for_matchup(
    game_date: date,
    away_abbr: str,
    home_abbr: str,
    prop_type: str = "points",
) -> Tuple[Dict[str, float], Optional[int]]:
    """Fetch player prop lines for a specific matchup.
    
    Returns a simple dict mapping player names to lines (compatible with simulation_engine.py),
    plus the game_id for reference.
    
    Args:
        game_date: Date of the game.
        away_abbr: Away team abbreviation (e.g., "BOS").
        home_abbr: Home team abbreviation (e.g., "MIN").
        prop_type: Type of prop ("points", etc.)
        
    Returns:
        Tuple of (player_name -> line dict, game_id or None)
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
    
    props = fetch_player_props_for_game(target_game.game_id, prop_type)
    
    lines: Dict[str, float] = {}
    for prop in props:
        if prop.player_name:
            lines[prop.player_name] = prop.line
    
    return lines, target_game.game_id


def get_baselines_for_simulation(
    game_date: date,
    away_abbr: Optional[str] = None,
    home_abbr: Optional[str] = None,
) -> Dict[str, float]:
    """Get player point lines formatted for simulation_engine.py baselines parameter.
    
    If away_abbr and home_abbr are provided, fetches lines for that specific matchup.
    Otherwise, fetches all available lines for the date.
    
    Args:
        game_date: Date to fetch lines for.
        away_abbr: Optional away team abbreviation.
        home_abbr: Optional home team abbreviation.
        
    Returns:
        Dict mapping player names to their point lines (e.g., {"Jayson Tatum": 27.5}).
    """
    if away_abbr and home_abbr:
        lines, _ = fetch_lines_for_matchup(game_date, away_abbr, home_abbr, "points")
        return lines
    else:
        all_lines = fetch_all_starter_lines(game_date, "points", starters_only=True)
        # Simplify to just name -> line for simulation engine
        return {name: info["line"] for name, info in all_lines.items()}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch live player prop lines from BallDontLie GOAT API."
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
        default="points",
        help="Prop type to fetch (default: points).",
    )
    parser.add_argument(
        "--all-players",
        action="store_true",
        help="Include all players, not just probable starters.",
    )
    parser.add_argument(
        "--simple",
        action="store_true",
        help="Output simple name->line format for simulation_engine.py.",
    )
    args = parser.parse_args()

    # Validate args
    if not args.game_id and not args.date:
        # Default to today
        args.date = date.today().isoformat()
        print(f"[INFO] No date specified, using today: {args.date}", file=sys.stderr)
    
    # Fetch based on args
    if args.game_id:
        # Single game by ID
        props = fetch_player_props_for_game(args.game_id, args.prop_type)
        if args.simple:
            output = {p.player_name: p.line for p in props}
        else:
            output = [asdict(p) for p in props]
        print(json.dumps(output, indent=2))
        
    elif args.away and args.home and args.date:
        # Specific matchup
        game_date = date.fromisoformat(args.date)
        lines, game_id = fetch_lines_for_matchup(
            game_date, args.away, args.home, args.prop_type
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
        print(json.dumps(output, indent=2))
        
    elif args.date:
        # All games on date
        game_date = date.fromisoformat(args.date)
        all_lines = fetch_all_starter_lines(
            game_date,
            args.prop_type,
            starters_only=not args.all_players,
        )
        if args.simple:
            output = {name: info["line"] for name, info in all_lines.items()}
        else:
            output = all_lines
        print(json.dumps(output, indent=2))
    
    else:
        print("Error: Must provide --game-id, --date, or both --away and --home", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
