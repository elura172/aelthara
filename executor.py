"""Executor — scans Polymarket for Impossible Rungs and places limit orders."""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()  # Ensure .env is loaded before is_dry_run() is evaluated

from gate import GateDecision, to_distribution, prob_above, prob_below, prob_band
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from config import ICAO_TIMEZONES as _ICAO_TZ

# The CLOB API expects the raw strings "BUY" / "SELL".
# We define them here to avoid chasing version-dependent import paths.
BUY = "BUY"
SELL = "SELL"

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
CHAIN_ID = 137
MIN_ORDER = 5.0
JOURNAL_PATH = Path(__file__).parent / "trades.jsonl"


def is_dry_run() -> bool:
    """Check DRY_RUN dynamically at runtime."""
    return os.environ.get("MIRROR_DRY_RUN", "true").lower() == "true"


@dataclass
class Rung:
    market_id: str
    token_id_yes: str
    token_id_no: str
    question: str
    threshold_celsius: float
    yes_price: float
    no_price: float
    outcome_type: str = "below"  # "below" (<=) or "above" (>=)
    temp_type: str = "TX"  # "TX" (highest) or "TN" (lowest)
    resolution_datetime: Optional[datetime] = None

    def __str__(self) -> str:
        return (
            f"{self.question[:55]:<55}  "
            f"threshold={self.threshold_celsius:.1f}°C  "
            f"type={self.temp_type}  price={self.yes_price:.2f}  edge={self.edge:.2f}"
        )


class Executor:
    def __init__(self, bankroll: float = 50.0):
        self.bankroll = bankroll
        self._gamma_cache = []
        self._gamma_cache_time = 0
        self._open_positions: dict[str, str] = {}  # market_id -> action already placed this session

        # Initialize CLOB Client if credentials exist
        self.client: Optional[ClobClient] = None
        self._init_clob_client()

    def _init_clob_client(self):
        pk = os.environ.get("POLYMARKET_PRIVATE_KEY")
        key_owner = os.environ.get("RELAYER_API_KEY_ADDRESS")
        pa = os.environ.get("POLYMARKET_PROXY_ADDRESS")

        if pk:
            try:
                from py_clob_client.client import ClobClient
                self.client = ClobClient(
                    CLOB_BASE,
                    key=pk,
                    chain_id=CHAIN_ID,
                    signature_type=2,
                    funder=pa,
                )
                if key_owner:
                    self.client.signer.key_owner = key_owner
                
                # Derive fresh API credentials (binds to current signer/funder setup)
                try:
                    derived = self.client.derive_api_key()
                    if derived:
                        from py_clob_client.clob_types import ApiCreds
                        self.client.set_api_creds(ApiCreds(
                            api_key=derived.api_key,
                            api_secret=derived.api_secret,
                            api_passphrase=derived.api_passphrase,
                        ))
                        logger.info("CLOB Client: signer=%s key_owner=%s derived_cred=%s", 
                                   self.client.get_address(), key_owner, derived.api_key[:20])
                    else:
                        logger.warning("Could not derive API credentials")
                except Exception as e:
                    logger.warning(f"Failed to derive credentials: {e}")
                    
            except Exception as e:
                logger.error(f"Failed to initialize CLOB Client: {e}")
        else:
            logger.warning("Private key missing - operating in observation-only mode.")

    async def sync_bankroll(self) -> float:
        """Sync live USDC balance. Falls back to env if API fails."""
        if not self.client:
            return self.bankroll
        
        pa = os.environ.get("POLYMARKET_PROXY_ADDRESS", self.client.get_address())
        url = f"https://clob.polymarket.com/balance-allowance?asset_type=COLLATERAL&address={pa}"
        
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(url)
            
            if r.status_code == 200:
                data = r.json()
                usdc = int(data.get("balance", 0)) / 1_000_000
                if usdc > 0:
                    if abs(usdc - self.bankroll) > 0.01:
                        logger.info(f"Bankroll synced: ${self.bankroll:.2f} → ${usdc:.2f} USDC")
                    self.bankroll = usdc
                    return self.bankroll
        except Exception as e:
            logger.warning(f"Could not sync bankroll: {e}")
        
        # Balance check failed or returned 0 - use configured bankroll
        configured = float(os.environ.get("AELTHARA_BANKROLL", "3.65"))
        if configured != self.bankroll:
            logger.info(f"Using configured bankroll: ${configured:.2f}")
            self.bankroll = configured
        return self.bankroll

    # ── Market Discovery ──────────────────────────────────────────────────────

    async def scan_rungs(self, city: str, icao: str = None, current_utc_hour: int = None) -> list[Rung]:
        """
        Search Gamma for temperature markets matching city, extract threshold
        in °C. Doesn't score yet, simply parses.
        """
        markets = await self._fetch_markets(city)
        rungs: list[Rung] = []

        for m in markets:
            if m.get("closed") or not m.get("acceptingOrders"):
                continue
            
            threshold = self._parse_threshold_celsius(m.get("question", ""))
            if threshold is None:
                continue

            # Detect Highest (TX) vs Lowest (TN)
            q = m.get("question", "").lower()
            if "between" in q:
                continue  # band market — Gaussian CDF is wrong model, skip until prob_band is wired in
            temp_type = "TX"
            if any(w in q for w in ["lowest", "minimum", "low of", "min temp"]):
                temp_type = "TN"

            outcome_type = self._detect_outcome_type(q)
            
            tokens = json.loads(m.get("clobTokenIds", "[]"))
            prices = json.loads(m.get("outcomePrices", "[0.5,0.5]"))
            outcomes = json.loads(m.get("outcomes", '["Yes","No"]'))
            if len(tokens) < 2 or len(prices) < 2:
                continue

            side_idx_yes = 0 if outcomes[0].lower().startswith("y") else 1
            side_idx_no = 1 - side_idx_yes
            yes_price = float(prices[side_idx_yes]) if side_idx_yes < len(prices) else 0.5
            no_price = float(prices[side_idx_no]) if side_idx_no < len(prices) else 0.5
            
            end_date_str = m.get("endDate", datetime.now(timezone.utc).isoformat())
            try:
                res_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            except ValueError:
                res_dt = datetime.now(timezone.utc)
                
            # We no longer filter out post-peak markets here if they aren't resolving today! 
            # Actually pulse.py or oracle.py should handle high_has_passed filtering based on datetimes. 
            # But we can keep it simple here and let the orchestrator filter.

            rung = Rung(
                market_id=str(m.get("id", "")),
                token_id_yes=tokens[side_idx_yes],
                token_id_no=tokens[side_idx_no],
                question=m.get("question", ""),
                threshold_celsius=threshold,
                yes_price=yes_price,
                no_price=no_price,
                outcome_type=outcome_type,
                temp_type=temp_type,
                resolution_datetime=res_dt,
            )
            rungs.append(rung)

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
            r"\blowest\b",
            r"\bminimum\b",
            r"\bmin\s+temp\b",
            r"\bat\s+or\s+below\b",
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

    def _high_has_passed(self, icao: str, current_utc_hour: int, temp_type: str = "TX") -> bool:
        """
        Check if the daily temp extreme has likely already occurred.

        TX (high): peaks 14:00-16:00 local, skip after 21:00
        TN (low): lowest around 04:00-06:00 local, skip after 10:00
        """
        from zoneinfo import ZoneInfo
        from datetime import datetime, timezone as _tz
        tz_name = _ICAO_TZ.get(icao, "UTC")
        try:
            local_now = datetime.now(_tz.utc).astimezone(ZoneInfo(tz_name))
            local_hour = local_now.hour
        except Exception:
            local_hour = current_utc_hour
        
        if temp_type == "TX":
            # High typically occurs between 14:00-16:00 local
            # If it's past 21:00 local, the high has definitely passed
            return local_hour >= 21
        else:  # TN
            # Low typically occurs 04:00-06:00 local
            # If it's past 10:00 local, the low has definitely passed
            return local_hour >= 10

    def _distribution_for_celsius(self, celsius: float) -> dict:
        """Simple conversion of threshold to a distribution for gate/model use."""
        return {"mean": celsius, "std": 2.0}

    async def _fetch_markets(self, city: str) -> list[dict]:
        """Fetch markets globally from Gamma API with rate limiting."""
        import time
        now = time.time()
        
        if not hasattr(self, "_gamma_cache"):
            self._gamma_cache = []
            self._gamma_cache_time = 0

        # Rate limit: max 1 request per 10 seconds to Gamma
        RATE_LIMIT_SECONDS = 10
        MIN_REQUEST_INTERVAL = getattr(self, "_last_gamma_request", 0) + RATE_LIMIT_SECONDS
        
        if not self._gamma_cache or (now - self._gamma_cache_time > 60 and now >= MIN_REQUEST_INTERVAL):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.get(
                        f"{GAMMA_BASE}/events",
                        params={
                            "tag_slug": "weather",
                            "active": "true",
                            "closed": "false",
                            "limit": 200,
                        },
                    )
                    r.raise_for_status()
                    self._gamma_cache = r.json()
                    self._gamma_cache_time = now
                    self._last_gamma_request = now
                    logger.info(f"Snapshot: Synchronized {len(self._gamma_cache)} global weather events from Gamma.")
            except Exception as e:
                logger.warning("Failed to fetch global markets: %s", e)
                return []

        if now - self._gamma_cache_time > 300:
            logger.info("Cache stale (>5min), forcing refresh on next cycle")
            self._gamma_cache = []  # Force refresh on next call

        filtered_markets = []
        for event in self._gamma_cache:
            if city.lower() in event.get("title", "").lower():
                for m in event.get("markets", []):
                    # Pass the end date from the parent event down into the market if needed
                    if "endDate" not in m and "endDate" in event:
                        m["endDate"] = event["endDate"]
                    filtered_markets.append(m)
                    
        return filtered_markets

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

    async def get_liquidity_cap(self, token_id: str, price: float) -> float:
        """
        Check the CLOB orderbook and return the maximum USD size we can 
        place at the current price without moving the market significantly.
        """
        if not self.client or is_dry_run():
            return 9999.0  # No cap in dry run or if client missing

        try:
            # SDK fix: use get_order_book (with underscore)
            orderbook = self.client.get_order_book(token_id)
            
            # The SDK can return a dictionary or a 'Book' object
            asks = []
            if isinstance(orderbook, dict):
                asks = orderbook.get('asks', [])
            elif hasattr(orderbook, 'asks'):
                asks = orderbook.asks
            
            if not asks:
                return 0.0
            
            # Look at top of book
            top_ask = asks[0]
            if isinstance(top_ask, dict):
                available_size = float(top_ask.get('size', 0))
            else:
                available_size = float(getattr(top_ask, 'size', 0))
                
            available_usd = available_size * price
            
            # Safety: Never take more than 20% of what's sitting at the top price
            return available_usd * 0.20
        except Exception as e:
            logger.warning(f"Liquidity check failed for {token_id}: {e}")
            return 5.0  # Fallback to very small size for safety

    async def size_for_edge(
        self,
        edge: float,
        bankroll: Optional[float] = None,
        strategy: str = "VANILLA",
        token_id: Optional[str] = None,
        price: Optional[float] = None,
        action: str = "BUY_YES",
        model_prob: float = 0.5,
        market_prob: float = 0.5,
    ) -> float:
        b = bankroll if bankroll is not None else self.bankroll

        if strategy == "HARVESTER":
            size = b * 0.10
        elif edge <= 0 and action not in ("SELL_NO", "LEAN_NO"):
            size = 0.0
        else:
            # Fractional Kelly (25%) for binary market
            if action in ("BUY_YES", "LEAN_YES") and market_prob < 1.0:
                full_kelly = (model_prob - market_prob) / (1.0 - market_prob)
            elif action in ("SELL_NO", "LEAN_NO") and market_prob > 0.0:
                full_kelly = (market_prob - model_prob) / market_prob
            else:
                full_kelly = abs(edge)
            kelly_frac = max(0.0, full_kelly) * 0.25
            size = b * kelly_frac
            size = min(size, b * 0.05)  # hard cap: 5% of bankroll per position

        # Bankroll-aware floor: never force a bet above 20% of bankroll
        floor = min(MIN_ORDER, b * 0.20)
        size = max(size, floor)
        size = min(size, b * 0.90)  # never risk more than 90% of bankroll

        if token_id and price:
            liq_cap = await self.get_liquidity_cap(token_id, price)
            if size > liq_cap:
                logger.info(f"LIQUIDITY SCALE-DOWN: ${size:.2f} → ${max(floor, liq_cap):.2f} (Cap: 20% of depth)")
                size = max(floor, liq_cap)

        return round(size, 2)

    # ── Journal ─────────────────────────────────────────────────────────────

    def _write_journal(self, decision: "GateDecision", rung: "Rung", size_usd: float, shares: float, price: float, token_id: str, dry_run: bool, order_id: str = "") -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": decision.action,
            "market_id": rung.market_id,
            "token_id": token_id,
            "question": rung.question,
            "threshold_c": rung.threshold_celsius,
            "temp_type": rung.temp_type,
            "outcome_type": rung.outcome_type,
            "entry_price": price,
            "size_usd": size_usd,
            "shares": shares,
            "edge_pct": round(decision.edge * 100, 2),
            "model_prob": decision.model_prob,
            "market_prob": decision.market_prob,
            "resolution_dt": rung.resolution_datetime.isoformat() if rung.resolution_datetime else None,
            "dry_run": dry_run,
            "order_id": order_id,
            "outcome": None,
            "pnl": None,
            "resolved_at": None,
        }
        with open(JOURNAL_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")

    # ── Execution ───────────────────────────────────────────────────────────

    async def execute(self, decision: GateDecision, rung: Rung, bankroll: Optional[float] = None, size_multiplier: float = 1.0) -> dict:
        market_label = f"{rung.market_id} ({rung.question[:30]}...)"

        if decision.action == "PASS":
            return {"action": "PASS", "market": market_label, "size": 0, "reason": decision.reason}

        # Tail filter: skip cheap YES only when edge is marginal — massive edges (>0.20) pass through
        if decision.action in ("BUY_YES", "LEAN_YES") and rung.yes_price < 0.05 and decision.edge < 0.20:
            logger.debug(f"SKIP marginal tail: yes_price={rung.yes_price:.3f}, edge={decision.edge:.3f} on {market_label}")
            return {"action": "PASS", "market": market_label, "size": 0, "reason": f"marginal tail: yes_price={rung.yes_price:.3f} edge={decision.edge:.3f}"}

        # Duplicate position guard: skip if same action already open for this market
        existing = self._open_positions.get(rung.market_id)
        if existing == decision.action:
            logger.debug(f"SKIP duplicate position: {decision.action} on {market_label}")
            return {"action": decision.action, "status": "skipped", "reason": "duplicate position"}

        effective_bankroll = bankroll if bankroll is not None else self.bankroll

        # Side mapping
        side = BUY
        token_id = rung.token_id_yes
        price = rung.yes_price

        if decision.action in ("LEAN_NO", "SELL_NO"):
            token_id = rung.token_id_no
            price = rung.no_price

        # Apply mesh coherence multiplier before sizing floor (so it actually affects size)
        adjusted_bankroll = effective_bankroll * max(size_multiplier, 0.1)

        # Fractional Kelly sizing with bankroll-aware floor
        size_usd = await self.size_for_edge(
            decision.edge, adjusted_bankroll, strategy=decision.strategy,
            token_id=token_id, price=price, action=decision.action,
            model_prob=decision.model_prob, market_prob=decision.market_prob,
        )

        # HARVEST gets full un-multiplied size
        if decision.action == "HARVEST":
            return await self._execute_harvest(decision, rung, size_usd)

        # Shares calculation
        shares = round(size_usd / price, 2) if price > 0 else 0

        if is_dry_run():
            logger.info(f"EXECUTE (is_dry_run()): {decision.action} {market_label} | shares={shares} @ ${price:.2f} | strategy={decision.strategy}")
            self._open_positions[rung.market_id] = decision.action
            self._write_journal(decision, rung, size_usd, shares, price, token_id, dry_run=True)
            return {"action": decision.action, "status": "executed", "dry_run": True, "size": size_usd}

        if not self.client:
            logger.warning(f"EXECUTE FAILED: No CLOB Client available for live trade on {market_label}")
            return {"action": decision.action, "status": "failed", "reason": "No client"}

        try:
            from py_clob_client.clob_types import OrderArgs
            resp = self.client.create_and_post_order(
                OrderArgs(
                    price=price,
                    size=shares,
                    side=side,
                    token_id=token_id,
                )
            )
            order_id = resp.get("orderID", "") if isinstance(resp, dict) else ""
            logger.info(f"LIVE ORDER PLACED: {decision.action} {market_label} | size=${size_usd} | shares={shares} | resp={resp}")
            self._open_positions[rung.market_id] = decision.action
            self._write_journal(decision, rung, size_usd, shares, price, token_id, dry_run=False, order_id=order_id)
            return {"action": decision.action, "status": "executed", "order_id": order_id, "size": size_usd}
        except Exception as e:
            logger.error(f"EXECUTE ERROR: Failed to post order for {market_label}: {e}")
            return {"action": decision.action, "status": "failed", "reason": str(e)}

    async def _execute_harvest(self, decision: GateDecision, rung: Rung, heavy_size: float) -> dict:
        """Execute the Harvester: Heavy NO + Dusting YES."""
        market_label = f"{rung.market_id} ({rung.question[:30]}...)"
        dust_size = 1.0  # Constant dusting floor
        
        # 1. The Heavy NO (Harvesting inflation)
        no_shares = round(heavy_size / rung.no_price, 2) if rung.no_price > 0 else 0
        
        # 2. The Dusting YES (Capping tail risk)
        yes_shares = round(dust_size / rung.yes_price, 2) if rung.yes_price > 0 else 0

        if is_dry_run():
            logger.info(f"EXECUTE (is_dry_run()): HARVEST {market_label} | Heavy NO=${heavy_size:.2f} ({no_shares} sh) | Dusting YES=${dust_size:.2f} ({yes_shares} sh)")
            return {"action": "HARVEST", "status": "executed", "dry_run": True}

        if not self.client:
            return {"status": "failed", "reason": "No client"}

        try:
            from py_clob_client.clob_types import OrderArgs
            # Post Heavy NO
            resp_no = self.client.create_and_post_order(OrderArgs(
                price=rung.no_price, size=no_shares, side=BUY, token_id=rung.token_id_no
            ))
            # Post Dusting YES
            resp_yes = self.client.create_and_post_order(OrderArgs(
                price=rung.yes_price, size=yes_shares, side=BUY, token_id=rung.token_id_yes
            ))
            
            logger.info(f"LIVE HARVEST EXECUTED: {market_label} | Heavy NO={resp_no.get('orderID')} | Dust YES={resp_yes.get('orderID')}")
            return {"action": "HARVEST", "status": "executed"}
        except Exception as e:
            logger.error(f"HARVEST ERROR on {market_label}: {e}")
            return {"status": "failed", "reason": str(e)}
