"""Domain models for event-driven market realization analysis."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Direction(StrEnum):
    """Expected market direction after an event."""

    UP = "up"
    DOWN = "down"
    MIXED = "mixed"
    NONE = "none"


class Verdict(StrEnum):
    """Reviewable outcome of a market realization evaluation."""

    CONFIRMED = "confirmed"
    PARTIALLY_CONFIRMED = "partially_confirmed"
    CONTRADICTED = "contradicted"
    PRICED_IN = "priced_in"
    NO_REACTION = "no_reaction"
    NO_SIGNAL = "no_signal"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True)
class ExpectedReaction:
    """A falsifiable expectation recorded before market evaluation."""

    asset: str
    direction: Direction
    horizon_minutes: int
    minimum_move_pct: float = 0.5

    def __post_init__(self) -> None:
        if not self.asset.strip():
            raise ValueError("asset must not be empty")
        if self.horizon_minutes <= 0:
            raise ValueError("horizon_minutes must be positive")
        if self.minimum_move_pct < 0:
            raise ValueError("minimum_move_pct must not be negative")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ExpectedReaction:
        return cls(
            asset=str(data["asset"]),
            direction=Direction(str(data["direction"])),
            horizon_minutes=int(data["horizon_minutes"]),
            minimum_move_pct=float(data.get("minimum_move_pct", 0.5)),
        )


@dataclass(frozen=True, slots=True)
class EventRecord:
    """Timestamped event and its pre-registered expected reaction."""

    event_id: str
    headline: str
    source: str
    occurred_at: datetime
    published_at: datetime
    reaction: ExpectedReaction
    source_confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id must not be empty")
        if not self.headline.strip():
            raise ValueError("headline must not be empty")
        if not self.source.strip():
            raise ValueError("source must not be empty")
        if not 0.0 <= self.source_confidence <= 1.0:
            raise ValueError("source_confidence must be between 0 and 1")
        if self.published_at < self.occurred_at:
            raise ValueError("published_at must not be earlier than occurred_at")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> EventRecord:
        return cls(
            event_id=str(data["event_id"]),
            headline=str(data["headline"]),
            source=str(data["source"]),
            occurred_at=datetime.fromisoformat(str(data["occurred_at"])),
            published_at=datetime.fromisoformat(str(data["published_at"])),
            reaction=ExpectedReaction.from_mapping(data["reaction"]),
            source_confidence=float(data.get("source_confidence", 1.0)),
        )


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Normalized market state at a specific time.

    Optional evidence uses ``None`` for unavailable data. Numeric zero is retained as
    an observed value and must never be interpreted as missing evidence.
    """

    timestamp: datetime
    price: float
    volume: float | None = None
    buy_volume: float | None = None
    sell_volume: float | None = None
    bid_depth: float | None = None
    ask_depth: float | None = None
    spread_bps: float | None = None

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError("price must be greater than zero")
        optional_values = (
            self.volume,
            self.buy_volume,
            self.sell_volume,
            self.bid_depth,
            self.ask_depth,
            self.spread_bps,
        )
        if any(value is not None and value < 0 for value in optional_values):
            raise ValueError("market snapshot values must not be negative")
        if (self.buy_volume is None) is not (self.sell_volume is None):
            raise ValueError("buy_volume and sell_volume must be provided together")
        if (self.bid_depth is None) is not (self.ask_depth is None):
            raise ValueError("bid_depth and ask_depth must be provided together")
        if (
            self.volume is not None
            and self.buy_volume is not None
            and self.sell_volume is not None
            and self.buy_volume + self.sell_volume > self.volume
        ):
            raise ValueError("classified flow must not exceed total volume")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> MarketSnapshot:
        return cls(
            timestamp=datetime.fromisoformat(str(data["timestamp"])),
            price=float(data["price"]),
            volume=_optional_float(data, "volume"),
            buy_volume=_optional_float(data, "buy_volume"),
            sell_volume=_optional_float(data, "sell_volume"),
            bid_depth=_optional_float(data, "bid_depth"),
            ask_depth=_optional_float(data, "ask_depth"),
            spread_bps=_optional_float(data, "spread_bps"),
        )


@dataclass(frozen=True, slots=True)
class RealizationResult:
    """Deterministic verdict with transparent evidence."""

    verdict: Verdict
    score: float
    reasons: tuple[str, ...]
    features: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("score must be between 0 and 1")


def _optional_float(data: Mapping[str, Any], key: str) -> float | None:
    value = data.get(key)
    return None if value is None else float(value)
