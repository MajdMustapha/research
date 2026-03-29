"""
Health checks for system startup.
Run geoblock check before any trading activity.
"""

from lib.logger import get_logger
from lib.state import log_event

logger = get_logger(__name__)


def check_geoblock() -> bool:
    """
    Run at orchestrator startup. Returns True if trading is available.
    """
    from lib.cli import _run, PolymarketCLIError

    try:
        result = _run(["clob", "geoblock"])
        blocked = result.get("blocked", True) if result else True
        if blocked:
            logger.error(f"Geoblock active: {result}")
            log_event("orchestrator", "startup_blocked", f"Geoblock active: {result}")
            return False
        logger.info("Geoblock check passed — trading available")
        return True
    except PolymarketCLIError as e:
        logger.error(f"Geoblock check failed: {e}")
        log_event("orchestrator", "geoblock_check_failed", str(e))
        return False


def check_cli_available() -> bool:
    """Verify polymarket-cli is installed and reachable."""
    import subprocess

    try:
        result = subprocess.run(
            ["polymarket", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            logger.info(f"polymarket-cli: {result.stdout.strip()}")
            return True
        logger.error(f"polymarket-cli error: {result.stderr}")
        return False
    except FileNotFoundError:
        logger.error("polymarket-cli not found in PATH")
        return False
    except subprocess.TimeoutExpired:
        logger.error("polymarket-cli version check timed out")
        return False
