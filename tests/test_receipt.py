from __future__ import annotations

import json
from pathlib import Path

from tmi.models import EventRecord
from tmi.receipt import build_recording_manifest, event_fingerprint_sha256


def _event() -> EventRecord:
    payload = json.loads(
        Path("examples/btc_gateway_event.json").read_text(encoding="utf-8")
    )
    return EventRecord.from_mapping(payload["event"])


def test_manifest_identifies_verified_rich_ledger_without_market_values() -> None:
    manifest = build_recording_manifest(Path("examples/gateway_quotes.jsonl"))
    payload = manifest.as_dict()

    assert payload["ledger_verified"] is True
    assert payload["fingerprint_kind"] == "ledger_head"
    assert len(payload["evidence_fingerprint_sha256"]) == 64
    assert payload["ledger_head_hash"] == payload["evidence_fingerprint_sha256"]
    assert payload["symbols"] == ["BTC/USDT"]
    assert payload["providers"] == ["demo-provider"]
    assert payload["session_ids"] == ["demo-rich-session"]
    assert payload["records"] > 5
    assert payload["records_by_symbol"] == {"BTC/USDT": payload["records"]}
    assert "volume" in payload["capabilities_on_every_record"]
    assert "top_of_book_depth" in payload["capabilities_on_every_record"]

    forbidden_market_value_keys = {
        "price",
        "bid",
        "ask",
        "volume",
        "buy_volume",
        "sell_volume",
        "bid_depth",
        "ask_depth",
        "spread_bps",
        "features",
        "score",
        "verdict",
    }
    assert forbidden_market_value_keys.isdisjoint(payload)


def test_event_fingerprint_is_deterministic_and_semantic() -> None:
    event = _event()

    first = event_fingerprint_sha256(event)
    second = event_fingerprint_sha256(event)
    changed = EventRecord(
        event_id=event.event_id,
        headline=event.headline,
        source=event.source,
        occurred_at=event.occurred_at,
        published_at=event.published_at,
        reaction=type(event.reaction)(
            asset=event.reaction.asset,
            direction=event.reaction.direction,
            horizon_minutes=event.reaction.horizon_minutes,
            minimum_move_pct=event.reaction.minimum_move_pct + 0.1,
        ),
        source_confidence=event.source_confidence,
    )

    assert len(first) == 64
    assert first == second
    assert event_fingerprint_sha256(changed) != first
