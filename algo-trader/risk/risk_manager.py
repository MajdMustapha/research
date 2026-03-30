"""Daily loss limit enforcement."""

from datetime import date, datetime, timezone


class RiskManager:
    """
    Tracks daily PnL and enforces the daily loss limit.
    Auto-resets at the start of each new UTC day.
    """

    def __init__(self, config: dict):
        self.daily_loss_limit_pct = config["risk"]["daily_loss_limit_pct"]
        self._daily_start_equity: float = 0.0
        self._current_date: date | None = None

    def reset_daily(self, equity: float):
        """Reset daily tracking with current equity."""
        self._daily_start_equity = equity
        self._current_date = datetime.now(timezone.utc).date()

    def _check_date_reset(self, equity: float):
        """Auto-reset if the date has changed."""
        today = datetime.now(timezone.utc).date()
        if self._current_date is None or today != self._current_date:
            self.reset_daily(equity)

    def daily_pnl_pct(self, current_equity: float) -> float:
        """Current day's PnL as a fraction of start-of-day equity."""
        self._check_date_reset(current_equity)
        if self._daily_start_equity == 0:
            return 0.0
        return (current_equity - self._daily_start_equity) / self._daily_start_equity

    def check_daily_loss(self, current_equity: float) -> bool:
        """Returns True if within daily loss limit (trading allowed)."""
        pnl_pct = self.daily_pnl_pct(current_equity)
        return pnl_pct > -self.daily_loss_limit_pct
