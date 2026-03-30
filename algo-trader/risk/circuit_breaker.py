"""Circuit breaker state machine for trading safety."""

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class State(str, Enum):
    ACTIVE = "ACTIVE"
    DAILY_LIMIT = "DAILY_LIMIT"
    DRAWDOWN = "DRAWDOWN"
    MANUAL_HALT = "MANUAL_HALT"


class CircuitBreaker:
    """
    Trading circuit breaker with states:
    - ACTIVE: Normal trading allowed
    - DAILY_LIMIT: Daily loss limit hit, auto-resets next UTC day
    - DRAWDOWN: Max drawdown exceeded, requires manual resume
    - MANUAL_HALT: Manually halted, requires manual resume

    All order placement must check can_trade() first.
    """

    def __init__(self, config: dict):
        self.daily_loss_limit_pct = config["risk"]["daily_loss_limit_pct"]
        self.max_drawdown_pct = config["risk"]["max_drawdown_pct"]
        self._state = State.ACTIVE
        self._manual_halt = False
        self._peak_equity: float = 0.0

    @property
    def state(self) -> State:
        return self._state

    def can_trade(self) -> bool:
        """Returns True only if state is ACTIVE."""
        return self._state == State.ACTIVE

    def update(self, current_equity: float, peak_equity: float, daily_pnl_pct: float):
        """
        Update circuit breaker state based on current conditions.
        Call this on every candle.
        """
        self._peak_equity = max(self._peak_equity, peak_equity)
        previous_state = self._state

        if self._manual_halt:
            self._state = State.MANUAL_HALT
        elif self._peak_equity > 0 and (self._peak_equity - current_equity) / self._peak_equity > self.max_drawdown_pct:
            self._state = State.DRAWDOWN
        elif daily_pnl_pct < -self.daily_loss_limit_pct:
            self._state = State.DAILY_LIMIT
        elif self._state == State.DAILY_LIMIT:
            # Daily limit auto-clears when daily_pnl_pct resets (new day)
            self._state = State.ACTIVE
        elif self._state not in (State.DRAWDOWN, State.MANUAL_HALT):
            self._state = State.ACTIVE

        if self._state != previous_state:
            logger.warning(f"Circuit breaker: {previous_state.value} → {self._state.value}")

    def reset_daily_limit(self):
        """Reset daily limit state (called at start of new UTC day)."""
        if self._state == State.DAILY_LIMIT:
            self._state = State.ACTIVE
            logger.info("Circuit breaker: DAILY_LIMIT reset → ACTIVE")

    def halt(self):
        """Manually halt trading."""
        self._manual_halt = True
        self._state = State.MANUAL_HALT
        logger.warning("Circuit breaker: MANUAL_HALT engaged")

    def resume(self):
        """Manually resume trading. Required to clear DRAWDOWN or MANUAL_HALT."""
        self._manual_halt = False
        if self._state in (State.DRAWDOWN, State.MANUAL_HALT):
            self._state = State.ACTIVE
            logger.info(f"Circuit breaker: Manual resume → ACTIVE")
