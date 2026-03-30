"""Layer 2 signal: Binance open interest. Scaffold — activate after Layer 1 validated."""


class OpenInterest:
    """Fetches open interest data from Binance futures."""

    def __init__(self, config: dict):
        self.enabled = config["signals"]["use_open_interest"]

    def fetch(self, symbol: str) -> float | None:
        """Fetch current open interest. Returns None if disabled."""
        if not self.enabled:
            return None
        raise NotImplementedError("Layer 2 signal — implement after Layer 1 is live-validated")
