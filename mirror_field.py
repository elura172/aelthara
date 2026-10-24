import math
from collections import deque
from typing import Dict, Optional

_prev_coherence: Optional[float] = None
_coherence_history: deque[float] = deque(maxlen=10)


def normalize(x: float, k: float) -> float:
    return x / (x + k + 1e-9)


def classify_regime(C: float) -> str:
    if C >= 0.70:
        return "CALM"
    if C >= 0.15:
        return "TRANSITION"
    return "CHAOS"


def compute_mirror_field(
    atmo_entropy: float,
    market_entropy: float,
    model_uncertainty: float,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    global _prev_coherence

    if weights is None:
        weights = {"atmo": 1.0, "market": 1.0, "model": 1.0}

    KA = 20.0
    KM = 0.1
    KO = 0.3

    A = normalize(atmo_entropy, KA)
    M = normalize(market_entropy, KM)
    O = normalize(model_uncertainty, KO)

    E = (
        weights["atmo"] * A
        + weights["market"] * M
        + weights["model"] * O
    )

    C = math.exp(-E)

    global _prev_coherence
    if _prev_coherence is None:
        dC = 0.0
    else:
        dC = C - _prev_coherence
    _prev_coherence = C

    _coherence_history.append(C)
    stability = max(_coherence_history) - min(_coherence_history) if len(_coherence_history) > 1 else 0.0

    return {
        "coherence": C,
        "delta_coherence": dC,
        "entropy_energy": E,
        "regime": classify_regime(C),
        "stability": stability,
    }