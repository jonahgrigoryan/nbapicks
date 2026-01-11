"""
Data fetching clients for NBA Live Win Probability Predictor.

Handles API communication with balldontlie and The Odds API.
Includes rate limiting, retry logic, and caching.
"""

import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from .config import CONFIG
from .model import GameState, SeasonStats, TeamStats


@dataclass
class APIError:
    """Represents an API error for logging."""
    source: str
    code: int
    message: str
    timestamp: str


class RateLimiter:
    """Simple rate limiter for API calls."""
    
    def __init__(self, max_requests: int, window_sec: int):
        self.max_requests = max_requests
        self.window_sec = window_sec
        self.requests: List[float] = []
    
    def wait_if_needed(self) -> None:
        """Block until we can make another request."""
        now = time.time()
        # Remove old requests outside window
        self.requests = [t for t in self.requests if now - t < self.window_sec]
        
        if len(self.requests) >= self.max_requests:
            oldest = self.requests[0]
            sleep_time = self.window_sec - (now - oldest) + 0.1
            if sleep_time > 0:
                time.sleep(sleep_time)
        
        self.requests.append(time.time())


class BallDontLieClient:
    """Client for balldontlie API."""
    
    BASE_URL = "https://api.balldontlie.io/v1"
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("BALLDONTLIE_API_KEY")
        self.rate_limiter = RateLimiter(max_requests=60, window_sec=60)
        self.session = requests.Session()
        if self.api_key:
            self.session.headers["Authorization"] = self.api_key
        self.errors: List[APIError] = []
        self._season_stats_cache: Dict[str, Dict[int, SeasonStats]] = {}
        self._cache_date: Optional[str] = None
    
    def _request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make a request with rate limiting and retry logic."""
        url = f"{self.BASE_URL}/{endpoint}"
        
        for attempt in range(CONFIG["api_max_retries"]):
            self.rate_limiter.wait_if_needed()
            
            try:
                response = self.session.get(url, params=params, timeout=30)
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 429:
                    # Rate limited, wait and retry
                    backoff = CONFIG["api_retry_backoff_ms"] * (2 ** attempt) / 1000
                    time.sleep(backoff)
                    continue
                else:
                    self.errors.append(APIError(
                        source="balldontlie",
                        code=response.status_code,
                        message=response.text[:200],
                        timestamp=datetime.utcnow().isoformat() + "Z"
                    ))
                    return None
                    
            except requests.RequestException as e:
                self.errors.append(APIError(
                    source="balldontlie",
                    code=0,
                    message=str(e)[:200],
                    timestamp=datetime.utcnow().isoformat() + "Z"
                ))
                if attempt < CONFIG["api_max_retries"] - 1:
                    backoff = CONFIG["api_retry_backoff_ms"] * (2 ** attempt) / 1000
                    time.sleep(backoff)
                    continue
                return None
        
        return None
    
    def get_live_games(self, date: Optional[str] = None) -> List[Dict]:
        """Get games for a specific date (default: today)."""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        
        data = self._request("games", {"dates[]": date})
        if data and "data" in data:
            return data["data"]
        return []
    
    def get_game(self, game_id: int) -> Optional[Dict]:
        """Get a specific game by ID."""
        data = self._request(f"games/{game_id}")
        if data and "data" in data:
            return data["data"]
        return data
    
    def get_box_score(self, game_id: int) -> Optional[Dict]:
        """Get box score for a game."""
        data = self._request(f"box_scores", {"game_ids[]": game_id})
        if data and "data" in data and len(data["data"]) > 0:
            return data["data"][0]
        return None
    
    def get_team_season_stats(self, team_id: int, season: int) -> Optional[SeasonStats]:
        """Get season stats for a team with caching."""
        today = datetime.now().strftime("%Y-%m-%d")
        
        # Check cache freshness
        if self._cache_date != today:
            self._season_stats_cache = {}
            self._cache_date = today
        
        cache_key = f"{season}"
        if cache_key in self._season_stats_cache:
            if team_id in self._season_stats_cache[cache_key]:
                return self._season_stats_cache[cache_key][team_id]
        
        # Fetch from API
        data = self._request("season_averages", {
            "season": season,
            "team_ids[]": team_id
        })
        
        if data and "data" in data and len(data["data"]) > 0:
            stats = data["data"][0]
            # Calculate eFG and TOV rate from raw stats
            fgm = stats.get("fgm", 0)
            fga = stats.get("fga", 1)
            fg3m = stats.get("fg3m", 0)
            fta = stats.get("fta", 0)
            tov = stats.get("turnover", 0)
            orb = stats.get("oreb", 0)
            
            efg = (fgm + 0.5 * fg3m) / fga if fga > 0 else CONFIG["league_avg_efg"]
            poss = fga + 0.44 * fta + tov - orb
            tov_rate = tov / poss if poss > 0 else CONFIG["league_avg_tov_rate"]
            
            season_stats = SeasonStats(efg=efg, tov_rate=tov_rate)
            
            # Cache it
            if cache_key not in self._season_stats_cache:
                self._season_stats_cache[cache_key] = {}
            self._season_stats_cache[cache_key][team_id] = season_stats
            
            return season_stats
        
        # Return league averages as fallback
        return SeasonStats(
            efg=CONFIG["league_avg_efg"],
            tov_rate=CONFIG["league_avg_tov_rate"]
        )
    
    def parse_game_state(self, game_data: Dict) -> GameState:
        """Parse API response into GameState."""
        home_team = game_data.get("home_team", {})
        away_team = game_data.get("visitor_team", {})
        
        return GameState(
            home_team=home_team.get("full_name", "Home"),
            away_team=away_team.get("full_name", "Away"),
            home_team_abbrev=home_team.get("abbreviation", home_team.get("full_name", "HOM")[:3].upper()),
            away_team_abbrev=away_team.get("abbreviation", away_team.get("full_name", "AWY")[:3].upper()),
            home_score=game_data.get("home_team_score", 0),
            away_score=game_data.get("visitor_team_score", 0),
            quarter=game_data.get("period", 0),
            clock=game_data.get("time", None),
            status=game_data.get("status", "")
        )
    
    def parse_team_stats(self, box_score: Dict, is_home: bool) -> TeamStats:
        """Parse box score into TeamStats."""
        key = "home_team" if is_home else "visitor_team"
        team_stats = box_score.get(key, {})
        
        return TeamStats(
            fgm=team_stats.get("fgm", 0),
            fga=team_stats.get("fga", 0),
            fg3m=team_stats.get("fg3m", 0),
            fta=team_stats.get("fta", 0),
            tov=team_stats.get("turnover", 0),
            orb=team_stats.get("oreb", 0)
        )


class OddsAPIClient:
    """Client for The Odds API."""
    
    BASE_URL = "https://api.the-odds-api.com/v4"
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ODDS_API_KEY")
        self.session = requests.Session()
        self.errors: List[APIError] = []
        self._spread_cache: Dict[str, float] = {}
    
    def _request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make a request to the Odds API."""
        if not self.api_key:
            return None
        
        url = f"{self.BASE_URL}/{endpoint}"
        params = params or {}
        params["apiKey"] = self.api_key
        
        try:
            response = self.session.get(url, params=params, timeout=30)
            
            if response.status_code == 200:
                return response.json()
            else:
                self.errors.append(APIError(
                    source="odds_api",
                    code=response.status_code,
                    message=response.text[:200],
                    timestamp=datetime.utcnow().isoformat() + "Z"
                ))
                return None
                
        except requests.RequestException as e:
            self.errors.append(APIError(
                source="odds_api",
                code=0,
                message=str(e)[:200],
                timestamp=datetime.utcnow().isoformat() + "Z"
            ))
            return None
    
    def get_nba_spreads(self) -> Dict[str, float]:
        """
        Get current NBA spreads.
        
        Returns dict mapping "home_team vs away_team" to home spread.
        """
        data = self._request(
            "sports/basketball_nba/odds",
            {
                "regions": "us",
                "markets": "spreads",
                "oddsFormat": "american"
            }
        )
        
        spreads = {}
        if data:
            for game in data:
                home_team = game.get("home_team", "")
                away_team = game.get("away_team", "")
                key = f"{home_team} vs {away_team}"
                
                # Find spread from bookmakers
                for bookmaker in game.get("bookmakers", []):
                    for market in bookmaker.get("markets", []):
                        if market.get("key") == "spreads":
                            for outcome in market.get("outcomes", []):
                                if outcome.get("name") == home_team:
                                    spread = outcome.get("point", 0)
                                    spreads[key] = spread
                                    break
                    if key in spreads:
                        break
        
        self._spread_cache = spreads
        return spreads
    
    def get_spread_for_game(
        self,
        home_team: str,
        away_team: str,
        refresh: bool = False
    ) -> Optional[float]:
        """
        Get spread for a specific game.
        
        Returns spread from home team perspective (negative = home favored).
        """
        if refresh or not self._spread_cache:
            self.get_nba_spreads()
        
        # Try exact match first
        key = f"{home_team} vs {away_team}"
        if key in self._spread_cache:
            return self._spread_cache[key]
        
        # Extract team identifiers for fuzzy matching (use nickname to avoid LA/NY collisions)
        def get_team_words(name: str) -> set:
            words = [w for w in name.lower().split() if w]
            return {words[-1]} if words else set()
        
        home_words = get_team_words(home_team)
        away_words = get_team_words(away_team)
        
        # Try fuzzy match with home/away awareness (flip sign on reversed match)
        for cached_key, spread in self._spread_cache.items():
            if " vs " not in cached_key:
                continue
            cached_home, cached_away = cached_key.split(" vs ", 1)
            cached_home_words = get_team_words(cached_home)
            cached_away_words = get_team_words(cached_away)
            
            home_match = bool(home_words & cached_home_words)
            away_match = bool(away_words & cached_away_words)
            if home_match and away_match:
                return spread
            
            home_match_rev = bool(home_words & cached_away_words)
            away_match_rev = bool(away_words & cached_home_words)
            if home_match_rev and away_match_rev:
                return -spread
        
        return None


class DataFetcher:
    """Combined data fetcher for all sources."""
    
    def __init__(
        self,
        bdl_api_key: Optional[str] = None,
        odds_api_key: Optional[str] = None
    ):
        self.bdl = BallDontLieClient(bdl_api_key)
        self.odds = OddsAPIClient(odds_api_key)
        self.last_fetch_time: Optional[float] = None
    
    def get_all_errors(self) -> List[Dict]:
        """Get all API errors for logging."""
        errors = []
        for e in self.bdl.errors + self.odds.errors:
            errors.append({
                "source": e.source,
                "code": e.code,
                "message": e.message,
                "timestamp": e.timestamp
            })
        return errors
    
    def clear_errors(self) -> None:
        """Clear error lists."""
        self.bdl.errors = []
        self.odds.errors = []
    
    def get_data_age_sec(self) -> float:
        """Get seconds since last fetch."""
        if self.last_fetch_time is None:
            return 0
        return time.time() - self.last_fetch_time
    
    def fetch_game_data(
        self,
        game_id: int,
        season: int = 2024
    ) -> Optional[Dict]:
        """
        Fetch all data needed for prediction.
        
        Returns dict with game_state, home_stats, away_stats,
        home_season, away_season, spread.
        """
        self.last_fetch_time = time.time()
        
        # Get game info
        game = self.bdl.get_game(game_id)
        if not game:
            return None
        
        game_state = self.bdl.parse_game_state(game)
        
        # Get box score
        box_score = self.bdl.get_box_score(game_id)
        if box_score:
            home_stats = self.bdl.parse_team_stats(box_score, is_home=True)
            away_stats = self.bdl.parse_team_stats(box_score, is_home=False)
        else:
            # Empty stats if no box score yet
            home_stats = TeamStats(fgm=0, fga=0, fg3m=0, fta=0, tov=0, orb=0)
            away_stats = TeamStats(fgm=0, fga=0, fg3m=0, fta=0, tov=0, orb=0)
        
        # Get season stats
        home_team_id = game.get("home_team", {}).get("id")
        away_team_id = game.get("visitor_team", {}).get("id")
        
        home_season = self.bdl.get_team_season_stats(home_team_id, season) if home_team_id else None
        away_season = self.bdl.get_team_season_stats(away_team_id, season) if away_team_id else None
        
        # Fallback to league averages
        if home_season is None:
            home_season = SeasonStats(
                efg=CONFIG["league_avg_efg"],
                tov_rate=CONFIG["league_avg_tov_rate"]
            )
        if away_season is None:
            away_season = SeasonStats(
                efg=CONFIG["league_avg_efg"],
                tov_rate=CONFIG["league_avg_tov_rate"]
            )
        
        # Get spread
        spread = self.odds.get_spread_for_game(
            game_state.home_team,
            game_state.away_team
        )
        
        return {
            "game_state": game_state,
            "home_stats": home_stats,
            "away_stats": away_stats,
            "home_season": home_season,
            "away_season": away_season,
            "spread": spread,
            "game_id": game_id
        }
