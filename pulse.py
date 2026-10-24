"""Pulse — Main orchestration loop. Oracle + MirrorGate + Executor."""

import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from typing import Optional

from config import ICAO_LIST, CITY_LIST, CITY_TO_ICAO, get_coords
from gate import MirrorGate, GateDecision
from executor import Executor
from oracle import Oracle
from position_shaper import (
    should_trade_market,
    get_market_template,
    calculate_position_sizing,
    is_ui_pin_opportunity,
    calculate_pin_short_size,
)
from mirror_field import compute_mirror_field
from mirror_field_node import MirrorFieldNode
from mirror_field_mesh import MirrorFieldMesh
from telegram_relay import (
    send_telegram_message,
    format_trade_alert,
    format_signal_alert,
    format_error_alert,
)

# ── Configuration ──────────────────────────────────────────────────────────────

import os
from dotenv import load_dotenv

load_dotenv()

BANKROLL_STATIC = float(os.getenv("AELTHARA_BANKROLL", "3.65"))
USE_LIVE_BANKROLL = os.getenv("MIRROR_LIVE_BANKROLL", "false").lower() == "true"
TAF_INTERVAL_HOURS = 3      # Refresh TAF every 3 hours
PRICE_POLL_MINUTES = 15    # Poll markets every 15 minutes
LOG_MAX_BYTES = 10_000_000   # 10MB per log file
LOG_BACKUP_COUNT = 5        # Keep 5 backup files

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

# ── City/ICAO helpers (using config.py) ────────────────────────────────────────────────────────

def normalize_market_text(text: str) -> str:
    """Normalize market text for matching."""
    s = text.upper()
    for ch in [',', '.', '(', ')', '[', ']', '{', '}', '/', '\\', '-', '_', '"', "'"]:
        s = s.replace(ch, ' ')
    return ' '.join(s.split())


def city_to_code(city: str) -> Optional[str]:
    """Convert city name to ICAO code."""
    from config import NAME_TO_CODE, CITY_TO_ICAO
    c = normalize_market_text(city)
    return CITY_TO_ICAO.get(c) or NAME_TO_CODE.get(c)


def city_to_coords(city: str):
    """Get coordinates for a city."""
    from config import get_coords
    code = city_to_code(city)
    if not code:
        return None
    return get_coords(code)


# ── Coherence Filters ──────────────────────────────────────────────────────────

MIN_HISTORY = 5
HISTORY_SPREAD_MAX = 0.05

signal_history: dict[tuple, list[float]] = defaultdict(list)


def apply_coherence_filters(decision: GateDecision, rung, city: str) -> tuple[bool, str]:
    """Require signal coherence before execution: persistence, direction, adaptive floor."""
    key = (city, rung.threshold_celsius, rung.temp_type)

    signal_history[key].append(decision.edge)
    signal_history[key] = signal_history[key][-MIN_HISTORY:]

    if len(signal_history[key]) < MIN_HISTORY:
        return False, "awaiting history"

    spread = max(signal_history[key]) - min(signal_history[key])
    if spread > HISTORY_SPREAD_MAX:
        return False, f"edge unstable (spread={spread:.3f})"

    uncertainty = 1 - abs(decision.model_prob - 0.5) * 2
    dynamic_floor = 0.04 + (uncertainty * 0.08)

    if rung.resolution_datetime:
        hours_left = (rung.resolution_datetime - datetime.now(timezone.utc)).total_seconds() / 3600
        if hours_left > 48:
            dynamic_floor *= 1.5
        elif hours_left > 24:
            dynamic_floor *= 1.2

    if abs(decision.edge) < dynamic_floor:
        return False, f"below dynamic floor ({abs(decision.edge):.3f} < {dynamic_floor:.3f})"

    edge_signs = {1 if e > 0 else -1 for e in signal_history[key] if e != 0}
    if len(edge_signs) > 1:
        return False, "crossed signal (edge direction unstable)"

    if decision.action == "PASS":
        return False, "no gate signal"

    return True, "passed"


# ── Regime / Entropy Tracker ───────────────────────────────────────────────────

import math
from collections import deque

MAX_ENTROPY_BUFFER = 20
MIN_ENTROPY_SAMPLES = 5

entropy_buffer: dict[tuple, deque[float]] = defaultdict(lambda: deque(maxlen=MAX_ENTROPY_BUFFER))


def _signal_energy(decision: GateDecision, rung) -> float:
    return abs(decision.model_prob - rung.yes_price) + abs(decision.edge)


def _compute_entropy(values: list[float]) -> float:
    if len(values) < MIN_ENTROPY_SAMPLES:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(variance)


def record_signal_energy(decision: GateDecision, rung, icao: str) -> None:
    """Append energy observation to entropy buffer after decision is made."""
    key = (icao, rung.temp_type)
    energy = _signal_energy(decision, rung)
    entropy_buffer[key].append(energy)


# ── Core components ────────────────────────────────────────────────────────────

_nodes = {icao: MirrorFieldNode(icao) for icao in ICAO_LIST}
_mesh = MirrorFieldMesh(_nodes)

import rossby as _rossby_module

oracles = {icao: Oracle(icao) for icao in ICAO_LIST}
executor = Executor(BANKROLL_STATIC)
gate = MirrorGate()


def _system_market_entropy() -> float:
    """Average market entropy across all tracked (icao, temp_type) pairs."""
    if not entropy_buffer:
        return 0.0
    total = 0.0
    count = 0
    for values in entropy_buffer.values():
        vals = list(values)
        if len(vals) >= 5:
            mean = sum(vals) / len(vals)
            variance = sum((x - mean) ** 2 for x in vals) / len(vals)
            total += math.sqrt(variance)
            count += 1
    return total / count if count > 0 else 0.0


def _market_entropy_for(icao: str) -> float:
    """Per-node market entropy: average of TX and TN buffers for this ICAO."""
    keys = [(icao, "TX"), (icao, "TN")]
    entropies = []
    for key in keys:
        if key in entropy_buffer:
            vals = list(entropy_buffer[key])
            if len(vals) >= 5:
                mean = sum(vals) / len(vals)
                variance = sum((x - mean) ** 2 for x in vals) / len(vals)
                entropies.append(math.sqrt(variance))
    return sum(entropies) / len(entropies) if entropies else 0.0


async def poll_markets():
    """Poll markets for a single iteration — called by the async loop."""
    await executor.sync_bankroll()  # Sync live USDC balance before each cycle
    logger.info("Initiating market scan cycle across all regions...")

    atmo_entropy = _rossby_module.get_system_atmo_entropy()
    model_uncertainty = 0.0

    mesh_inputs = {
        icao: {
            "atmo_entropy": atmo_entropy,
            "market_entropy": _market_entropy_for(icao),
            "model_uncertainty": model_uncertainty,
        }
        for icao in ICAO_LIST
    }
    mesh_result = _mesh.update(mesh_inputs)

    logger.debug(
        f"Mirror mesh: global_C={mesh_result['global_coherence']:.3f} "
        f"spatial_S={mesh_result['spatial_entropy']:.3f} "
        f"sync={mesh_result['synchronization']:.3f} "
        f"regime={mesh_result['regime']}"
    )

    for icao, city in zip(ICAO_LIST, CITY_LIST):
        try:
            oracle = oracles[icao]
            current_utc_hour = datetime.now(timezone.utc).hour
            
            rungs = await executor.scan_rungs(city, icao=icao, current_utc_hour=current_utc_hour)
            if not rungs:
                continue
                
            # Group by day so we only pull oracle once per day
            rungs_by_day = defaultdict(list)
            for r in rungs:
                day = r.resolution_datetime.strftime("%Y-%m-%d")
                rungs_by_day[day].append(r)
                
            for day, day_rungs in rungs_by_day.items():
                resolution_time = day_rungs[0].resolution_datetime
                
                # Skip markets that have already resolved
                hours_left = (resolution_time - datetime.now(timezone.utc)).total_seconds() / 3600
                if hours_left <= 0:
                    logger.debug(f"Skipping expired market {icao} [{day}] (resolved {abs(hours_left):.1f}h ago)")
                    continue
                
                # Skip TX/TN markets for TODAY where temp extreme has already passed
                resolution_date = resolution_time.date()
                today = datetime.now(timezone.utc).date()
                
                if resolution_date == today:
                    if day_rungs[0].temp_type == "TX" and executor._high_has_passed(icao, current_utc_hour, "TX"):
                        logger.debug(f"Skipping {icao} TX [{day}] - daily high already passed")
                        continue
                    if day_rungs[0].temp_type == "TN" and executor._high_has_passed(icao, current_utc_hour, "TN"):
                        logger.debug(f"Skipping {icao} TN [{day}] - daily low already passed")
                        continue

                reading = await oracle.pull(resolution_time=resolution_time)
                if not reading:
                    continue

                tn_str = f" TN={reading.tn_celsius:.1f}°C" if reading.tn_celsius is not None else ""
                logger.info(f"Oracle {reading.icao} TX={reading.tx_celsius:.1f}°C{tn_str} [{day}] source={reading.source} | {reading.raw}")

                # Check if this market should be traded (focused markets)
                market_date = day
                template = get_market_template(city, market_date)
                is_focused = should_trade_market(city, market_date)

                if not is_focused:
                    logger.debug(f"Skipping {icao} [{day}] - not in focused markets")
                    continue

                # Use template to get core band sizing
                tx_temp = reading.tx_celsius
                tn_temp = reading.tn_celsius

                for rung in day_rungs:
                    # Get target temp for this rung type
                    target_temp = tx_temp if rung.temp_type == "TX" else tn_temp
                    if target_temp is None:
                        logger.warning(f"No target temp for {icao} {day} {rung.temp_type}")
                        continue
                    
                    # Check if this specific bin should be traded
                    # Get gate decision
                    decision = gate.assess(
                        taf_tx=target_temp,
                        market_threshold=rung.threshold_celsius,
                        market_prob=rung.yes_price,
                        outcome_type=rung.outcome_type,
                    )
                    
                    # UI Pin check: confirmed distortion only
                    pin_triggered = False
                    if is_ui_pin_opportunity(city, rung.threshold_celsius, rung.yes_price, decision.model_prob):
                        if decision.edge >= 0.08:
                            pin_size = calculate_pin_short_size(decision.model_prob, rung.yes_price)
                            if pin_size > 0:
                                decision.action = "SELL_NO"
                                decision.edge = abs(decision.edge)
                                pin_triggered = True
                                logger.info(f"UI PIN CONFIRMED: {icao} [{rung.threshold_celsius:.1f}°C] {rung.yes_price*100:.1f}% vs {decision.model_prob*100:.1f}% — shorting ${pin_size}")

                    if not pin_triggered:
                        passes, filter_reason = apply_coherence_filters(decision, rung, city)
                        if not passes:
                            logger.debug(f"Filtered {icao} {rung.threshold_celsius:.1f}°C: {filter_reason}")
                            continue

                    size_multiplier = mesh_result["global_coherence"]  # 0-1 based on system coherence
                    
                    # Use template-based sizing for focused markets
                    if is_focused and template:
                        # Adjust size based on template
                        is_in_core = template.core_band_celsius[0] <= rung.threshold_celsius <= template.core_band_celsius[1]
                        if is_in_core:
                            # Core band - full size
                            pass  # Use gate's sizing
                        else:
                            # Not in core - may reduce size
                            if abs(rung.threshold_celsius - target_temp) > 5:
                                # Far tail - reduce significantly
                                decision.edge *= 0.5
                    
                    type_label = "High" if rung.temp_type == "TX" else "Low"
                    logger.info(f"Signal {icao} {type_label} [{rung.threshold_celsius:.1f}°C]: {decision.action} | {decision.reason} | Edge: {decision.edge*100:.1f}% | mesh_result={mesh_result['regime']}")
                    if decision.action != "PASS":
                        await send_telegram_message(
                            format_signal_alert(
                                icao=icao,
                                temp_type=type_label,
                                threshold=rung.threshold_celsius,
                                action=decision.action,
                                edge=decision.edge * 100,
                                source=reading.source,
                            )
                        )

                    result = await executor.execute(decision, rung, size_multiplier=size_multiplier)
                    if result.get("status") == "executed":
                        mode = "DRY_RUN" if result.get("dry_run") else "LIVE"
                        logger.info(f"Trade executed [{mode}]: {decision.action} {icao} {type_label} [{rung.threshold_celsius:.1f}°C] edge={decision.edge*100:.1f}% mesh_result={mesh_result['regime']}")
                        record_signal_energy(decision, rung, icao)
                        trade_size = result.get("size", 0)
                        trade_price = rung.yes_price if decision.action in ("BUY_YES", "LEAN_YES") else rung.no_price
                        await send_telegram_message(
                            format_trade_alert(
                                action=decision.action,
                                market=rung.question[:60],
                                size=trade_size,
                                price=trade_price,
                                edge=decision.edge * 100,
                                dry_run=result.get("dry_run", False),
                            )
                        )

        except RuntimeError as e:
            if "DORMANT" in str(e):
                logger.info(f"Dormant {icao}: {e}")
            else:
                logger.error(f"Poll runtime error {icao}: {e}", exc_info=True)
                await send_telegram_message(format_error_alert(icao, str(e)))
        except Exception as e:
            logger.error(f"Poll error {icao}: {e}", exc_info=True)
            await send_telegram_message(format_error_alert(icao, str(e)))

        await asyncio.sleep(2.5)


async def refresh_taf():
    """Refresh TAF data for all airports."""
    for icao, city in zip(ICAO_LIST, CITY_LIST):
        try:
            oracle = oracles[icao]
            reading = await oracle.pull()
            if reading:
                if oracle.is_new(reading):
                    logger.info(f"NEW {reading.icao} TX={reading.tx_celsius:.0f}°C source={reading.source} issued={reading.issued_at}")
                    if reading.source == "taf":
                        tn_str = f" TN={reading.tn_celsius:.0f}°C" if reading.tn_celsius is not None else ""
                        await send_telegram_message(
                            f"*Aelthara Oracle Update*\n{reading.icao}{tn_str} = {reading.tx_celsius:.0f}°C\nSource: {reading.source}"
                        )
                else:
                    logger.debug(f"Cached {reading.icao} TX={reading.tx_celsius:.0f}°C source={reading.source}")
        except RuntimeError as e:
            if "DORMANT" in str(e):
                logger.info(f"Dormant {icao}: {e}")
            else:
                logger.error(f"Oracle runtime error {icao}: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Oracle error {icao}: {e}", exc_info=True)


# ── Main Loop ─────────────────────────────────────────────────────────────────

async def main():
    bankroll = executor.bankroll
    mode_tag = "LIVE" if USE_LIVE_BANKROLL else "CONFIG"
    logger.info(f"Pulse started | BANKROLL=${bankroll:,.2f} [{mode_tag}]")
    logger.info(f"Running | TAF refresh every {TAF_INTERVAL_HOURS}h | Poll every {PRICE_POLL_MINUTES}min | LIVE_BANKROLL={USE_LIVE_BANKROLL}")

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
