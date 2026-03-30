"""Position sizing based on fixed fractional risk."""


class PositionSizer:
    """
    Calculates position size based on:
    - Risk per trade = equity * max_position_pct
    - Size = risk_amount / stop_distance
    - Caps so total position value doesn't exceed equity
    """

    def __init__(self, config: dict):
        self.max_position_pct = config["risk"]["max_position_pct"]

    def calculate(self, equity: float, entry_price: float, stop_price: float) -> float:
        """
        Calculate position size in units.

        Args:
            equity: Current account equity
            entry_price: Expected entry price
            stop_price: Stop-loss price

        Returns:
            Position size in units of the asset
        """
        risk_amount = equity * self.max_position_pct
        stop_distance = abs(entry_price - stop_price)

        if stop_distance == 0:
            return 0.0

        size = risk_amount / stop_distance

        # Cap so position value doesn't exceed equity
        max_size = equity / entry_price
        return min(size, max_size)
