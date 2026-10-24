import os
import textwrap
import logging
import sys
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _get_telegram_config() -> tuple[Optional[str], Optional[str]]:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        logger.warning(f"Telegram config missing: token={'set' if bot_token else 'missing'}, chat_id={'set' if chat_id else 'missing'}")
        return None, None
    return bot_token, chat_id


async def send_telegram_message(text: str, parse_mode: str = "Markdown") -> bool:
    """Send a message to Telegram. Returns True on success."""
    bot_token, chat_id = _get_telegram_config()
    if not bot_token or not chat_id:
        logger.debug("Telegram not configured; skipping alert")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": int(chat_id),
        "text": textwrap.shorten(text, width=4000, placeholder="…"),
        "parse_mode": parse_mode,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
        if not resp.is_success:
            logger.warning("Telegram send failed: %s %s", resp.status_code, resp.text)
            return False
        return True
    except Exception as e:
        logger.warning("Telegram send exception: %s", e)
        return False


def format_trade_alert(action: str, market: str, size: float, price: float, edge: float, dry_run: bool) -> str:
    mode = "DRY-RUN" if dry_run else "LIVE"
    edge_str = f"{edge:+.2f}¢" if edge else ""
    return f"*{mode}* {action}\n{market}\n${size:.2f} @ {price:.2f} {edge_str}"


def format_signal_alert(icao: str, temp_type: str, threshold: float, action: str, edge: float, source: str) -> str:
    return (
        f"*Aelthara Signal*\n"
        f"{icao} {temp_type} [{threshold:.0f}°C]\n"
        f"Action: {action}\n"
        f"Edge: {edge:+.1f}¢\n"
        f"Source: {source}"
    )


def format_error_alert(icao: str, error: str) -> str:
    return f"*Aelthara Error*\n{icao}: {error}"