"""
Unit tests for model.py.

Tests all calculation functions with edge cases and zero guards.
"""

import pytest
from math import tanh, sqrt, exp

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from predictor.model import (
    parse_clock,
    calc_time_values,
    calc_possessions,
    calc_efg,
    calc_tov_rate,
    calc_lead_advantage,
    calc_spread_advantage,
    calc_efficiency_advantage,
    normalize_weights,
    calc_combined_score,
    calc_win_probability,
    check_blowout,
    calc_confidence,
    check_trailing_edge,
    get_game_status,
    predict,
    GameState,
    TeamStats,
    SeasonStats,
    FactorResult,
)
from predictor.config import CONFIG


class TestParseClock:
    """Tests for parse_clock function."""
    
    def test_standard_format(self):
        """Test M:SS format."""
        assert parse_clock("5:30") == (5, 30)
        assert parse_clock("0:45") == (0, 45)
        assert parse_clock("12:00") == (12, 0)
    
    def test_tenths_format(self):
        """Test MM:SS.s format with tenths truncated."""
        assert parse_clock("1:23.4") == (1, 23)
        assert parse_clock("0:05.9") == (0, 5)
    
    def test_none_input(self):
        """Test None input returns None."""
        assert parse_clock(None) is None
    
    def test_empty_string(self):
        """Test empty string returns None."""
        assert parse_clock("") is None
    
    def test_invalid_format(self):
        """Test invalid formats return None."""
        assert parse_clock("invalid") is None
        assert parse_clock("abc:def") is None
        assert parse_clock("12") is None  # No colon


class TestCalcTimeValues:
    """Tests for calc_time_values function."""
    
    def test_start_of_game(self):
        """Test Q1 12:00 - start of game."""
        mins_remaining, mins_played = calc_time_values(1, 12, 0)
        assert mins_remaining == 48.0
        assert mins_played == 0.0
    
    def test_end_of_first_quarter(self):
        """Test Q1 0:00 - end of first quarter."""
        mins_remaining, mins_played = calc_time_values(1, 0, 0)
        assert mins_remaining == 36.0
        assert mins_played == 12.0
    
    def test_halftime(self):
        """Test Q2 0:00 - halftime."""
        mins_remaining, mins_played = calc_time_values(2, 0, 0)
        assert mins_remaining == 24.0
        assert mins_played == 24.0
    
    def test_mid_third_quarter(self):
        """Test Q3 4:32 - mid third quarter."""
        mins_remaining, mins_played = calc_time_values(3, 4, 32)
        expected_remaining = 12 + 4 + 32/60  # 16.53
        assert abs(mins_remaining - expected_remaining) < 0.01
        assert abs(mins_played - (48 - expected_remaining)) < 0.01
    
    def test_end_of_regulation(self):
        """Test Q4 0:00 - end of regulation."""
        mins_remaining, mins_played = calc_time_values(4, 0, 0)
        assert mins_remaining == 0.0
        assert mins_played == 48.0
    
    def test_overtime_start(self):
        """Test OT1 5:00 - start of first overtime."""
        mins_remaining, mins_played = calc_time_values(5, 5, 0)
        assert mins_remaining == 5.0
        assert mins_played == 48.0
    
    def test_overtime_mid(self):
        """Test OT1 2:30 - mid first overtime."""
        mins_remaining, mins_played = calc_time_values(5, 2, 30)
        assert mins_remaining == 2.5
        assert mins_played == 48 + 2.5
    
    def test_second_overtime(self):
        """Test OT2 3:00 - second overtime."""
        mins_remaining, mins_played = calc_time_values(6, 3, 0)
        assert mins_remaining == 3.0
        # 48 + 5 (full OT1) + 2 (elapsed in OT2) = 55
        assert mins_played == 55.0
    
    def test_negative_guard(self):
        """Test that minutes_remaining never goes negative."""
        mins_remaining, _ = calc_time_values(4, -1, 0)
        assert mins_remaining >= 0


class TestCalcPossessions:
    """Tests for calc_possessions function."""
    
    def test_normal_calculation(self):
        """Test normal possession calculation."""
        # poss = FGA + 0.44*FTA + TOV - ORB
        poss = calc_possessions(fga=80, fta=20, tov=15, orb=10)
        expected = 80 + 0.44 * 20 + 15 - 10  # 93.8
        assert poss == expected
    
    def test_zero_guard_all_zeros(self):
        """Test zero guard when all inputs are zero."""
        poss = calc_possessions(fga=0, fta=0, tov=0, orb=0)
        assert poss == 1  # Should return 1, not 0
    
    def test_zero_guard_negative_result(self):
        """Test zero guard when calculation would be negative."""
        # More ORB than other stats combined
        poss = calc_possessions(fga=5, fta=0, tov=0, orb=10)
        assert poss == 1  # Should return 1, not -5
    
    def test_minimum_one(self):
        """Test that possessions is always at least 1."""
        poss = calc_possessions(fga=0, fta=0, tov=0, orb=100)
        assert poss >= 1


class TestCalcEfg:
    """Tests for calc_efg function."""
    
    def test_normal_calculation(self):
        """Test normal eFG% calculation."""
        # eFG = (FGM + 0.5*FG3M) / FGA
        efg = calc_efg(fgm=30, fg3m=10, fga=70)
        expected = (30 + 0.5 * 10) / 70  # 0.5
        assert efg == expected
    
    def test_zero_fga_guard(self):
        """Test zero division guard when FGA is 0."""
        efg = calc_efg(fgm=0, fg3m=0, fga=0)
        assert efg == 0.0
    
    def test_all_threes(self):
        """Test when all makes are threes."""
        efg = calc_efg(fgm=10, fg3m=10, fga=20)
        # (10 + 5) / 20 = 0.75
        assert efg == 0.75
    
    def test_no_threes(self):
        """Test when no three-pointers."""
        efg = calc_efg(fgm=40, fg3m=0, fga=80)
        assert efg == 0.5


class TestCalcTovRate:
    """Tests for calc_tov_rate function."""
    
    def test_normal_calculation(self):
        """Test normal turnover rate calculation."""
        rate = calc_tov_rate(tov=15, poss=100)
        assert rate == 0.15
    
    def test_zero_turnovers(self):
        """Test zero turnovers."""
        rate = calc_tov_rate(tov=0, poss=100)
        assert rate == 0.0
    
    def test_poss_already_guarded(self):
        """Test with poss=1 (minimum from calc_possessions)."""
        rate = calc_tov_rate(tov=1, poss=1)
        assert rate == 1.0


class TestCalcLeadAdvantage:
    """Tests for calc_lead_advantage function."""
    
    def test_home_leading(self):
        """Test when home team is leading."""
        result = calc_lead_advantage(
            home_score=100, away_score=90,
            minutes_remaining=12, minutes_played=36
        )
        assert result.active is True
        assert result.advantage > 0  # Home advantage is positive
        assert result.raw_value == 10
    
    def test_away_leading(self):
        """Test when away team is leading."""
        result = calc_lead_advantage(
            home_score=85, away_score=95,
            minutes_remaining=12, minutes_played=36
        )
        assert result.advantage < 0  # Away advantage is negative
        assert result.raw_value == -10
    
    def test_tie_game(self):
        """Test tie game with home court adjustment."""
        result = calc_lead_advantage(
            home_score=80, away_score=80,
            minutes_remaining=24, minutes_played=24
        )
        # 0 - 2.5 home court adjustment = -2.5 adjusted lead
        assert result.advantage < 0  # Slightly negative due to HCA adjustment
    
    def test_weight_progression(self):
        """Test that weight increases as game progresses."""
        early = calc_lead_advantage(100, 95, 45, 3)  # Early game
        late = calc_lead_advantage(100, 95, 5, 43)   # Late game
        
        assert late.weight > early.weight
        assert early.weight >= CONFIG["lead_weight_min"]
        assert late.weight <= CONFIG["lead_weight_max"]
    
    def test_end_of_game(self):
        """Test weight at end of game."""
        result = calc_lead_advantage(100, 95, 0, 48)
        assert result.weight == pytest.approx(CONFIG["lead_weight_max"], abs=0.01)


class TestCalcSpreadAdvantage:
    """Tests for calc_spread_advantage function."""
    
    def test_home_favored(self):
        """Test when home team is favored (negative spread)."""
        result = calc_spread_advantage(-7.0)
        assert result.active is True
        assert result.advantage > 0  # Positive = home advantage
        assert result.raw_value == -7.0
    
    def test_away_favored(self):
        """Test when away team is favored (positive spread)."""
        result = calc_spread_advantage(5.0)
        assert result.active is True
        assert result.advantage < 0  # Negative = away advantage
        assert result.raw_value == 5.0
    
    def test_pick_em(self):
        """Test pick'em (spread = 0)."""
        result = calc_spread_advantage(0.0)
        assert result.advantage == 0.0
    
    def test_missing_spread(self):
        """Test when spread is None."""
        result = calc_spread_advantage(None)
        assert result.active is False
        assert result.weight == 0.0
        assert result.raw_value is None
    
    def test_spread_weight(self):
        """Test spread always has base weight when available."""
        result = calc_spread_advantage(-3.5)
        assert result.weight == CONFIG["spread_base_weight"]


class TestCalcEfficiencyAdvantage:
    """Tests for calc_efficiency_advantage function."""
    
    def test_home_better_efficiency(self):
        """Test when home team has better efficiency."""
        home_stats = TeamStats(fgm=35, fga=70, fg3m=10, fta=20, tov=10, orb=10)
        away_stats = TeamStats(fgm=28, fga=70, fg3m=8, fta=20, tov=15, orb=10)
        home_season = SeasonStats(efg=0.50, tov_rate=0.12)
        away_season = SeasonStats(efg=0.50, tov_rate=0.12)
        
        result = calc_efficiency_advantage(
            home_stats, away_stats, home_season, away_season,
            minutes_played=30
        )
        assert result.advantage > 0  # Home has better efficiency
    
    def test_gating_early_game(self):
        """Test efficiency is gated early in game."""
        home_stats = TeamStats(fgm=10, fga=20, fg3m=3, fta=5, tov=3, orb=2)
        away_stats = TeamStats(fgm=10, fga=20, fg3m=3, fta=5, tov=3, orb=2)
        home_season = SeasonStats(efg=0.50, tov_rate=0.12)
        away_season = SeasonStats(efg=0.50, tov_rate=0.12)
        
        result = calc_efficiency_advantage(
            home_stats, away_stats, home_season, away_season,
            minutes_played=10  # Early game
        )
        assert result.gated is True
        assert result.weight == CONFIG["efficiency_weight_gated"]
    
    def test_ungated_late_game(self):
        """Test efficiency is ungated late in game with enough possessions."""
        home_stats = TeamStats(fgm=35, fga=70, fg3m=10, fta=20, tov=10, orb=10)
        away_stats = TeamStats(fgm=35, fga=70, fg3m=10, fta=20, tov=10, orb=10)
        home_season = SeasonStats(efg=0.50, tov_rate=0.12)
        away_season = SeasonStats(efg=0.50, tov_rate=0.12)
        
        result = calc_efficiency_advantage(
            home_stats, away_stats, home_season, away_season,
            minutes_played=30  # Late enough
        )
        assert result.gated is False
        assert result.weight == CONFIG["efficiency_weight_full"]
    
    def test_zero_stats(self):
        """Test with zero stats (zero guards in action)."""
        home_stats = TeamStats(fgm=0, fga=0, fg3m=0, fta=0, tov=0, orb=0)
        away_stats = TeamStats(fgm=0, fga=0, fg3m=0, fta=0, tov=0, orb=0)
        home_season = SeasonStats(efg=0.50, tov_rate=0.12)
        away_season = SeasonStats(efg=0.50, tov_rate=0.12)
        
        # Should not raise division by zero
        result = calc_efficiency_advantage(
            home_stats, away_stats, home_season, away_season,
            minutes_played=10
        )
        assert result.active is True


class TestNormalizeWeights:
    """Tests for normalize_weights function."""
    
    def test_all_active(self):
        """Test normalization with all factors active."""
        factors = [
            FactorResult("lead", 0.2, 0.3, True),
            FactorResult("spread", -0.1, 0.4, True),
            FactorResult("efficiency", 0.15, 0.25, True),
        ]
        
        normalized = normalize_weights(factors)
        total = sum(f.weight for f in normalized if f.active)
        assert total == pytest.approx(1.0)
    
    def test_one_inactive(self):
        """Test normalization with one factor inactive."""
        factors = [
            FactorResult("lead", 0.2, 0.3, True),
            FactorResult("spread", 0.0, 0.0, False),  # Inactive
            FactorResult("efficiency", 0.15, 0.25, True),
        ]
        
        normalized = normalize_weights(factors)
        active_weights = [f.weight for f in normalized if f.active]
        assert sum(active_weights) == pytest.approx(1.0)
    
    def test_all_zero_weights(self):
        """Test when all weights are zero."""
        factors = [
            FactorResult("lead", 0.0, 0.0, False),
            FactorResult("spread", 0.0, 0.0, False),
            FactorResult("efficiency", 0.0, 0.0, False),
        ]
        
        # Should return unchanged (no division by zero)
        normalized = normalize_weights(factors)
        assert normalized == factors


class TestCalcCombinedScore:
    """Tests for calc_combined_score function."""
    
    def test_positive_combined(self):
        """Test positive combined score (home favored)."""
        factors = [
            FactorResult("lead", 0.3, 0.4, True),
            FactorResult("spread", 0.2, 0.35, True),
            FactorResult("efficiency", 0.1, 0.25, True),
        ]
        
        combined = calc_combined_score(factors, is_overtime=False)
        expected = 0.4 * 0.3 + 0.35 * 0.2 + 0.25 * 0.1
        assert combined == pytest.approx(expected)
    
    def test_overtime_dampening(self):
        """Test OT dampening reduces combined score."""
        factors = [
            FactorResult("lead", 0.5, 0.5, True),
            FactorResult("spread", 0.3, 0.5, True),
        ]
        
        regular = calc_combined_score(factors, is_overtime=False)
        overtime = calc_combined_score(factors, is_overtime=True)
        
        assert overtime == regular * CONFIG["ot_dampen_factor"]
        assert overtime < regular


class TestCalcWinProbability:
    """Tests for calc_win_probability function."""
    
    def test_positive_combined(self):
        """Test positive combined gives home > 50%."""
        home, away = calc_win_probability(0.5)
        assert home > 0.5
        assert away < 0.5
        assert home + away == pytest.approx(1.0)
    
    def test_negative_combined(self):
        """Test negative combined gives home < 50%."""
        home, away = calc_win_probability(-0.5)
        assert home < 0.5
        assert away > 0.5
    
    def test_zero_combined(self):
        """Test zero combined gives 50/50."""
        home, away = calc_win_probability(0.0)
        assert home == 0.5
        assert away == 0.5
    
    def test_bounds(self):
        """Test probabilities are bounded 0-1."""
        home, away = calc_win_probability(10.0)  # Large positive
        assert 0 <= home <= 1
        assert 0 <= away <= 1
        
        home, away = calc_win_probability(-10.0)  # Large negative
        assert 0 <= home <= 1
        assert 0 <= away <= 1


class TestCheckBlowout:
    """Tests for check_blowout function."""
    
    def test_home_blowout(self):
        """Test home team blowout."""
        is_blowout, probs = check_blowout(
            home_score=120, away_score=95,  # 25 point lead
            minutes_remaining=3
        )
        assert is_blowout is True
        assert probs == (0.99, 0.01)
    
    def test_away_blowout(self):
        """Test away team blowout."""
        is_blowout, probs = check_blowout(
            home_score=85, away_score=110,  # 25 point deficit
            minutes_remaining=3
        )
        assert is_blowout is True
        assert probs == (0.01, 0.99)
    
    def test_not_blowout_close_game(self):
        """Test close game is not blowout."""
        is_blowout, probs = check_blowout(
            home_score=100, away_score=95,  # 5 point lead
            minutes_remaining=3
        )
        assert is_blowout is False
        assert probs is None
    
    def test_not_blowout_too_much_time(self):
        """Test big lead with too much time is not blowout."""
        is_blowout, probs = check_blowout(
            home_score=120, away_score=95,  # 25 point lead
            minutes_remaining=10  # Too much time
        )
        assert is_blowout is False
    
    def test_threshold_exact(self):
        """Test exactly at threshold."""
        is_blowout, _ = check_blowout(
            home_score=100, away_score=80,  # Exactly 20
            minutes_remaining=5  # Exactly 5
        )
        assert is_blowout is True


class TestCalcConfidence:
    """Tests for calc_confidence function."""
    
    def test_high_confidence(self):
        """Test conditions for High confidence."""
        factors = [
            FactorResult("lead", 0.2, 0.3, True),
            FactorResult("spread", 0.15, 0.4, True),  # Same sign
            FactorResult("efficiency", 0.1, 0.25, True),
        ]
        
        confidence = calc_confidence(
            factors, data_age_sec=60, minutes_played=30,
            spread_available=True
        )
        assert confidence == "High"
    
    def test_medium_mixed_signs(self):
        """Test Medium when factors have mixed signs."""
        factors = [
            FactorResult("lead", 0.2, 0.3, True),
            FactorResult("spread", -0.1, 0.4, True),  # Opposite sign
            FactorResult("efficiency", 0.1, 0.25, True),
        ]
        
        confidence = calc_confidence(
            factors, data_age_sec=60, minutes_played=30,
            spread_available=True
        )
        assert confidence == "Medium"
    
    def test_medium_spread_missing(self):
        """Test Medium when spread is missing."""
        factors = [
            FactorResult("lead", 0.2, 0.5, True),
            FactorResult("spread", 0.0, 0.0, False),
            FactorResult("efficiency", 0.1, 0.5, True),
        ]
        
        confidence = calc_confidence(
            factors, data_age_sec=60, minutes_played=30,
            spread_available=False
        )
        assert confidence == "Medium"
    
    def test_low_stale_data(self):
        """Test Low when data is very stale."""
        factors = [
            FactorResult("lead", 0.2, 0.5, True),
            FactorResult("spread", 0.15, 0.5, True),
        ]
        
        confidence = calc_confidence(
            factors, data_age_sec=400,  # Very stale
            minutes_played=30, spread_available=True
        )
        assert confidence == "Low"
    
    def test_low_early_game(self):
        """Test Low early in game."""
        factors = [
            FactorResult("lead", 0.2, 0.5, True),
            FactorResult("spread", 0.15, 0.5, True),
        ]
        
        confidence = calc_confidence(
            factors, data_age_sec=30, minutes_played=8,  # Early game
            spread_available=True
        )
        assert confidence == "Low"
    
    def test_low_high_factor_spread(self):
        """Test Low when factors disagree significantly."""
        factors = [
            FactorResult("lead", 0.5, 0.5, True),
            FactorResult("spread", -0.3, 0.5, True),  # Big disagreement
        ]
        
        confidence = calc_confidence(
            factors, data_age_sec=30, minutes_played=30,
            spread_available=True
        )
        # Factor spread = 0.5 - (-0.3) = 0.8 > 0.2
        assert confidence == "Low"
    
    def test_empty_factors(self):
        """Test with no active factors."""
        factors = [
            FactorResult("lead", 0.0, 0.0, False),
            FactorResult("spread", 0.0, 0.0, False),
        ]
        
        confidence = calc_confidence(
            factors, data_age_sec=30, minutes_played=30,
            spread_available=False
        )
        assert confidence == "Low"


class TestCheckTrailingEdge:
    """Tests for check_trailing_edge function."""
    
    def test_tie_game(self):
        """Test tie game returns None trailing team."""
        factors = [
            FactorResult("lead", 0.0, 0.5, True),
            FactorResult("spread", 0.2, 0.5, True),
        ]
        
        trailing_team, show_alert = check_trailing_edge(80, 80, factors)
        assert trailing_team is None
        assert show_alert is False
    
    def test_trailing_edge_alert(self):
        """Test alert when trailing team has edge."""
        # Home is trailing (away leading by 5)
        # But spread and efficiency favor home
        factors = [
            FactorResult("lead", -0.2, 0.3, True),  # Favors away (home trailing)
            FactorResult("spread", 0.25, 0.4, True),  # Favors home >= 0.15
            FactorResult("efficiency", 0.20, 0.3, True),  # Favors home >= 0.15
        ]
        
        trailing_team, show_alert = check_trailing_edge(75, 80, factors)
        assert trailing_team == "home"
        assert show_alert is True  # 2+ factors favor trailing team
    
    def test_no_alert_small_lead(self):
        """Test no alert when lead is too small."""
        factors = [
            FactorResult("lead", -0.1, 0.3, True),
            FactorResult("spread", 0.25, 0.4, True),
            FactorResult("efficiency", 0.20, 0.3, True),
        ]
        
        # Only 2 point lead (below threshold of 3)
        trailing_team, show_alert = check_trailing_edge(78, 80, factors)
        assert trailing_team == "home"
        assert show_alert is False
    
    def test_away_trailing(self):
        """Test when away team is trailing."""
        factors = [
            FactorResult("lead", 0.2, 0.3, True),  # Favors home
            FactorResult("spread", -0.25, 0.4, True),  # Favors away >= 0.15
            FactorResult("efficiency", -0.20, 0.3, True),  # Favors away >= 0.15
        ]
        
        trailing_team, show_alert = check_trailing_edge(85, 80, factors)
        assert trailing_team == "away"
        assert show_alert is True
    
    def test_no_active_factors(self):
        """Test with no active factors."""
        factors = [
            FactorResult("lead", 0.0, 0.0, False),
            FactorResult("spread", 0.0, 0.0, False),
        ]
        
        trailing_team, show_alert = check_trailing_edge(85, 80, factors)
        assert trailing_team == "away"
        assert show_alert is False


class TestGetGameStatus:
    """Tests for get_game_status function."""
    
    def test_final(self):
        """Test final game status."""
        assert get_game_status("Final", 4, "0:00") == "final"
    
    def test_pre_game(self):
        """Test pre-game status."""
        assert get_game_status("Scheduled", 0, None) == "pre_game"
    
    def test_in_progress(self):
        """Test in-progress status."""
        assert get_game_status("In Progress", 2, "5:30") == "in_progress"
    
    def test_halftime(self):
        """Test halftime status."""
        assert get_game_status("In Progress", 2, "0:00") == "halftime"
    
    def test_between_quarters_q1(self):
        """Test between Q1 and Q2."""
        assert get_game_status("In Progress", 1, "0:00") == "between_quarters"
    
    def test_between_quarters_q3(self):
        """Test between Q3 and Q4."""
        assert get_game_status("In Progress", 3, "0:00") == "between_quarters"


class TestPredict:
    """Tests for main predict function."""
    
    def test_in_progress_game(self):
        """Test prediction for in-progress game."""
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
        
        assert result is not None
        assert 0 <= result.win_prob_home <= 1
        assert 0 <= result.win_prob_away <= 1
        assert result.win_prob_home + result.win_prob_away == pytest.approx(1.0)
        assert result.confidence in ["High", "Medium", "Low"]
        assert len(result.factors) == 3
    
    def test_pre_game_with_spread(self):
        """Test pre-game prediction with spread available."""
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
        
        result = predict(
            game_state, home_stats, away_stats,
            home_season, away_season,
            spread=-5.0, data_age_sec=0
        )
        
        assert result is not None
        # Only spread should be active
        active = [f for f in result.factors if f.active]
        assert len(active) == 1
        assert active[0].name == "spread"
    
    def test_pre_game_without_spread(self):
        """Test pre-game prediction without spread returns None."""
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
        
        result = predict(
            game_state, home_stats, away_stats,
            home_season, away_season,
            spread=None, data_age_sec=0
        )
        
        assert result is None
    
    def test_blowout_override(self):
        """Test blowout overrides normal calculation."""
        game_state = GameState(
            home_team="Lakers",
            away_team="Celtics",
            home_score=120,
            away_score=95,  # 25 point lead
            quarter=4,
            clock="3:00",  # Less than 5 min remaining
            status="In Progress"
        )
        home_stats = TeamStats(fgm=45, fga=85, fg3m=12, fta=20, tov=8, orb=10)
        away_stats = TeamStats(fgm=35, fga=82, fg3m=8, fta=15, tov=14, orb=8)
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
        assert result.win_prob_away == 0.01
    
    def test_overtime_dampening(self):
        """Test overtime applies dampening."""
        game_state = GameState(
            home_team="Lakers",
            away_team="Celtics",
            home_score=110,
            away_score=108,
            quarter=5,  # OT
            clock="2:30",
            status="In Progress"
        )
        home_stats = TeamStats(fgm=42, fga=88, fg3m=10, fta=22, tov=11, orb=10)
        away_stats = TeamStats(fgm=40, fga=86, fg3m=9, fta=20, tov=12, orb=9)
        home_season = SeasonStats(efg=0.52, tov_rate=0.12)
        away_season = SeasonStats(efg=0.51, tov_rate=0.13)
        
        result = predict(
            game_state, home_stats, away_stats,
            home_season, away_season,
            spread=-3.5, data_age_sec=30
        )
        
        assert result is not None
        assert result.is_overtime is True
        # Probability should be closer to 50% due to dampening
        assert 0.4 < result.win_prob_home < 0.7


class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_all_zeros_no_crash(self):
        """Test system handles all zero inputs without crashing."""
        game_state = GameState(
            home_team="Team A",
            away_team="Team B",
            home_score=0,
            away_score=0,
            quarter=1,
            clock="12:00",
            status="In Progress"
        )
        home_stats = TeamStats(fgm=0, fga=0, fg3m=0, fta=0, tov=0, orb=0)
        away_stats = TeamStats(fgm=0, fga=0, fg3m=0, fta=0, tov=0, orb=0)
        home_season = SeasonStats(efg=0.52, tov_rate=0.12)
        away_season = SeasonStats(efg=0.51, tov_rate=0.13)
        
        # Should not raise any exceptions
        result = predict(
            game_state, home_stats, away_stats,
            home_season, away_season,
            spread=-3.5, data_age_sec=0
        )
        
        assert result is not None
    
    def test_extreme_lead(self):
        """Test extreme lead values."""
        result = calc_lead_advantage(
            home_score=150, away_score=50,  # 100 point lead
            minutes_remaining=1, minutes_played=47
        )
        # Should be bounded by tanh
        assert -1 <= result.advantage <= 1
    
    def test_extreme_spread(self):
        """Test extreme spread values."""
        result = calc_spread_advantage(-30.0)  # Huge favorite
        assert -1 <= result.advantage <= 1
