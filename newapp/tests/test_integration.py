"""
Integration tests with mocked API responses.

Tests the full prediction pipeline with realistic mock data.
"""

import json
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from predictor.data_fetcher import (
    BallDontLieClient,
    OddsAPIClient,
    DataFetcher,
    RateLimiter,
)
from predictor.model import predict, GameState, TeamStats, SeasonStats
from predictor.logger import format_prediction_log, log_prediction


# Mock API responses
MOCK_GAME_RESPONSE = {
    "id": 12345,
    "home_team": {
        "id": 1,
        "full_name": "Los Angeles Lakers"
    },
    "visitor_team": {
        "id": 2,
        "full_name": "Boston Celtics"
    },
    "home_team_score": 87,
    "visitor_team_score": 82,
    "period": 3,
    "time": "4:32",
    "status": "In Progress"
}

MOCK_BOX_SCORE_RESPONSE = {
    "data": [{
        "home_team": {
            "fgm": 35,
            "fga": 70,
            "fg3m": 10,
            "fta": 15,
            "turnover": 10,
            "oreb": 8
        },
        "visitor_team": {
            "fgm": 32,
            "fga": 72,
            "fg3m": 8,
            "fta": 12,
            "turnover": 12,
            "oreb": 7
        }
    }]
}

MOCK_SEASON_STATS_RESPONSE = {
    "data": [{
        "fgm": 40,
        "fga": 85,
        "fg3m": 12,
        "fta": 20,
        "turnover": 12,
        "oreb": 10
    }]
}

MOCK_ODDS_RESPONSE = [
    {
        "home_team": "Los Angeles Lakers",
        "away_team": "Boston Celtics",
        "bookmakers": [{
            "markets": [{
                "key": "spreads",
                "outcomes": [
                    {"name": "Los Angeles Lakers", "point": -3.5},
                    {"name": "Boston Celtics", "point": 3.5}
                ]
            }]
        }]
    }
]


class TestRateLimiter:
    """Tests for RateLimiter class."""
    
    def test_allows_requests_under_limit(self):
        """Test that requests under limit are allowed immediately."""
        limiter = RateLimiter(max_requests=10, window_sec=60)
        
        # Should not block
        for _ in range(5):
            limiter.wait_if_needed()
        
        assert len(limiter.requests) == 5
    
    def test_clears_old_requests(self):
        """Test that old requests are cleared from window."""
        limiter = RateLimiter(max_requests=2, window_sec=1)
        
        # Add old timestamp
        limiter.requests = [0.0]  # Very old
        limiter.wait_if_needed()
        
        # Old request should be cleared
        assert len(limiter.requests) == 1


class TestBallDontLieClient:
    """Tests for BallDontLieClient with mocked responses."""
    
    @patch('requests.Session.get')
    def test_get_live_games(self, mock_get):
        """Test fetching live games."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [MOCK_GAME_RESPONSE]}
        mock_get.return_value = mock_response
        
        client = BallDontLieClient(api_key="test_key")
        games = client.get_live_games("2024-01-15")
        
        assert len(games) == 1
        assert games[0]["id"] == 12345
    
    @patch('requests.Session.get')
    def test_get_game(self, mock_get):
        """Test fetching a specific game."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = MOCK_GAME_RESPONSE
        mock_get.return_value = mock_response
        
        client = BallDontLieClient(api_key="test_key")
        game = client.get_game(12345)
        
        assert game is not None
        assert game["home_team"]["full_name"] == "Los Angeles Lakers"
    
    @patch('requests.Session.get')
    def test_parse_game_state(self, mock_get):
        """Test parsing game data into GameState."""
        client = BallDontLieClient(api_key="test_key")
        state = client.parse_game_state(MOCK_GAME_RESPONSE)
        
        assert state.home_team == "Los Angeles Lakers"
        assert state.away_team == "Boston Celtics"
        assert state.home_score == 87
        assert state.away_score == 82
        assert state.quarter == 3
        assert state.clock == "4:32"
    
    @patch('requests.Session.get')
    def test_retry_on_429(self, mock_get):
        """Test retry logic on rate limit."""
        # First call returns 429, second returns 200
        mock_429 = Mock()
        mock_429.status_code = 429
        
        mock_200 = Mock()
        mock_200.status_code = 200
        mock_200.json.return_value = {"data": []}
        
        mock_get.side_effect = [mock_429, mock_200]
        
        client = BallDontLieClient(api_key="test_key")
        result = client.get_live_games()
        
        assert mock_get.call_count == 2
    
    @patch('requests.Session.get')
    def test_error_logging(self, mock_get):
        """Test that errors are logged."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_get.return_value = mock_response
        
        client = BallDontLieClient(api_key="test_key")
        client.get_live_games()
        
        assert len(client.errors) == 1
        assert client.errors[0].code == 500


class TestOddsAPIClient:
    """Tests for OddsAPIClient with mocked responses."""
    
    @patch('requests.Session.get')
    def test_get_nba_spreads(self, mock_get):
        """Test fetching NBA spreads."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = MOCK_ODDS_RESPONSE
        mock_get.return_value = mock_response
        
        client = OddsAPIClient(api_key="test_key")
        spreads = client.get_nba_spreads()
        
        assert "Los Angeles Lakers vs Boston Celtics" in spreads
        assert spreads["Los Angeles Lakers vs Boston Celtics"] == -3.5
    
    @patch('requests.Session.get')
    def test_get_spread_for_game(self, mock_get):
        """Test getting spread for specific game."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = MOCK_ODDS_RESPONSE
        mock_get.return_value = mock_response
        
        client = OddsAPIClient(api_key="test_key")
        spread = client.get_spread_for_game(
            "Los Angeles Lakers",
            "Boston Celtics",
            refresh=True
        )
        
        assert spread == -3.5
    
    def test_no_api_key(self):
        """Test behavior when no API key provided."""
        client = OddsAPIClient(api_key=None)
        spreads = client.get_nba_spreads()
        
        assert spreads == {}
    
    @patch('requests.Session.get')
    def test_partial_match(self, mock_get):
        """Test partial team name matching."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = MOCK_ODDS_RESPONSE
        mock_get.return_value = mock_response
        
        client = OddsAPIClient(api_key="test_key")
        client.get_nba_spreads()
        
        # Should match partial names
        spread = client.get_spread_for_game("Lakers", "Celtics")
        assert spread == -3.5


class TestDataFetcher:
    """Tests for combined DataFetcher."""
    
    @patch.object(BallDontLieClient, 'get_game')
    @patch.object(BallDontLieClient, 'get_box_score')
    @patch.object(BallDontLieClient, 'get_team_season_stats')
    @patch.object(OddsAPIClient, 'get_spread_for_game')
    def test_fetch_game_data(
        self, mock_spread, mock_season, mock_box, mock_game
    ):
        """Test fetching all game data."""
        mock_game.return_value = MOCK_GAME_RESPONSE
        mock_box.return_value = MOCK_BOX_SCORE_RESPONSE["data"][0]
        mock_season.return_value = SeasonStats(efg=0.52, tov_rate=0.12)
        mock_spread.return_value = -3.5
        
        fetcher = DataFetcher()
        data = fetcher.fetch_game_data(12345)
        
        assert data is not None
        assert data["game_state"].home_team == "Los Angeles Lakers"
        assert data["spread"] == -3.5
    
    @patch.object(BallDontLieClient, 'get_game')
    def test_fetch_game_data_failure(self, mock_game):
        """Test handling game fetch failure."""
        mock_game.return_value = None
        
        fetcher = DataFetcher()
        data = fetcher.fetch_game_data(99999)
        
        assert data is None
    
    def test_error_aggregation(self):
        """Test that errors from both clients are aggregated."""
        fetcher = DataFetcher()
        
        # Manually add errors
        from predictor.data_fetcher import APIError
        fetcher.bdl.errors.append(APIError(
            source="balldontlie", code=500, message="Error 1",
            timestamp="2024-01-15T00:00:00Z"
        ))
        fetcher.odds.errors.append(APIError(
            source="odds_api", code=401, message="Error 2",
            timestamp="2024-01-15T00:00:00Z"
        ))
        
        errors = fetcher.get_all_errors()
        assert len(errors) == 2
        
        fetcher.clear_errors()
        assert len(fetcher.get_all_errors()) == 0


class TestFullPipeline:
    """Integration tests for the full prediction pipeline."""
    
    def test_full_prediction_flow(self):
        """Test complete prediction flow with mock data."""
        game_state = GameState(
            home_team="Los Angeles Lakers",
            away_team="Boston Celtics",
            home_score=87,
            away_score=82,
            quarter=3,
            clock="4:32",
            status="In Progress"
        )
        home_stats = TeamStats(fgm=35, fga=70, fg3m=10, fta=15, tov=10, orb=8)
        away_stats = TeamStats(fgm=32, fga=72, fg3m=8, fta=12, tov=12, orb=7)
        home_season = SeasonStats(efg=0.52, tov_rate=0.12)
        away_season = SeasonStats(efg=0.51, tov_rate=0.13)
        
        # Run prediction
        result = predict(
            game_state, home_stats, away_stats,
            home_season, away_season,
            spread=-3.5, data_age_sec=45
        )
        
        assert result is not None
        
        # Format for logging
        log_entry = format_prediction_log(
            prediction=result,
            game_id="12345",
            home_team="Los Angeles Lakers",
            away_team="Boston Celtics",
            home_score=87,
            away_score=82,
            quarter=3,
            clock="4:32",
            spread=-3.5,
            data_freshness_sec=45,
            api_errors=[]
        )
        
        # Verify log structure
        assert log_entry["model_version"] == "1.0.0"
        assert log_entry["game_id"] == "12345"
        assert "factors" in log_entry
        assert "win_prob" in log_entry
        assert log_entry["win_prob"]["home"] + log_entry["win_prob"]["away"] == pytest.approx(1.0)
    
    def test_pre_game_flow(self):
        """Test pre-game prediction flow."""
        game_state = GameState(
            home_team="Lakers",
            away_team="Celtics",
            home_score=0,
            away_score=0,
            quarter=0,
            clock=None,
            status="Scheduled"
        )
        home_stats = TeamStats(fgm=0, fga=0, fg3m=0, fta=0, tov=0, orb=0)
        away_stats = TeamStats(fgm=0, fga=0, fg3m=0, fta=0, tov=0, orb=0)
        home_season = SeasonStats(efg=0.52, tov_rate=0.12)
        away_season = SeasonStats(efg=0.51, tov_rate=0.13)
        
        # With spread - should work
        result = predict(
            game_state, home_stats, away_stats,
            home_season, away_season,
            spread=-5.0, data_age_sec=0
        )
        assert result is not None
        assert result.win_prob_home > 0.5  # Home favored
        
        # Without spread - should return None
        result = predict(
            game_state, home_stats, away_stats,
            home_season, away_season,
            spread=None, data_age_sec=0
        )
        assert result is None
    
    def test_blowout_detection_flow(self):
        """Test blowout detection in full flow."""
        game_state = GameState(
            home_team="Lakers",
            away_team="Celtics",
            home_score=115,
            away_score=90,
            quarter=4,
            clock="2:00",
            status="In Progress"
        )
        home_stats = TeamStats(fgm=45, fga=80, fg3m=12, fta=20, tov=8, orb=10)
        away_stats = TeamStats(fgm=35, fga=85, fg3m=8, fta=15, tov=14, orb=8)
        home_season = SeasonStats(efg=0.52, tov_rate=0.12)
        away_season = SeasonStats(efg=0.51, tov_rate=0.13)
        
        result = predict(
            game_state, home_stats, away_stats,
            home_season, away_season,
            spread=-3.5, data_age_sec=30
        )
        
        assert result is not None
        assert result.is_blowout is True
        assert result.win_prob_home == 0.99


class TestLoggerIntegration:
    """Tests for logger integration."""
    
    def test_log_format_matches_schema(self):
        """Test that log format matches expected schema."""
        game_state = GameState(
            home_team="Lakers",
            away_team="Celtics",
            home_score=87,
            away_score=82,
            quarter=3,
            clock="4:32",
            status="In Progress"
        )
        home_stats = TeamStats(fgm=35, fga=70, fg3m=10, fta=15, tov=10, orb=8)
        away_stats = TeamStats(fgm=32, fga=72, fg3m=8, fta=12, tov=12, orb=7)
        home_season = SeasonStats(efg=0.52, tov_rate=0.12)
        away_season = SeasonStats(efg=0.51, tov_rate=0.13)
        
        result = predict(
            game_state, home_stats, away_stats,
            home_season, away_season,
            spread=-3.5, data_age_sec=45
        )
        
        log_entry = format_prediction_log(
            prediction=result,
            game_id="12345",
            home_team="Lakers",
            away_team="Celtics",
            home_score=87,
            away_score=82,
            quarter=3,
            clock="4:32",
            spread=-3.5,
            data_freshness_sec=45,
            api_errors=[]
        )
        
        # Verify all required fields
        required_fields = [
            "model_version", "config_hash", "timestamp", "game_id",
            "home_team", "away_team", "game_status", "score", "quarter",
            "clock", "minutes_remaining", "minutes_played", "is_overtime",
            "is_blowout", "factors", "win_prob", "confidence",
            "data_freshness_sec", "trailing_team", "trailing_edge_alert",
            "api_errors"
        ]
        
        for field in required_fields:
            assert field in log_entry, f"Missing field: {field}"
        
        # Verify JSON serializable
        json_str = json.dumps(log_entry)
        assert json_str is not None
    
    def test_log_with_api_errors(self):
        """Test logging with API errors included."""
        game_state = GameState(
            home_team="Lakers",
            away_team="Celtics",
            home_score=87,
            away_score=82,
            quarter=3,
            clock="4:32",
            status="In Progress"
        )
        home_stats = TeamStats(fgm=35, fga=70, fg3m=10, fta=15, tov=10, orb=8)
        away_stats = TeamStats(fgm=32, fga=72, fg3m=8, fta=12, tov=12, orb=7)
        home_season = SeasonStats(efg=0.52, tov_rate=0.12)
        away_season = SeasonStats(efg=0.51, tov_rate=0.13)
        
        result = predict(
            game_state, home_stats, away_stats,
            home_season, away_season,
            spread=-3.5, data_age_sec=45
        )
        
        api_errors = [
            {
                "source": "balldontlie",
                "code": 429,
                "message": "Rate limit exceeded",
                "timestamp": "2024-01-15T20:30:00Z"
            }
        ]
        
        log_entry = format_prediction_log(
            prediction=result,
            game_id="12345",
            home_team="Lakers",
            away_team="Celtics",
            home_score=87,
            away_score=82,
            quarter=3,
            clock="4:32",
            spread=-3.5,
            data_freshness_sec=45,
            api_errors=api_errors
        )
        
        assert len(log_entry["api_errors"]) == 1
        assert log_entry["api_errors"][0]["code"] == 429
