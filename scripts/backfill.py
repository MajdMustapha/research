#!/usr/bin/env python3
"""
Historical wallet data loader.
Backfills wallet_activity table for tracked wallets.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from lib.cli import get_wallet_trades, PolymarketCLIError
from lib.state import init_db, save_wallet_activity, log_event
from lib.logger import get_logger

logger = get_logger("backfill")


def backfill_wallets(limit_per_wallet: int = 200):
    """Load historical trades for all wallets in wallets.yaml."""
    wallets_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "wallets.yaml"
    )
    with open(wallets_path) as f:
        config = yaml.safe_load(f)

    wallets_config = config.get("wallets") or {}
    total_loaded = 0

    for domain, wallets in wallets_config.items():
        if not wallets:
            continue

        print(f"\nBackfilling domain: {domain} ({len(wallets)} wallets)")

        for wallet in wallets:
            address = wallet.get("address")
            label = wallet.get("label", address[:10])
            if not address:
                continue

            print(f"  Loading {label}...", end=" ")

            try:
                trades = get_wallet_trades(address, limit=limit_per_wallet)
                if not trades:
                    print("no trades found")
                    continue

                count = 0
                for trade in trades:
                    market_id = (
                        trade.get("market", {}).get("id")
                        or trade.get("conditionId")
                        or ""
                    )
                    if not market_id:
                        continue

                    save_wallet_activity({
                        "wallet_address": address,
                        "wallet_domain": domain,
                        "market_id": market_id,
                        "token_id": trade.get("tokenId") or trade.get("assetId") or "",
                        "side": str(trade.get("side") or trade.get("outcomeIndex", "")),
                        "price": float(trade.get("price", 0)),
                        "size_usdc": float(trade.get("size", 0)),
                        "tx_hash": trade.get("transactionHash") or trade.get("id") or "",
                    })
                    count += 1

                total_loaded += count
                print(f"{count} trades loaded")
                time.sleep(0.5)  # Rate limit

            except PolymarketCLIError as e:
                print(f"ERROR: {e}")
            except Exception as e:
                print(f"ERROR: {e}")

    print(f"\nBackfill complete: {total_loaded} total trades loaded")
    log_event("backfill", "system", f"Loaded {total_loaded} historical trades")


if __name__ == "__main__":
    init_db()
    backfill_wallets()
