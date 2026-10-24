import math
from collections import deque
from typing import Dict


def normalize(x: float, k: float) -> float:
    return x / (x + k + 1e-9)


class MirrorFieldNode:
    def __init__(self, node_id: str, window_size: int = 20):
        self.node_id = node_id
        self.atmo = deque(maxlen=window_size)
        self.market = deque(maxlen=window_size)
        self.model = deque(maxlen=window_size)
        self.coherence = deque(maxlen=window_size)

    def update(
        self,
        atmo_entropy: float,
        market_entropy: float,
        model_uncertainty: float,
    ) -> Dict:
        self.atmo.append(atmo_entropy)
        self.market.append(market_entropy)
        self.model.append(model_uncertainty)

        if len(self.atmo) < 5:
            return self._output(1.0, "WARMUP", 0.0, 0.0)

        A = sum(self.atmo) / len(self.atmo)
        M = sum(self.market) / len(self.market)
        O = sum(self.model) / len(self.model)

        A = normalize(A, 20.0)
        M = normalize(M, 0.1)
        O = normalize(O, 0.3)

        E = A + M + O
        C = math.exp(-E)

        self.coherence.append(C)
        dC = 0.0 if len(self.coherence) < 2 else self.coherence[-1] - self.coherence[-2]
        stability = max(self.coherence) - min(self.coherence)
        regime = self._classify(C, stability)

        return self._output(C, regime, dC, stability)

    def _classify(self, C: float, stability: float) -> str:
        if C >= 0.75 and stability < 0.15:
            return "CALM"
        if C >= 0.4:
            return "TRANSITION" if stability < 0.25 else "FRAGILE_TRANSITION"
        return "CHAOS"

    def _output(self, C: float, regime: str, dC: float, stability: float) -> Dict:
        return {
            "node_id": self.node_id,
            "coherence": C,
            "delta_coherence": dC,
            "stability": stability,
            "regime": regime,
        }