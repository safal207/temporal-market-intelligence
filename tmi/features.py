"""Pure feature calculations for market realization evidence."""

from __future__ import annotations

from dataclasses import dataclass

from tmi.models import MarketSnapshot


@dataclass(frozen=True, slots=True)
class MarketFeatures:
    """Calculated evidence used by the realization scorer."""

    price_change_pct: float
    relative_volume: float
    aggressive_sell_ratio: float
    order_book_imbalance: float
    spread_change_ratio: float

    def as_dict(self) -> dict[str, float]:
        return {
            "price_change_pct": self.price_change_pct,
            "relative_volume": self.relative_volume,
            "aggressive_sell_ratio": self.aggressive_sell_ratio,
            "order_book_imbalance": self.order_book_imbalance,
            "spread_change_ratio": self.spread_change_ratio,
        }


def percentage_change(start: float, end: float) -> float:
    """Return percentage change from start to end."""

    if start <= 0:
        raise ValueError("start must be greater than zero")
    return ((end - start) / start) * 100.0


def ratio(value: float, baseline: float) -> float:
    """Return a finite ratio for a strictly positive baseline."""

    if value < 0:
        raise ValueError("ratio value must not be negative")
    if baseline <= 0:
        raise ValueError("ratio baseline must be greater than zero")
    return value / baseline


def aggressive_sell_ratio(snapshot: MarketSnapshot) -> float:
    """Share of reported aggressive volume executed by sellers."""

    total = snapshot.buy_volume + snapshot.sell_volume
    if total == 0:
        return 0.5
    return snapshot.sell_volume / total


def order_book_imbalance(snapshot: MarketSnapshot) -> float:
    """Top-level depth imbalance in the interval [-1, 1]."""

    total = snapshot.bid_depth + snapshot.ask_depth
    if total == 0:
        return 0.0
    return (snapshot.bid_depth - snapshot.ask_depth) / total


def calculate_market_features(
    before: MarketSnapshot,
    after: MarketSnapshot,
    baseline_volume: float,
) -> MarketFeatures:
    """Calculate the minimal deterministic feature set for one event window."""

    if after.timestamp <= before.timestamp:
        raise ValueError("after snapshot must be later than before snapshot")

    spread_change_ratio = 1.0
    if before.spread_bps > 0:
        spread_change_ratio = ratio(after.spread_bps, before.spread_bps)

    return MarketFeatures(
        price_change_pct=percentage_change(before.price, after.price),
        relative_volume=ratio(after.volume, baseline_volume),
        aggressive_sell_ratio=aggressive_sell_ratio(after),
        order_book_imbalance=order_book_imbalance(after),
        spread_change_ratio=spread_change_ratio,
    )
