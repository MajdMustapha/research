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


def validate_env_vars() -> list[str]:
    """
    Validate that required environment variables are set.
    Returns list of error messages (empty = all good).
    """
    import os
    errors = []

    # Check trading credentials (only required if not in paper mode)
    import yaml
    settings_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config", "settings.yaml"
    )
    try:
        with open(settings_path) as f:
            settings = yaml.safe_load(f)
    except FileNotFoundError:
        errors.append("config/settings.yaml not found")
        return errors

    paper_mode = settings.get("system", {}).get("paper_trade", True)

    if not paper_mode:
        if not os.getenv("POLYMARKET_PRIVATE_KEY"):
            errors.append("POLYMARKET_PRIVATE_KEY not set (required for live trading)")
        if not os.getenv("POLYMARKET_FUNDER_ADDRESS"):
            errors.append("POLYMARKET_FUNDER_ADDRESS not set (required for live trading)")

    # Analyst uses `claude` CLI (Max subscription) — verify it's available
    # No ANTHROPIC_API_KEY needed

    if not os.getenv("TELEGRAM_BOT_TOKEN"):
        errors.append("TELEGRAM_BOT_TOKEN not set (required for notifications)")

    if not os.getenv("TELEGRAM_OPERATOR_CHAT_ID"):
        errors.append("TELEGRAM_OPERATOR_CHAT_ID not set (required for notifications)")

    return errors


def run_startup_checks() -> bool:
    """
    Run all startup checks. Returns True if safe to proceed.
    Logs all issues and blocks on critical failures.
    """
    from lib.state import log_event

    all_ok = True

    # 1. Validate environment variables
    env_errors = validate_env_vars()
    for err in env_errors:
        logger.error(f"Startup check FAILED: {err}")
        log_event("startup_error", "health", err)
        all_ok = False

    # 2. Check CLI is available
    if not check_cli_available():
        all_ok = False

    # 3. Check geoblock (only if not paper mode)
    import os, yaml
    settings_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config", "settings.yaml"
    )
    try:
        with open(settings_path) as f:
            settings = yaml.safe_load(f)
        paper_mode = settings.get("system", {}).get("paper_trade", True)
        if not paper_mode and not check_geoblock():
            all_ok = False
    except Exception as e:
        logger.error(f"Startup check error reading settings: {e}")
        all_ok = False

    if all_ok:
        logger.info("All startup checks passed")
        log_event("startup", "health", "All startup checks passed")
    else:
        logger.error("STARTUP CHECKS FAILED — review errors above")
        log_event("startup_error", "health", "One or more startup checks failed")

    return all_ok


class Heartbeat:
    """
    Simple heartbeat tracker. Each agent calls beat() periodically.
    Check stale() to detect agents that stopped responding.
    """
    def __init__(self, max_stale_seconds: int = 600):
        self._last_beat: dict[str, float] = {}
        self._max_stale = max_stale_seconds

    def beat(self, agent_name: str) -> None:
        import time
        self._last_beat[agent_name] = time.time()

    def stale_agents(self) -> list[str]:
        import time
        now = time.time()
        return [
            name for name, last in self._last_beat.items()
            if now - last > self._max_stale
        ]

    def status(self) -> dict[str, float]:
        """Returns {agent_name: seconds_since_last_beat}."""
        import time
        now = time.time()
        return {name: round(now - last, 1) for name, last in self._last_beat.items()}


# Global heartbeat instance — agents import and call heartbeat.beat("agent_name")
heartbeat = Heartbeat()
