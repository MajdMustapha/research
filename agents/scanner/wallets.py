"""
Wallet basket activity poller.
Polls tracked wallets every 5 minutes, checks for consensus signals.
"""

import os
from collections import Counter

import yaml

from lib.cli import get_wallet_trades, PolymarketCLIError
from lib.state import (
    save_wallet_activity,
    get_wallet_activity,
    is_signal_on_cooldown,
    set_signal_cooldown,
    is_tx_hash_seen,
    log_event,
)
from lib.logger import get_logger

logger = get_logger(__name__)


def _load_settings() -> dict:
    settings_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "config",
        "settings.yaml",
    )
    with open(settings_path) as f:
        return yaml.safe_load(f)


def _load_wallets() -> dict:
    wallets_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "config",
        "wallets.yaml",
    )
    with open(wallets_path) as f:
        config = yaml.safe_load(f)
    return config.get("wallets") or {}


def get_wallets_by_domain(domain: str) -> list[dict]:
    """Get all wallets for a specific domain."""
    wallets = _load_wallets()
    return wallets.get(domain, [])


def poll_wallet_trades() -> list[dict]:
    """
    Poll all tracked wallets for new trades.
    Stores new trades in wallet_activity table.
    Returns list of new trades observed.
    """
    wallets_config = _load_wallets()
    new_trades = []

    for domain, wallets in wallets_config.items():
        if not wallets:
            continue

        for wallet in wallets:
            address = wallet.get("address")
            if not address:
                continue

            min_trade_size = wallet.get("min_trade_size", 50)

            try:
                trades = get_wallet_trades(address, limit=20)
                if not trades:
                    continue

                for trade in trades:
                    tx_hash = trade.get("transactionHash") or trade.get("id") or ""
                    if not tx_hash or is_tx_hash_seen(tx_hash):
                        continue

                    size_usdc = float(trade.get("size", 0))
                    if size_usdc < min_trade_size:
                        continue

                    market_id = (
                        trade.get("market", {}).get("id")
                        or trade.get("conditionId")
                        or ""
                    )
                    if not market_id:
                        logger.warning(f"Skipping trade with empty market_id: tx={tx_hash[:16]}")
                        continue

                    token_id = trade.get("tokenId") or trade.get("assetId") or ""
                    side = trade.get("side") or trade.get("outcomeIndex", "")

                    activity = {
                        "wallet_address": address,
                        "wallet_domain": domain,
                        "market_id": market_id,
                        "token_id": token_id,
                        "side": str(side),
                        "price": float(trade.get("price", 0)),
                        "size_usdc": size_usdc,
                        "tx_hash": tx_hash,
                    }
                    save_wallet_activity(activity)
                    new_trades.append(activity)

            except PolymarketCLIError as e:
                logger.warning(
                    f"Failed to poll wallet {wallet.get('label', address[:10])}: {e}"
                )

    if new_trades:
        logger.info(f"Observed {len(new_trades)} new wallet trades")

    return new_trades


def check_basket_consensus(
    domain: str, lookback_hours: int = 48
) -> list[dict]:
    """
    Returns list of market signals where >=70% of domain basket agrees.
    """
    settings = _load_settings()
    basket_settings = settings.get("wallet_basket", {})
    threshold_pct = basket_settings.get("consensus_threshold_pct", 0.70)
    min_wallets = basket_settings.get("consensus_min_wallets", 3)
    cooldown_hours = basket_settings.get("signal_cooldown_hours", 24)

    trades = get_wallet_activity(domain=domain, since_hours=lookback_hours)
    wallets_in_domain = get_wallets_by_domain(domain)
    total_wallets = len(wallets_in_domain)

    if total_wallets == 0:
        return []

    # Group by (market_id, side) — count unique wallets, not trade count
    market_side_wallets: dict[tuple, set] = {}
    for trade in trades:
        key = (trade["market_id"], trade["side"])
        if key not in market_side_wallets:
            market_side_wallets[key] = set()
        market_side_wallets[key].add(trade["wallet_address"])

    signals = []
    for (market_id, side), wallet_set in market_side_wallets.items():
        count = len(wallet_set)
        consensus_pct = count / total_wallets

        if count >= min_wallets and consensus_pct >= threshold_pct:
            # Check cooldown
            if is_signal_on_cooldown(market_id, cooldown_hours):
                logger.info(
                    f"Wallet consensus for {market_id} on cooldown — skipping"
                )
                continue

            set_signal_cooldown(market_id)
            signal = {
                "source": "wallet_basket",
                "domain": domain,
                "market_id": market_id,
                "side": side,
                "wallet_count": count,
                "wallet_total": total_wallets,
                "consensus_pct": round(consensus_pct, 3),
            }
            signals.append(signal)
            logger.info(
                f"Wallet consensus signal: {domain}/{market_id} "
                f"side={side} ({count}/{total_wallets} = {consensus_pct:.0%})"
            )

    return signals


def check_all_baskets() -> list[dict]:
    """Check consensus across all configured domains."""
    settings = _load_settings()
    lookback = settings.get("wallet_basket", {}).get("lookback_hours", 48)

    wallets_config = _load_wallets()
    all_signals = []

    for domain in wallets_config:
        signals = check_basket_consensus(domain, lookback)
        all_signals.extend(signals)

    return all_signals
