"""Layer 2 signal: Binance futures funding rate. Scaffold — activate after Layer 1 validated."""


class FundingRate:
    """Fetches perpetual futures funding rate from Binance."""

    def __init__(self, config: dict):
        self.enabled = config["signals"]["use_funding_rate"]

    def fetch(self, symbol: str) -> float | None:
        """Fetch current funding rate. Returns None if disabled."""
        if not self.enabled:
            return None
        raise NotImplementedError("Layer 2 signal — implement after Layer 1 is live-validated")
