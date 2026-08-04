"""Provider-neutral boundary for normalized market evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from tmi.models import MarketSnapshot


class MarketDataGateway(Protocol):
    """Minimal interface expected from a market-data adapter."""

    def snapshot(self, asset: str, at: datetime) -> MarketSnapshot:
        """Return the normalized market snapshot nearest to the requested time."""
        ...

    def baseline_volume(
        self,
        asset: str,
        *,
        before: datetime,
        lookback_minutes: int,
    ) -> float:
        """Return comparable baseline volume for the selected event window."""
        ...
