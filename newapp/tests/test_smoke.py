"""
Smoke tests for API connectivity.

Tests basic connectivity to external APIs.
Run these tests to verify API keys and network connectivity.
"""

import os
import pytest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from predictor.data_fetcher import BallDontLieClient, OddsAPIClient


class TestBallDontLieConnectivity:
    """Smoke tests for balldontlie API connectivity."""
    
    @pytest.mark.smoke
    @pytest.mark.skipif(
        not os.getenv("BALLDONTLIE_API_KEY"),
        reason="BALLDONTLIE_API_KEY not set"
    )
    def test_can_connect_to_balldontlie(self):
        """Test basic connectivity to balldontlie API."""
        client = BallDontLieClient()
        
        # Try to fetch games (will return empty list if no games today)
        games = client.get_live_games()
        
        # Should not have errors
        assert len(client.errors) == 0, f"API errors: {client.errors}"
        # Should return a list (even if empty)
        assert isinstance(games, list)
    
    @pytest.mark.smoke
    @pytest.mark.skipif(
        not os.getenv("BALLDONTLIE_API_KEY"),
        reason="BALLDONTLIE_API_KEY not set"
    )
    def test_can_fetch_historical_game(self):
        """Test fetching a historical game date."""
        client = BallDontLieClient()
        
        # Fetch games from a known date with games
        games = client.get_live_games("2024-01-15")
        
        assert len(client.errors) == 0, f"API errors: {client.errors}"
        # Note: May be empty if date is in future or no games scheduled


class TestOddsAPIConnectivity:
    """Smoke tests for The Odds API connectivity."""
    
    @pytest.mark.smoke
    @pytest.mark.skipif(
        not os.getenv("ODDS_API_KEY"),
        reason="ODDS_API_KEY not set"
    )
    def test_can_connect_to_odds_api(self):
        """Test basic connectivity to The Odds API."""
        client = OddsAPIClient()
        
        # Try to fetch NBA spreads
        spreads = client.get_nba_spreads()
        
        # Should not have errors
        assert len(client.errors) == 0, f"API errors: {client.errors}"
        # Should return a dict (even if empty when no games)
        assert isinstance(spreads, dict)
    
    @pytest.mark.smoke
    def test_graceful_handling_without_api_key(self):
        """Test that missing API key is handled gracefully."""
        # Temporarily unset key
        client = OddsAPIClient(api_key=None)
        
        spreads = client.get_nba_spreads()
        
        # Should return empty dict, not crash
        assert spreads == {}
        assert len(client.errors) == 0


class TestCombinedConnectivity:
    """Smoke tests for combined data fetcher."""
    
    @pytest.mark.smoke
    @pytest.mark.skipif(
        not (os.getenv("BALLDONTLIE_API_KEY") and os.getenv("ODDS_API_KEY")),
        reason="API keys not set"
    )
    def test_full_data_fetch_connectivity(self):
        """Test that all APIs can be reached."""
        from predictor.data_fetcher import DataFetcher
        
        fetcher = DataFetcher()
        
        # Test balldontlie
        games = fetcher.bdl.get_live_games()
        assert isinstance(games, list)
        
        # Test odds API
        spreads = fetcher.odds.get_nba_spreads()
        assert isinstance(spreads, dict)
        
        # Check no critical errors
        errors = fetcher.get_all_errors()
        critical_errors = [e for e in errors if e.get("code", 0) >= 500]
        assert len(critical_errors) == 0, f"Critical API errors: {critical_errors}"


class TestEnvironmentSetup:
    """Tests to verify environment is correctly configured."""
    
    @pytest.mark.smoke
    def test_env_file_readable(self):
        """Test that .env file exists or env vars are set."""
        bdl_key = os.getenv("BALLDONTLIE_API_KEY")
        odds_key = os.getenv("ODDS_API_KEY")
        
        # At least one should be set for the predictor to be useful
        if not bdl_key and not odds_key:
            pytest.skip(
                "No API keys configured. Set BALLDONTLIE_API_KEY and/or "
                "ODDS_API_KEY environment variables."
            )
    
    @pytest.mark.smoke
    def test_config_loads(self):
        """Test that configuration loads without errors."""
        from predictor.config import CONFIG, get_config_hash
        
        assert CONFIG is not None
        assert isinstance(CONFIG, dict)
        assert "sigmoid_k" in CONFIG
        
        # Hash should be consistent
        hash1 = get_config_hash()
        hash2 = get_config_hash()
        assert hash1 == hash2
        assert len(hash1) == 8
    
    @pytest.mark.smoke
    def test_imports_work(self):
        """Test that all modules can be imported."""
        from predictor import predict, CONFIG, GameState, TeamStats, SeasonStats
        from predictor.data_fetcher import DataFetcher
        from predictor.display import display_prediction
        from predictor.logger import log_prediction
        
        assert predict is not None
        assert CONFIG is not None
        assert DataFetcher is not None
