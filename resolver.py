#!/usr/bin/env python3
"""Aelthara Resolver — update trades.jsonl with outcomes and P&L from Polymarket.

Reads trades.jsonl, queries the Polymarket positions API for any trade whose
outcome is still None, matches by token_id (asset field), and writes back
the resolved outcome + P&L.

Usage:
    source venv/bin/activate
    python resolver.py           # update journal + print summary
    python resolver.py --summary # print summary only, no API calls
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
import os

load_dotenv()

JOURNAL = Path(__file__).parent / "trades.jsonl"
DATA_API = "https://data-api.polymarket.com"
PROXY = os.environ.get("POLYMARKET_PROXY_ADDRESS", "")


def load_journal() -> list[dict]:
    if not JOURNAL.exists():
        return []
    records = []
    with open(JOURNAL) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def save_journal(records: list[dict]) -> None:
    with open(JOURNAL, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


async def fetch_positions(proxy: str) -> dict[str, dict]:
    """Return dict keyed by asset (token_id)."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{DATA_API}/positions", params={"user": proxy, "limit": 500})
        r.raise_for_status()
        return {p["asset"]: p for p in r.json()}


def compute_pnl(position: dict, action: str) -> tuple[float, str]:
    """Return (pnl_usd, outcome_str) from a resolved position."""
    cur_price = position.get("curPrice", -1)
    redeemable = position.get("redeemable", False)
    initial = position.get("initialValue", 0.0)
    current = position.get("currentValue", 0.0)
    cash_pnl = position.get("cashPnl", 0.0)
    realized = position.get("realizedPnl", 0.0)

    if redeemable:
        if cur_price >= 0.99:
            outcome = "won"
        elif cur_price == 0:
            outcome = "lost"
        else:
            outcome = "partial"
        pnl = cash_pnl + realized
    else:
        outcome = "open"
        pnl = cash_pnl + realized

    return round(pnl, 6), outcome


async def update_journal(records: list[dict], positions: dict[str, dict]) -> tuple[list[dict], int]:
    updated = 0
    for rec in records:
        if rec.get("outcome") is not None:
            continue
        if rec.get("dry_run"):
            continue
        token_id = rec.get("token_id", "")
        if token_id not in positions:
            continue
        pos = positions[token_id]
        pnl, outcome = compute_pnl(pos, rec.get("action", ""))
        if outcome == "open":
            continue
        rec["outcome"] = outcome
        rec["pnl"] = pnl
        rec["resolved_at"] = datetime.now(timezone.utc).isoformat()
        rec["avg_fill_price"] = pos.get("avgPrice")
        updated += 1
    return records, updated


def print_summary(records: list[dict]) -> None:
    live = [r for r in records if not r.get("dry_run")]
    dry = [r for r in records if r.get("dry_run")]

    resolved = [r for r in live if r.get("outcome") in ("won", "lost", "partial")]
    open_trades = [r for r in live if r.get("outcome") is None]
    won = [r for r in resolved if r.get("outcome") == "won"]
    lost = [r for r in resolved if r.get("outcome") == "lost"]

    total_pnl = sum(r.get("pnl", 0) or 0 for r in resolved)
    total_spent = sum(r.get("size_usd", 0) for r in resolved)
    win_rate = len(won) / len(resolved) * 100 if resolved else 0

    W = 60
    print("=" * W)
    print("  AELTHARA JOURNAL SUMMARY")
    print("=" * W)
    print(f"  Journal entries : {len(records)}  ({len(live)} live / {len(dry)} dry)")
    print(f"  Resolved        : {len(resolved)}  ({len(won)} won / {len(lost)} lost)")
    print(f"  Open            : {len(open_trades)}")
    print(f"  Win rate        : {win_rate:.1f}%")
    print(f"  Total spent     : ${total_spent:.4f}")
    print(f"  Net P&L         : ${total_pnl:+.4f}")
    print()

    if won:
        print(f"── WINS ({len(won)}) " + "─" * (W - 10 - len(str(len(won)))))
        for r in sorted(won, key=lambda x: x.get("pnl", 0) or 0, reverse=True):
            pnl = r.get("pnl", 0) or 0
            ts = r.get("ts", "")[:10]
            print(f"  [{ts}] +${pnl:.4f}  {r.get('question', '')[:52]}")

    if lost:
        print(f"\n── LOSSES ({len(lost)}) " + "─" * (W - 12 - len(str(len(lost)))))
        for r in sorted(lost, key=lambda x: abs(x.get("pnl", 0) or 0), reverse=True):
            pnl = r.get("pnl", 0) or 0
            ts = r.get("ts", "")[:10]
            print(f"  [{ts}] {pnl:.4f}  {r.get('question', '')[:52]}")

    if open_trades:
        print(f"\n── OPEN ({len(open_trades)}) " + "─" * (W - 10 - len(str(len(open_trades)))))
        for r in open_trades:
            ts = r.get("ts", "")[:10]
            print(f"  [{ts}] ${r.get('size_usd', 0):.4f}  {r.get('question', '')[:52]}")

    print("=" * W)


async def main():
    summary_only = "--summary" in sys.argv

    if not JOURNAL.exists():
        print("No trades.jsonl found. Run pulse.py first to generate trade records.")
        return

    records = load_journal()

    if summary_only:
        print_summary(records)
        return

    if not PROXY:
        print("ERROR: POLYMARKET_PROXY_ADDRESS not set in .env")
        return

    print(f"Fetching positions for {PROXY[:10]}…{PROXY[-6:]}…")
    positions = await fetch_positions(PROXY)
    print(f"Got {len(positions)} positions from API.")

    records, updated = await update_journal(records, positions)

    if updated > 0:
        save_journal(records)
        print(f"Updated {updated} trade(s) with outcomes.")
    else:
        print("No new resolutions found.")

    print()
    print_summary(records)


if __name__ == "__main__":
    asyncio.run(main())
