"""
Configuration for NBA Live Win Probability Predictor.

All tunable parameters externalized for calibration.
Loads from config.json if present (merge with defaults).
"""

import json
import os
from typing import Any, Dict

# Default configuration - all tunable parameters
DEFAULT_CONFIG: Dict[str, Any] = {
    # Factor scaling
    "home_court_adjustment": 2.5,      # Points to subtract from home lead
    "lead_scale": 0.15,                # tanh scaling for lead advantage
    "spread_scale": 0.08,              # tanh scaling for spread advantage
    "efficiency_scale": 5.0,           # tanh scaling for efficiency delta
    "sigmoid_k": 2.5,                  # Steepness of final probability curve
    
    # Weight bounds
    "lead_weight_min": 0.20,           # Lead weight at game start
    "lead_weight_max": 0.35,           # Lead weight at game end
    "spread_base_weight": 0.40,        # Weight when spread available
    "efficiency_weight_full": 0.25,    # Efficiency weight when not gated
    "efficiency_weight_gated": 0.10,   # Efficiency weight when gated
    "possession_edge_weight_full": 0.12,  # Possession edge weight when ungated
    "possession_edge_weight_gated": 0.05, # Possession edge weight when gated
    
    # Gating thresholds
    "efficiency_gate_minutes": 18,     # Min minutes for full efficiency weight
    "efficiency_gate_poss": 30,        # Min possessions for full efficiency weight
    "possession_edge_gate_minutes": 24,  # Min minutes for full possession edge weight
    "possession_edge_gate_poss": 40,     # Min possessions for full possession edge weight
    
    # Trailing edge detection
    "trailing_edge_min_margin": 3,     # Min point margin to trigger alert
    "trailing_edge_factor_threshold": 0.15,  # Min advantage to count as "favoring"

    # Underdog watch
    "upset_min_minutes": 12,           # Min minutes played before alerting
    "upset_win_prob_threshold": 0.40,  # Underdog win prob needed for watch
    "upset_flip_buffer_threshold": 0.08,  # Combined score near 50%
    "upset_flip_swing_threshold": 4.0,  # Points swing to reach 50%
    
    # Garbage time
    "blowout_lead_threshold": 20,      # Points for blowout detection (raw, not adjusted)
    "blowout_minutes_threshold": 5,    # Minutes remaining for blowout
    
    # Overtime
    "ot_dampen_factor": 0.8,           # Compress combined score toward 50% in OT

    # Possession edge
    "possession_edge_scale": 4.0,      # tanh scaling for possession edge
    
    # Data freshness
    "stale_warning_sec": 120,          # 2 min → Medium confidence
    "stale_critical_sec": 300,         # 5 min → Low confidence
    
    # API settings
    "poll_interval_sec": 30,
    "api_max_retries": 3,
    "api_retry_backoff_ms": 500,
    
    # League averages (fallback when season stats unavailable)
    "league_avg_efg": 0.52,
    "league_avg_tov_rate": 0.13,
}


def load_config(config_path: str = "config.json") -> Dict[str, Any]:
    """
    Load configuration, merging config.json with defaults.
    
    Missing keys use defaults, provided keys override.
    """
    config = DEFAULT_CONFIG.copy()
    
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                user_config = json.load(f)
            # Merge: user config overrides defaults
            config.update(user_config)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not load {config_path}: {e}. Using defaults.")
    
    return config


# Global CONFIG instance
CONFIG = load_config()


def get_config_hash() -> str:
    """Return first 8 chars of SHA256 hash of config for logging."""
    import hashlib
    config_str = json.dumps(CONFIG, sort_keys=True)
    return hashlib.sha256(config_str.encode()).hexdigest()[:8]
