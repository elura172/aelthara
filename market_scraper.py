"""Market Scraper — Polymarket liquidity bridge.

Pulls live market prices, parses temperature-band questions,
and matches them to ICAO cities for TAF ↔ Market comparison.
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)


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
    resolution_datetime: datetime

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
    "dfw": "dallas",
    "dal": "dallas",
    "hou": "houston",
    "iah": "houston",
    "phx": "phoenix",
    "las": "las vegas",
    "lvs": "las vegas",
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
    "fco": "rome",
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
    "tpe": "taipei",
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

    GAMMA_BASE = "https://gamma-api.polymarket.com"
    GAMMA_MARKETS = f"{GAMMA_BASE}/markets"

    def __init__(self):
        self._markets: List[Market] = []

    async def fetch_markets(self, keywords: str = "temperature", limit: int = 50) -> List[Market]:
        """Pull active scalar/band markets from Polymarket Gamma Markets API."""
        try:
            r = await self._aget(
                f"{self.GAMMA_MARKETS}",
                params={"q": keywords, "limit": limit, "closed": "false"},
            )
            self._markets = []
            for raw in r:
                if raw.get("acceptingOrders") and not raw.get("closed"):
                    parsed = self._parse_m(raw)
                    if parsed:
                        self._markets.append(parsed)
            return self._markets
        except Exception as e:
            logger.warning("Failed to fetch markets for keywords '%s': %s", keywords, e)
            return []

    def _parse_m(self, raw: dict) -> Optional[Market]:
        question = raw.get("question", "")
        end_date_str = raw.get("endDate", "")
        if not end_date_str:
            end_date_str = datetime.now(timezone.utc).isoformat()
        try:
            res_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        except ValueError:
            res_dt = datetime.now(timezone.utc)

        outcomes = json.loads(raw.get("outcomes", "[]"))
        prices = json.loads(raw.get("outcomePrices", "[0.5,0.5]"))
        last_traded = str(raw.get("lastTradedAt", datetime.now(timezone.utc).isoformat()))

        outcomes_type = raw.get("outcomeType", "binary")
        mkt_id = str(raw.get("id", ""))
        yes_price = float(prices[0]) if len(prices) > 0 else 0.5
        no_price = float(prices[1]) if len(prices) > 1 else 0.5

        # Try to parse band questions like "Austin 82–83°F"
        m_band = re.search(r"([A-Za-z\s]+?)\s+(\d+)[–-](\d+)\s*[°]?F", question)
        if m_band:
            city = m_band.group(1).strip()
            return Market(
                id=mkt_id,
                city=city,
                low=int(m_band.group(2)),
                high=int(m_band.group(3)),
                yes_price=yes_price,
                no_price=no_price,
                outcome_type="band",
                question=question,
                last_traded_at=last_traded,
                resolution_datetime=res_dt,
            )

        # Try to parse single threshold questions like "Will Chicago be 90°F or higher?"
        m_thresh = re.search(
            r"([A-Za-z\s]+?)\s+(?:be\s+)?(\d+)\s*(?:°\s*F)?\s*(?:degrees?\s*F)?\s*(?:or\s+(higher|lower|above|below))?",
            question,
            re.I
        )
        if m_thresh and outcomes_type == "binary":
            city = m_thresh.group(1).strip()
            threshold = int(m_thresh.group(2))
            return Market(
                id=mkt_id,
                city=city,
                low=threshold,
                high=threshold,
                yes_price=yes_price,
                no_price=no_price,
                outcome_type="scalar",
                question=question,
                last_traded_at=last_traded,
                resolution_datetime=res_dt,
            )

        # Fallback: generic binary market
        if len(outcomes) >= 2:
            return Market(
                id=mkt_id,
                city="UNKNOWN",
                low=0,
                high=0,
                yes_price=yes_price,
                no_price=no_price,
                outcome_type=outcomes_type,
                question=question,
                last_traded_at=last_traded,
                resolution_datetime=res_dt,
            )
        return None

    async def _aget(self, url: str, params: dict) -> list:
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
                data = r.json()
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning("Async GET failed for %s: %s", url, e)
            return []

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
