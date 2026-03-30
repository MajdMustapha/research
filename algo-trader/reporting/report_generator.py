"""HTML weekly performance report generator."""

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Template

from reporting.trade_logger import TradeLogger

logger = logging.getLogger(__name__)

REPORT_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Algo Trader Weekly Report - {{ start_date }} to {{ end_date }}</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }
        .container { max-width: 900px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; }
        h1 { color: #333; border-bottom: 2px solid #4CAF50; padding-bottom: 10px; }
        h2 { color: #555; margin-top: 30px; }
        table { width: 100%; border-collapse: collapse; margin: 15px 0; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }
        th { background-color: #4CAF50; color: white; }
        tr:hover { background-color: #f5f5f5; }
        .metric { display: inline-block; margin: 10px 20px 10px 0; padding: 15px; background: #f9f9f9; border-radius: 5px; min-width: 150px; }
        .metric-label { font-size: 12px; color: #888; text-transform: uppercase; }
        .metric-value { font-size: 24px; font-weight: bold; color: #333; }
        .positive { color: #4CAF50; }
        .negative { color: #f44336; }
    </style>
</head>
<body>
<div class="container">
    <h1>Weekly Performance Report</h1>
    <p>{{ start_date }} to {{ end_date }}</p>

    <div>
        <div class="metric">
            <div class="metric-label">Total Trades</div>
            <div class="metric-value">{{ total_trades }}</div>
        </div>
        <div class="metric">
            <div class="metric-label">Total PnL</div>
            <div class="metric-value {{ 'positive' if total_pnl >= 0 else 'negative' }}">{{ "%.2f"|format(total_pnl) }} USDT</div>
        </div>
        <div class="metric">
            <div class="metric-label">Win Rate</div>
            <div class="metric-value">{{ "%.1f"|format(win_rate * 100) }}%</div>
        </div>
        <div class="metric">
            <div class="metric-label">Total Fees</div>
            <div class="metric-value">{{ "%.2f"|format(total_fees) }} USDT</div>
        </div>
    </div>

    <h2>Trade Log</h2>
    <table>
        <tr>
            <th>Symbol</th>
            <th>Side</th>
            <th>Entry</th>
            <th>Exit</th>
            <th>Size</th>
            <th>PnL</th>
            <th>Time</th>
        </tr>
        {% for trade in trades %}
        <tr>
            <td>{{ trade.symbol }}</td>
            <td>{{ trade.side }}</td>
            <td>{{ "%.2f"|format(trade.entry_price) }}</td>
            <td>{{ "%.2f"|format(trade.exit_price) }}</td>
            <td>{{ "%.6f"|format(trade.size) }}</td>
            <td class="{{ 'positive' if trade.pnl >= 0 else 'negative' }}">{{ "%.2f"|format(trade.pnl) }}</td>
            <td>{{ trade.exit_time }}</td>
        </tr>
        {% endfor %}
    </table>
</div>
</body>
</html>
"""


class ReportGenerator:
    """Generates HTML weekly performance reports."""

    def __init__(self, trade_logger: TradeLogger):
        self.trade_logger = trade_logger
        self.reports_dir = Path(__file__).parent.parent / "reports"
        self.reports_dir.mkdir(exist_ok=True)

    def generate_weekly(self, end_date: date | None = None) -> str:
        """Generate and save a weekly HTML report. Returns the file path."""
        if end_date is None:
            end_date = date.today()
        start_date = end_date - timedelta(days=7)

        start_dt = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
        end_dt = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, tzinfo=timezone.utc)

        trades = self.trade_logger.get_trades(start=start_dt, end=end_dt)

        total_pnl = sum(t["pnl"] for t in trades)
        total_fees = sum(t["fees"] for t in trades)
        winners = sum(1 for t in trades if t["pnl"] > 0)
        win_rate = winners / len(trades) if trades else 0

        template = Template(REPORT_TEMPLATE)
        html = template.render(
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            total_trades=len(trades),
            total_pnl=total_pnl,
            total_fees=total_fees,
            win_rate=win_rate,
            trades=trades,
        )

        filename = f"weekly_{end_date.isoformat()}.html"
        filepath = self.reports_dir / filename
        filepath.write_text(html)
        logger.info(f"Weekly report generated: {filepath}")
        return str(filepath)
