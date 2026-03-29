"""
Analyst agent entry point.
This agent is called by the Scanner — it does not run independently.
It stays alive to maintain the Anthropic client connection.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.state import init_db, log_event
from lib.logger import get_logger

logger = get_logger("analyst")


async def main():
    init_db()
    log_event("startup", "analyst", "Analyst agent started")
    logger.info("Analyst agent started — waiting for signals from scanner")

    # The analyst is called directly by the scanner agent.
    # This process stays alive for supervisord health monitoring.
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
