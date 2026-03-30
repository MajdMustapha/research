"""Layer 3 signal: Fear & Greed Index. Scaffold — activate after Layer 1 validated."""


class FearGreedIndex:
    """Fetches Fear & Greed Index from alternative.me."""

    def __init__(self, config: dict):
        self.enabled = config["signals"]["use_fear_greed"]

    def fetch(self) -> int | None:
        """Fetch current Fear & Greed score (0-100). Returns None if disabled."""
        if not self.enabled:
            return None
        raise NotImplementedError("Layer 3 signal — implement after Layer 1 is live-validated")
