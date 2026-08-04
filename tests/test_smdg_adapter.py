from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tmi import Direction, EventRecord, ExpectedReaction, Verdict
from tmi.adapters import (
    GatewayContractError,
    RecordedSmartMarketDataGateway,
    SmartMarketQuote,
)
from tmi.service import RealizationService

PUBLISHED_AT = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def test_quote_uses_midpoint_and_computes_spread() -> None:
    quote = SmartMarketQuote.from_mapping(
        {
            "symbol": "BTC/USDT",
            "bid": 99.0,
            "ask": 101.0,
            "provider_timestamp": "2026-08-01T12:00:00Z",
        }
    )

    assert quote.price == 100.0
    assert quote.spread_bps == 200.0


def test_quote_rejects_crossed_book() -> None:
    with pytest.raises(GatewayContractError, match="bid must not exceed ask"):
        SmartMarketQuote.from_mapping(
            {
                "symbol": "BTC/USDT",
                "bid": 101.0,
                "ask": 99.0,
                "provider_timestamp": "2026-08-01T12:00:00Z",
            }
        )


def test_recorded_gateway_rejects_distant_evidence() -> None:
    gateway = RecordedSmartMarketDataGateway(
        [
            SmartMarketQuote.from_mapping(
                {
                    "symbol": "BTC/USDT",
                    "price": 100.0,
                    "provider_timestamp": "2026-08-01T12:00:00Z",
                }
            )
        ],
        max_distance_seconds=30.0,
    )

    with pytest.raises(GatewayContractError, match="from requested time"):
        gateway.snapshot("BTC/USDT", datetime(2026, 8, 1, 12, 1, tzinfo=UTC))


def test_service_evaluates_recorded_gateway_vertical_slice() -> None:
    gateway = RecordedSmartMarketDataGateway.from_jsonl(
        Path("examples/gateway_quotes.jsonl")
    )
    event = EventRecord(
        event_id="btc-policy-shock-001",
        headline="Unexpected policy announcement pressures risk assets",
        source="official-source",
        occurred_at=PUBLISHED_AT,
        published_at=PUBLISHED_AT,
        source_confidence=0.98,
        reaction=ExpectedReaction(
            asset="BTC/USDT",
            direction=Direction.DOWN,
            horizon_minutes=30,
            minimum_move_pct=0.5,
        ),
    )

    result = RealizationService().evaluate(event, gateway)

    assert result.verdict is Verdict.CONFIRMED
    assert result.score >= 0.9
    assert result.features["relative_volume"] > 2.0
