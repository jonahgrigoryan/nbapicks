#!/usr/bin/env python
"""points_picks.py

Points-only pick generator with 2024-25 NBA adjustments.

This script uses the `GAME_DATA` payload produced by `fetch_points_game_data.py`
and applies a points-focused scoring model calibrated for the 2024-25 season.

Features:
- Usage Rate: High usage players (>28%) get scoring boost
- Days of Rest: Optimal (2 days) vs B2B vs rust (3+ days)
- DvP (Defense vs Position): Automated from opponent's defensive stats
- Pace Environment: Calibrated for 2024-25 high-pace era (avg ~101.9)

Usage:
  python points_picks.py \
    --game-date 2025-11-29 \
    --away BOS \
    --home MIN \
    --season 2025

Notes:
- Uses automated DvP from GAME_DATA (no manual web research needed)
- Injury statuses are respected: OUT/DOUBTFUL are excluded
- Scheme/FT environment adjustments remain optional (prompt workflow)
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


try:
    # Local import from repository root.
    from fetch_points_game_data import build_points_game_payload
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "Failed to import build_points_game_payload from fetch_points_game_data.py. "
        "Run this script from the repo root."
    ) from e


INACTIVE_STATUSES = {"OUT", "DOUBTFUL"}


def _normalize_status(status: Optional[str]) -> str:
    if not status:
        return "AVAILABLE"
    s = status.strip().upper()
    # Common vendor variants.
    if s in {"GTD", "GAME TIME DECISION"}:
        return "QUESTIONABLE"
    return s


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _cap(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _normalize_name_for_match(name: str) -> str:
    """Normalize player names for fuzzy matching."""

    s = name.strip().lower()
    for ch in [".", ","]:
        s = s.replace(ch, "")
    parts = [p for p in s.split() if p]
    if parts and parts[-1] in _NAME_SUFFIXES:
        parts = parts[:-1]
    return " ".join(parts)


def _parse_csv_names(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [p.strip() for p in value.split(",") if p.strip()]


@dataclass(frozen=True)
class Candidate:
    player: str
    team: str
    opponent: str
    position: str
    proj_minutes: float
    season_pts: float
    l5_pts_avg: float
    l5_pts_stdev: float
    usg_pct: Optional[float]
    days_rest: int
    dvp_bucket: str
    points_outcome_score: float
    proj_pts: float
    confidence_0_100: int
    why_summary: str


def _project_minutes(player: Dict[str, Any]) -> Tuple[float, float]:
    """Return (proj_minutes, recent_minutes_avg)."""

    recent = player.get("recent") or {}
    season = player.get("season") or {}

    recent_minutes_avg = _safe_float(recent.get("minutes_avg"), 0.0)
    season_minutes = _safe_float(season.get("minutes"), 0.0)

    # If we have a meaningful recent sample, trust it; otherwise fall back.
    sample_size = int(recent.get("sample_size") or 0)
    if sample_size >= 3 and recent_minutes_avg > 0:
        proj_minutes = recent_minutes_avg
    else:
        proj_minutes = season_minutes or recent_minutes_avg

    return round(proj_minutes, 2), round(recent_minutes_avg, 2)


def _compute_environment_adj(projected_game_pace: Optional[float], proj_minutes: float) -> float:
    """Pace adjustment calibrated for 2024-25 (league avg ~101.9)."""
    if projected_game_pace is None:
        return 0.0

    # Updated thresholds for 2024-25 high-pace era
    if projected_game_pace > 104:
        return 1.5 * (proj_minutes / 35.0)
    if projected_game_pace < 99:
        return -1.5 * (proj_minutes / 35.0)
    return 0.0


def _compute_usage_adj(usg_pct: Optional[float]) -> float:
    """Usage rate adjustment - higher usage = more scoring opportunities."""
    if usg_pct is None:
        return 0.0
    
    if usg_pct >= 28.0:
        return 1.5  # Primary offensive option
    if usg_pct < 20.0:
        return -1.0  # Low usage role player
    return 0.0  # Standard starter (20-28%)


def _compute_rest_adj(days_rest: int, is_away: bool) -> float:
    """Days of rest adjustment (more nuanced than simple B2B)."""
    if days_rest == 0:  # Back-to-back
        return -2.5 if is_away else -1.5
    if days_rest == 1:  # Standard rest
        return 0.0
    if days_rest == 2:  # Optimal recovery
        return 0.5
    # 3+ days - potential rust
    return -0.3


def _compute_dvp_adj(dvp_bucket: str) -> float:
    """DvP (Defense vs Position) adjustment."""
    if dvp_bucket == "WEAK":
        return 2.0  # Weak defense = more points
    if dvp_bucket == "STRONG":
        return -2.0  # Strong defense = fewer points
    return 0.0  # AVERAGE or unknown


def _compute_minutes_role_adj(proj_minutes: float, recent_minutes_avg: float) -> float:
    if recent_minutes_avg <= 0:
        return 0.0

    diff = proj_minutes - recent_minutes_avg
    weight = 0.30 if abs(diff) > 5 else 0.15
    return diff * weight


def _compute_form_adj(l5_pts_avg: float, season_pts: float) -> float:
    if season_pts <= 0:
        return 0.0
    raw = ((l5_pts_avg - season_pts) / season_pts) * 5.0
    return _cap(raw, -3.0, 3.0)


def _compute_consistency_adj(l5_pts_stdev: float) -> float:
    if l5_pts_stdev <= 5:
        return 2.0
    if l5_pts_stdev <= 7:
        return 0.0
    return -2.0


def _fatigue_penalty(is_away: bool, is_b2b: bool, high_travel: bool) -> float:
    """Legacy B2B penalty - now superseded by _compute_rest_adj but kept for compatibility."""
    penalty = 0.0
    # Note: This is now mostly handled by _compute_rest_adj
    # Only apply high_travel penalty here
    if high_travel:
        penalty -= 0.5
    return penalty


def _project_points(
    season_pts: float,
    l5_pts_avg: float,
    proj_minutes: float,
    recent_minutes_avg: float,
    points_outcome_score: float,
) -> float:
    baseline = 0.55 * season_pts + 0.45 * l5_pts_avg

    minute_scale = 1.0
    if recent_minutes_avg > 0:
        minute_scale = _cap(proj_minutes / recent_minutes_avg, 0.85, 1.15)

    context_bump = _cap(points_outcome_score / 10.0, -0.20, 0.20)

    proj_pts = baseline * minute_scale * (1.0 + context_bump)

    # Sanity constraints (mirror prompt intent).
    if l5_pts_avg > 0 and proj_pts > (l5_pts_avg * 1.30):
        proj_pts = l5_pts_avg * 1.30
    if proj_minutes < 24:
        proj_pts *= 0.90

    return round(proj_pts, 2)


def _confidence(
    points_outcome_score: float,
    proj_minutes: float,
    l5_pts_stdev: float,
    usg_pct: Optional[float],
    days_rest: int,
    dvp_bucket: str,
) -> int:
    """Compute confidence score (0-100) based on all available factors."""
    base = 65

    # Minutes stability
    if proj_minutes >= 30:
        base += 10
    elif proj_minutes < 26:
        base -= 12

    # Volatility
    if l5_pts_stdev <= 5:
        base += 6
    elif l5_pts_stdev > 7:
        base -= 6

    # Overall score
    if points_outcome_score >= 4:
        base += 4
    elif points_outcome_score <= -4:
        base -= 4

    # Usage rate
    if usg_pct is not None:
        if usg_pct >= 28.0:
            base += 3  # High usage = more predictable scoring
        elif usg_pct < 20.0:
            base -= 2  # Low usage = less predictable

    # Days of rest
    if days_rest == 0:  # B2B
        base -= 6
    elif days_rest == 2:  # Optimal
        base += 2
    elif days_rest >= 3:  # Rust
        base -= 2

    # DvP matchup
    if dvp_bucket == "WEAK":
        base += 5
    elif dvp_bucket == "STRONG":
        base -= 5

    return int(_cap(base, 50, 95))


def _get_dvp_bucket(teams: Dict[str, Any], opp_abbr: str, position: str) -> str:
    """Get DvP bucket for a player's position against opponent."""
    opp_team = teams.get(opp_abbr) or {}
    dvp = opp_team.get("dvp") or {}
    
    # Try exact position match first
    pos_upper = position.upper()
    if pos_upper in dvp:
        return str(dvp[pos_upper].get("bucket", "AVERAGE"))
    
    # Handle combo positions like "G-F" or "F-G"
    if "-" in pos_upper:
        parts = pos_upper.split("-")
        for part in parts:
            if part in dvp:
                return str(dvp[part].get("bucket", "AVERAGE"))
    
    # Fallback: map to closest position
    position_map = {
        "G": ["PG", "SG"],
        "F": ["SF", "PF"],
    }
    for key, candidates in position_map.items():
        if key in pos_upper:
            for cand in candidates:
                if cand in dvp:
                    return str(dvp[cand].get("bucket", "AVERAGE"))
    
    return "AVERAGE"


def build_candidates(
    payload: Dict[str, Any],
    away_abbr: str,
    home_abbr: str,
    *,
    away_starters: Optional[List[str]] = None,
    home_starters: Optional[List[str]] = None,
) -> Tuple[List[Candidate], List[Candidate]]:
    teams = payload.get("teams") or {}

    away_team = teams.get(away_abbr) or {}
    home_team = teams.get(home_abbr) or {}

    away_pace = away_team.get("pace_last_10")
    home_pace = home_team.get("pace_last_10")

    projected_game_pace: Optional[float]
    if away_pace is None and home_pace is None:
        projected_game_pace = None
    elif away_pace is None:
        projected_game_pace = _safe_float(home_pace, 0.0) or None
    elif home_pace is None:
        projected_game_pace = _safe_float(away_pace, 0.0) or None
    else:
        projected_game_pace = (_safe_float(away_pace, 0.0) + _safe_float(home_pace, 0.0)) / 2.0

    high_travel = bool((payload.get("meta") or {}).get("high_travel", False))

    away_allowed_norm = (
        {_normalize_name_for_match(n) for n in away_starters} if away_starters else None
    )
    home_allowed_norm = (
        {_normalize_name_for_match(n) for n in home_starters} if home_starters else None
    )

    def _team_candidates(
        team_abbr: str,
        opp_abbr: str,
        is_away: bool,
        allowed_norm: Optional[set[str]],
    ) -> List[Candidate]:
        team_players = (payload.get("players") or {}).get(team_abbr) or []
        team_data = teams.get(team_abbr) or {}
        b2b = bool(team_data.get("back_to_back", False))
        days_rest = int(team_data.get("days_rest", 1))  # Default to 1 day rest

        candidates: List[Candidate] = []
        for p in team_players:
            player_name = str(p.get("name") or "")
            player_norm = _normalize_name_for_match(player_name)
            if allowed_norm is not None and player_norm not in allowed_norm:
                continue

            status = _normalize_status(p.get("injury_status"))
            if status in INACTIVE_STATUSES:
                continue

            proj_minutes, recent_minutes_avg = _project_minutes(p)
            if allowed_norm is None and proj_minutes < 20:
                continue

            season = p.get("season") or {}
            recent = p.get("recent") or {}
            position = str(p.get("position") or "")

            season_pts = _safe_float(season.get("pts"), 0.0)
            l5_pts = _safe_float((recent.get("pts") or {}).get("avg"), 0.0)
            l5_stdev = _safe_float((recent.get("pts") or {}).get("stdev"), 0.0)
            
            # Get usage rate from recent stats
            usg_pct = recent.get("usg_pct")
            if usg_pct is not None:
                usg_pct = _safe_float(usg_pct, None)
            
            # Get DvP bucket for this player's position vs opponent
            dvp_bucket = _get_dvp_bucket(teams, opp_abbr, position)

            # Calculate adjustments
            environment_adj = _compute_environment_adj(projected_game_pace, proj_minutes)
            minutes_role_adj = _compute_minutes_role_adj(proj_minutes, recent_minutes_avg)
            form_adj = _compute_form_adj(l5_pts, season_pts)
            consistency_adj = _compute_consistency_adj(l5_stdev)
            usage_adj = _compute_usage_adj(usg_pct)
            rest_adj = _compute_rest_adj(days_rest, is_away)
            dvp_adj = _compute_dvp_adj(dvp_bucket)
            fatigue = _fatigue_penalty(is_away=is_away, is_b2b=b2b, high_travel=high_travel)

            # Compute total points outcome score
            points_outcome_score = (
                environment_adj
                + minutes_role_adj
                + form_adj
                + consistency_adj
                + usage_adj
                + rest_adj
                + dvp_adj
                + fatigue
            )

            proj_pts = _project_points(
                season_pts=season_pts,
                l5_pts_avg=l5_pts,
                proj_minutes=proj_minutes,
                recent_minutes_avg=recent_minutes_avg,
                points_outcome_score=points_outcome_score,
            )

            conf = _confidence(
                points_outcome_score=points_outcome_score,
                proj_minutes=proj_minutes,
                l5_pts_stdev=l5_stdev,
                usg_pct=usg_pct,
                days_rest=days_rest,
                dvp_bucket=dvp_bucket,
            )

            why_bits = []
            if projected_game_pace is not None:
                why_bits.append(f"pace={projected_game_pace:.1f}")
            why_bits.append(f"min={proj_minutes:.1f}")
            if season_pts > 0:
                why_bits.append(f"season={season_pts:.1f}")
            if l5_pts > 0:
                why_bits.append(f"L5={l5_pts:.1f}")
            if usg_pct is not None:
                why_bits.append(f"usg={usg_pct:.1f}%")
            why_bits.append(f"rest={days_rest}d")
            if dvp_bucket != "AVERAGE":
                why_bits.append(f"dvp={dvp_bucket}")
            why_bits.append(f"score={points_outcome_score:.2f}")

            candidates.append(
                Candidate(
                    player=str(p.get("name") or ""),
                    team=team_abbr,
                    opponent=opp_abbr,
                    position=position,
                    proj_minutes=proj_minutes,
                    season_pts=season_pts,
                    l5_pts_avg=l5_pts,
                    l5_pts_stdev=l5_stdev,
                    usg_pct=usg_pct,
                    days_rest=days_rest,
                    dvp_bucket=dvp_bucket,
                    points_outcome_score=round(points_outcome_score, 3),
                    proj_pts=proj_pts,
                    confidence_0_100=conf,
                    why_summary="; ".join(why_bits),
                )
            )

        candidates.sort(key=lambda c: (c.points_outcome_score, c.proj_pts), reverse=True)
        return candidates

    away_candidates = _team_candidates(
        away_abbr, home_abbr, is_away=True, allowed_norm=away_allowed_norm
    )
    home_candidates = _team_candidates(
        home_abbr, away_abbr, is_away=False, allowed_norm=home_allowed_norm
    )

    return away_candidates, home_candidates


def select_top_n_unique(candidates: List[Candidate], n: int) -> List[Candidate]:
    selected: List[Candidate] = []
    seen = set()
    for c in candidates:
        if c.player in seen:
            continue
        selected.append(c)
        seen.add(c.player)
        if len(selected) >= n:
            break
    return selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-date", required=True, help="Game date YYYY-MM-DD")
    parser.add_argument("--away", required=True, help="Away team abbreviation, e.g. BOS")
    parser.add_argument("--home", required=True, help="Home team abbreviation, e.g. MIN")
    parser.add_argument("--season", type=int, required=True, help="Season year, e.g. 2025")
    parser.add_argument(
        "--away-starters",
        default=None,
        help="Optional comma-separated away starter names (filters output).",
    )
    parser.add_argument(
        "--home-starters",
        default=None,
        help="Optional comma-separated home starter names (filters output).",
    )
    args = parser.parse_args()

    game_date = datetime.fromisoformat(args.game_date).replace(tzinfo=timezone.utc)
    away = args.away.upper()
    home = args.home.upper()

    away_starters = _parse_csv_names(args.away_starters)
    home_starters = _parse_csv_names(args.home_starters)

    payload = build_points_game_payload(game_date, away, home, args.season)

    away_candidates, home_candidates = build_candidates(
        payload,
        away,
        home,
        away_starters=away_starters or None,
        home_starters=home_starters or None,
    )

    if away_starters:
        expected = {_normalize_name_for_match(n) for n in away_starters}
        got = {_normalize_name_for_match(c.player) for c in away_candidates}
        missing = sorted(expected - got)
        if missing:
            raise RuntimeError(f"Away starters not found in GAME_DATA: {missing}")

    if home_starters:
        expected = {_normalize_name_for_match(n) for n in home_starters}
        got = {_normalize_name_for_match(c.player) for c in home_candidates}
        missing = sorted(expected - got)
        if missing:
            raise RuntimeError(f"Home starters not found in GAME_DATA: {missing}")
    away_picks = select_top_n_unique(away_candidates, 3)
    home_picks = select_top_n_unique(home_candidates, 3)

    out = {
        "away_picks": [
            {
                "player": p.player,
                "team": p.team,
                "opponent": p.opponent,
                "primary_stat": "PTS",
                "proj_value": p.proj_pts,
                "confidence_0_100": p.confidence_0_100,
                "why_summary": p.why_summary,
            }
            for p in away_picks
        ],
        "home_picks": [
            {
                "player": p.player,
                "team": p.team,
                "opponent": p.opponent,
                "primary_stat": "PTS",
                "proj_value": p.proj_pts,
                "confidence_0_100": p.confidence_0_100,
                "why_summary": p.why_summary,
            }
            for p in home_picks
        ],
    }

    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
