from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class SignalType(str, Enum):
    SELL_NO = "SELL_NO"
    BUY_YES = "BUY_YES"
    LEAN_YES = "LEAN_YES"
    LEAN_NO = "LEAN_NO"
    MID_VALUE = "MID_VALUE"
    HARVEST = "HARVEST"
    PASS = "PASS"


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class OracleSignal:
    market: str
    model_mean_c: float
    model_prob: float
    market_price: float
    edge: float
    confidence: Confidence
    signal: SignalType
    reason: str
    kelly_fraction: float
    timestamp: Optional[datetime] = None
    market_id: Optional[str] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)

    def to_json(self) -> dict:
        return {
            "market": self.market,
            "market_id": self.market_id,
            "model_mean_c": round(self.model_mean_c, 1),
            "model_prob": round(self.model_prob, 2),
            "market_price": round(self.market_price, 2),
            "edge": round(self.edge, 4),
            "confidence": self.confidence.value,
            "signal": self.signal.value,
            "reason": self.reason,
            "kelly_fraction": round(self.kelly_fraction, 4),
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }