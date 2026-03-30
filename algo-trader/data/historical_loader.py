"""Historical OHLCV data loader with local CSV caching."""

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import pandas as pd

logger = logging.getLogger(__name__)


class HistoricalLoader:
    """Fetches and caches historical OHLCV data via ccxt."""

    def __init__(self, config: dict):
        self.config = config
        exchange_cfg = config["exchange"]
        exchange_cls = getattr(ccxt, exchange_cfg["name"])

        params = {}
        api_key = os.getenv("BINANCE_API_KEY", exchange_cfg.get("api_key", ""))
        api_secret = os.getenv("BINANCE_API_SECRET", exchange_cfg.get("api_secret", ""))
        if api_key:
            params["apiKey"] = api_key
        if api_secret:
            params["secret"] = api_secret

        self.exchange = exchange_cls(params)

        if exchange_cfg.get("testnet", True):
            self.exchange.set_sandbox_mode(True)

        self.cache_dir = Path(__file__).parent / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: datetime | None = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """Fetch OHLCV candles from exchange with pagination."""
        since_ms = int(since.timestamp() * 1000) if since else None
        all_candles = []

        while True:
            candles = self.exchange.fetch_ohlcv(
                symbol, timeframe, since=since_ms, limit=limit
            )
            if not candles:
                break

            all_candles.extend(candles)
            logger.info(f"Fetched {len(candles)} candles for {symbol}, total: {len(all_candles)}")

            if len(candles) < limit:
                break

            # Move since to after the last candle
            since_ms = candles[-1][0] + 1
            time.sleep(self.exchange.rateLimit / 1000)

        if not all_candles:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        return self._to_dataframe(all_candles)

    def load_or_fetch(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Load from CSV cache if available, fetch missing data from exchange."""
        cache_file = self._cache_path(symbol, timeframe)

        cached_df = None
        if cache_file.exists():
            cached_df = pd.read_csv(cache_file, parse_dates=["timestamp"])
            cached_df["timestamp"] = pd.to_datetime(cached_df["timestamp"], utc=True)
            logger.info(f"Loaded {len(cached_df)} cached candles for {symbol}")

        # Determine what we need to fetch
        if cached_df is not None and not cached_df.empty:
            cached_end = cached_df["timestamp"].max()
            if cached_end >= pd.Timestamp(end, tz=timezone.utc):
                # Cache covers requested range
                mask = (cached_df["timestamp"] >= pd.Timestamp(start, tz=timezone.utc)) & (
                    cached_df["timestamp"] <= pd.Timestamp(end, tz=timezone.utc)
                )
                return cached_df[mask].reset_index(drop=True)

            # Fetch from end of cache
            fetch_start = cached_end.to_pydatetime()
        else:
            cached_df = pd.DataFrame()
            fetch_start = start

        # Fetch new data
        new_df = self.fetch_ohlcv(symbol, timeframe, since=fetch_start)

        if not new_df.empty:
            combined = pd.concat([cached_df, new_df]).drop_duplicates(
                subset=["timestamp"]
            ).sort_values("timestamp").reset_index(drop=True)

            # Save to cache
            combined.to_csv(cache_file, index=False)
            logger.info(f"Cached {len(combined)} candles for {symbol}")
        else:
            combined = cached_df

        # Filter to requested range
        if not combined.empty:
            mask = (combined["timestamp"] >= pd.Timestamp(start, tz=timezone.utc)) & (
                combined["timestamp"] <= pd.Timestamp(end, tz=timezone.utc)
            )
            return combined[mask].reset_index(drop=True)

        return combined

    def _cache_path(self, symbol: str, timeframe: str) -> Path:
        safe_symbol = symbol.replace("/", "_")
        return self.cache_dir / f"{safe_symbol}_{timeframe}.csv"

    @staticmethod
    def _to_dataframe(candles: list) -> pd.DataFrame:
        df = pd.DataFrame(
            candles, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        return df
