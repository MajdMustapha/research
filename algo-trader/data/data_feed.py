"""Live data feed handler with REST polling fallback."""

import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

import pandas as pd

from data.historical_loader import HistoricalLoader

logger = logging.getLogger(__name__)


class DataFeed:
    """
    Live data feed using REST polling.
    Polls exchange at regular intervals and calls callback with new candle data.
    Sufficient for 1h timeframe.
    """

    def __init__(self, config: dict, loader: HistoricalLoader):
        self.config = config
        self.loader = loader
        self.symbols = config["trading"]["symbols"]
        self.timeframe = config["trading"]["timeframe"]
        self._running = False
        self._thread: threading.Thread | None = None
        self._callbacks: list[Callable] = []
        self._buffers: dict[str, pd.DataFrame] = defaultdict(pd.DataFrame)
        self._poll_interval = 55  # seconds, slightly before 1h candle close

    def add_callback(self, callback: Callable[[str, pd.DataFrame], None]):
        """Register a callback for new candle data. Called with (symbol, df)."""
        self._callbacks.append(callback)

    def start(self):
        """Start polling in a background thread."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("DataFeed started")

    def stop(self):
        """Stop polling."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("DataFeed stopped")

    def get_latest(self, symbol: str, limit: int = 100) -> pd.DataFrame:
        """Get the latest candles from the buffer."""
        if symbol in self._buffers and not self._buffers[symbol].empty:
            return self._buffers[symbol].tail(limit).reset_index(drop=True)
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    def _poll_loop(self):
        """Main polling loop."""
        # Initial fetch
        for symbol in self.symbols:
            try:
                df = self.loader.fetch_ohlcv(symbol, self.timeframe, limit=100)
                self._buffers[symbol] = df
                logger.info(f"Initial fetch: {len(df)} candles for {symbol}")
            except Exception as e:
                logger.error(f"Initial fetch failed for {symbol}: {e}")

        while self._running:
            for symbol in self.symbols:
                try:
                    df = self.loader.fetch_ohlcv(symbol, self.timeframe, limit=5)
                    if not df.empty:
                        self._update_buffer(symbol, df)
                        for callback in self._callbacks:
                            try:
                                callback(symbol, self._buffers[symbol])
                            except Exception as e:
                                logger.error(f"Callback error for {symbol}: {e}")
                except Exception as e:
                    logger.error(f"Poll failed for {symbol}: {e}")

            time.sleep(self._poll_interval)

    def _update_buffer(self, symbol: str, new_data: pd.DataFrame):
        """Merge new candles into the buffer, keeping the last 500 candles."""
        if self._buffers[symbol].empty:
            self._buffers[symbol] = new_data
        else:
            combined = pd.concat([self._buffers[symbol], new_data])
            combined = combined.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
            self._buffers[symbol] = combined.tail(500).reset_index(drop=True)
