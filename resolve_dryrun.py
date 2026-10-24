#!/usr/bin/env python3
"""Aelthara Dry-Run Resolver — close dry-run journal entries by querying Gamma for market outcomes.

Unlike resolver.py (which hits the positions API and only sees live wallet positions),
this script queries Gamma directly for the settled YES/NO of each market. Works for
dry-run trades that never touched the wallet.

Usage:
    source venv/bin/activate
    python resolve_dryrun.py           # resolve dry runs + print summary
    python resolve_dryrun.py --summary # show dry-run summary only
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

JOURNAL = Path(__file__).parent / "trades.jsonl"
GAMMA_BASE = "https://gamma-api.polymarket.com"


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


async def fetch_market(client: httpx.AsyncClient, market_id: str) -> dict | None:
    try:
        r = await client.get(f"{GAMMA_BASE}/markets/{market_id}")
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  WARN: could not fetch market {market_id}: {e}")
        return None


def resolve_outcome(market: dict, action: str) -> tuple[str, float] | None:
    """
    Determine outcome and P&L from a resolved Gamma market.
    Returns (outcome_str, pnl_usd) or None if market not yet resolved.

    Gamma marks resolution by: closed=True + outcomePrices having a 1.0 winner.
    """
    if not market.get("closed"):
        return None

    prices_raw = market.get("outcomePrices", "[0.5,0.5]")
    outcomes_raw = market.get("outcomes", '["Yes","No"]')
    try:
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
    except (json.JSONDecodeError, TypeError):
        return None

    prices = [float(p) for p in prices]
    outcomes = [o.lower() for o in outcomes]

    # Find winning side — price rounds to 1.0 on winner
    yes_idx = next((i for i, o in enumerate(outcomes) if o.startswith("y")), 0)
    no_idx = 1 - yes_idx

    yes_resolved = prices[yes_idx] >= 0.99 if yes_idx < len(prices) else False
    no_resolved = prices[no_idx] >= 0.99 if no_idx < len(prices) else False

    if not yes_resolved and not no_resolved:
        return None  # ambiguous / still resolving

    market_resolved_yes = yes_resolved

    # Map action to what we bet on
    bet_yes = action in ("BUY_YES", "LEAN_YES")
    bet_no = action in ("SELL_NO", "LEAN_NO")

    if bet_yes:
        won = market_resolved_yes
    elif bet_no:
        won = not market_resolved_yes
    else:
        # HARVEST — composite, treat as special
        return "harvest_resolved", 0.0

    return ("won" if won else "lost"), None  # P&L computed from journal fields below


def compute_pnl(record: dict, outcome: str) -> float:
    size_usd = record.get("size_usd", 0.0)
    entry_price = record.get("entry_price", 0.5)
    shares = record.get("shares") or (size_usd / entry_price if entry_price > 0 else 0)

    if outcome == "won":
        return round(shares - size_usd, 6)  # payout at $1/share minus cost
    elif outcome == "lost":
        return round(-size_usd, 6)
    return 0.0


async def update_dry_runs(records: list[dict]) -> tuple[list[dict], int]:
    pending = [
        r for r in records
        if r.get("dry_run") and r.get("outcome") is None
    ]

    if not pending:
        return records, 0

    print(f"Checking {len(pending)} unresolved dry-run trade(s) against Gamma…")

    updated = 0
    async with httpx.AsyncClient(timeout=20) as client:
        for rec in pending:
            market_id = rec.get("market_id", "")
            if not market_id:
                continue

            market = await fetch_market(client, market_id)
            if market is None:
                continue

            result = resolve_outcome(market, rec.get("action", ""))
            if result is None:
                continue

            outcome, _ = result
            if outcome == "harvest_resolved":
                rec["outcome"] = "harvest_resolved"
                rec["pnl"] = 0.0
            else:
                rec["outcome"] = outcome
                rec["pnl"] = compute_pnl(rec, outcome)

            rec["resolved_at"] = datetime.now(timezone.utc).isoformat()
            rec["resolved_price_yes"] = _get_yes_price(market)
            updated += 1
            status = "WON" if outcome == "won" else "LOST"
            print(f"  {status}  {rec.get('question', '')[:60]}  pnl=${rec['pnl']:+.4f}")

    return records, updated


def _get_yes_price(market: dict) -> float:
    prices_raw = market.get("outcomePrices", "[0.5,0.5]")
    outcomes_raw = market.get("outcomes", '["Yes","No"]')
    try:
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        yes_idx = next((i for i, o in enumerate(outcomes) if o.lower().startswith("y")), 0)
        return float(prices[yes_idx]) if yes_idx < len(prices) else 0.5
    except Exception:
        return 0.5


def print_summary(records: list[dict]) -> None:
    dry = [r for r in records if r.get("dry_run")]
    if not dry:
        print("No dry-run trades in journal.")
        return

    resolved = [r for r in dry if r.get("outcome") in ("won", "lost")]
    open_trades = [r for r in dry if r.get("outcome") is None]
    won = [r for r in resolved if r.get("outcome") == "won"]
    lost = [r for r in resolved if r.get("outcome") == "lost"]

    total_pnl = sum(r.get("pnl", 0) or 0 for r in resolved)
    total_spent = sum(r.get("size_usd", 0) for r in resolved)
    win_rate = len(won) / len(resolved) * 100 if resolved else 0

    W = 62
    print("=" * W)
    print("  DRY-RUN PERFORMANCE")
    print("=" * W)
    print(f"  Total dry trades  : {len(dry)}")
    print(f"  Resolved          : {len(resolved)}  ({len(won)} won / {len(lost)} lost)")
    print(f"  Open              : {len(open_trades)}")
    print(f"  Win rate          : {win_rate:.1f}%")
    print(f"  Total spent       : ${total_spent:.4f}  (paper)")
    print(f"  Net P&L           : ${total_pnl:+.4f}  (paper)")
    print()

    # Calibration table: group by model_prob decile
    buckets: dict[str, list] = {}
    for r in resolved:
        mp = r.get("model_prob", 0) or 0
        bucket = f"{int(mp * 10) * 10:02d}-{int(mp * 10) * 10 + 10:02d}%"
        buckets.setdefault(bucket, []).append(r)

    if buckets:
        print("── CALIBRATION (model_prob vs actual win rate) " + "─" * (W - 47))
        for bucket in sorted(buckets):
            rs = buckets[bucket]
            actual_wr = sum(1 for r in rs if r.get("outcome") == "won") / len(rs) * 100
            avg_edge = sum(r.get("edge_pct", 0) or 0 for r in rs) / len(rs)
            print(f"  model {bucket}  →  actual {actual_wr:5.1f}%  (n={len(rs):2d}, avg edge={avg_edge:+.1f}%)")
        print()

    # Edge tier breakdown
    massive = [r for r in resolved if (r.get("edge_pct") or 0) >= 20]
    strong  = [r for r in resolved if 15 <= (r.get("edge_pct") or 0) < 20]
    normal  = [r for r in resolved if  8 <= (r.get("edge_pct") or 0) < 15]

    if massive or strong or normal:
        print("── EDGE TIER OUTCOMES " + "─" * (W - 22))
        for label, group in [("massive (≥20%)", massive), ("strong (15-20%)", strong), ("normal (8-15%)", normal)]:
            if not group:
                continue
            wr = sum(1 for r in group if r.get("outcome") == "won") / len(group) * 100
            pnl = sum(r.get("pnl", 0) or 0 for r in group)
            print(f"  {label:<18}  n={len(group):2d}  win={wr:5.1f}%  pnl=${pnl:+.4f}")
        print()

    if won:
        print(f"── WINS ({len(won)}) " + "─" * (W - 10 - len(str(len(won)))))
        for r in sorted(won, key=lambda x: x.get("pnl", 0) or 0, reverse=True):
            pnl = r.get("pnl", 0) or 0
            ep = r.get("edge_pct", 0) or 0
            ts = r.get("ts", "")[:10]
            print(f"  [{ts}] +${pnl:.4f}  edge={ep:+.1f}%  {r.get('question', '')[:48]}")

    if lost:
        print(f"\n── LOSSES ({len(lost)}) " + "─" * (W - 12 - len(str(len(lost)))))
        for r in sorted(lost, key=lambda x: abs(x.get("pnl", 0) or 0), reverse=True):
            pnl = r.get("pnl", 0) or 0
            ep = r.get("edge_pct", 0) or 0
            ts = r.get("ts", "")[:10]
            print(f"  [{ts}] {pnl:.4f}  edge={ep:+.1f}%  {r.get('question', '')[:48]}")

    if open_trades:
        print(f"\n── OPEN ({len(open_trades)}) " + "─" * (W - 10 - len(str(len(open_trades)))))
        for r in open_trades:
            ts = r.get("ts", "")[:10]
            ep = r.get("edge_pct", 0) or 0
            print(f"  [{ts}] edge={ep:+.1f}%  {r.get('question', '')[:50]}")

    print("=" * W)


async def main():
    summary_only = "--summary" in sys.argv

    if not JOURNAL.exists():
        print("No trades.jsonl found. Run pulse.py first.")
        return

    records = load_journal()

    if summary_only:
        print_summary(records)
        return

    records, updated = await update_dry_runs(records)

    if updated > 0:
        save_journal(records)
        print(f"\nResolved {updated} dry-run trade(s).")
    else:
        print("No new dry-run resolutions found.")

    print()
    print_summary(records)


if __name__ == "__main__":
    asyncio.run(main())
