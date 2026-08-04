"""Domain models for event-driven market realization analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping


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
    """Normalized market state at a specific time."""

    timestamp: datetime
    price: float
    volume: float
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    bid_depth: float = 0.0
    ask_depth: float = 0.0
    spread_bps: float = 0.0

    def __post_init__(self) -> None:
        numeric_values = (
            self.price,
            self.volume,
            self.buy_volume,
            self.sell_volume,
            self.bid_depth,
            self.ask_depth,
            self.spread_bps,
        )
        if any(value < 0 for value in numeric_values):
            raise ValueError("market snapshot values must not be negative")
        if self.price == 0:
            raise ValueError("price must be greater than zero")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> MarketSnapshot:
        return cls(
            timestamp=datetime.fromisoformat(str(data["timestamp"])),
            price=float(data["price"]),
            volume=float(data["volume"]),
            buy_volume=float(data.get("buy_volume", 0.0)),
            sell_volume=float(data.get("sell_volume", 0.0)),
            bid_depth=float(data.get("bid_depth", 0.0)),
            ask_depth=float(data.get("ask_depth", 0.0)),
            spread_bps=float(data.get("spread_bps", 0.0)),
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
