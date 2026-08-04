"""Temporal Market Intelligence public API."""

from tmi.adapters import (
    GatewayContractError,
    RecordedSmartMarketDataGateway,
    SmartMarketQuote,
)
from tmi.models import (
    Direction,
    EventRecord,
    ExpectedReaction,
    MarketSnapshot,
    RealizationResult,
    Verdict,
)
from tmi.scoring import RealizationConfig, RealizationScorer
from tmi.service import EvaluationWindow, RealizationService

__all__ = [
    "Direction",
    "EvaluationWindow",
    "EventRecord",
    "ExpectedReaction",
    "GatewayContractError",
    "MarketSnapshot",
    "RealizationConfig",
    "RealizationResult",
    "RealizationScorer",
    "RealizationService",
    "RecordedSmartMarketDataGateway",
    "SmartMarketQuote",
    "Verdict",
]

__version__ = "0.1.0"
