"""
IBKR Client — connects to TWS/IB Gateway via ib_async.
Provides real-time portfolio data, positions, and P&L streaming.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Position:
    ticker: str
    name: str
    exchange: str
    shares: float
    avg_cost: float
    market_price: float
    market_value: float
    unrealized_pnl: float
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0


@dataclass
class PortfolioSnapshot:
    timestamp: datetime
    net_liquidation: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: float
    cash: float
    buying_power: float
    daily_pnl: float
    positions: list[Position] = field(default_factory=list)


class IBKRClient:
    """
    Async client for IBKR via ib_async (successor to ib_insync).

    Usage:
        client = IBKRClient(host="127.0.0.1", port=7497)
        await client.connect()
        snapshot = await client.get_portfolio()
        await client.disconnect()

    Requirements:
        - TWS or IB Gateway running with API enabled
        - Port 7497 (TWS) or 4001 (IB Gateway)
        - 127.0.0.1 added to Trusted IPs in TWS/Gateway config
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1):
        self.host = host
        self.port = port
        self.client_id = client_id
        self._ib = None
        self._connected = False

    async def connect(self) -> bool:
        try:
            from ib_async import IB
            self._ib = IB()
            await self._ib.connectAsync(
                self.host, self.port, clientId=self.client_id
            )
            self._connected = True
            logger.info(
                f"Connected to IBKR at {self.host}:{self.port} "
                f"(client_id={self.client_id})"
            )
            return True
        except ImportError:
            logger.error(
                "ib_async not installed. Install with: pip install ib_async"
            )
            return False
        except Exception as e:
            logger.error(f"Failed to connect to IBKR: {e}")
            self._connected = False
            return False

    async def disconnect(self):
        if self._ib and self._connected:
            self._ib.disconnect()
            self._connected = False
            logger.info("Disconnected from IBKR")

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ib is not None

    async def get_portfolio(self) -> Optional[PortfolioSnapshot]:
        if not self.is_connected:
            logger.warning("Not connected to IBKR")
            return None

        try:
            # Account summary
            account_values = self._ib.accountValues()
            av = {item.tag: float(item.value)
                  for item in account_values
                  if item.currency in ("USD", "BASE", "")}

            # Portfolio positions
            portfolio_items = self._ib.portfolio()
            positions = []
            for item in portfolio_items:
                contract = item.contract
                pos = Position(
                    ticker=contract.symbol,
                    name=contract.localSymbol or contract.symbol,
                    exchange=contract.exchange or contract.primaryExchange,
                    shares=item.position,
                    avg_cost=item.averageCost,
                    market_price=item.marketPrice,
                    market_value=item.marketValue,
                    unrealized_pnl=item.unrealizedPNL,
                )
                positions.append(pos)

            snapshot = PortfolioSnapshot(
                timestamp=datetime.now(),
                net_liquidation=av.get("NetLiquidation", 0),
                market_value=av.get("GrossPositionValue", 0),
                unrealized_pnl=av.get("UnrealizedPnL", 0),
                realized_pnl=av.get("RealizedPnL", 0),
                cash=av.get("TotalCashValue", 0),
                buying_power=av.get("BuyingPower", 0),
                daily_pnl=sum(p.daily_pnl for p in positions),
                positions=positions,
            )
            return snapshot

        except Exception as e:
            logger.error(f"Error fetching portfolio: {e}")
            return None

    async def get_market_data(self, symbols: list[str]) -> dict:
        """Fetch real-time quotes for a list of symbols."""
        if not self.is_connected:
            return {}

        from ib_async import Stock
        results = {}
        for symbol in symbols:
            try:
                contract = Stock(symbol, "SMART", "USD")
                self._ib.qualifyContracts(contract)
                ticker = self._ib.reqMktData(contract, "", False, False)
                await asyncio.sleep(0.5)  # Allow data to arrive
                results[symbol] = {
                    "last": ticker.last,
                    "bid": ticker.bid,
                    "ask": ticker.ask,
                    "high": ticker.high,
                    "low": ticker.low,
                    "volume": ticker.volume,
                    "close": ticker.close,
                }
                self._ib.cancelMktData(contract)
            except Exception as e:
                logger.error(f"Error fetching market data for {symbol}: {e}")

        return results

    async def get_historical_data(
        self, symbol: str, duration: str = "1 Y", bar_size: str = "1 day"
    ) -> list[dict]:
        """Fetch historical bars for technical analysis."""
        if not self.is_connected:
            return []

        from ib_async import Stock
        try:
            contract = Stock(symbol, "SMART", "USD")
            self._ib.qualifyContracts(contract)
            bars = await self._ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=True,
            )
            return [
                {
                    "date": bar.date,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }
                for bar in bars
            ]
        except Exception as e:
            logger.error(f"Error fetching historical data for {symbol}: {e}")
            return []


class IBKRMockClient:
    """
    Mock client for development/testing without a live IBKR connection.
    Uses the portfolio data provided by the user.
    """

    def __init__(self, portfolio_data: dict):
        self._data = portfolio_data
        self._connected = True

    async def connect(self) -> bool:
        self._connected = True
        return True

    async def disconnect(self):
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def get_portfolio(self) -> PortfolioSnapshot:
        summary = self._data["portfolio"]["summary"]
        positions = []
        for p in self._data["portfolio"]["positions"]:
            pos = Position(
                ticker=p["ticker"],
                name=p["name"],
                exchange=p["exchange"],
                shares=p["shares"],
                avg_cost=(p["last_price"] * p["shares"] - p["pnl"]) / p["shares"]
                if p["shares"] > 0
                else 0,
                market_price=p["last_price"],
                market_value=p["last_price"] * p["shares"],
                unrealized_pnl=p["pnl"],
                daily_pnl=p["pnl"],
                daily_pnl_pct=p["daily_change_pct"],
            )
            positions.append(pos)

        cash = self._data["portfolio"]["cash_balances"]
        return PortfolioSnapshot(
            timestamp=datetime.now(),
            net_liquidation=summary["net_liquidation_value"],
            market_value=summary["market_value"],
            unrealized_pnl=summary["unrealized_pnl"],
            realized_pnl=summary["realized_pnl"],
            cash=cash["total_cash"],
            buying_power=summary["buying_power"],
            daily_pnl=summary["daily_pnl"],
            positions=positions,
        )
