"""
Takes approved wallets + calibration results.
Writes wallets.yaml and sends Telegram summary.
"""

import os
from datetime import date

import yaml

from lib.logger import get_logger

logger = get_logger(__name__)


def generate_wallets_yaml(
    approved_wallets: list[dict],
    domain: str,
    calibration_report: dict,
    output_path: str = None,
) -> None:
    """
    Write approved wallets to wallets.yaml, ranked by score.
    Score = win_rate * gain_loss_ratio (composite quality metric).
    """
    if output_path is None:
        output_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "config",
            "wallets.yaml",
        )

    # Score and rank
    for w in approved_wallets:
        w["composite_score"] = round(
            w.get("win_rate", 0) * w.get("gain_loss_ratio", 0), 4
        )

    ranked = sorted(
        approved_wallets, key=lambda x: x.get("composite_score", 0), reverse=True
    )

    # Format for wallets.yaml
    wallet_entries = []
    for w in ranked[:10]:  # Top 10 per domain
        entry = {
            "address": w["address"],
            "label": f"{domain}_wallet_{w.get('leaderboard_rank', '?')}",
            "win_rate": w.get("win_rate", 0),
            "gain_loss_ratio": w.get("gain_loss_ratio", 0),
            "composite_score": w.get("composite_score", 0),
            "settled_trades": w.get("settled_trades", 0),
            "min_trade_size": 100,
            "added_date": str(date.today()),
        }
        # Flag geopolitics wallets with fee advantage
        if domain == "geopolitics":
            entry["fee_advantage"] = True
        wallet_entries.append(entry)

    # Load existing yaml and merge
    try:
        with open(output_path) as f:
            existing = yaml.safe_load(f) or {"wallets": {}}
    except FileNotFoundError:
        existing = {"wallets": {}}

    if not isinstance(existing.get("wallets"), dict):
        existing["wallets"] = {}

    existing["wallets"][domain] = wallet_entries

    with open(output_path, "w") as f:
        yaml.dump(existing, f, default_flow_style=False, sort_keys=False)

    print(
        f"\nwallets.yaml updated: {len(wallet_entries)} wallets "
        f"added to domain '{domain}'"
    )
    if ranked:
        print(
            f"   Top wallet: {ranked[0]['address'][:10]}... "
            f"(score: {ranked[0].get('composite_score', 0):.3f})"
        )
