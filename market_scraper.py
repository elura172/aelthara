"""Market Scraper — Polymarket liquidity bridge.

Pulls live market prices, parses temperature-band questions,
and matches them to ICAO cities for TAF ↔ Market comparison.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import httpx


@dataclass
class Market:
    id: str
    city: str
    low: int
    high: int
    yes_price: float
    no_price: float
    outcome_type: str       # "band" | "scalar"
    question: str
    last_traded_at: str

    @property
    def mid_price(self) -> float:
        return (self.yes_price + self.no_price) / 2.0

    @property
    def band_center(self) -> float:
        return (self.low + self.high) / 2.0

    def __str__(self) -> str:
        return (
            f"Market({self.city} {self.low}–{self.high}° "
            f"yes={self.yes_price:.3f} no={self.no_price:.3f} "
            f"type={self.outcome_type})"
        )


# City alias mapping for matching market city names to canonical names
CITY_ALIASES = {
    # US cities
    "nyc": "new york",
    "jfk": "new york",
    "la": "los angeles",
    "lax": "los angeles",
    "sf": "san francisco",
    "sfo": "san francisco",
    "chi": "chicago",
    "ord": "chicago",
    "mia": "miami",
    "atl": "atlanta",
    "sea": "seattle",
    "den": "denver",
    "phx": "phoenix",
    "aus": "austin",
    "bos": "boston",
    "phi": "philadelphia",
    "sd": "san diego",
    "lve": "las vegas",
    "iad": "washington",
    "dca": "washington",
    # European cities
    "lon": "london",
    "ldn": "london",
    "par": "paris",
    "fra": "frankfurt",
    "ams": "amsterdam",
    "rom": "rome",
    "mil": "milan",
    "mad": "madrid",
    "bcn": "barcelona",
    "muc": "munich",
    "zrh": "zurich",
    "vie": "vienna",
    "prg": "prague",
    "cph": "copenhagen",
    "osl": "oslo",
    "sto": "stockholm",
    "hel": "helsinki",
    "dub": "dublin",
    "lis": "lisbon",
    # Asian cities
    "tyo": "tokyo",
    "sel": "seoul",
    "sin": "singapore",
    "hkg": "hong kong",
    "pek": "beijing",
    "pvf": "shanghai",
    "bkk": "bangkok",
    "del": "delhi",
    "bom": "mumbai",
    "kul": "kuala lumpur",
    "dxb": "dubai",
    # Oceania
    "syd": "sydney",
    "mel": "melbourne",
    "bne": "brisbane",
    "per": "perth",
    "akl": "auckland",
    "wlg": "wellington",
    "chc": "christchurch",
    # South America
    "gru": "sao paulo",
    "eze": "buenos aires",
}


class MarketScraper:
    """Fetches and parses Polymarket temperature markets."""

    CLOB_BASE = "https://clob.polymarket.com"

    def __init__(self):
        self._markets: List[Market] = []

    async def fetch_markets(self, keywords: str = "temperature", limit: int = 50) -> List[Market]:
        """Pull active scalar/band markets from Polymarket CLOB."""
        try:
            r = await self._aget(
                f"{self.CLOB_BASE}/markets",
                params={"keyword": keywords, "limit": limit, "closed": "false"},
            )
            self._markets = [self._parse_m(m) for m in r if m.get("acceptingOrders") and not m.get("closed")]
            return self._markets
        except Exception:
            return []

    def _parse_m(self, raw: dict) -> Optional[Market]:
        question = raw.get("question", "")
        # Try to parse band questions like "Austin 82–83°F"
        m_band = re.search(r"([A-Za-z\s]+?)\s+(\d+)[–-](\d+)\s*[°]?F", question)
        if m_band:
            city = m_band.group(1).strip()
            low = int(m_band.group(2))
            high = int(m_band.group(3))
            outcomes = json.loads(raw.get("outcomes", "[]"))
            prices = json.loads(raw.get("outcomePrices", "[0.5,0.5]"))
            return Market(
                id=str(raw.get("id", "")),
                city=city,
                low=low,
                high=high,
                yes_price=float(prices[0]) if len(prices) > 0 else 0.5,
                no_price=float(prices[1]) if len(prices) > 1 else 0.5,
                outcome_type="band",
                question=question,
                last_traded_at=str(raw.get("lastTradedAt", datetime.now(timezone.utc).isoformat())),
            )
        # Scalar markets — fallback generic parsing
        outcomes = json.loads(raw.get("outcomes", "[]"))
        prices = json.loads(raw.get("outcomePrices", "[0.5,0.5]"))
        if len(outcomes) >= 2:
            return Market(
                id=str(raw.get("id", "")),
                city="UNKNOWN",
                low=0,
                high=0,
                yes_price=float(prices[0]) if len(prices) > 0 else 0.5,
                no_price=float(prices[1]) if len(prices) > 1 else 0.5,
                outcome_type=raw.get("outcomeType", "scalar"),
                question=question,
                last_traded_at=str(raw.get("lastTradedAt", datetime.now(timezone.utc).isoformat())),
            )
        return None

    async def _aget(self, url: str, params: dict) -> list:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else []

    def match_to_city(self, market: Market, city: str) -> bool:
        """Match market city to target city with alias support."""
        m = market.city.lower().strip()
        c = city.lower().strip()

        # Direct match
        if m == c:
            return True

        # Resolve both through alias table
        m_canonical = CITY_ALIASES.get(m, m)
        c_canonical = CITY_ALIASES.get(c, c)

        return m_canonical == c_canonical

    def lag_edge(self, market: Market, oracle_mid: float) -> float:
        """Edge as |oracle_mid − market_mid|; positive means market is wrong relative to oracle."""
        return abs(oracle_mid - market.mid_price)
