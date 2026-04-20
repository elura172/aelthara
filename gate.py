"""MirrorGate — the sole arbiter. Kills trades where reality and market agree."""

from dataclasses import dataclass
from scipy.stats import norm


@dataclass
class GateDecision:
    passed: bool
    diff: float
    taf_tx: float
    market_threshold: float
    reason: str
    action: str       # "PASS", "SELL_NO", "BUY_YES", "LEAN_YES", "LEAN_NO", "MID VALUE"
    edge: float = 0.0
    model_prob: float = 0.0
    market_prob: float = 0.0


def to_distribution(tx_celsius: float) -> dict:
    """Convert a point TX estimate into a simple Gaussian distribution."""
    return {"mean": float(tx_celsius), "std": 2.0}


def prob_band(mean: float, std: float, low: float, high: float) -> float:
    return norm.cdf(high, mean, std) - norm.cdf(low, mean, std)


def prob_above(mean: float, std: float, threshold: float) -> float:
    return 1.0 - norm.cdf(threshold, mean, std)


def prob_below(mean: float, std: float, threshold: float) -> float:
    return norm.cdf(threshold, mean, std)


def decide_signal(model_prob: float, market_prob: float) -> str:
    """Centralized signal decision logic."""
    edge = model_prob - market_prob

    if market_prob > 0.95 and model_prob < 0.93:
        return "SELL_NO"
    if market_prob < 0.02 and model_prob > 0.05:
        return "BUY_YES"
    if 0.15 <= market_prob <= 0.40 and model_prob > market_prob + 0.15:
        return "LEAN_YES"
    if 0.60 <= market_prob <= 0.85 and model_prob < market_prob - 0.15:
        return "LEAN_NO"
    if 0.3 < market_prob < 0.7 and edge > 0.15:
        return "MID_VALUE"
    return "PASS"


class MirrorGate:
    """
    Core gate: assess whether a market is mispriced enough to act.

    Uses a probability-aware decision model:
      - model_prob = prob_above(...) for "above" markets, prob_below(...) for "below" markets
      - market_prob = market_price (the YES probability from CLOB)
      - edge = model_prob - market_prob
    """

    def __init__(self, kill_threshold: float = 1.5):
        self.kill_threshold = kill_threshold

    def assess(
        self,
        taf_tx: float,
        market_threshold: float,
        market_prob: float,
        outcome_type: str = "above",
    ) -> GateDecision:
        """
        Assess whether a market is mispriced.

        Args:
            taf_tx: The TAF-predicted max temperature (°C).
            market_threshold: The temperature threshold in the market question.
            market_prob: The market's YES probability (0-1).
            outcome_type: Market direction, "above" (>=) or "below" (<=).

        Returns:
            GateDecision with action, human-readable reason, and edge.
        """
        diff = abs(market_threshold - taf_tx)
        passed = diff >= self.kill_threshold

        if not passed:
            return GateDecision(
                passed=False,
                diff=diff,
                taf_tx=taf_tx,
                market_threshold=market_threshold,
                reason=f"KILL  |{market_threshold:.1f} − {taf_tx:.1f}| = {diff:.1f}° < {self.kill_threshold}° (noise)",
                action="PASS",
                edge=0.0,
                model_prob=market_prob,
                market_prob=market_prob,
            )

        # Probability-aware decision logic
        action, detail, edge, model_prob, market_prob = self._decide_action(
            taf_tx,
            market_threshold,
            market_prob,
            outcome_type=outcome_type,
        )
        reason = f"{action} | {detail} | ΔT={diff:.1f}°"

        return GateDecision(
            passed=True,
            diff=diff,
            taf_tx=taf_tx,
            market_threshold=market_threshold,
            reason=reason,
            action=action,
            edge=edge,
            model_prob=model_prob,
            market_prob=market_prob,
        )

    def _decide_action(
        self,
        taf_tx: float,
        market_threshold: float,
        market_prob: float,
        outcome_type: str = "above",
    ) -> tuple[str, str, float, float, float]:
        """Probability-aware trading decisions."""
        # Model probability depends on market type.
        if outcome_type == "below":
            model_prob = prob_below(
                mean=taf_tx,
                std=2.0,
                threshold=market_threshold,
            )
        else:
            model_prob = prob_above(
                mean=taf_tx,
                std=2.0,
                threshold=market_threshold,
            )
        edge = model_prob - market_prob

        # --- SELL expensive YES (your main engine) ---
        if market_prob > 0.95 and model_prob < 0.93:
            return "SELL_NO", f"Overpriced YES: mkt={market_prob:.3f}, model={model_prob:.3f}, edge={edge:.3f}", edge, model_prob, market_prob

        # --- BUY dirt cheap YES (lottery mode) ---
        if market_prob < 0.02 and model_prob > 0.05:
            return "BUY_YES", f"Underpriced YES: mkt={market_prob:.3f}, model={model_prob:.3f}, edge={edge:.3f}", edge, model_prob, market_prob

        # --- LEAN signals (middle probability band - subtle directional edge) ---
        # LEAN_YES: market underprices, model says temp likely exceeds threshold
        if 0.15 <= market_prob <= 0.40 and model_prob > market_prob + 0.15:
            return "LEAN_YES", f"Lean YES: mkt={market_prob:.3f}, model={model_prob:.3f}, edge={edge:.3f}", edge, model_prob, market_prob

        # LEAN_NO: market overprices, model says temp likely below threshold
        if 0.60 <= market_prob <= 0.85 and model_prob < market_prob - 0.15:
            return "LEAN_NO", f"Lean NO: mkt={market_prob:.3f}, model={model_prob:.3f}, edge={edge:.3f}", edge, model_prob, market_prob

        # --- MID VALUE ---
        if 0.3 < market_prob < 0.7 and edge > 0.15:
            return "MID VALUE", f"Mid-value: mkt={market_prob:.3f}, model={model_prob:.3f}, edge={edge:.3f}", edge, model_prob, market_prob

        return "PASS", f"No edge: mkt={market_prob:.3f}, model={model_prob:.3f}, edge={edge:.3f}", edge, model_prob, market_prob
