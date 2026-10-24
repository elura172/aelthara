#!/usr/bin/env python3
"""Aelthara P&L Audit — pull all positions from Polymarket data API and report.

Usage:
    source venv/bin/activate
    python audit_pnl.py
"""

import asyncio
from collections import defaultdict
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
import os

load_dotenv()

DATA_API = "https://data-api.polymarket.com"
PROXY = os.environ.get("POLYMARKET_PROXY_ADDRESS", "")


async def fetch_positions(proxy: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{DATA_API}/positions", params={"user": proxy, "limit": 500})
        r.raise_for_status()
        return r.json()


async def fetch_activity(proxy: str, limit: int = 500) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{DATA_API}/activity", params={"user": proxy, "limit": limit})
        r.raise_for_status()
        return r.json()


def classify(p: dict) -> str:
    redeemable = p.get("redeemable", False)
    cur_price = p.get("curPrice", -1)
    mergeable = p.get("mergeable", False)
    if mergeable:
        return "mergeable"
    if redeemable and cur_price == 0:
        return "lost"
    if redeemable and cur_price >= 0.99:
        return "won"
    if not redeemable:
        return "open"
    return "partial"


def extract_city(title: str) -> str:
    """Best-effort city extraction from Polymarket market title."""
    title = title.lower()
    for keyword in ["in ", "for "]:
        idx = title.find(keyword)
        if idx != -1:
            rest = title[idx + len(keyword):]
            city_end = rest.find(" be ")
            if city_end == -1:
                city_end = rest.find(" on ")
            if city_end > 0:
                return rest[:city_end].strip().title()
    return "Unknown"


async def main():
    if not PROXY:
        print("ERROR: POLYMARKET_PROXY_ADDRESS not set in .env")
        return

    print(f"Fetching positions for {PROXY[:10]}…{PROXY[-6:]}…")
    positions = await fetch_positions(PROXY)
    activity = await fetch_activity(PROXY)

    # ── Position-level P&L ────────────────────────────────────────────────────

    won, lost, open_pos, other = [], [], [], []
    total_spent = 0.0
    total_current = 0.0
    total_realized = 0.0
    total_unrealized_pnl = 0.0

    city_pnl: dict[str, float] = defaultdict(float)
    city_spent: dict[str, float] = defaultdict(float)

    for p in positions:
        status = classify(p)
        initial = p.get("initialValue", 0.0)
        current = p.get("currentValue", 0.0)
        cash_pnl = p.get("cashPnl", 0.0)
        realized = p.get("realizedPnl", 0.0)
        title = p.get("title", "?")
        end_date = p.get("endDate", "?")
        avg_price = p.get("avgPrice", 0.0)
        size = p.get("size", 0.0)
        outcome = p.get("outcome", "?")

        total_spent += initial
        total_current += current
        total_realized += realized
        total_unrealized_pnl += cash_pnl

        city = extract_city(title)
        city_pnl[city] += cash_pnl + realized
        city_spent[city] += initial

        row = (title, end_date, initial, current, cash_pnl, realized, avg_price, size, outcome)
        if status == "won":
            won.append(row)
        elif status == "lost":
            lost.append(row)
        elif status == "open":
            open_pos.append(row)
        else:
            other.append(row)

    net_pnl = total_unrealized_pnl + total_realized
    win_rate = len(won) / (len(won) + len(lost)) * 100 if (won or lost) else 0.0

    # ── Activity summary ──────────────────────────────────────────────────────

    buy_count = sum(1 for a in activity if a.get("side") == "BUY")
    sell_count = sum(1 for a in activity if a.get("side") == "SELL")
    total_usdc_in = sum(a.get("usdcSize", 0) for a in activity if a.get("side") == "BUY")

    oldest_ts = min((a["timestamp"] for a in activity), default=0)
    newest_ts = max((a["timestamp"] for a in activity), default=0)
    oldest_dt = datetime.fromtimestamp(oldest_ts, tz=timezone.utc).strftime("%Y-%m-%d") if oldest_ts else "?"
    newest_dt = datetime.fromtimestamp(newest_ts, tz=timezone.utc).strftime("%Y-%m-%d") if newest_ts else "?"

    # ── Print Report ──────────────────────────────────────────────────────────

    W = 70
    print("=" * W)
    print("  AELTHARA  —  P&L AUDIT")
    print("=" * W)
    print(f"  Period          : {oldest_dt} → {newest_dt}")
    print(f"  Activity        : {len(activity)} fills  ({buy_count} buys / {sell_count} sells)")
    print(f"  USDC deployed   : ${total_usdc_in:.4f}")
    print()
    print(f"  Positions       : {len(positions)} total")
    print(f"  Won             : {len(won)}")
    print(f"  Lost            : {len(lost)}")
    print(f"  Open            : {len(open_pos)}")
    if other:
        print(f"  Other           : {len(other)}")
    print(f"  Win rate        : {win_rate:.1f}%  (resolved only)")
    print()
    print(f"  Total spent     : ${total_spent:.4f}")
    print(f"  Current value   : ${total_current:.4f}")
    print(f"  Unrealized P&L  : ${total_unrealized_pnl:+.4f}")
    print(f"  Realized P&L    : ${total_realized:+.4f}")
    print(f"  ── NET P&L      : ${net_pnl:+.4f}")
    print()

    # ── By City ───────────────────────────────────────────────────────────────

    if city_pnl:
        print("── BY CITY " + "─" * (W - 11))
        sorted_cities = sorted(city_pnl.items(), key=lambda x: x[1])
        for city, pnl in sorted_cities:
            spent = city_spent[city]
            bar = "█" * max(0, int(abs(pnl) * 20 / max(abs(v) for v in city_pnl.values()))) if city_pnl else ""
            sign = "+" if pnl >= 0 else ""
            print(f"  {city:<20}  {sign}{pnl:.4f}  (spent ${spent:.4f})  {bar}")
        print()

    # ── Wins ─────────────────────────────────────────────────────────────────

    if won:
        print(f"── WINS ({len(won)}) " + "─" * (W - 10 - len(str(len(won)))))
        for title, end_date, initial, current, cash_pnl, realized, avg_price, size, outcome in sorted(won, key=lambda x: x[4], reverse=True):
            net = cash_pnl + realized
            print(f"  [{end_date}] ${initial:.4f} → +${net:.4f}  {title[:60]}")

    # ── Losses ────────────────────────────────────────────────────────────────

    if lost:
        print(f"\n── LOSSES ({len(lost)}) " + "─" * (W - 12 - len(str(len(lost)))))
        for title, end_date, initial, current, cash_pnl, realized, avg_price, size, outcome in sorted(lost, key=lambda x: x[2], reverse=True):
            print(f"  [{end_date}] -${initial:.4f}  {title[:60]}")

    # ── Open ──────────────────────────────────────────────────────────────────

    if open_pos:
        print(f"\n── OPEN ({len(open_pos)}) " + "─" * (W - 10 - len(str(len(open_pos)))))
        for title, end_date, initial, current, cash_pnl, realized, avg_price, size, outcome in open_pos:
            pct = (cash_pnl / initial * 100) if initial else 0
            print(f"  [{end_date}] ${initial:.4f} → ${current:.4f}  ({pct:+.1f}%)  {title[:52]}")

    print("\n" + "=" * W)


if __name__ == "__main__":
    asyncio.run(main())
