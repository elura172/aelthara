"""MirrorGate — the sole arbiter. Kills trades where reality and market agree."""

from dataclasses import dataclass
from scipy.stats import norm

from signal import SignalType

MODEL_STD_DEG_C = 2.0


@dataclass
class GateDecision:
    passed: bool
    diff: float
    taf_tx: float
    market_threshold: float
    reason: str
    action: str       # String value of SignalType (PASS/SELL_NO/BUY_YES/LEAN_YES/LEAN_NO/MID_VALUE)
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


def decide_signal(model_prob: float, market_prob: float) -> SignalType:
    edge = model_prob - market_prob
    if market_prob > 0.95 and model_prob < 0.93:
        return SignalType.SELL_NO
    if market_prob < 0.02 and model_prob > 0.05:
        return SignalType.BUY_YES
    if 0.15 <= market_prob <= 0.40 and model_prob > market_prob + 0.15:
        return SignalType.LEAN_YES
    if 0.60 <= market_prob <= 0.85 and model_prob < market_prob - 0.15:
        return SignalType.LEAN_NO
    if 0.3 < market_prob < 0.7 and edge > 0.15:
        return SignalType.MID_VALUE
    return SignalType.PASS


class MirrorGate:
    """
    Core gate: assess whether a market is mispriced enough to act.

    Uses a probability-aware decision model:
      - model_prob = prob_above(mean, std, market_threshold)  (P(temp >= threshold))
      - market_prob = market_price (the YES probability from CLOB)
      - edge = model_prob - market_prob
    """

    def __init__(self, kill_threshold: float = 1.5):
        self.kill_threshold = kill_threshold

    def assess(self, taf_tx: float, market_threshold: float, market_prob: float, outcome_type: str = "above") -> GateDecision:
        """
        Assess whether a market is mispriced.

        Args:
            taf_tx: The TAF-predicted max temperature (°C).
            market_threshold: The temperature threshold in the market question.
            market_prob: The market's YES probability (0-1).

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
        action, detail, edge, model_prob, market_prob = self._decide_action(taf_tx, market_threshold, market_prob, outcome_type)
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

    def _decide_action(self, taf_tx: float, market_threshold: float, market_prob: float, outcome_type: str) -> tuple[str, str, float, float, float]:
        """Probability-aware trading decisions."""
        prob_kwargs = {"mean": taf_tx, "std": MODEL_STD_DEG_C, "threshold": market_threshold}
        model_prob = prob_below(**prob_kwargs) if outcome_type == "below" else prob_above(**prob_kwargs)
        edge = model_prob - market_prob
        signal = decide_signal(model_prob, market_prob)

        if signal == SignalType.SELL_NO:
            detail = f"Overpriced YES: mkt={market_prob:.3f}, model={model_prob:.3f}, edge={edge:.3f}"
        elif signal == SignalType.BUY_YES:
            detail = f"Underpriced YES: mkt={market_prob:.3f}, model={model_prob:.3f}, edge={edge:.3f}"
        elif signal == SignalType.LEAN_YES:
            detail = f"Lean YES: mkt={market_prob:.3f}, model={model_prob:.3f}, edge={edge:.3f}"
        elif signal == SignalType.LEAN_NO:
            detail = f"Lean NO: mkt={market_prob:.3f}, model={model_prob:.3f}, edge={edge:.3f}"
        elif signal == SignalType.MID_VALUE:
            detail = f"Mid-value: mkt={market_prob:.3f}, model={model_prob:.3f}, edge={edge:.3f}"
        else:
            detail = f"No edge: mkt={market_prob:.3f}, model={model_prob:.3f}, edge={edge:.3f}"
        return signal.value, detail, edge, model_prob, market_prob
