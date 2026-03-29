"""
Monthly basket health check.
Re-validates active wallets in wallets.yaml.
Flags wallets that have drifted below quality threshold.
"""

import os

import yaml

from agents.scout.wallet_filter import filter_wallets
from lib.logger import get_logger

logger = get_logger(__name__)


def run_drift_check(
    wallets_yaml_path: str = None,
) -> dict:
    """Check all active basket wallets. Return health report."""
    if wallets_yaml_path is None:
        wallets_yaml_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "config",
            "wallets.yaml",
        )

    with open(wallets_yaml_path) as f:
        config = yaml.safe_load(f)

    wallets_config = config.get("wallets") or {}
    report = {"domains": {}, "degraded_wallets": [], "action_required": False}

    for domain, wallets in wallets_config.items():
        if not wallets:
            continue

        candidates = [
            {"address": w["address"], "leaderboard_rank": 999} for w in wallets
        ]
        approved, rejected = filter_wallets(candidates)

        degraded = [
            r.get("address", "unknown") for r in rejected
        ]
        if degraded:
            report["degraded_wallets"].extend(degraded)
            report["action_required"] = True

        report["domains"][domain] = {
            "total_wallets": len(wallets),
            "still_healthy": len(approved),
            "degraded": len(rejected),
            "degraded_addresses": degraded,
        }

    if report["action_required"]:
        print(
            "\nDRIFT ALERT: Some basket wallets no longer meet quality criteria."
        )
        for domain, data in report["domains"].items():
            if data["degraded"] > 0:
                print(f"  {domain}: {data['degraded']} degraded wallet(s)")
                for addr in data["degraded_addresses"]:
                    print(f"    - {addr[:16]}...")
        print(
            "\nRun `python scripts/run_scout.py --domain <domain>` to find replacements."
        )
    else:
        print("All basket wallets are healthy.")

    return report
