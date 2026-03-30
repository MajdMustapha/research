"""Exchange client wrapper using ccxt. Same interface regardless of exchange backend."""

import logging
import os
from dataclasses import dataclass

import ccxt
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class Order:
    id: str
    symbol: str
    side: str
    amount: float
    price: float
    status: str
    timestamp: str


class ExchangeClient:
    """
    Abstraction over ccxt exchange.
    Swap exchanges by changing config — only this file changes.
    """

    def __init__(self, config: dict):
        exchange_cfg = config["exchange"]
        exchange_cls = getattr(ccxt, exchange_cfg["name"])

        api_key = os.getenv("BINANCE_API_KEY", exchange_cfg.get("api_key", ""))
        api_secret = os.getenv("BINANCE_API_SECRET", exchange_cfg.get("api_secret", ""))

        params = {
            "enableRateLimit": True,
        }
        if api_key:
            params["apiKey"] = api_key
        if api_secret:
            params["secret"] = api_secret

        self.exchange = exchange_cls(params)

        if exchange_cfg.get("testnet", True):
            self.exchange.set_sandbox_mode(True)
            logger.info("Exchange client initialized in TESTNET mode")
        else:
            logger.warning("Exchange client initialized in LIVE mode")

    def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> pd.DataFrame:
        """Fetch OHLCV candles."""
        try:
            candles = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            if not candles:
                return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
            df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            return df
        except ccxt.BaseError as e:
            logger.error(f"Failed to fetch OHLCV for {symbol}: {e}")
            raise

    def fetch_ticker(self, symbol: str) -> dict:
        """Fetch current ticker data."""
        try:
            return self.exchange.fetch_ticker(symbol)
        except ccxt.BaseError as e:
            logger.error(f"Failed to fetch ticker for {symbol}: {e}")
            raise

    def fetch_balance(self) -> dict:
        """Fetch account balance."""
        try:
            balance = self.exchange.fetch_balance()
            return {
                "total": balance.get("total", {}),
                "free": balance.get("free", {}),
                "used": balance.get("used", {}),
            }
        except ccxt.BaseError as e:
            logger.error(f"Failed to fetch balance: {e}")
            raise

    def create_market_order(self, symbol: str, side: str, amount: float) -> Order:
        """Place a market order."""
        try:
            result = self.exchange.create_market_order(symbol, side, amount)
            order = Order(
                id=result["id"],
                symbol=result["symbol"],
                side=result["side"],
                amount=result["amount"],
                price=result.get("average") or result.get("price", 0),
                status=result["status"],
                timestamp=result["datetime"],
            )
            logger.info(f"Market order placed: {side} {amount} {symbol} @ {order.price}")
            return order
        except ccxt.BaseError as e:
            logger.error(f"Failed to place market order: {side} {amount} {symbol}: {e}")
            raise

    def create_limit_order(
        self, symbol: str, side: str, amount: float, price: float
    ) -> Order:
        """Place a limit order."""
        try:
            result = self.exchange.create_limit_order(symbol, side, amount, price)
            order = Order(
                id=result["id"],
                symbol=result["symbol"],
                side=result["side"],
                amount=result["amount"],
                price=result.get("price", price),
                status=result["status"],
                timestamp=result["datetime"],
            )
            logger.info(f"Limit order placed: {side} {amount} {symbol} @ {price}")
            return order
        except ccxt.BaseError as e:
            logger.error(f"Failed to place limit order: {side} {amount} {symbol} @ {price}: {e}")
            raise

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an open order."""
        try:
            self.exchange.cancel_order(order_id, symbol)
            logger.info(f"Order {order_id} cancelled for {symbol}")
            return True
        except ccxt.BaseError as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def fetch_order(self, order_id: str, symbol: str) -> dict:
        """Fetch order details."""
        try:
            return self.exchange.fetch_order(order_id, symbol)
        except ccxt.BaseError as e:
            logger.error(f"Failed to fetch order {order_id}: {e}")
            raise

    def fetch_open_orders(self, symbol: str = None) -> list[dict]:
        """Fetch all open orders."""
        try:
            return self.exchange.fetch_open_orders(symbol)
        except ccxt.BaseError as e:
            logger.error(f"Failed to fetch open orders: {e}")
            raise
