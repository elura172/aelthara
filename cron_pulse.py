#!/usr/bin/env python3
"""Cron-triggered pulse runner.

Usage (crontab -e):
  */15 * * * * cd /opt/aelthara && /opt/aelthara/venv/bin/python cron_pulse.py >> /var/log/aelthara-cron.log 2>&1

This runs a single market scan cycle and exits — suitable for cron.
For continuous daemon mode, use the systemd service instead.
"""

import asyncio
import logging
import signal
import sys
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import ICAO_LIST, CITY_LIST
from gate import MirrorGate, GateDecision
from executor import Executor
from oracle import Oracle
from position_shaper import is_ui_pin_opportunity, calculate_pin_short_size
from telegram_relay import (
    send_telegram_message,
    format_trade_alert,
    format_signal_alert,
    format_error_alert,
)

# Use same config as main pulse
BANKROLL_STATIC = float(os.getenv("AELTHARA_BANKROLL", "3.65"))
USE_LIVE_BANKROLL = os.getenv("MIRROR_LIVE_BANKROLL", "false").lower() == "true"
LOG_MAX_BYTES = 10_000_000


def setup_logging(name: str = "cron_pulse") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        "pulse.log",
        maxBytes=LOG_MAX_BYTES,
        backupCount=5,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


logger = setup_logging("cron_pulse")


async def run_single_cycle(oracles: dict, gate: MirrorGate, executor: Executor):
    """Run one market scan iteration."""
    await executor.sync_bankroll()
    logger.info("Cron cycle initiated...")
    
    for icao, city in zip(ICAO_LIST, CITY_LIST):
        try:
            oracle = oracles[icao]
            current_utc_hour = datetime.now(timezone.utc).hour
            
            rungs = await executor.scan_rungs(city, icao=icao, current_utc_hour=current_utc_hour)
            if not rungs:
                continue
            
            from collections import defaultdict
            rungs_by_day = defaultdict(list)
            for r in rungs:
                day = r.resolution_datetime.strftime("%Y-%m-%d")
                rungs_by_day[day].append(r)
            
            for day, day_rungs in rungs_by_day.items():
                resolution_time = day_rungs[0].resolution_datetime
                hours_left = (resolution_time - datetime.now(timezone.utc)).total_seconds() / 3600
                if hours_left <= 0:
                    continue
                
                reading = await oracle.pull(resolution_time=resolution_time)
                if not reading:
                    continue
                
                for rung in day_rungs:
                    target_temp = reading.tx_celsius
                    if rung.temp_type == "TN":
                        if reading.tn_celsius is not None:
                            target_temp = reading.tn_celsius
                        else:
                            continue
                    
                    decision = gate.assess(
                        taf_tx=target_temp,
                        market_threshold=rung.threshold_celsius,
                        market_prob=rung.yes_price,
                        outcome_type=rung.outcome_type,
                    )

                    pin_triggered = False
                    if is_ui_pin_opportunity(city, rung.threshold_celsius, rung.yes_price, decision.model_prob):
                        if decision.edge >= 0.08:
                            pin_size = calculate_pin_short_size(decision.model_prob, rung.yes_price)
                            if pin_size > 0:
                                decision.action = "SELL_NO"
                                decision.edge = abs(decision.edge)
                                pin_triggered = True
                                logger.info(f"UI PIN CONFIRMED: {icao} [{rung.threshold_celsius:.1f}°C] — shorting ${pin_size}")

                    if not pin_triggered:
                        uncertainty = 1 - abs(decision.model_prob - 0.5) * 2
                        dynamic_floor = 0.04 + (uncertainty * 0.08)
                        if abs(decision.edge) < dynamic_floor:
                            logger.debug(f"Filtered {icao} {rung.threshold_celsius:.1f}°C: below dynamic floor")
                            continue

                        model_dir = 1 if decision.model_prob > rung.yes_price else -1
                        market_dir = 1 if rung.yes_price > 0.5 else -1
                        if model_dir != market_dir:
                            logger.debug(f"Filtered {icao} {rung.threshold_celsius:.1f}°C: crossed signal")
                            continue

                    if decision.action != "PASS":
                        await send_telegram_message(
                            format_signal_alert(
                                icao=icao,
                                temp_type=rung.temp_type,
                                threshold=rung.threshold_celsius,
                                action=decision.action,
                                edge=decision.edge * 100,
                                source=reading.source,
                            )
                        )
                    
                    result = await executor.execute(decision, rung)
                    if result.get("status") == "executed":
                        mode = "DRY_RUN" if result.get("dry_run") else "LIVE"
                        logger.info(f"Trade [{mode}]: {decision.action} {icao} [{rung.threshold_celsius:.1f}°C]")
        
        except RuntimeError as e:
            if "DORMANT" in str(e):
                logger.debug(f"Dormant {icao}: {e}")
            else:
                logger.error(f"Poll runtime error {icao}: {e}")
                await send_telegram_message(format_error_alert(icao, str(e)))
        except Exception as e:
            logger.error(f"Poll error {icao}: {e}", exc_info=True)
            await send_telegram_message(format_error_alert(icao, str(e)))
        
        await asyncio.sleep(2.5)


async def main():
    oracles = {icao: Oracle(icao) for icao in ICAO_LIST}
    gate = MirrorGate()
    executor = Executor(BANKROLL_STATIC)

    logger.info(f"Cron pulse started | BANKROLL=${executor.bankroll:,.2f} [LIVE_BANKROLL={USE_LIVE_BANKROLL}]")
    
    await run_single_cycle(oracles, gate, executor)
    
    logger.info("Cron pulse cycle complete.")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    
    asyncio.run(main())