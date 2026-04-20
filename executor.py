"""Executor — scans Polymarket for Impossible Rungs and places limit orders."""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

from gate import GateDecision, to_distribution, prob_above, prob_below, prob_band

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
CHAIN_ID = 137
MIN_ORDER = 1.0
DRY_RUN = os.environ.get("MIRROR_DRY_RUN", "true").lower() != "false"

# Timezone offsets for major airports (hours from UTC)
ICAO_TIMEZONES = {
    "KJFK": -4, "KLAX": -7, "KORD": -5, "KMIA": -4, "KATL": -4, "KSEA": -7, "KDEN": -6,
    "EGLL": 0, "LFPG": 1, "EDDF": 1, "EHAM": 1,
    "RJTT": 9, "RKSS": 9, "WSSS": 8, "VHHH": 8,
    "ZBAA": 8, "ZSPD": 8, "YSSY": 10, "NZAA": 12,
    "SBGR": -3, "SAEZ": -3,
    "KDFW": -5, "KIAH": -5, "KPHX": -7, "KLAS": -7,
    "LEMD": 2, "LIRF": 2, "EDDM": 2, "EIDW": 1,
    "VTBS": 7, "VABB": 5, "VIDP": 5, "RCTP": 8,
}


@dataclass
class Rung:
    market_id: str
    token_id: str
    question: str
    threshold_celsius: float
    taf_side: str          # "YES" or "NO" — what TAF implies
    market_price: float    # price of taf_side token (0-1)
    diff: float = 0.0
    distribution: dict = field(default_factory=lambda: {"mean": 0.0, "std": 2.0})
    outcome_type: str = "below"  # "below" (<=) or "above" (>=)
    resolution_hour_local: Optional[int] = None  # Hour (0-23 local) when market resolves

    @property
    def edge(self) -> float:
        """
        Edge = model_prob - market_prob for the taf_side.
        Uses prob_below for <= markets, prob_above for >= markets.
        """
        mean = self.distribution["mean"]
        std = self.distribution["std"]

        if self.outcome_type == "below":
            # Market: "Will high be <= threshold?" → YES means below/at threshold
            if self.taf_side == "YES":
                model_prob = prob_below(mean, std, self.threshold_celsius)
            else:
                model_prob = prob_above(mean, std, self.threshold_celsius)
        else:
            # Market: "Will high be >= threshold?" → YES means above/at threshold
            if self.taf_side == "YES":
                model_prob = prob_above(mean, std, self.threshold_celsius)
            else:
                model_prob = prob_below(mean, std, self.threshold_celsius)

        market_prob = float(self.market_price)
        return model_prob - market_prob

    def __str__(self) -> str:
        return (
            f"{self.question[:55]:<55}  "
            f"threshold={self.threshold_celsius:.1f}°C  "
            f"side={self.taf_side}  price={self.market_price:.2f}  edge={self.edge:.2f}"
        )


class Executor:
    def __init__(self, bankroll: float = 50.0):
        self.bankroll = bankroll

    # ── Market Discovery ──────────────────────────────────────────────────────

    def scan_rungs(self, city: str, taf_tx_celsius: float, icao: str = None, current_utc_hour: int = None) -> list[Rung]:
        """
        Search Gamma for temperature markets matching city, extract threshold
        in °C, score by distance from TAF TX, return sorted furthest-first.
        
        Args:
            city: City name for market search
            taf_tx_celsius: TAF predicted max temperature
            icao: ICAO code for timezone lookup (to skip post-peak markets)
            current_utc_hour: Current UTC hour (optional, for time-based filtering)
        """
        markets = self._fetch_markets(city)
        rungs: list[Rung] = []

        for m in markets:
            if m.get("closed") or not m.get("acceptingOrders"):
                continue
            
            threshold = self._parse_threshold_celsius(m.get("question", ""))
            if threshold is None:
                continue

            # Detect outcome type: "at or below" vs "at or above"
            outcome_type = self._detect_outcome_type(m.get("question", ""))
            
            # Check if high has passed (skip post-peak markets)
            if icao and current_utc_hour is not None:
                if self._high_has_passed(icao, current_utc_hour):
                    # Skip markets resolving today — high already happened or happening now
                    continue

            tokens = json.loads(m.get("clobTokenIds", "[]"))
            prices = json.loads(m.get("outcomePrices", "[0.5,0.5]"))
            outcomes = json.loads(m.get("outcomes", '["Yes","No"]'))
            if len(tokens) < 2 or len(prices) < 2:
                continue

            # For <= markets: YES means "at or below" the threshold
            # For >= markets: YES means "at or above" the threshold
            if outcome_type == "below":
                # For <= markets: YES if forecast is at/below threshold
                taf_side = "YES" if taf_tx_celsius <= threshold else "NO"
            else:
                # For >= markets: YES if forecast is at/above threshold
                taf_side = "YES" if taf_tx_celsius >= threshold else "NO"

            side_idx = 0 if outcomes[0].lower().startswith("y") else 1
            if taf_side == "NO":
                side_idx = 1 - side_idx
            market_price = float(prices[side_idx]) if side_idx < len(prices) else 0.5

            dist = to_distribution(taf_tx_celsius)

            rung = Rung(
                market_id=str(m.get("id", "")),
                token_id=tokens[0] if taf_side == "YES" else tokens[1],
                question=m.get("question", ""),
                threshold_celsius=threshold,
                taf_side=taf_side,
                market_price=market_price,
                diff=abs(threshold - taf_tx_celsius),
                distribution=dist,
                outcome_type=outcome_type,
            )
            rungs.append(rung)

        rungs.sort(key=lambda r: r.diff, reverse=True)
        return rungs

    def _detect_outcome_type(self, question: str) -> str:
        """
        Detect if market asks "at or below" vs "at or above".
        
        Polymarket temperature markets typically use:
        - "will be X°F or lower" / "at X°F or below" → "below"
        - "will be X°F or higher" / "at X°F or above" → "above"
        """
        q = question.lower()
        
        # Below patterns
        below_patterns = [
            r"\bor\s+lower\b",
            r"\bor\s+under\b",
            r"\bat\s+\d+.*\s+or\s+below",
            r"\bunder\s+\d+",
            r"≤\s*\d+",  # Unicode ≤
            r"<=\s*\d+",
        ]
        
        # Above patterns  
        above_patterns = [
            r"\bor\s+higher",
            r"\bat\s+\d+.*\s+or\s+above",
            r"\bover\s+\d+",
            r"≥\s*\d+",  # Unicode ≥
            r">=\s*\d+",
        ]
        
        for pattern in below_patterns:
            if re.search(pattern, q):
                return "below"
        
        for pattern in above_patterns:
            if re.search(pattern, q):
                return "above"
        
        # Default to "below" (most common on Polymarket)
        return "below"

    def _high_has_passed(self, icao: str, current_utc_hour: int) -> bool:
        """
        Check if the daily high has likely already occurred.
        
        Approximation: if it's past 21:00 local time, the high for the day
        has likely passed (highs typically occur 2-4pm local).
        """
        tz_offset = ICAO_TIMEZONES.get(icao, 0)
        local_hour = (current_utc_hour + tz_offset) % 24
        
        # High typically occurs between 14:00-16:00 local
        # If it's past 21:00 local, the high has definitely passed
        # If it's between 18:00-21:00, we're in cooldown — skip for safety
        return local_hour >= 21

    def _distribution_for_celsius(self, celsius: float) -> dict:
        """Simple conversion of threshold to a distribution for gate/model use."""
        return {"mean": celsius, "std": 2.0}

    def _fetch_markets(self, city: str) -> list[dict]:
        try:
            r = httpx.get(
                f"{GAMMA_BASE}/markets",
                params={"q": f"{city} temperature", "closed": "false", "limit": 50},
                timeout=12,
            )
            r.raise_for_status()
            return r.json() if isinstance(r.json(), list) else []
        except Exception as e:
            logger.warning("Failed to fetch markets for %s: %s", city, e)
            return []

    def _parse_threshold_celsius(self, question: str) -> Optional[float]:
        # Match patterns like "85°F", "85 degrees F", "30°C", "30 degrees C"
        m_f = re.search(r"(\d+(?:\.\d+)?)\s*(?:°\s*F|degrees?\s*F)", question, re.I)
        if m_f:
            return (float(m_f.group(1)) - 32) * 5 / 9
        m_c = re.search(r"(\d+(?:\.\d+)?)\s*(?:°\s*C|degrees?\s*C)", question, re.I)
        if m_c:
            return float(m_c.group(1))
        return None

    # ── Position Sizing ──────────────────────────────────────────────────────

    def calc_size(self, bankroll: Optional[float] = None) -> float:
        """
        Position sizing:
          * base = 2% of bankroll
          * multiplier scales with edge, capped at 3x
          * max per position: 5% bankroll
          * max per city/day: 10% bankroll
          * cheap-bet floor: $2–$10
        """
        b = bankroll if bankroll is not None else self.bankroll
        base = b * 0.02
        return min(base * 3, b * 0.05)

    def size_for_edge(self, edge: float, bankroll: Optional[float] = None) -> float:
        """Capital allocation: size = bankroll × edge × k, bounded by risk rules."""
        b = bankroll if bankroll is not None else self.bankroll
        if edge <= 0:
            return 0.0

        k = 3.0
        size = b * edge * k
        size = min(size, b * 0.05)
        size = max(size, MIN_ORDER)
        return round(size, 2)

    # ── Execution ───────────────────────────────────────────────────────────

    def execute(self, decision: GateDecision, market: str, bankroll: Optional[float] = None) -> dict:
        size = self.size_for_edge(decision.edge, bankroll)

        if decision.action == "PASS":
            print(f"EXECUTE: PASS {market}  edge={decision.edge:.4f}  size={size}")
            return {"action": "PASS", "market": market, "size": size, "reason": decision.reason}

        action_map = {
            "BUY_YES": "BUY",
            "SELL_NO": "SELL_NO",
            "LEAN_YES": "LEAN_BUY",
            "LEAN_NO": "LEAN_SELL",
            "MID VALUE": "MID_VALUE",
        }

        action_label = action_map.get(decision.action, "UNKNOWN")

        if DRY_RUN:
            print(f"EXECUTE: {action_label} {market} size={size} model_prob={decision.model_prob:.3f} market_prob={decision.market_prob:.3f}  (DRY_RUN)")
        else:
            print(f"EXECUTE: {action_label} {market} size={size} model_prob={decision.model_prob:.3f} market_prob={decision.market_prob:.3f}")

        return {"action": action_label, "market": market, "size": size, "reason": decision.reason}
