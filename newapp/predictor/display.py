"""
Terminal display for NBA Live Win Probability Predictor.

Uses Rich library for beautiful terminal output.
"""

from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table
from rich.text import Text

from .model import PredictionResult


console = Console()


def format_clock(clock: Optional[str], quarter: int) -> str:
    """Format clock display with quarter."""
    if quarter == 0:
        return "Pre-game"
    
    if quarter <= 4:
        q_str = f"Q{quarter}"
    else:
        ot_num = quarter - 4
        q_str = f"OT{ot_num}" if ot_num > 1 else "OT"
    
    if clock:
        return f"{q_str} {clock}"
    return q_str


def get_confidence_color(confidence: str) -> str:
    """Get color for confidence level."""
    colors = {
        "High": "green",
        "Medium": "yellow",
        "Low": "red"
    }
    return colors.get(confidence, "white")


def get_factor_favor_text(advantage: float, home_team: str, away_team: str) -> tuple:
    """Get which team a factor favors and format it."""
    if advantage > 0.01:
        return (home_team[:3].upper(), "green", f"+{advantage:.2f}")
    elif advantage < -0.01:
        return (away_team[:3].upper(), "red", f"{advantage:.2f}")
    else:
        return ("—", "white", "0.00")


def create_probability_bar(prob: float, width: int = 20) -> str:
    """Create a simple probability bar."""
    filled = int(prob * width)
    empty = width - filled
    return "█" * filled + "░" * empty


def format_flip_lead(flip_lead_home: float, home_abbr: str, away_abbr: str) -> str:
    """Format the lead needed to reach 50% (home minus away)."""
    if abs(flip_lead_home) < 0.05:
        return "PK"
    if flip_lead_home > 0:
        return f"{home_abbr} +{flip_lead_home:.1f}"
    return f"{away_abbr} +{abs(flip_lead_home):.1f}"


def display_prediction(
    prediction: PredictionResult,
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
    quarter: int,
    clock: Optional[str],
    data_age_sec: float,
    home_abbrev: Optional[str] = None,
    away_abbrev: Optional[str] = None
) -> None:
    """Display full prediction output."""
    # Use provided abbreviations or fall back to first 3 chars
    home_abbr = home_abbrev or home_team[:3].upper()
    away_abbr = away_abbrev or away_team[:3].upper()
    
    # Header with score and time
    clock_str = format_clock(clock, quarter)
    data_age_str = f"{int(data_age_sec)}s ago" if data_age_sec < 120 else f"{int(data_age_sec/60)}m ago"
    
    header = f"  {home_abbr} {home_score} - {away_score} {away_abbr}   {clock_str}   [Data: {data_age_str}]"
    
    # Win probability section
    home_bar = create_probability_bar(prediction.win_prob_home)
    away_bar = create_probability_bar(prediction.win_prob_away)
    
    prob_section = f"""  WIN PROBABILITY
  {home_abbr} {home_bar} {prediction.win_prob_home*100:.0f}%
  {away_abbr} {away_bar} {prediction.win_prob_away*100:.0f}%"""
    
    # Factor breakdown table
    factor_table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    factor_table.add_column("FACTOR BREAKDOWN", style="dim")
    factor_table.add_column("Favors", justify="center")
    factor_table.add_column("Margin", justify="right")
    
    factor_names = {
        "lead": "Current Lead",
        "spread": "Pre-game Spread",
        "efficiency": "Live Efficiency",
        "possession_edge": "Possession Edge"
    }
    
    for factor in prediction.factors:
        if factor.active:
            team, color, margin = get_factor_favor_text(
                factor.advantage, home_abbr, away_abbr
            )
            weight_pct = f"({factor.weight*100:.0f}%)"
            factor_table.add_row(
                f"{factor_names.get(factor.name, factor.name)} {weight_pct}",
                Text(team, style=color),
                margin
            )
    
    # Flip buffer and lead
    flip_buffer = f"  Flip buffer: {prediction.combined_score:+.2f}"
    if prediction.flip_lead_home is not None:
        flip_lead = format_flip_lead(prediction.flip_lead_home, home_abbr, away_abbr)
        if prediction.flip_swing is not None:
            flip_buffer += f"\n  Flip lead (50%): {flip_lead} (swing {prediction.flip_swing:+.1f})"
        else:
            flip_buffer += f"\n  Flip lead (50%): {flip_lead}"
    
    # Underdog close-to-flip indicator
    close_str = ""
    if prediction.underdog_team and prediction.underdog_close_to_flip:
        underdog_abbr = home_abbr if prediction.underdog_team == "home" else away_abbr
        if prediction.flip_swing is not None:
            close_str = (
                f"  [bold cyan]Close to flip: {underdog_abbr} within "
                f"{abs(prediction.flip_swing):.1f} pts[/bold cyan]"
            )
        else:
            close_str = f"  [bold cyan]Close to flip: {underdog_abbr} near 50%[/bold cyan]"
    
    # Underdog watch
    underdog_str = ""
    if prediction.underdog_team and prediction.underdog_watch:
        underdog_abbr = home_abbr if prediction.underdog_team == "home" else away_abbr
        underdog_prob = prediction.underdog_prob or 0.0
        reason = f" ({prediction.underdog_reason})" if prediction.underdog_reason else ""
        underdog_str = (
            f"  [bold yellow]Upset watch: {underdog_abbr} "
            f"{underdog_prob * 100:.0f}%{reason}[/bold yellow]"
        )
    
    # Confidence
    conf_color = get_confidence_color(prediction.confidence)
    confidence_str = f"  Confidence: [{conf_color}]{prediction.confidence}[/{conf_color}]"
    
    # Trailing edge alert
    alert_str = ""
    if prediction.trailing_edge_alert:
        trailing_abbr = away_abbr if prediction.trailing_team == "away" else home_abbr
        alert_str = f"\n  [bold yellow]⚠️ Trailing team ({trailing_abbr}) has underlying edge[/bold yellow]"
    
    # Blowout indicator
    if prediction.is_blowout:
        alert_str += "\n  [dim]🗑️ Garbage time detected[/dim]"
    
    # Build full output
    console.print()
    console.print(Panel(
        f"[bold]{header}[/bold]",
        border_style="blue"
    ))
    console.print(prob_section)
    console.print()
    console.print(factor_table)
    console.print()
    console.print(flip_buffer)
    console.print()
    if close_str:
        console.print(close_str)
        console.print()
    if underdog_str:
        console.print(underdog_str)
        console.print()
    console.print(confidence_str + alert_str)
    console.print()


def display_awaiting_tipoff(
    home_team: str,
    away_team: str,
    spread: Optional[float],
    home_abbrev: Optional[str] = None,
    away_abbrev: Optional[str] = None
) -> None:
    """Display pre-game state when no prediction possible."""
    home_abbr = home_abbrev or home_team[:3].upper()
    away_abbr = away_abbrev or away_team[:3].upper()
    
    console.print()
    console.print(Panel(
        f"[bold]  {home_abbr} vs {away_abbr}   Pre-game[/bold]",
        border_style="dim"
    ))
    
    if spread is not None:
        spread_str = f"{spread:+.1f}" if spread != 0 else "PK"
        console.print(f"  Pre-game spread: {home_abbr} {spread_str}")
    else:
        console.print("  [dim]Awaiting tipoff — no spread available[/dim]")
    console.print()


def display_final(
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
    home_abbrev: Optional[str] = None,
    away_abbrev: Optional[str] = None
) -> None:
    """Display final game result."""
    home_abbr = home_abbrev or home_team[:3].upper()
    away_abbr = away_abbrev or away_team[:3].upper()
    winner = home_team if home_score > away_score else away_team
    
    console.print()
    console.print(Panel(
        f"[bold]  FINAL: {home_abbr} {home_score} - {away_score} {away_abbr}[/bold]\n"
        f"  Winner: {winner}",
        border_style="green"
    ))
    console.print()


def display_error(message: str) -> None:
    """Display an error message."""
    console.print(f"[bold red]Error:[/bold red] {message}")


def format_game_status(status: str) -> tuple:
    """
    Format game status for display.
    
    Returns (display_status, is_scheduled).
    balldontlie returns:
    - datetime string (e.g., "2025-12-28T20:30:00Z") for scheduled games
    - "Final" for completed games
    - Period info for in-progress games
    """
    if not status:
        return ("Unknown", True)
    
    # Check if status is a datetime string (scheduled game)
    if "T" in status and status.endswith("Z"):
        try:
            from datetime import datetime, timezone, timedelta
            # Parse UTC time
            dt_utc = datetime.fromisoformat(status.replace("Z", "+00:00"))
            # Convert to Pacific Time (UTC-8 for PST, UTC-7 for PDT)
            # Using fixed PST offset; for automatic DST handling, use pytz or zoneinfo
            pst = timezone(timedelta(hours=-8))
            dt_pacific = dt_utc.astimezone(pst)
            # Format as Pacific time
            local_time = dt_pacific.strftime("%I:%M %p").lstrip("0")
            return (f"Scheduled {local_time} PT", True)
        except (ValueError, TypeError):
            return (status, True)
    
    return (status, False)


def display_games_list(games: list) -> None:
    """Display list of available games."""
    table = Table(title="Today's Games", show_header=True)
    table.add_column("ID", style="cyan")
    table.add_column("Matchup")
    table.add_column("Status")
    table.add_column("Score")
    
    for game in games:
        home = game.get("home_team", {}).get("full_name", "Home")
        away = game.get("visitor_team", {}).get("full_name", "Away")
        raw_status = game.get("status", "")
        home_score = game.get("home_team_score", 0)
        away_score = game.get("visitor_team_score", 0)
        
        display_status, is_scheduled = format_game_status(raw_status)
        
        table.add_row(
            str(game.get("id", "")),
            f"{away} @ {home}",
            display_status,
            "—" if is_scheduled else f"{away_score} - {home_score}"
        )
    
    console.print()
    console.print(table)
    console.print()


def display_halftime(
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
    home_abbrev: Optional[str] = None,
    away_abbrev: Optional[str] = None
) -> None:
    """Display halftime state."""
    home_abbr = home_abbrev or home_team[:3].upper()
    away_abbr = away_abbrev or away_team[:3].upper()
    
    console.print()
    console.print(Panel(
        f"[bold]  {home_abbr} {home_score} - {away_score} {away_abbr}   [Halftime][/bold]",
        border_style="yellow"
    ))
    console.print("  [dim]Prediction frozen until play resumes[/dim]")
    console.print()


def display_between_quarters(
    home_team: str,
    away_team: str, 
    home_score: int,
    away_score: int,
    quarter: int,
    home_abbrev: Optional[str] = None,
    away_abbrev: Optional[str] = None
) -> None:
    """Display between quarters state."""
    home_abbr = home_abbrev or home_team[:3].upper()
    away_abbr = away_abbrev or away_team[:3].upper()
    
    console.print()
    console.print(Panel(
        f"[bold]  {home_abbr} {home_score} - {away_score} {away_abbr}   [Q{quarter} Break][/bold]",
        border_style="yellow"
    ))
    console.print("  [dim]Prediction frozen until play resumes[/dim]")
    console.print()
