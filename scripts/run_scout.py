#!/usr/bin/env python3
"""
Run the Scout agent.
Usage:
  python scripts/run_scout.py --domain crypto
  python scripts/run_scout.py --domain politics --skip-calibration
  python scripts/run_scout.py --drift-check
  python scripts/run_scout.py --domain sports --top-n 200
"""

import argparse
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.scout.calibration import run_calibration_session
from agents.scout.leaderboard import mine_leaderboard
from agents.scout.wallet_filter import filter_wallets
from agents.scout.reporter import generate_wallets_yaml
from agents.scout.drift_monitor import run_drift_check

VALID_DOMAINS = ["crypto", "politics", "sports", "macro", "geopolitics"]


def main():
    parser = argparse.ArgumentParser(description="Polymarket Scout Agent")
    parser.add_argument(
        "--domain", choices=VALID_DOMAINS, help="Domain to scout"
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=100,
        help="Leaderboard candidates to check (default: 100)",
    )
    parser.add_argument(
        "--skip-calibration",
        action="store_true",
        help="Skip operator calibration",
    )
    parser.add_argument(
        "--drift-check",
        action="store_true",
        help="Run monthly drift check on active basket",
    )
    args = parser.parse_args()

    if args.drift_check:
        run_drift_check()
        return

    if not args.domain:
        parser.error("--domain is required unless --drift-check is set")

    print(f"\n{'='*50}")
    print(f"  POLYMARKET SCOUT — {args.domain.upper()}")
    print(f"{'='*50}\n")

    # Step 1: Calibrate operator
    calibration = {"has_edge": True, "verdict": "Calibration skipped"}
    if not args.skip_calibration:
        calibration = run_calibration_session(args.domain, num_markets=20)
        if not calibration.get("has_edge"):
            print("\nCalibration failed. Wallet mining skipped.")
            print(
                "   Try: study the domain more, or run --domain with a different category."
            )
            return

    print("\nCalibration passed. Mining leaderboard for domain wallets...")

    # Step 2: Mine leaderboard
    candidates = mine_leaderboard(
        period="month",
        top_n=args.top_n,
        domain_filter=args.domain,
    )
    print(f"Found {len(candidates)} {args.domain} candidates on leaderboard.")

    # Step 3: Apply quality filter
    print(f"\nApplying quality filter ({len(candidates)} candidates)...")
    approved, rejected = filter_wallets(candidates)
    print(f"Result: {len(approved)} approved, {len(rejected)} rejected\n")

    if not approved:
        print("No wallets passed the quality filter.")
        print("   Try --top-n 200 to expand the candidate pool.")
        return

    # Step 4: Write wallets.yaml
    generate_wallets_yaml(approved, args.domain, calibration)

    print("\n=== SCOUT COMPLETE ===")
    print(f"Domain:           {args.domain}")
    print(
        f"Operator edge:    {'CONFIRMED' if calibration.get('has_edge') else 'NOT CONFIRMED'}"
    )
    print(f"Wallets approved: {len(approved)}")
    print(f"wallets.yaml:     Updated")
    print(f"\nNext: review config/wallets.yaml, then start the main system.")


if __name__ == "__main__":
    main()
