"""MirrorGate — the sole arbiter. Kills trades where reality and market agree."""

import logging
from dataclasses import dataclass
from math import isfinite

logger = logging.getLogger(__name__)

try:
    from scipy.stats import norm
    SCIPY_AVAILABLE = True
except ImportError:
    logger.warning("scipy not available, using fallback Gaussian")
    SCIPY_AVAILABLE = False
    norm = None


def _fallback_norm_cdf(x: float, loc: float = 0.0, scale: float = 1.0) -> float:
    """Simple error function approximation when scipy unavailable."""
    from math import sqrt
    z = (x - loc) / (scale * sqrt(2)) if scale > 0 else 0
    return 0.5 * (1 + _fallback_erf(z))


def _fallback_erf(x: float) -> float:
    """Approximation of error function."""
    from math import exp
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = 1 if x >= 0 else -1
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * exp(-x * x)
    return sign * y


def _safe_cdf(x: float, loc: float = 0.0, scale: float = 1.0) -> float:
    """Safe CDF with input validation."""
    if not (isfinite(x) and isfinite(loc) and isfinite(scale) and scale > 0):
        return 0.5
    try:
        if SCIPY_AVAILABLE:
            return norm.cdf(x, loc, scale)
        return _fallback_norm_cdf(x, loc, scale)
    except Exception as e:
        logger.warning(f"CDF error: {e}")
        return 0.5


@dataclass
class GateDecision:
    passed: bool
    diff: float
    taf_tx: float
    market_threshold: float
    reason: str
    action: str       # "PASS", "SELL_NO", "BUY_YES", "LEAN_YES", "LEAN_NO", "MID VALUE", "HARVEST"
    edge: float = 0.0
    model_prob: float = 0.0
    market_prob: float = 0.0
    is_pin: bool = False
    strategy: str = "VANILLA"


def to_distribution(tx_celsius: float) -> dict:
    """Convert a point TX estimate into a Gaussian distribution with adaptive sigma."""
    # base_sigma 2.0°C reflects honest TAF/model forecast uncertainty
    base_sigma = 2.0
    temp_factor = min(abs(tx_celsius) / 30.0, 1.0)
    sigma = base_sigma + temp_factor
    return {"mean": float(tx_celsius), "std": sigma}


def prob_band(mean: float, std: float, low: float, high: float) -> float:
    return _safe_cdf(high, mean, std) - _safe_cdf(low, mean, std)


def prob_above(mean: float, std: float, threshold: float) -> float:
    return 1.0 - _safe_cdf(threshold, mean, std)


def prob_below(mean: float, std: float, threshold: float) -> float:
    return _safe_cdf(threshold, mean, std)


class MirrorGate:
    """
    MirrorGate — the sole arbiter. 
    Implements 'The Harvester' strategy by detecting overconfident pins.
    """

    def __init__(self, min_edge: float = 0.08):
        self.min_edge = min_edge

    def assess(
        self,
        taf_tx: float,
        market_threshold: float,
        market_prob: float,
        outcome_type: str = "above",
    ) -> GateDecision:
        """
        Assess whether a market is mispriced, with specific focus on 'Pin' outcomes.
        """
        diff = abs(market_threshold - taf_tx)
        
        # Detect 'The Pin'
        # Since markets are often Fahrenheit, 0.3°C is a safe epsilon (~0.5°F)
        is_pin = diff < 0.3 

        dist = to_distribution(taf_tx)
        action, detail, edge, model_prob, market_prob = self._decide_action(
            dist,
            market_threshold,
            market_prob,
            outcome_type=outcome_type,
            is_pin=is_pin
        )
        
        strategy = "HARVESTER" if (is_pin and action == "SELL_NO") else "VANILLA"
        if strategy == "HARVESTER":
            action = "HARVEST"
            detail = f"PIN HARVEST | {detail}"

        reason = f"{action} | {detail} | ΔT={diff:.1f}°"

        return GateDecision(
            passed=action != "PASS",
            diff=diff,
            taf_tx=taf_tx,
            market_threshold=market_threshold,
            reason=reason,
            action=action,
            edge=edge,
            model_prob=model_prob,
            market_prob=market_prob,
            is_pin=is_pin,
            strategy=strategy
        )

    def _decide_action(
        self,
        dist: dict,
        market_threshold: float,
        market_prob: float,
        outcome_type: str = "above",
        is_pin: bool = False
    ) -> tuple[str, str, float, float, float]:
        """Probability-aware trading decisions."""
        mean = dist.get("mean")
        sigma = dist.get("std")

        if not all(isfinite(x) for x in (mean, sigma, market_threshold, market_prob)) or sigma <= 0:
            return "PASS", f"Invalid input: mean={mean}, sigma={sigma}", 0.0, 0.0, 0.0

        if outcome_type == "below":
            model_prob = prob_below(mean, sigma, market_threshold)
        else:
            model_prob = prob_above(mean, sigma, market_threshold)

        edge = model_prob - market_prob

        # Expected value approximation (binary market)
        ev = model_prob * (1 - market_prob) - (1 - model_prob) * market_prob

        # --- PIN HARVEST ---
        if is_pin and market_prob > 0.65 and edge < -self.min_edge:
            return "SELL_NO", f"Pin Inflation EV={ev:.3f}", edge, model_prob, market_prob

        # --- MAIN EV-DRIVEN LOGIC ---
        if ev > self.min_edge:
            return "BUY_YES", f"Positive EV={ev:.3f}", edge, model_prob, market_prob

        if ev < -self.min_edge:
            return "SELL_NO", f"Negative EV={ev:.3f}", edge, model_prob, market_prob

        # --- WEAK SIGNALS ---
        if edge > self.min_edge:
            return "LEAN_YES", f"Weak edge={edge:.3f}", edge, model_prob, market_prob

        if edge < -self.min_edge:
            return "LEAN_NO", f"Weak neg edge={edge:.3f}", edge, model_prob, market_prob

        return "PASS", f"No edge: ev={ev:.3f}", edge, model_prob, market_prob
