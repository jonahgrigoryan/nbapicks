"""
NBA Live Win Probability Predictor - Main Entry Point.

CLI interface for analyzing NBA games in real-time.
"""

import argparse
import sys
import time
from typing import Optional

from .config import CONFIG
from .data_fetcher import DataFetcher
from .display import (
    console,
    display_awaiting_tipoff,
    display_between_quarters,
    display_error,
    display_final,
    display_games_list,
    display_halftime,
    display_prediction,
)
from .logger import log_prediction
from .model import GameState, get_game_status, predict


def list_games(fetcher: DataFetcher, date: Optional[str] = None) -> None:
    """List all games for a given date."""
    games = fetcher.bdl.get_live_games(date)
    
    if not games:
        console.print("[dim]No games found for this date.[/dim]")
        return
    
    display_games_list(games)


def analyze_game(
    fetcher: DataFetcher,
    game_id: int,
    poll: bool = False,
    log: bool = True
) -> None:
    """
    Analyze a single game.
    
    If poll=True, continuously poll until game ends.
    """
    # Fetch initial spread
    fetcher.odds.get_nba_spreads()
    
    while True:
        # Fetch game data
        data = fetcher.fetch_game_data(game_id)
        
        if data is None:
            display_error(f"Could not fetch game {game_id}")
            if poll:
                time.sleep(CONFIG["poll_interval_sec"])
                continue
            return
        
        game_state: GameState = data["game_state"]
        spread = data["spread"]
        data_age_sec = fetcher.get_data_age_sec()
        
        # Check game status
        status = get_game_status(
            game_state.status,
            game_state.quarter,
            game_state.clock
        )
        
        # Handle different game states
        if status == "final":
            display_final(
                game_state.home_team,
                game_state.away_team,
                game_state.home_score,
                game_state.away_score,
                home_abbrev=game_state.home_team_abbrev,
                away_abbrev=game_state.away_team_abbrev
            )
            return  # Stop polling on final
        
        if status == "halftime":
            display_halftime(
                game_state.home_team,
                game_state.away_team,
                game_state.home_score,
                game_state.away_score,
                home_abbrev=game_state.home_team_abbrev,
                away_abbrev=game_state.away_team_abbrev
            )
            if poll:
                time.sleep(CONFIG["poll_interval_sec"])
                continue
            return
        
        if status == "between_quarters":
            display_between_quarters(
                game_state.home_team,
                game_state.away_team,
                game_state.home_score,
                game_state.away_score,
                game_state.quarter,
                home_abbrev=game_state.home_team_abbrev,
                away_abbrev=game_state.away_team_abbrev
            )
            if poll:
                time.sleep(CONFIG["poll_interval_sec"])
                continue
            return
        
        # Run prediction
        prediction = predict(
            game_state=game_state,
            home_stats=data["home_stats"],
            away_stats=data["away_stats"],
            home_season=data["home_season"],
            away_season=data["away_season"],
            spread=spread,
            data_age_sec=data_age_sec,
            enable_possession_edge=poll
        )
        
        if prediction is None:
            # Pre-game without spread
            display_awaiting_tipoff(
                game_state.home_team,
                game_state.away_team,
                spread,
                home_abbrev=game_state.home_team_abbrev,
                away_abbrev=game_state.away_team_abbrev
            )
            if poll:
                time.sleep(CONFIG["poll_interval_sec"])
                continue
            return
        
        # Display prediction
        display_prediction(
            prediction=prediction,
            home_team=game_state.home_team,
            away_team=game_state.away_team,
            home_score=game_state.home_score,
            away_score=game_state.away_score,
            quarter=game_state.quarter,
            clock=game_state.clock,
            data_age_sec=data_age_sec,
            home_abbrev=game_state.home_team_abbrev,
            away_abbrev=game_state.away_team_abbrev
        )
        
        # Log prediction
        if log:
            log_prediction(
                prediction=prediction,
                game_id=str(game_id),
                home_team=game_state.home_team,
                away_team=game_state.away_team,
                home_score=game_state.home_score,
                away_score=game_state.away_score,
                quarter=game_state.quarter,
                clock=game_state.clock,
                spread=spread,
                data_freshness_sec=data_age_sec,
                api_errors=fetcher.get_all_errors()
            )
            fetcher.clear_errors()
        
        if not poll:
            return
        
        # Wait before next poll
        time.sleep(CONFIG["poll_interval_sec"])


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="NBA Live Win Probability Predictor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List today's games
  python -m predictor --list
  
  # List games for a specific date
  python -m predictor --list --date 2024-01-15
  
  # Analyze a specific game (one-time)
  python -m predictor --game 12345
  
  # Continuously poll a game
  python -m predictor --game 12345 --poll
  
  # Analyze without logging
  python -m predictor --game 12345 --no-log
"""
    )
    
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List available games"
    )
    parser.add_argument(
        "--date", "-d",
        type=str,
        default=None,
        help="Date for game list (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--game", "-g",
        type=int,
        default=None,
        help="Game ID to analyze"
    )
    parser.add_argument(
        "--poll", "-p",
        action="store_true",
        help="Continuously poll game until final"
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="Disable prediction logging"
    )
    
    args = parser.parse_args()
    
    # Initialize data fetcher
    fetcher = DataFetcher()
    
    if args.list:
        list_games(fetcher, args.date)
        return 0
    
    if args.game:
        try:
            analyze_game(
                fetcher,
                args.game,
                poll=args.poll,
                log=not args.no_log
            )
        except KeyboardInterrupt:
            console.print("\n[dim]Stopped.[/dim]")
        return 0
    
    # No action specified
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
