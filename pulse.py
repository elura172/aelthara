"""Pulse — Main orchestration loop. Oracle + MirrorGate + Executor."""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from typing import Optional

from rich.console import Console
from rich.style import Style

from gate import MirrorGate, GateDecision
from executor import Executor
from market_scraper import MarketScraper
from oracle import Oracle

# ── Configuration ──────────────────────────────────────────────────────────────

BANKROLL = 100000.0          # $100,000 starting capital
TAF_INTERVAL_HOURS = 6      # Refresh TAF every 6 hours
PRICE_POLL_MINUTES = 15      # Poll markets every 15 minutes
LOG_MAX_BYTES = 10_000_000   # 10MB per log file
LOG_BACKUP_COUNT = 5        # Keep 5 backup files

console = Console()

# ── Logging Setup ──────────────────────────────────────────────────────────────

def setup_logging(name: str = "pulse") -> logging.Logger:
    """Configure rotating file logging + console output."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # Avoid duplicate handlers on re-import
    if logger.handlers:
        return logger
    
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    
    # File handler: rotate on size
    file_handler = RotatingFileHandler(
        "pulse.log",
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    
    # Console handler: info and above
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging("pulse")

# ── Airport Data ───────────────────────────────────────────────────────────────

ICAO_LIST = [
    "KJFK", "KLAX", "KORD", "KMIA", "KATL", "KSEA", "KDEN",
    "EGLL", "LFPG", "EDDF", "EHAM", "RJTT", "RKSS", "WSSS",
    "VHHH", "ZBAA", "ZSPD", "YSSY", "NZAA", "SBGR", "SAEZ",
    "KDFW", "KIAH", "KPHX", "KLAS",
    "LEMD", "LIRF", "EDDM", "EIDW",
    "VTBS", "VABB", "VIDP", "RCTP",
]

CITY_LIST = [
    "NEW YORK", "LOS ANGELES", "CHICAGO", "MIAMI", "ATLANTA", "SEATTLE", "DENVER",
    "LONDON", "PARIS", "FRANKFURT", "AMSTERDAM", "TOKYO", "SEOUL", "SINGAPORE",
    "HONG KONG", "BEIJING", "SHANGHAI", "SYDNEY", "AUCKLAND", "SAO PAULO", "BUENOS AIRES",
    "DALLAS", "HOUSTON", "PHOENIX", "LAS VEGAS",
    "MADRID", "ROME", "MUNICH", "DUBLIN",
    "BANGKOK", "MUMBAI", "DELHI", "TAIPEI",
]

LOCAL_COORDS = {
    "KJFK": (40.6413, -73.7781), "KLAX": (33.9416, -118.4085), "KORD": (41.9742, -87.9073),
    "KMIA": (25.7959, -80.2870), "KATL": (33.6407, -84.4277), "KSEA": (47.4502, -122.3088),
    "KDEN": (39.7017, -104.7516), "EGLL": (51.4700, -0.4543), "LFPG": (49.0097, 2.5479),
    "EDDF": (50.0379, 8.5622), "EHAM": (52.3105, 4.7683), "RJTT": (35.7720, 140.3929),
    "RKSS": (37.4602, 126.4407), "WSSS": (1.3644, 103.9915), "VHHH": (22.3020, 114.1742),
    "ZBAA": (40.0801, 116.5970), "ZSPD": (31.1434, 121.8050), "YSSY": (-33.9461, 151.1772),
    "NZAA": (-37.0082, 174.7917), "SBGR": (-23.4356, -46.4731), "SAEZ": (-34.8222, -58.5358),
    "KDFW": (32.8471, -96.8518), "KIAH": (29.9902, -95.3368), "KPHX": (33.4373, -112.0078),
    "KLAS": (36.0840, -115.1537), "LEMD": (40.4983, -3.5676), "LIRF": (41.8003, 12.2389),
    "EDDM": (48.3538, 11.7861), "EIDW": (53.4264, -6.2499), "VTBS": (13.6900, 100.7501),
    "VABB": (19.0896, 72.8656), "VIDP": (28.5562, 77.1000), "RCTP": (25.0797, 121.2342),
}

NAME_TO_CODE = {
    "TOKYO": "RJTT", "CHICAGO": "KORD", "NEW YORK": "KJFK", "NYC": "KJFK",
    "TORONTO": "CYYZ", "LONDON": "EGLL", "MIAMI": "KMIA", "ATLANTA": "KATL",
    "SEATTLE": "KSEA", "DENVER": "KDEN", "LOS ANGELES": "KLAX", "VANCOUVER": "CYVR",
    "EDMONTON": "CYEG", "MONTREAL": "CYUL", "PARIS": "LFPG", "FRANKFURT": "EDDF",
    "AMSTERDAM": "EHAM", "BARCELONA": "LEBL", "ROME": "LIRF", "SEOUL": "RKSS",
    "SINGAPORE": "WSSS", "DUBAI": "OMDB", "AUCKLAND": "NZAA", "SYDNEY": "YSSY",
    "HONOLULU": "PHNL", "JAKARTA": "WIII", "SHENZHEN": "ZGSZ", "BEIJING": "ZBAA",
    "HONG KONG": "VHHH", "SHANGHAI": "ZSPD", "HOUSTON": "KIAH", "MEXICO CITY": "MMMX",
    "KUALA LUMPUR": "WMKK", "TEL AVIV": "LLBG", "SAN FRANCISCO": "KSFO",
    "DALLAS": "KDFW", "TAIPEI": "RCTP", "ANKARA": "LTAC", "WELLINGTON": "NZWN",
    "MUNICH": "EDDM", "MILAN": "LIMC", "MADRID": "LEMD", "WARSAW": "EPWA",
    "AUSTIN": "KAUS", "PHOENIX": "KPHX", "SAO PAULO": "SBGR", "BUENOS AIRES": "SAEZ",
    "BOSTON": "KBOS", "PHILADELPHIA": "KPHL", "SAN DIEGO": "KSAN", "LAS VEGAS": "KLAS",
    "CALGARY": "CYYC", "OTTAWA": "CYOW", "DUBLIN": "EIDW", "LISBON": "LPPT",
    "COPENHAGEN": "EKCH", "STOCKHOLM": "ESSA", "OSLO": "ENGM", "HELSINKI": "EFHK",
    "PRAGUE": "LKPR", "VIENNA": "LOWW", "ZURICH": "LSZH", "BRUSSELS": "EBBR",
    "MALAGA": "LEMG", "VALENCIA": "LEVC", "SEVILLE": "LEZL", "BILBAO": "LEBB",
    "GLASGOW": "EGPF", "MANCHESTER": "EGCC", "EDINBURGH": "EGPH", "MUMBAI": "VABB",
    "DELHI": "VIDP", "BANGKOK": "VTBS", "OSAKA": "RJBB", "NAGOYA": "RJGG",
    "KYOTO": "RJOT", "FUKUOKA": "RJFF", "PERTH": "YPPH", "BRISBANE": "YBBN",
    "MELBOURNE": "YMML", "ADELAIDE": "YPAD", "CHRISTCHURCH": "NZCH",
    "FORT WORTH": "KDFW", "DFW": "KDFW", "VEGAS": "KLAS", "ROMA": "LIRF",
    "MUNCHEN": "EDDM", "BOMBAY": "VABB", "NEW DELHI": "VIDP",
}

CITY_OVERRIDES = {
    "NEW YORK": "KJFK", "LONDON": "EGLL", "MIAMI": "KMIA", "ATLANTA": "KATL",
    "DENVER": "KDEN", "LOS ANGELES": "KLAX", "TAIPEI": "RCTP", "SEOUL": "RKSS",
    "JAKARTA": "WIII", "WASHINGTON": "KDCA", "BOSTON": "KBOS",
    "PHILADELPHIA": "KPHL", "SAN DIEGO": "KSAN", "LAS VEGAS": "KLAS",
    "MUMBAI": "VABB", "DELHI": "VIDP", "OSAKA": "RJBB", "NAGOYA": "RJGG",
    "KYOTO": "RJOT", "CHRISTCHURCH": "NZCH",
}

CODE_TO_NAME = {v: k for k, v in NAME_TO_CODE.items()}


def normalize_market_text(text: str) -> str:
    """Normalize market text for matching."""
    s = text.upper()
    for ch in [',', '.', '(', ')', '[', ']', '{', '}', '/', '\\', '-', '_', '"', "'"]:
        s = s.replace(ch, ' ')
    return ' '.join(s.split())


def city_to_code(city: str) -> Optional[str]:
    """Convert city name to ICAO code."""
    c = normalize_market_text(city)
    return CITY_OVERRIDES.get(c) or NAME_TO_CODE.get(c)


def city_to_coords(city: str):
    """Get coordinates for a city."""
    code = city_to_code(city)
    if not code:
        return None
    return LOCAL_COORDS.get(code)


def resolve_market_city(comment_text: str) -> Optional[str]:
    """Resolve city from market comment text."""
    s = normalize_market_text(comment_text)
    if "NEW YORK" in s: return "NEW YORK"
    if "WASHINGTON" in s: return "WASHINGTON"
    if "LOS ANGELES" in s: return "LOS ANGELES"
    if "SAN FRANCISCO" in s: return "SAN FRANCISCO"
    if "HONG KONG" in s: return "HONG KONG"
    if "MEXICO CITY" in s: return "MEXICO CITY"
    if "KUALA LUMPUR" in s: return "KUALA LUMPUR"
    if "SAO PAULO" in s: return "SAO PAULO"
    if "BUENOS AIRES" in s: return "BUENOS AIRES"
    if "QUEBEC CITY" in s: return "QUEBEC CITY"
    if "BOSTON" in s: return "BOSTON"
    if "PHILADELPHIA" in s: return "PHILADELPHIA"
    if "SAN DIEGO" in s: return "SAN DIEGO"
    if "DALLAS" in s or "FORT WORTH" in s or "DFW" in s: return "DALLAS"
    if "HOUSTON" in s: return "HOUSTON"
    if "PHOENIX" in s: return "PHOENIX"
    if "LAS VEGAS" in s: return "LAS VEGAS"
    if "VEGAS" in s: return "LAS VEGAS"
    if "CALGARY" in s: return "CALGARY"
    if "OTTAWA" in s: return "OTTAWA"
    if "DUBLIN" in s: return "DUBLIN"
    if "LISBON" in s: return "LISBON"
    if "MADRID" in s: return "MADRID"
    if "ROME" in s or "ROMA" in s: return "ROME"
    if "MUNICH" in s: return "MUNICH"
    if "MUNCHEN" in s: return "MUNICH"
    if "STOCKHOLM" in s: return "STOCKHOLM"
    if "OSLO" in s: return "OSLO"
    if "HELSINKI" in s: return "HELSINKI"
    if "PRAGUE" in s: return "PRAGUE"
    if "VIENNA" in s: return "VIENNA"
    if "ZURICH" in s: return "ZURICH"
    if "BRUSSELS" in s: return "BRUSSELS"
    if "MALAGA" in s: return "MALAGA"
    if "VALENCIA" in s: return "VALENCIA"
    if "SEVILLE" in s: return "SEVILLE"
    if "BILBAO" in s: return "BILBAO"
    if "GLASGOW" in s: return "GLASGOW"
    if "MANCHESTER" in s: return "MANCHESTER"
    if "EDINBURGH" in s: return "EDINBURGH"
    if "MUMBAI" in s or "BOMBAY" in s: return "MUMBAI"
    if "DELHI" in s or "NEW DELHI" in s: return "DELHI"
    if "BANGKOK" in s: return "BANGKOK"
    if "TAIPEI" in s: return "TAIPEI"
    if "OSAKA" in s: return "OSAKA"
    if "NAGOYA" in s: return "NAGOYA"
    if "KYOTO" in s: return "KYOTO"
    if "FUKUOKA" in s: return "FUKUOKA"
    if "PERTH" in s: return "PERTH"
    if "BRISBANE" in s: return "BRISBANE"
    if "MELBOURNE" in s: return "MELBOURNE"
    if "ADELAIDE" in s: return "ADELAIDE"
    if "CHRISTCHURCH" in s: return "CHRISTCHURCH"
    return None


def resolve_market_city_name(text: str) -> str:
    """Resolve market city name from text."""
    return resolve_market_city(text) or "UNKNOWN"


def resolve_market_icao(comment_text: str) -> Optional[str]:
    """Resolve ICAO code from market comment text."""
    city = resolve_market_city(comment_text)
    if not city:
        return None
    return city_to_code(city)


def resolve_market_coords(comment_text: str):
    """Resolve coordinates from market comment text."""
    city = resolve_market_city(comment_text)
    if not city:
        return None
    return city_to_coords(city)


# ── Core components ────────────────────────────────────────────────────────────

# Create persistent Oracle instances to maintain cache across iterations
oracles = {icao: Oracle(icao) for icao in ICAO_LIST}
gate = MirrorGate()
executor = Executor(BANKROLL)
market_scraper = MarketScraper()


# ── Main Loop Functions ────────────────────────────────────────────────────────

async def poll_markets():
    """Poll markets for a single iteration — called by the async loop."""
    for icao, city in zip(ICAO_LIST, CITY_LIST):
        try:
            # Use persistent oracle (maintains _last_issued cache)
            oracle = oracles[icao]
            reading = oracle.pull()
            if not reading:
                continue

            logger.info(f"Oracle {reading.icao} TX={reading.tx_celsius:.0f}°C source={reading.source} issued={reading.issued_at}")

            # Get current UTC hour for time-based filtering
            current_utc_hour = datetime.now(timezone.utc).hour

            # Scan rungs and execute — pass actual TAF tx, not rung mean
            for rung in executor.scan_rungs(city, reading.tx_celsius, icao=icao, current_utc_hour=current_utc_hour):
                # Pass actual TAF tx to gate instead of rung distribution mean
                decision = gate.assess(
                    taf_tx=reading.tx_celsius,  # Actual TAF temperature
                    market_threshold=rung.threshold_celsius,
                    market_prob=rung.market_price,
                )
                result = executor.execute(decision, f"{icao}_{rung.market_id}", BANKROLL)
                if result.get("status") == "executed":
                    logger.info(f"Trade executed: {decision} {result.get('trade', {}).get('quantity', 0):.2f} @ ${result.get('trade', {}).get('price', 0):.2f}")

            # Market-side: fetch and match
            markets = await market_scraper.fetch_markets(keywords=f"{city} temperature", limit=50)
            for m in markets:
                if not market_scraper.match_to_city(m, city):  # Use city name, not ICAO
                    continue
                lage = market_scraper.lag_edge(m, reading.tx_celsius)
                if lage > 0.02:
                    logger.warning(f"Market {m.city} lag=0.020 mid=0.300")

        except Exception as e:
            logger.error(f"Poll error {icao}: {e}", exc_info=True)


async def refresh_taf():
    """Refresh TAF data for all airports."""
    for icao, city in zip(ICAO_LIST, CITY_LIST):
        try:
            oracle = oracles[icao]
            reading = oracle.pull()
            if reading:
                if oracle.is_new(reading):
                    logger.info(f"NEW {reading.icao} TX={reading.tx_celsius:.0f}°C source={reading.source} issued={reading.issued_at}")
                else:
                    logger.debug(f"Cached {reading.icao} TX={reading.tx_celsius:.0f}°C source={reading.source}")
        except Exception as e:
            logger.error(f"Oracle error {icao}: {e}", exc_info=True)


# ── Main Loop ─────────────────────────────────────────────────────────────────

async def main():
    logger.info(f"Pulse started | BANKROLL=${BANKROLL:,.0f}")
    logger.info(f"Running | TAF refresh every {TAF_INTERVAL_HOURS}h | Poll every {PRICE_POLL_MINUTES}min")

    taf_interval = TAF_INTERVAL_HOURS * 3600  # seconds
    poll_interval = PRICE_POLL_MINUTES * 60   # seconds

    last_taf = 0
    last_poll = 0

    while True:
        now = time.time()

        # TAF refresh on schedule
        if now - last_taf >= taf_interval:
            await refresh_taf()
            last_taf = now

        # Market poll on schedule
        if now - last_poll >= poll_interval:
            await poll_markets()
            last_poll = now

        # Sleep small interval to avoid busy-waiting
        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Pulse stopped.")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
