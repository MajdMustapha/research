#!/usr/bin/env python3
"""Send the daily brief via webhook (Slack or generic).

Usage: python skills/send_webhook.py --file workspace/{DATE}/final_brief.md
"""

import argparse
import json
import os
import sys

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skills.utils import PROJECT_ROOT

load_dotenv(PROJECT_ROOT / ".env")


def main():
    parser = argparse.ArgumentParser(description="Send brief via webhook")
    parser.add_argument("--file", required=True, help="Path to brief file")
    args = parser.parse_args()

    webhook_url = os.environ.get("WEBHOOK_URL", "").strip()
    webhook_enabled = os.environ.get("WEBHOOK_ENABLED", "false").lower() == "true"

    if not webhook_enabled:
        print("Webhook disabled (WEBHOOK_ENABLED=false). Skipping.")
        return

    if not webhook_url:
        print("ERROR: WEBHOOK_URL not set", file=sys.stderr)
        sys.exit(1)

    filepath = PROJECT_ROOT / args.file if not os.path.isabs(args.file) else args.file
    if not os.path.exists(filepath):
        print(f"ERROR: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    with open(filepath) as f:
        content = f.read()

    # Detect Slack vs generic webhook
    if "hooks.slack.com" in webhook_url:
        # Slack format
        # Truncate if over 3000 chars (Slack block limit)
        if len(content) > 3000:
            content = content[:2950] + "\n\n... (truncated)"
        payload = {
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": content}}
            ]
        }
    else:
        # Generic webhook
        payload = {"text": content, "format": "markdown"}

    try:
        resp = requests.post(webhook_url, json=payload, timeout=30)
        resp.raise_for_status()
        print(f"Webhook sent successfully (HTTP {resp.status_code})")
    except requests.RequestException as e:
        print(f"ERROR: Webhook failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
