from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from tmi import Direction, EventRecord, ExpectedReaction, Verdict
from tmi.adapters import (
    GatewayContractError,
    RecordedSmartMarketDataGateway,
    SmartMarketQuote,
)
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
    row["record_hash"] = hashlib.sha256(
        json.dumps(
            row,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    return row


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


def test_recorded_gateway_rejects_tampered_ledger(tmp_path: Path) -> None:
    path = tmp_path / "tampered.jsonl"
    first = ledger_row(index=0, previous_hash=GENESIS_HASH)
    second = ledger_row(index=1, previous_hash=first["record_hash"], price=101.0)
    first["price"] = 999.0
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
