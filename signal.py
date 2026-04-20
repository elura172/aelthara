from dataclasses import dataclass
from enum import Enum


class SignalType(str, Enum):
    SELL_NO = "SELL_NO"
    BUY_YES = "BUY_YES"
    LEAN_YES = "LEAN_YES"
    LEAN_NO = "LEAN_NO"
    MID_VALUE = "MID_VALUE"
    PASS = "PASS"


class Confidence(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


@dataclass
class OracleSignal:
    market: str
    model_mean_c: float
    model_prob: float
    market_price: int
    edge: int
    confidence: Confidence
    signal: SignalType
    reason: str
    kelly_fraction: float

    def to_json(self) -> dict:
        return {
            "market": self.market,
            "model_mean_c": round(self.model_mean_c, 1),
            "model_prob": round(self.model_prob, 2),
            "market_price": self.market_price,
            "edge": self.edge,
            "confidence": self.confidence.value,
            "signal": self.signal.value,
            "reason": self.reason,
            "kelly_fraction": round(self.kelly_fraction, 3),
        }
