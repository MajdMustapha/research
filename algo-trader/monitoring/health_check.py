"""Health check watchdog for the trading bot."""

import logging
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class HealthCheck:
    """
    Background watchdog that monitors:
    - Main loop heartbeat (detects hangs)
    - Exchange connectivity
    """

    def __init__(self, config: dict, alerter=None):
        self.interval = config.get("monitoring", {}).get("health_check_interval", 300)
        self.alerter = alerter
        self._last_heartbeat: datetime | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._exchange_client = None

    def set_exchange_client(self, client):
        """Set exchange client for connectivity checks."""
        self._exchange_client = client

    def heartbeat(self):
        """Called by the main loop to signal it's alive."""
        self._last_heartbeat = datetime.now(timezone.utc)

    def start(self):
        """Start the health check thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._check_loop, daemon=True)
        self._thread.start()
        logger.info("Health check started")

    def stop(self):
        """Stop the health check thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Health check stopped")

    def _check_loop(self):
        while self._running:
            try:
                self._run_checks()
            except Exception as e:
                logger.error(f"Health check error: {e}")
            time.sleep(self.interval)

    def _run_checks(self):
        # Check heartbeat
        if self._last_heartbeat is not None:
            elapsed = (datetime.now(timezone.utc) - self._last_heartbeat).total_seconds()
            if elapsed > self.interval * 2:
                msg = f"Main loop heartbeat missed: last seen {elapsed:.0f}s ago"
                logger.error(msg)
                if self.alerter:
                    self.alerter.send_alert(msg, level="ERROR")

        # Check exchange connectivity
        if self._exchange_client:
            try:
                self._exchange_client.exchange.fetch_time()
                logger.debug("Exchange connectivity: OK")
            except Exception as e:
                msg = f"Exchange connectivity failed: {e}"
                logger.error(msg)
                if self.alerter:
                    self.alerter.send_alert(msg, level="ERROR")
