"""Executor agent entry point — runs fill monitor loop."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.state import init_db, log_event
from lib.logger import get_logger
from lib.health import run_startup_checks, heartbeat
from agents.executor.monitor import monitor_fills

logger = get_logger("executor")


async def main():
    init_db()

    if not run_startup_checks():
        logger.error("Startup checks failed — executor not starting")
        return

    log_event("startup", "executor", "Executor agent started")
    logger.info("Executor agent started — monitoring fills")

    await monitor_fills(poll_interval=30)


if __name__ == "__main__":
    asyncio.run(main())
