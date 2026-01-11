"""
NBA Live Win Probability Model.

All calculation functions for the win probability predictor.
Follows predictor.md spec exactly.
"""

from dataclasses import dataclass
from math import atanh, exp, sqrt, tanh
from typing import Dict, List, Optional, Tuple

from .config import CONFIG


@dataclass
class GameState:
    """Current state of a game."""
    home_team: str
    away_team: str
    home_team_abbrev: str
    away_team_abbrev: str
    home_score: int
    away_score: int
    quarter: int
    clock: Optional[str]
    status: str  # 'pre_game', 'in_progress', 'halftime', 'between_quarters', 'final'


@dataclass
class TeamStats:
    """Box score stats for a team."""
    fgm: int
    fga: int
    fg3m: int
    fta: int
    tov: int
    orb: int


@dataclass
class SeasonStats:
    """Season average stats for a team."""
    efg: float
    tov_rate: float


@dataclass
class FactorResult:
    """Result of a single factor calculation."""
    name: str
    advantage: float
    weight: float
    active: bool
    raw_value: Optional[float] = None
    gated: Optional[bool] = None


@dataclass
class PredictionResult:
    """Complete prediction result."""
    win_prob_home: float
    win_prob_away: float
    combined_score: float
    confidence: str
    factors: List[FactorResult]
    trailing_team: Optional[str]
    trailing_edge_alert: bool
    is_blowout: bool
    is_overtime: bool
    minutes_played: float
    minutes_remaining: float
    flip_lead_home: Optional[float]
    flip_swing: Optional[float]
    underdog_team: Optional[str]
    underdog_prob: Optional[float]
    underdog_watch: bool
    underdog_reason: Optional[str]
    underdog_close_to_flip: bool


def parse_clock(clock_str: Optional[str]) -> Optional[Tuple[int, int]]:
    """
    Parse 'M:SS' or 'MM:SS' or 'MM:SS.s' format.
    
    Returns (minutes, seconds) or None.
    """
    if clock_str is None or clock_str == '':
        return None
    try:
        parts = clock_str.split(':')
        minutes = int(parts[0])
        # Handle tenths of seconds (e.g., "1:23.4" → truncate to "23")
        sec_part = parts[1].split('.')[0] if len(parts) > 1 else '0'
        seconds = int(sec_part)
        return (minutes, seconds)
    except (ValueError, IndexError):
        return None


def calc_time_values(
    quarter: int,
    clock_minutes: int,
    clock_seconds: int
) -> Tuple[float, float]:
    """
    Calculate minutes_remaining and minutes_played.
    
    Returns (minutes_remaining, minutes_played).
    """
    if quarter <= 4:  # Regulation
        minutes_remaining = max(0, (4 - quarter) * 12 + clock_minutes + clock_seconds / 60)
        minutes_played = 48 - minutes_remaining
    else:  # Overtime
        ot_period = quarter - 4
        ot_clock_elapsed = 5 - (clock_minutes + clock_seconds / 60)
        minutes_remaining = max(0, 5 - ot_clock_elapsed)
        minutes_played = 48 + (ot_period - 1) * 5 + ot_clock_elapsed
    
    return (minutes_remaining, minutes_played)


def calc_possessions(fga: int, fta: int, tov: int, orb: int) -> float:
    """
    Calculate possessions with zero guard.
    
    Returns max(poss, 1) to prevent division by zero.
    """
    poss = fga + 0.44 * fta + tov - orb
    return max(poss, 1)


def calc_efg(fgm: int, fg3m: int, fga: int) -> float:
    """
    Calculate effective field goal percentage.
    
    Returns 0.0 if fga == 0 to prevent division by zero.
    """
    if fga == 0:
        return 0.0
    return (fgm + 0.5 * fg3m) / fga


def calc_tov_rate(tov: int, poss: float) -> float:
    """Calculate turnover rate. Poss should already be guarded >= 1."""
    return tov / poss


def calc_lead_advantage(
    home_score: int,
    away_score: int,
    minutes_remaining: float,
    minutes_played: float
) -> FactorResult:
    """
    Calculate time-adjusted lead advantage.
    
    Positive = home advantage, negative = away advantage.
    """
    raw_lead = home_score - away_score
    adjusted_lead = raw_lead - CONFIG["home_court_adjustment"]
    lead_score = adjusted_lead / sqrt(minutes_remaining + 1)
    lead_advantage = tanh(lead_score * CONFIG["lead_scale"])
    
    # Dynamic weight based on game progress
    game_progress = min(1.0, minutes_played / 48)
    lead_base_weight = CONFIG["lead_weight_min"] + (
        (CONFIG["lead_weight_max"] - CONFIG["lead_weight_min"]) * game_progress
    )
    
    return FactorResult(
        name="lead",
        advantage=lead_advantage,
        weight=lead_base_weight,
        active=True,
        raw_value=float(raw_lead)
    )


def calc_spread_advantage(spread: Optional[float]) -> FactorResult:
    """
    Calculate spread advantage from pre-game spread.
    
    Spread is from home team perspective:
    - Negative spread = home favored (e.g., -3.5)
    - Positive spread = home underdog (e.g., +5.0)
    
    Formula converts to our convention (positive = home advantage).
    """
    if spread is not None:
        spread_advantage = tanh(spread * -CONFIG["spread_scale"])
        return FactorResult(
            name="spread",
            advantage=spread_advantage,
            weight=CONFIG["spread_base_weight"],
            active=True,
            raw_value=spread
        )
    else:
        return FactorResult(
            name="spread",
            advantage=0.0,
            weight=0.0,
            active=False,
            raw_value=None
        )


def calc_efficiency_advantage(
    home_stats: TeamStats,
    away_stats: TeamStats,
    home_season: SeasonStats,
    away_season: SeasonStats,
    minutes_played: float
) -> FactorResult:
    """
    Calculate live efficiency advantage.
    
    Compares current shooting efficiency to season averages.
    """
    # Calculate current game stats
    home_poss = calc_possessions(home_stats.fga, home_stats.fta, home_stats.tov, home_stats.orb)
    away_poss = calc_possessions(away_stats.fga, away_stats.fta, away_stats.tov, away_stats.orb)
    
    home_efg = calc_efg(home_stats.fgm, home_stats.fg3m, home_stats.fga)
    away_efg = calc_efg(away_stats.fgm, away_stats.fg3m, away_stats.fga)

    # Calculate shooting deltas vs season
    home_eff_delta = home_efg - home_season.efg
    away_eff_delta = away_efg - away_season.efg
    
    efficiency_advantage = tanh((home_eff_delta - away_eff_delta) * CONFIG["efficiency_scale"])
    
    # Gating based on minutes played and possessions
    min_poss = min(home_poss, away_poss)
    if minutes_played < CONFIG["efficiency_gate_minutes"] or min_poss < CONFIG["efficiency_gate_poss"]:
        weight = CONFIG["efficiency_weight_gated"]
        gated = True
    else:
        weight = CONFIG["efficiency_weight_full"]
        gated = False
    
    return FactorResult(
        name="efficiency",
        advantage=efficiency_advantage,
        weight=weight,
        active=True,
        gated=gated
    )


def calc_possession_edge_advantage(
    home_stats: TeamStats,
    away_stats: TeamStats,
    minutes_played: float
) -> FactorResult:
    """
    Calculate possession edge advantage.
    
    Uses turnover and offensive rebound margins as extra possessions.
    """
    home_poss = calc_possessions(home_stats.fga, home_stats.fta, home_stats.tov, home_stats.orb)
    away_poss = calc_possessions(away_stats.fga, away_stats.fta, away_stats.tov, away_stats.orb)
    total_poss = max(home_poss + away_poss, 1)
    
    extra_poss = (away_stats.tov - home_stats.tov) + (home_stats.orb - away_stats.orb)
    poss_edge_rate = extra_poss / total_poss
    advantage = tanh(poss_edge_rate * CONFIG["possession_edge_scale"])
    
    # Gating based on minutes played and possessions
    min_poss = min(home_poss, away_poss)
    if minutes_played < CONFIG["possession_edge_gate_minutes"] or min_poss < CONFIG["possession_edge_gate_poss"]:
        weight = CONFIG["possession_edge_weight_gated"]
        gated = True
    else:
        weight = CONFIG["possession_edge_weight_full"]
        gated = False
    
    return FactorResult(
        name="possession_edge",
        advantage=advantage,
        weight=weight,
        active=True,
        raw_value=float(extra_poss),
        gated=gated
    )


def normalize_weights(factors: List[FactorResult]) -> List[FactorResult]:
    """
    Normalize weights of active factors to sum to 1.0.
    
    Returns updated factors with normalized weights.
    """
    total = sum(f.weight for f in factors if f.active)
    
    if total == 0:
        return factors
    
    normalized = []
    for f in factors:
        if f.active:
            normalized.append(FactorResult(
                name=f.name,
                advantage=f.advantage,
                weight=f.weight / total,
                active=f.active,
                raw_value=f.raw_value,
                gated=f.gated
            ))
        else:
            normalized.append(f)
    
    return normalized


def calc_combined_score(factors: List[FactorResult], is_overtime: bool) -> float:
    """
    Calculate combined score from all active factors.
    
    Applies OT dampening if in overtime.
    """
    combined = sum(f.weight * f.advantage for f in factors if f.active)
    
    if is_overtime:
        combined = combined * CONFIG["ot_dampen_factor"]
    
    return combined


def calc_win_probability(combined: float) -> Tuple[float, float]:
    """
    Calculate win probabilities from combined score.
    
    Returns (win_prob_home, win_prob_away).
    """
    k = CONFIG["sigmoid_k"]
    win_prob_home = 1 / (1 + exp(-k * combined))
    win_prob_away = 1 - win_prob_home
    return (win_prob_home, win_prob_away)


def calc_flip_lead_home(
    factors: List[FactorResult],
    minutes_remaining: float
) -> Optional[float]:
    """
    Calculate the home lead (home - away) needed to flip to 50%.
    
    Returns None if lead factor is inactive or the target is unreachable.
    """
    lead_factor = next((f for f in factors if f.name == "lead"), None)
    if lead_factor is None or not lead_factor.active or lead_factor.weight == 0:
        return None
    
    other_contrib = sum(
        f.weight * f.advantage for f in factors if f.active and f.name != "lead"
    )
    target_lead_adv = -other_contrib / lead_factor.weight
    
    if target_lead_adv <= -0.999 or target_lead_adv >= 0.999:
        return None
    
    lead_score = atanh(target_lead_adv) / CONFIG["lead_scale"]
    adjusted_lead = lead_score * sqrt(minutes_remaining + 1)
    return adjusted_lead + CONFIG["home_court_adjustment"]


def get_underdog_team(spread: Optional[float]) -> Optional[str]:
    """
    Determine underdog from home-perspective spread.
    
    Returns 'home', 'away', or None if no spread/PK.
    """
    if spread is None or spread == 0:
        return None
    return "home" if spread > 0 else "away"


def calc_underdog_watch(
    underdog_team: Optional[str],
    win_prob_home: float,
    win_prob_away: float,
    combined_score: float,
    minutes_played: float,
    data_age_sec: float,
    trailing_team: Optional[str],
    trailing_edge_alert: bool,
    is_blowout: bool
) -> Tuple[Optional[float], bool, Optional[str]]:
    """
    Determine whether the pre-game underdog is capable of winning.
    
    Returns (underdog_prob, underdog_watch, reason).
    """
    if underdog_team is None:
        return (None, False, None)
    
    underdog_prob = win_prob_home if underdog_team == "home" else win_prob_away
    
    if is_blowout:
        return (underdog_prob, False, None)
    if minutes_played < CONFIG["upset_min_minutes"]:
        return (underdog_prob, False, None)
    if data_age_sec >= CONFIG["stale_warning_sec"]:
        return (underdog_prob, False, None)
    
    reason = None
    prob_threshold = CONFIG["upset_win_prob_threshold"]
    flip_threshold = CONFIG["upset_flip_buffer_threshold"]
    
    if underdog_prob >= prob_threshold:
        reason = f"prob >= {int(prob_threshold * 100)}%"
    elif abs(combined_score) <= flip_threshold:
        reason = "near 50%"
    elif trailing_edge_alert and trailing_team == underdog_team:
        reason = "trailing edge"
    
    return (underdog_prob, reason is not None, reason)


def calc_underdog_close_to_flip(
    underdog_prob: Optional[float],
    combined_score: float,
    flip_swing: Optional[float],
    minutes_played: float,
    data_age_sec: float,
    is_blowout: bool
) -> bool:
    """
    Determine if the underdog is within a small swing of 50%.
    """
    if underdog_prob is None or underdog_prob >= 0.5:
        return False
    if is_blowout:
        return False
    if minutes_played < CONFIG["upset_min_minutes"]:
        return False
    if data_age_sec >= CONFIG["stale_warning_sec"]:
        return False
    if flip_swing is None:
        return False
    
    return (
        abs(combined_score) <= CONFIG["upset_flip_buffer_threshold"] and
        abs(flip_swing) <= CONFIG["upset_flip_swing_threshold"]
    )


def check_blowout(
    home_score: int,
    away_score: int,
    minutes_remaining: float
) -> Tuple[bool, Optional[Tuple[float, float]]]:
    """
    Check for blowout (garbage time) condition.
    
    Returns (is_blowout, override_probs) where override_probs is
    (win_prob_home, win_prob_away) if blowout, else None.
    """
    raw_lead = home_score - away_score
    
    if (abs(raw_lead) >= CONFIG["blowout_lead_threshold"] and
        minutes_remaining <= CONFIG["blowout_minutes_threshold"]):
        if raw_lead > 0:
            return (True, (0.99, 0.01))
        else:
            return (True, (0.01, 0.99))
    
    return (False, None)


def calc_confidence(
    factors: List[FactorResult],
    data_age_sec: float,
    minutes_played: float,
    spread_available: bool
) -> str:
    """
    Calculate confidence level: 'High', 'Medium', or 'Low'.
    """
    active_advantages = [f.advantage for f in factors if f.active]
    
    if len(active_advantages) == 0:
        return 'Low'
    
    factor_spread = max(active_advantages) - min(active_advantages)
    all_same_sign = (
        all(a >= 0 for a in active_advantages) or 
        all(a <= 0 for a in active_advantages)
    )
    
    # Hard failures → Low
    if data_age_sec >= CONFIG["stale_critical_sec"]:
        return 'Low'
    if factor_spread > 0.20:
        return 'Low'
    if minutes_played < 12:
        return 'Low'
    
    # Spread missing caps at Medium
    max_confidence = 'High' if spread_available else 'Medium'
    
    # Other Medium conditions
    if not all_same_sign:
        return 'Medium'
    if data_age_sec >= CONFIG["stale_warning_sec"]:
        return 'Medium'
    if minutes_played < 24:
        return 'Medium'
    
    return max_confidence


def check_trailing_edge(
    home_score: int,
    away_score: int,
    factors: List[FactorResult]
) -> Tuple[Optional[str], bool]:
    """
    Check for trailing team edge alert.
    
    Returns (trailing_team, show_alert).
    """
    # Handle ties explicitly
    if home_score == away_score:
        return (None, False)
    
    if home_score > away_score:
        trailing_team = 'away'
        trailing_sign = -1
    else:
        trailing_team = 'home'
        trailing_sign = 1
    
    lead_margin = abs(home_score - away_score)
    
    # Guard: no active factors
    active_factors = [(f.name, f.advantage) for f in factors if f.active]
    if len(active_factors) == 0:
        return (trailing_team, False)
    
    # Count factors favoring trailing team
    factors_favoring_trailing = 0
    for (name, advantage) in active_factors:
        if (advantage * trailing_sign) >= CONFIG["trailing_edge_factor_threshold"]:
            factors_favoring_trailing += 1
    
    show_alert = (
        lead_margin >= CONFIG["trailing_edge_min_margin"] and
        factors_favoring_trailing >= 2
    )
    
    return (trailing_team, show_alert)


def get_game_status(status: str, period: int, clock: Optional[str]) -> str:
    """
    Determine game status.
    
    Returns: 'pre_game' | 'in_progress' | 'halftime' | 'between_quarters' | 'final'
    """
    if status == 'Final':
        return 'final'
    if period == 0 or clock is None:
        return 'pre_game'
    if clock == '0:00' and period in [1, 3]:
        return 'between_quarters'
    if clock == '0:00' and period == 2:
        return 'halftime'
    return 'in_progress'


def predict(
    game_state: GameState,
    home_stats: TeamStats,
    away_stats: TeamStats,
    home_season: SeasonStats,
    away_season: SeasonStats,
    spread: Optional[float],
    data_age_sec: float,
    enable_possession_edge: bool = False
) -> Optional[PredictionResult]:
    """
    Main prediction function.
    
    Returns PredictionResult or None if cannot produce prediction.
    """
    # Parse clock
    parsed = parse_clock(game_state.clock)
    if parsed is None:
        clock_minutes, clock_seconds = 0, 0
    else:
        clock_minutes, clock_seconds = parsed
    
    # Calculate time values
    minutes_remaining, minutes_played = calc_time_values(
        game_state.quarter, clock_minutes, clock_seconds
    )
    
    is_overtime = game_state.quarter > 4
    
    # Check game status for pre-game handling
    game_status = get_game_status(
        game_state.status, game_state.quarter, game_state.clock
    )
    
    # Calculate factors
    if game_status == 'pre_game':
        # Pre-game: only spread factor
        lead_factor = FactorResult(
            name="lead", advantage=0.0, weight=0.0, active=False
        )
        efficiency_factor = FactorResult(
            name="efficiency", advantage=0.0, weight=0.0, active=False
        )
        possession_edge_factor = None
    else:
        lead_factor = calc_lead_advantage(
            game_state.home_score, game_state.away_score,
            minutes_remaining, minutes_played
        )
        efficiency_factor = calc_efficiency_advantage(
            home_stats, away_stats, home_season, away_season, minutes_played
        )
        possession_edge_factor = (
            calc_possession_edge_advantage(home_stats, away_stats, minutes_played)
            if enable_possession_edge
            else None
        )
    
    spread_factor = calc_spread_advantage(spread)
    
    factors = [lead_factor, spread_factor, efficiency_factor]
    if possession_edge_factor is not None:
        factors.append(possession_edge_factor)
    
    # Check if we can produce a prediction
    total_weight = sum(f.weight for f in factors)
    if total_weight == 0:
        return None  # Cannot predict (pre-game + no spread)
    
    # Normalize weights
    factors = normalize_weights(factors)
    
    # Calculate combined score and probability
    combined = calc_combined_score(factors, is_overtime)
    win_prob_home, win_prob_away = calc_win_probability(combined)
    
    # Calculate flip lead (home - away) for a 50% outcome
    flip_lead_home = calc_flip_lead_home(factors, minutes_remaining)
    if flip_lead_home is not None:
        current_lead = game_state.home_score - game_state.away_score
        flip_swing = flip_lead_home - current_lead
    else:
        flip_swing = None
    
    # Check for blowout override
    is_blowout, blowout_probs = check_blowout(
        game_state.home_score, game_state.away_score, minutes_remaining
    )
    if is_blowout and blowout_probs:
        win_prob_home, win_prob_away = blowout_probs
        flip_lead_home = None
        flip_swing = None
    
    # Calculate confidence
    confidence = calc_confidence(
        factors, data_age_sec, minutes_played, spread_factor.active
    )
    
    # Check trailing edge
    trailing_team, trailing_edge_alert = check_trailing_edge(
        game_state.home_score, game_state.away_score, factors
    )
    
    # Underdog watch
    underdog_team = get_underdog_team(spread)
    underdog_prob, underdog_watch, underdog_reason = calc_underdog_watch(
        underdog_team=underdog_team,
        win_prob_home=win_prob_home,
        win_prob_away=win_prob_away,
        combined_score=combined,
        minutes_played=minutes_played,
        data_age_sec=data_age_sec,
        trailing_team=trailing_team,
        trailing_edge_alert=trailing_edge_alert,
        is_blowout=is_blowout
    )
    underdog_close_to_flip = calc_underdog_close_to_flip(
        underdog_prob=underdog_prob,
        combined_score=combined,
        flip_swing=flip_swing,
        minutes_played=minutes_played,
        data_age_sec=data_age_sec,
        is_blowout=is_blowout
    )
    
    return PredictionResult(
        win_prob_home=win_prob_home,
        win_prob_away=win_prob_away,
        combined_score=combined,
        confidence=confidence,
        factors=factors,
        trailing_team=trailing_team,
        trailing_edge_alert=trailing_edge_alert,
        is_blowout=is_blowout,
        is_overtime=is_overtime,
        minutes_played=minutes_played,
        minutes_remaining=minutes_remaining,
        flip_lead_home=flip_lead_home,
        flip_swing=flip_swing,
        underdog_team=underdog_team,
        underdog_prob=underdog_prob,
        underdog_watch=underdog_watch,
        underdog_reason=underdog_reason,
        underdog_close_to_flip=underdog_close_to_flip
    )
