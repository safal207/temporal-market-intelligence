from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tmi import Direction, EventRecord, ExpectedReaction, MarketSnapshot, Verdict
from tmi.adapters import (
    GatewayContractError,
    RecordedSmartMarketDataGateway,
    SmartMarketQuote,
)
from tmi.features import calculate_market_features
from tmi.service import RealizationService

PUBLISHED_AT = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
GENESIS_HASH = "0" * 64


def ledger_row(
    *,
    index: int,
    previous_hash: str,
    symbol: str = "BTC/USDT",
    price: float = 100.0,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": symbol,
        "price": price,
        "bid": price - 0.01,
        "ask": price + 0.01,
        "provider_timestamp": f"2026-08-01T12:00:{index:02d}Z",
        "provider": "mock",
        "sequence": index + 1,
        "ledger_version": "1.0",
        "ledger_algorithm": "sha256",
        "ledger_index": index,
        "previous_record_hash": previous_hash,
        "recorder_session_id": "session-a",
        "provenance_system": "smart-market-data-gateway",
        "provenance_component": "websocket-jsonl-recorder",
        "provenance_transport": "websocket",
    }
    row["record_hash"] = record_hash(row)
    return row


def rich_row(
    *,
    index: int,
    previous_hash: str,
    timestamp: str,
    price: float = 100.0,
    volume: float = 100.0,
    window_ms: int = 1_000,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "schema_version": "1.1",
        "symbol": "BTC/USDT",
        "price": price,
        "bid": price - 0.01,
        "ask": price + 0.01,
        "provider_timestamp": timestamp,
        "received_at": timestamp,
        "provider": "mock-provider",
        "sequence": index + 1,
        "capabilities": [
            "aggressor_flow",
            "level1_quote",
            "top_of_book_depth",
            "trade_count",
            "volume",
        ],
        "volume": volume,
        "buy_volume": volume * 0.4,
        "sell_volume": volume * 0.6,
        "trade_count": 10,
        "bid_depth": 40.0,
        "ask_depth": 60.0,
        "volume_semantics": {
            "kind": "interval",
            "unit": "base_asset",
            "aggregation_window_ms": window_ms,
            "origin": "provider_aggregated",
        },
        "depth_semantics": {
            "unit": "base_asset",
            "levels": 1,
            "origin": "native",
        },
        "ledger_version": "1.0",
        "ledger_algorithm": "sha256",
        "ledger_index": index,
        "previous_record_hash": previous_hash,
        "recorder_session_id": "session-rich",
        "provenance_system": "smart-market-data-gateway",
        "provenance_component": "websocket-jsonl-recorder",
        "provenance_transport": "websocket",
    }
    row["record_hash"] = record_hash(row)
    return row


def record_hash(row: dict[str, Any]) -> str:
    canonical = dict(row)
    canonical.pop("record_hash", None)
    return hashlib.sha256(
        json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


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


def test_quote_accepts_live_data_quote_wrapper() -> None:
    quote = SmartMarketQuote.from_mapping(
        {
            "type": "quote",
            "data": {
                "quote": {
                    "symbol": "BTC/USDT",
                    "price": 100.0,
                    "provider_timestamp": "2026-08-01T12:00:00Z",
                }
            },
        }
    )

    assert quote.symbol == "BTC/USDT"


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


def test_observed_zero_remains_available_evidence() -> None:
    before = MarketSnapshot(
        timestamp=datetime(2026, 8, 1, 11, 59, tzinfo=UTC),
        price=100.0,
        spread_bps=2.0,
    )
    quote = SmartMarketQuote.from_mapping(
        {
            "schema_version": "1.1",
            "symbol": "BTC/USDT",
            "price": 101.0,
            "bid": 100.99,
            "ask": 101.01,
            "provider_timestamp": "2026-08-01T12:01:00Z",
            "capabilities": [
                "aggressor_flow",
                "level1_quote",
                "top_of_book_depth",
                "trade_count",
                "volume",
            ],
            "volume": 0.0,
            "buy_volume": 0.0,
            "sell_volume": 0.0,
            "trade_count": 0,
            "bid_depth": 0.0,
            "ask_depth": 0.0,
            "volume_semantics": {
                "kind": "interval",
                "unit": "base_asset",
                "aggregation_window_ms": 1_000,
                "origin": "native",
            },
            "depth_semantics": {
                "unit": "base_asset",
                "levels": 1,
                "origin": "native",
            },
        }
    )

    features = calculate_market_features(before, quote.to_snapshot(), baseline_volume=100.0)

    assert features.relative_volume == 0.0
    assert features.volume_available == 1.0
    assert features.aggressive_flow_available == 1.0
    assert features.order_book_available == 1.0


def test_rejects_rich_evidence_on_schema_1_0() -> None:
    with pytest.raises(GatewayContractError, match="schema_version 1.1"):
        SmartMarketQuote.from_mapping(
            {
                "schema_version": "1.0",
                "symbol": "BTC/USDT",
                "price": 100.0,
                "provider_timestamp": "2026-08-01T12:00:00Z",
                "capabilities": ["level1_quote", "volume"],
                "volume": 1.0,
                "volume_semantics": {
                    "kind": "interval",
                    "unit": "base_asset",
                    "aggregation_window_ms": 1_000,
                    "origin": "native",
                },
            }
        )


def test_rejects_value_without_matching_capability() -> None:
    with pytest.raises(GatewayContractError, match="volume capability"):
        SmartMarketQuote.from_mapping(
            {
                "schema_version": "1.1",
                "symbol": "BTC/USDT",
                "price": 100.0,
                "provider_timestamp": "2026-08-01T12:00:00Z",
                "capabilities": ["level1_quote"],
                "volume": 1.0,
                "volume_semantics": {
                    "kind": "interval",
                    "unit": "base_asset",
                    "aggregation_window_ms": 1_000,
                    "origin": "native",
                },
            }
        )


def test_rejects_incomparable_volume_windows() -> None:
    first = SmartMarketQuote.from_mapping(
        _without_ledger(
            rich_row(
                index=0,
                previous_hash=GENESIS_HASH,
                timestamp="2026-08-01T11:59:00Z",
                window_ms=1_000,
            )
        )
    )
    second = SmartMarketQuote.from_mapping(
        _without_ledger(
            rich_row(
                index=1,
                previous_hash=GENESIS_HASH,
                timestamp="2026-08-01T12:01:00Z",
                window_ms=60_000,
            )
        )
    )

    with pytest.raises(GatewayContractError, match="incomparable volume semantics"):
        RecordedSmartMarketDataGateway([first, second])


def test_recorded_gateway_verifies_evidence_ledger(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    first = ledger_row(index=0, previous_hash=GENESIS_HASH)
    second = ledger_row(index=1, previous_hash=first["record_hash"], price=101.0)
    write_rows(path, [first, second])

    gateway = RecordedSmartMarketDataGateway.from_jsonl(path)

    snapshot = gateway.snapshot(
        "BTC/USDT",
        datetime(2026, 8, 1, 12, 0, 1, tzinfo=UTC),
    )
    assert snapshot.price == 101.0
    assert snapshot.volume is None


def test_service_replays_quote_only_ledger_without_inventing_volume(
    tmp_path: Path,
) -> None:
    path = tmp_path / "quote-only-ledger.jsonl"
    first = ledger_row(index=0, previous_hash=GENESIS_HASH, price=100.0)
    second = ledger_row(index=1, previous_hash=first["record_hash"], price=101.0)
    write_rows(path, [first, second])

    gateway = RecordedSmartMarketDataGateway.from_jsonl(path)
    event = EventRecord(
        event_id="quote-only-up-001",
        headline="Pre-registered upward quote-only test event",
        source="test-source",
        occurred_at=PUBLISHED_AT,
        published_at=PUBLISHED_AT,
        reaction=ExpectedReaction(
            asset="BTC/USDT",
            direction=Direction.UP,
            horizon_minutes=1,
            minimum_move_pct=0.5,
        ),
    )

    result = RealizationService().evaluate(event, gateway)

    assert result.verdict is Verdict.PARTIALLY_CONFIRMED
    assert result.features["price_change_pct"] == 1.0
    assert result.features["volume_available"] == 0.0
    assert result.features["aggressive_flow_available"] == 0.0
    assert result.features["order_book_available"] == 0.0
    assert result.features["spread_available"] == 1.0
    assert result.features["relative_volume"] == 0.0
    assert any("Volume baseline unavailable" in reason for reason in result.reasons)


def test_recorded_gateway_rejects_tampered_ledger(tmp_path: Path) -> None:
    path = tmp_path / "tampered.jsonl"
    first = rich_row(
        index=0,
        previous_hash=GENESIS_HASH,
        timestamp="2026-08-01T12:00:00Z",
    )
    second = rich_row(
        index=1,
        previous_hash=first["record_hash"],
        timestamp="2026-08-01T12:00:01Z",
        price=101.0,
    )
    first["volume"] = 999.0
    write_rows(path, [first, second])

    with pytest.raises(GatewayContractError, match="record_hash mismatch"):
        RecordedSmartMarketDataGateway.from_jsonl(path)


def test_recorded_gateway_rejects_broken_ledger_link(tmp_path: Path) -> None:
    path = tmp_path / "broken-link.jsonl"
    first = ledger_row(index=0, previous_hash=GENESIS_HASH)
    second = ledger_row(index=1, previous_hash=GENESIS_HASH, price=101.0)
    write_rows(path, [first, second])

    with pytest.raises(GatewayContractError, match="previous_record_hash linkage"):
        RecordedSmartMarketDataGateway.from_jsonl(path)


def test_recorded_gateway_rejects_mixed_legacy_and_ledger_rows(tmp_path: Path) -> None:
    path = tmp_path / "mixed.jsonl"
    ledger = ledger_row(index=0, previous_hash=GENESIS_HASH)
    legacy = {
        "symbol": "BTC/USDT",
        "price": 101.0,
        "provider_timestamp": "2026-08-01T12:00:01Z",
    }
    write_rows(path, [ledger, legacy])

    with pytest.raises(GatewayContractError, match="mixes legacy rows"):
        RecordedSmartMarketDataGateway.from_jsonl(path)


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


def test_service_evaluates_verified_rich_gateway_vertical_slice() -> None:
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
    assert result.features["volume_available"] == 1.0
    assert result.features["aggressive_flow_available"] == 1.0
    assert result.features["order_book_available"] == 1.0
    assert result.features["spread_available"] == 1.0


def _without_ledger(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in {
        "ledger_version",
        "ledger_algorithm",
        "ledger_index",
        "previous_record_hash",
        "record_hash",
        "recorder_session_id",
        "provenance_system",
        "provenance_component",
        "provenance_transport",
    }}
