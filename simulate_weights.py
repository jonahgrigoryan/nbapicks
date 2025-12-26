#!/usr/bin/env python3
"""simulate_weights.py

Weight optimization simulation for NBA points prediction model.

This script:
1. Loads historical game data (multiple days)
2. Splits into train/validation sets
3. Tests weight combinations using hybrid Monte Carlo + Grid Search
4. Finds optimal weights that minimize prediction error
5. Validates on held-out data
6. Outputs optimal weights with confidence metrics

Usage:
  # Full simulation with train/validation split
  python3 simulate_weights.py \
    --start-date 2025-12-15 \
    --end-date 2025-12-20 \
    --season 2025 \
    --train-ratio 0.75

  # Focused simulation (only optimize specific factors)
  python3 simulate_weights.py \
    --start-date 2025-12-15 \
    --end-date 2025-12-20 \
    --season 2025 \
    --optimize form_multiplier pace_fast pace_slow \
    --fix-all-others

Requirements:
  - env var `BALLDONTLIE_API_KEY` must be set.
  - numpy (optional, for faster Monte Carlo sampling)
  
Parallel Processing:
  - Automatically uses all available CPU cores (CPU count - 1)
  - Use --max-workers N to set specific number of workers
  - Use --no-parallel to disable parallel processing
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import multiprocessing

# Load .env file if it exists
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if "=" in line:
                key, value = line.strip().split("=", 1)
                os.environ[key] = value.strip("'").strip('"')

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# Import from auto_tune_model
from auto_tune_model import (
    PlayerGameRecord,
    get_games_on_date,
    process_game,
    compute_accuracy_metrics,
)

# Import from fetch_points_game_data
from fetch_points_game_data import get_teams_map

# Minimum improvement thresholds
MIN_MAE_IMPROVEMENT = 0.2  # Must improve MAE by at least 0.2 pts
MIN_BIAS_IMPROVEMENT = 0.3  # Must improve bias by at least 0.3 pts
MIN_WITHIN_5_IMPROVEMENT = 2.0  # Must improve % within 5 pts by at least 2%


@dataclass
class WeightConfig:
    """Configuration of weight values for all adjustment factors."""
    # Form
    form_multiplier: float = 6.0
    form_cap: float = 2.4
    
    # Pace
    pace_fast: float = 1.8  # When pace > 104
    pace_slow: float = -1.8  # When pace < 99
    
    # Usage
    usage_high: float = 1.2  # When usage >= 28%
    usage_low: float = -0.8  # When usage < 20%
    
    # Rest
    rest_b2b_road: float = -2.0
    rest_b2b_home: float = -1.2
    rest_optimal: float = 0.4  # 2 days rest
    rest_rust: float = -0.3  # 3+ days rest
    
    # DvP
    dvp_weak: float = 2.0
    dvp_strong: float = -2.0
    
    # Minutes
    minutes_high: float = 2.0  # >= 34 min
    minutes_med: float = 1.0  # 30-33.9 min
    minutes_low: float = -3.0  # < 26 min
    
    # Consistency
    consistency_stable: float = 1.5  # stdev <= 5
    consistency_volatile: float = -1.5  # stdev > 7
    
    def to_dict(self) -> Dict[str, float]:
        """Convert to dictionary."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: Dict[str, float]) -> WeightConfig:
        """Create from dictionary."""
        return cls(**d)
    
    @classmethod
    def current_weights(cls) -> WeightConfig:
        """Get current weights from points_prompt.md (defaults)."""
        return cls()  # Uses defaults which match current prompt


@dataclass
class SimulationResult:
    """Result of testing a weight configuration."""
    config: WeightConfig
    train_metrics: Dict[str, Any]
    validation_metrics: Dict[str, Any]
    config_id: str
    rank: int = 0


def compute_adjustment_score_with_weights(
    record: PlayerGameRecord,
    weights: WeightConfig,
) -> float:
    """
    Compute adjustment score using custom weights.
    This replaces the hardcoded logic in auto_tune_model.py.
    """
    score = 0.0
    
    # Form adjustment
    if record.season_pts_avg > 0:
        form_raw = ((record.l5_pts_avg - record.season_pts_avg) / record.season_pts_avg) * weights.form_multiplier
        score += max(-weights.form_cap, min(weights.form_cap, form_raw))
    
    # Pace adjustment
    if record.pace_env > 104:
        score += weights.pace_fast
    elif record.pace_env < 99:
        score += weights.pace_slow
    
    # Usage adjustment
    if record.usg_pct is not None:
        if record.usg_pct >= 28.0:
            score += weights.usage_high
        elif record.usg_pct < 20.0:
            score += weights.usage_low
    
    # Rest adjustment
    if record.days_rest == 0:  # B2B
        score += weights.rest_b2b_road if not record.is_home else weights.rest_b2b_home
    elif record.days_rest == 2:
        score += weights.rest_optimal
    elif record.days_rest >= 3:
        score += weights.rest_rust
    
    # DvP adjustment
    if record.dvp_bucket == "WEAK":
        score += weights.dvp_weak
    elif record.dvp_bucket == "STRONG":
        score += weights.dvp_strong
    
    # Minutes adjustment
    proj_minutes = record.l5_minutes_avg if record.l5_minutes_avg > 0 else record.season_minutes_avg
    if proj_minutes >= 34:
        score += weights.minutes_high
    elif proj_minutes >= 30:
        score += weights.minutes_med
    elif proj_minutes < 26:
        score += weights.minutes_low
    
    # Consistency adjustment
    if record.l5_pts_stdev <= 5:
        score += weights.consistency_stable
    elif record.l5_pts_stdev > 7:
        score += weights.consistency_volatile
    
    return score


def compute_predictions_with_weights(
    records: List[PlayerGameRecord],
    weights: WeightConfig,
) -> List[Tuple[float, float]]:
    """
    Compute predictions for all records using custom weights.
    Returns list of (predicted_pts, actual_pts) tuples.
    """
    predictions = []
    
    for record in records:
        # Baseline projection (season average)
        baseline_pts = record.baseline_proj
        
        # Compute adjustment score
        adj_score = compute_adjustment_score_with_weights(record, weights)
        
        # Convert to percentage adjustment (clamped to ±15%)
        adj_pct = max(-0.15, min(0.15, adj_score / 40.0))
        
        # Final projection
        adj_proj_pts = baseline_pts * (1.0 + adj_pct)
        
        # Sanity rules
        proj_minutes = record.l5_minutes_avg if record.l5_minutes_avg > 0 else record.season_minutes_avg
        if proj_minutes < 26:
            adj_proj_pts = min(adj_proj_pts, baseline_pts)  # No upside bump
        
        predictions.append((adj_proj_pts, record.actual_pts))
    
    return predictions


def _evaluate_config_worker(args: Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]) -> Dict[str, Any]:
    """
    Worker function for parallel evaluation.
    Converts dicts back to objects, evaluates, returns dict.
    """
    config_dict, train_records_dicts, validation_records_dicts = args
    
    # Reconstruct objects
    config = WeightConfig.from_dict(config_dict)
    train_records = [PlayerGameRecord(**r) for r in train_records_dicts]
    validation_records = [PlayerGameRecord(**r) for r in validation_records_dicts]
    
    # Evaluate
    result = evaluate_config(config, train_records, validation_records)
    
    # Return as dict for pickling
    return {
        "config": result.config.to_dict(),
        "train_metrics": result.train_metrics,
        "validation_metrics": result.validation_metrics,
        "config_id": result.config_id,
    }


def evaluate_config(
    config: WeightConfig,
    train_records: List[PlayerGameRecord],
    validation_records: List[PlayerGameRecord],
) -> SimulationResult:
    """Evaluate a weight configuration on train and validation sets."""
    # Train predictions
    train_preds = compute_predictions_with_weights(train_records, config)
    train_errors = [pred - actual for pred, actual in train_preds]
    train_abs_errors = [abs(e) for e in train_errors]
    
    train_mae = sum(train_abs_errors) / len(train_abs_errors) if train_abs_errors else 0.0
    train_bias = sum(train_errors) / len(train_errors) if train_errors else 0.0
    train_within_5 = sum(1 for e in train_abs_errors if e <= 5) / len(train_abs_errors) * 100 if train_abs_errors else 0.0
    
    # Validation predictions
    val_preds = compute_predictions_with_weights(validation_records, config)
    val_errors = [pred - actual for pred, actual in val_preds]
    val_abs_errors = [abs(e) for e in val_errors]
    
    val_mae = sum(val_abs_errors) / len(val_abs_errors) if val_abs_errors else 0.0
    val_bias = sum(val_errors) / len(val_errors) if val_errors else 0.0
    val_within_5 = sum(1 for e in val_abs_errors if e <= 5) / len(val_abs_errors) * 100 if val_abs_errors else 0.0
    
    # RMSE
    train_rmse = (sum(e ** 2 for e in train_errors) / len(train_errors)) ** 0.5 if train_errors else 0.0
    val_rmse = (sum(e ** 2 for e in val_errors) / len(val_errors)) ** 0.5 if val_errors else 0.0
    
    return SimulationResult(
        config=config,
        train_metrics={
            "mae": round(train_mae, 3),
            "rmse": round(train_rmse, 3),
            "bias": round(train_bias, 3),
            "within_5_pts_pct": round(train_within_5, 2),
            "n_players": len(train_records),
        },
        validation_metrics={
            "mae": round(val_mae, 3),
            "rmse": round(val_rmse, 3),
            "bias": round(val_bias, 3),
            "within_5_pts_pct": round(val_within_5, 2),
            "n_players": len(validation_records),
        },
        config_id="",
    )


def generate_monte_carlo_configs(
    base_config: WeightConfig,
    n_samples: int,
    optimize_factors: Optional[List[str]] = None,
) -> List[WeightConfig]:
    """
    Generate random weight configurations using Monte Carlo sampling.
    If optimize_factors is specified, only vary those factors.
    """
    configs = []
    
    # Define search spaces for each factor
    search_spaces = {
        "form_multiplier": [4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5],
        "form_cap": [2.0, 2.2, 2.4, 2.6, 2.8],
        "pace_fast": [1.2, 1.5, 1.8, 2.0, 2.2, 2.5],
        "pace_slow": [-1.2, -1.5, -1.8, -2.0, -2.2, -2.5],
        "usage_high": [0.8, 1.0, 1.2, 1.4, 1.6],
        "usage_low": [-0.6, -0.8, -1.0, -1.2],
        "rest_b2b_road": [-1.5, -2.0, -2.5, -3.0],
        "rest_b2b_home": [-1.0, -1.2, -1.5, -1.8],
        "rest_optimal": [0.2, 0.3, 0.4, 0.5, 0.6],
        "rest_rust": [-0.2, -0.3, -0.4, -0.5],
        "dvp_weak": [1.5, 2.0, 2.5],
        "dvp_strong": [-1.5, -2.0, -2.5],
        "minutes_high": [1.5, 2.0, 2.5],
        "minutes_med": [0.8, 1.0, 1.2],
        "minutes_low": [-2.5, -3.0, -3.5],
        "consistency_stable": [1.2, 1.5, 1.8],
        "consistency_volatile": [-1.2, -1.5, -1.8],
    }
    
    if HAS_NUMPY:
        rng = np.random.default_rng()
    
    for i in range(n_samples):
        config_dict = base_config.to_dict()
        
        # Sample factors to optimize
        factors_to_sample = optimize_factors if optimize_factors else list(search_spaces.keys())
        
        for factor in factors_to_sample:
            if factor in search_spaces:
                if HAS_NUMPY:
                    config_dict[factor] = float(rng.choice(search_spaces[factor]))
                else:
                    config_dict[factor] = float(random.choice(search_spaces[factor]))
        
        configs.append(WeightConfig.from_dict(config_dict))
    
    return configs


def generate_grid_configs(
    base_config: WeightConfig,
    optimize_factors: List[str],
    grid_steps: Dict[str, List[float]],
) -> List[WeightConfig]:
    """
    Generate grid search configurations around promising regions.
    """
    configs = []
    
    # Start with base config
    config_dict = base_config.to_dict()
    
    # Generate all combinations for specified factors
    import itertools
    
    factor_values = {factor: grid_steps.get(factor, [config_dict[factor]]) for factor in optimize_factors}
    factor_names = list(factor_values.keys())
    value_lists = [factor_values[name] for name in factor_names]
    
    for combo in itertools.product(*value_lists):
        new_config_dict = config_dict.copy()
        for name, value in zip(factor_names, combo):
            new_config_dict[name] = value
        configs.append(WeightConfig.from_dict(new_config_dict))
    
    return configs


def run_simulation(
    records: List[PlayerGameRecord],
    train_ratio: float = 0.75,
    monte_carlo_samples: int = 1000,
    optimize_factors: Optional[List[str]] = None,
    fix_all_others: bool = False,
    max_workers: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Run weight optimization simulation.
    
    Args:
        records: Historical player game records
        train_ratio: Fraction of data to use for training (rest for validation)
        monte_carlo_samples: Number of Monte Carlo samples
        optimize_factors: List of factors to optimize (None = all)
        fix_all_others: If True, only optimize specified factors, keep others at current values
    """
    print(f"=" * 70, file=sys.stderr)
    print(f"WEIGHT OPTIMIZATION SIMULATION", file=sys.stderr)
    print(f"=" * 70, file=sys.stderr)
    print(f"Total records: {len(records)}", file=sys.stderr)
    
    # Split into train/validation
    random.shuffle(records)
    split_idx = int(len(records) * train_ratio)
    train_records = records[:split_idx]
    validation_records = records[split_idx:]
    
    print(f"Train records: {len(train_records)} ({train_ratio*100:.1f}%)", file=sys.stderr)
    print(f"Validation records: {len(validation_records)} ({(1-train_ratio)*100:.1f}%)", file=sys.stderr)
    print(f"-" * 70, file=sys.stderr)
    
    # Get current weights baseline
    current_config = WeightConfig.current_weights()
    print(f"\nEvaluating CURRENT weights...", file=sys.stderr)
    current_result = evaluate_config(current_config, train_records, validation_records)
    
    print(f"Current Train MAE: {current_result.train_metrics['mae']:.3f} | "
          f"Validation MAE: {current_result.validation_metrics['mae']:.3f}", file=sys.stderr)
    print(f"Current Train Bias: {current_result.train_metrics['bias']:+.3f} | "
          f"Validation Bias: {current_result.validation_metrics['bias']:+.3f}", file=sys.stderr)
    
    # Generate configurations
    print(f"\nGenerating {monte_carlo_samples} Monte Carlo configurations...", file=sys.stderr)
    
    if fix_all_others and optimize_factors:
        # Only optimize specified factors, keep others at current
        base_config = current_config
    else:
        base_config = current_config
    
    monte_carlo_configs = generate_monte_carlo_configs(
        base_config,
        monte_carlo_samples,
        optimize_factors=optimize_factors,
    )
    
    # Evaluate all configurations (with parallel processing)
    print(f"Evaluating {monte_carlo_samples} configurations...", file=sys.stderr)
    
    # Determine number of workers
    if max_workers is None:
        max_workers = max(1, multiprocessing.cpu_count() - 1)  # Leave one core free
    
    print(f"Using {max_workers} parallel workers", file=sys.stderr)
    
    # Convert records to dicts for pickling
    train_records_dicts = [asdict(r) for r in train_records]
    validation_records_dicts = [asdict(r) for r in validation_records]
    
    # Prepare tasks
    tasks = [
        (config.to_dict(), train_records_dicts, validation_records_dicts)
        for config in monte_carlo_configs
    ]
    
    results = []
    completed = 0
    
    # Use ProcessPoolExecutor for parallel evaluation
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_config = {
            executor.submit(_evaluate_config_worker, task): i
            for i, task in enumerate(tasks)
        }
        
        # Collect results as they complete
        for future in as_completed(future_to_config):
            config_idx = future_to_config[future]
            try:
                result_dict = future.result()
                # Reconstruct SimulationResult
                result = SimulationResult(
                    config=WeightConfig.from_dict(result_dict["config"]),
                    train_metrics=result_dict["train_metrics"],
                    validation_metrics=result_dict["validation_metrics"],
                    config_id=f"mc_{config_idx+1}",
                )
                results.append(result)
                completed += 1
                
                # Progress update
                if completed % max(1, monte_carlo_samples // 20) == 0 or completed == monte_carlo_samples:
                    print(f"  Progress: {completed}/{monte_carlo_samples} ({completed*100//monte_carlo_samples}%)", file=sys.stderr)
            except Exception as e:
                print(f"  [ERROR] Config {config_idx+1} failed: {e}", file=sys.stderr)
    
    print(f"Completed evaluation of {len(results)} configurations", file=sys.stderr)
    
    # Sort by validation MAE (primary) and bias (secondary)
    results.sort(key=lambda r: (r.validation_metrics['mae'], abs(r.validation_metrics['bias'])))
    
    # Rank results
    for i, result in enumerate(results):
        result.rank = i + 1
    
    # Find current config rank
    current_rank = len(results) + 1
    for i, result in enumerate(results):
        if (result.config.form_multiplier == current_config.form_multiplier and
            result.config.pace_fast == current_config.pace_fast and
            result.config.usage_high == current_config.usage_high):
            current_rank = result.rank
            break
    
    # Get top 10 configurations
    top_configs = results[:10]
    
    # Check if improvements meet thresholds
    best_result = results[0]
    improvement_mae = current_result.validation_metrics['mae'] - best_result.validation_metrics['mae']
    improvement_bias = abs(current_result.validation_metrics['bias']) - abs(best_result.validation_metrics['bias'])
    improvement_within_5 = best_result.validation_metrics['within_5_pts_pct'] - current_result.validation_metrics['within_5_pts_pct']
    
    meets_thresholds = (
        improvement_mae >= MIN_MAE_IMPROVEMENT and
        improvement_bias >= MIN_BIAS_IMPROVEMENT and
        improvement_within_5 >= MIN_WITHIN_5_IMPROVEMENT
    )
    
    # Output summary
    print(f"\n" + "=" * 70, file=sys.stderr)
    print(f"RESULTS SUMMARY", file=sys.stderr)
    print(f"=" * 70, file=sys.stderr)
    print(f"\nCurrent Config Rank: #{current_rank} out of {len(results)}", file=sys.stderr)
    print(f"\nBEST CONFIGURATION:", file=sys.stderr)
    print(f"  Validation MAE: {best_result.validation_metrics['mae']:.3f} "
          f"(improvement: {improvement_mae:+.3f})", file=sys.stderr)
    print(f"  Validation Bias: {best_result.validation_metrics['bias']:+.3f} "
          f"(improvement: {improvement_bias:+.3f})", file=sys.stderr)
    print(f"  Within 5 pts: {best_result.validation_metrics['within_5_pts_pct']:.1f}% "
          f"(improvement: {improvement_within_5:+.1f}%)", file=sys.stderr)
    
    print(f"\nKey Weight Changes:", file=sys.stderr)
    best_weights = best_result.config.to_dict()
    current_weights = current_config.to_dict()
    for key in ["form_multiplier", "pace_fast", "pace_slow", "usage_high", "usage_low",
                "rest_b2b_road", "rest_b2b_home", "rest_optimal"]:
        if best_weights[key] != current_weights[key]:
            print(f"  {key}: {current_weights[key]:.2f} → {best_weights[key]:.2f}", file=sys.stderr)
    
    if meets_thresholds:
        print(f"\n✓ IMPROVEMENT MEETS THRESHOLDS - Recommended to apply", file=sys.stderr)
    else:
        print(f"\n⚠ IMPROVEMENT BELOW THRESHOLDS - Review carefully", file=sys.stderr)
        print(f"  Required: MAE improvement ≥{MIN_MAE_IMPROVEMENT}, "
              f"Bias improvement ≥{MIN_BIAS_IMPROVEMENT}, "
              f"Within 5% improvement ≥{MIN_WITHIN_5_IMPROVEMENT}%", file=sys.stderr)
        print(f"  Actual: MAE {improvement_mae:.3f}, Bias {improvement_bias:.3f}, "
              f"Within 5% {improvement_within_5:.1f}%", file=sys.stderr)
    
    return {
        "simulation_summary": {
            "total_configs_tested": len(results),
            "current_config_rank": current_rank,
            "meets_thresholds": meets_thresholds,
            "improvement_mae": round(improvement_mae, 3),
            "improvement_bias": round(improvement_bias, 3),
            "improvement_within_5_pct": round(improvement_within_5, 2),
        },
        "current_config": {
            "weights": current_config.to_dict(),
            "train_metrics": current_result.train_metrics,
            "validation_metrics": current_result.validation_metrics,
        },
        "best_config": {
            "weights": best_result.config.to_dict(),
            "train_metrics": best_result.train_metrics,
            "validation_metrics": best_result.validation_metrics,
            "config_id": best_result.config_id,
            "rank": best_result.rank,
        },
        "top_10_configs": [
            {
                "rank": r.rank,
                "weights": r.config.to_dict(),
                "validation_metrics": r.validation_metrics,
            }
            for r in top_configs
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Optimize prediction model weights using simulation."
    )
    parser.add_argument(
        "--start-date",
        required=True,
        help="Start date for historical data (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end-date",
        required=True,
        help="End date for historical data (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--season",
        type=int,
        required=True,
        help="Season year (e.g., 2025 for 2024-25 season)",
    )
    parser.add_argument(
        "--train-ratio",
        type=float,
        default=0.75,
        help="Fraction of data for training (default: 0.75)",
    )
    parser.add_argument(
        "--monte-carlo-samples",
        type=int,
        default=1000,
        help="Number of Monte Carlo samples (default: 1000)",
    )
    parser.add_argument(
        "--optimize",
        nargs="+",
        help="Specific factors to optimize (e.g., form_multiplier pace_fast)",
    )
    parser.add_argument(
        "--fix-all-others",
        action="store_true",
        help="Keep non-optimized factors at current values",
    )
    parser.add_argument(
        "--output",
        default="simulation_results.json",
        help="Output file path (default: simulation_results.json)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Number of parallel workers (default: auto-detect, CPU count - 1)",
    )
    parser.add_argument(
        "--no-parallel",
        action="store_true",
        help="Disable parallel processing (use single thread)",
    )
    args = parser.parse_args()
    
    # Parse dates
    try:
        start_date = date.fromisoformat(args.start_date)
        end_date = date.fromisoformat(args.end_date)
    except ValueError as e:
        print(f"Error: Invalid date format. Use YYYY-MM-DD. {e}", file=sys.stderr)
        sys.exit(1)
    
    if start_date >= end_date:
        print(f"Error: start-date must be before end-date", file=sys.stderr)
        sys.exit(1)
    
    # Collect all records
    print(f"Collecting historical data from {start_date} to {end_date}...", file=sys.stderr)
    teams_map = get_teams_map()
    all_records: List[PlayerGameRecord] = []
    
    current_date = start_date
    while current_date <= end_date:
        games = get_games_on_date(current_date)
        for game in games:
            try:
                records = process_game(game, args.season, teams_map)
                all_records.extend(records)
            except Exception as e:
                print(f"  [WARN] Failed to process game on {current_date}: {e}", file=sys.stderr)
        current_date += timedelta(days=1)
    
    if len(all_records) < 100:
        print(f"Error: Insufficient data ({len(all_records)} records). Need at least 100.", file=sys.stderr)
        sys.exit(1)
    
    # Determine max workers
    if args.no_parallel:
        max_workers = 1
    elif args.max_workers is not None:
        max_workers = args.max_workers
    else:
        max_workers = None  # Auto-detect
    
    # Run simulation
    results = run_simulation(
        all_records,
        train_ratio=args.train_ratio,
        monte_carlo_samples=args.monte_carlo_samples,
        optimize_factors=args.optimize,
        fix_all_others=args.fix_all_others,
        max_workers=max_workers,
    )
    
    # Add metadata
    results["metadata"] = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "season": args.season,
        "total_records": len(all_records),
        "train_ratio": args.train_ratio,
        "monte_carlo_samples": args.monte_carlo_samples,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    # Save results
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\nResults saved to {args.output}", file=sys.stderr)
    
    # Print JSON to stdout
    print("\n" + "=" * 70, file=sys.stderr)
    print("JSON OUTPUT:", file=sys.stderr)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()

