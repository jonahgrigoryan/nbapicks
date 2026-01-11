"""
Prediction logging for NBA Live Win Probability Predictor.

JSON-lines format with daily log rotation.
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from .config import CONFIG, get_config_hash
from .model import PredictionResult


MODEL_VERSION = "1.0.0"


def get_log_path(base_dir: str = "logs") -> str:
    """Get log file path for today."""
    os.makedirs(base_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(base_dir, f"predictions_{date_str}.jsonl")


def format_prediction_log(
    prediction: PredictionResult,
    game_id: str,
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
    quarter: int,
    clock: Optional[str],
    spread: Optional[float],
    data_freshness_sec: float,
    api_errors: List[Dict]
) -> Dict[str, Any]:
    """Format prediction result for logging."""
    
    # Build factors dict
    factors_dict = {}
    for f in prediction.factors:
        factor_data = {
            "advantage": round(f.advantage, 4),
            "weight": round(f.weight, 4),
            "active": f.active
        }
        if f.raw_value is not None:
            if f.name == "lead":
                factor_data["raw_lead"] = f.raw_value
            elif f.name == "spread":
                factor_data["raw_spread"] = f.raw_value
            elif f.name == "possession_edge":
                factor_data["raw_extra_poss"] = f.raw_value
        if f.gated is not None:
            factor_data["gated"] = f.gated
        factors_dict[f.name] = factor_data
    
    return {
        "model_version": MODEL_VERSION,
        "config_hash": get_config_hash(),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "game_id": str(game_id),
        "home_team": home_team,
        "away_team": away_team,
        "game_status": "in_progress",
        "score": {
            "home": home_score,
            "away": away_score
        },
        "quarter": quarter,
        "clock": clock,
        "minutes_remaining": round(prediction.minutes_remaining, 2),
        "minutes_played": round(prediction.minutes_played, 2),
        "is_overtime": prediction.is_overtime,
        "is_blowout": prediction.is_blowout,
        "combined_score": round(prediction.combined_score, 4),
        "flip": {
            "lead_home": None if prediction.flip_lead_home is None else round(prediction.flip_lead_home, 2),
            "swing": None if prediction.flip_swing is None else round(prediction.flip_swing, 2)
        },
        "underdog": {
            "team": prediction.underdog_team,
            "win_prob": (
                None if prediction.underdog_prob is None else round(prediction.underdog_prob, 4)
            ),
            "watch": prediction.underdog_watch,
            "reason": prediction.underdog_reason,
            "close_to_flip": prediction.underdog_close_to_flip
        },
        "factors": factors_dict,
        "win_prob": {
            "home": round(prediction.win_prob_home, 4),
            "away": round(prediction.win_prob_away, 4)
        },
        "confidence": prediction.confidence,
        "data_freshness_sec": round(data_freshness_sec, 1),
        "trailing_team": prediction.trailing_team,
        "trailing_edge_alert": prediction.trailing_edge_alert,
        "api_errors": api_errors
    }


def log_prediction(
    prediction: PredictionResult,
    game_id: str,
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
    quarter: int,
    clock: Optional[str],
    spread: Optional[float],
    data_freshness_sec: float,
    api_errors: List[Dict],
    log_dir: str = "logs"
) -> None:
    """
    Append prediction to JSON-lines log file.
    
    Creates new file for each day (daily rotation).
    """
    log_entry = format_prediction_log(
        prediction=prediction,
        game_id=game_id,
        home_team=home_team,
        away_team=away_team,
        home_score=home_score,
        away_score=away_score,
        quarter=quarter,
        clock=clock,
        spread=spread,
        data_freshness_sec=data_freshness_sec,
        api_errors=api_errors
    )
    
    log_path = get_log_path(log_dir)
    
    with open(log_path, "a") as f:
        f.write(json.dumps(log_entry) + "\n")


def read_predictions(log_path: str) -> List[Dict]:
    """Read all predictions from a log file."""
    predictions = []
    
    if not os.path.exists(log_path):
        return predictions
    
    with open(log_path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    predictions.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    
    return predictions


def get_recent_predictions(
    game_id: Optional[str] = None,
    limit: int = 100,
    log_dir: str = "logs"
) -> List[Dict]:
    """
    Get recent predictions, optionally filtered by game_id.
    
    Returns most recent predictions first.
    """
    log_path = get_log_path(log_dir)
    predictions = read_predictions(log_path)
    
    if game_id:
        predictions = [p for p in predictions if p.get("game_id") == str(game_id)]
    
    # Sort by timestamp descending
    predictions.sort(key=lambda p: p.get("timestamp", ""), reverse=True)
    
    return predictions[:limit]
