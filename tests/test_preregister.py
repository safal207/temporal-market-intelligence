from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tmi.models import Direction, EventRecord
from tmi.preregister import (
    HypothesisCommitment,
    create_commitment,
    finalize_commitment,
    load_commitment,
    main,
    verify_optional_preregistration,
)

REGISTERED_AT = datetime(2026, 8, 4, 16, 0, tzinfo=UTC)
SCHEDULED_AT = datetime(2026, 8, 4, 17, 0, tzinfo=UTC)
OCCURRED_AT = datetime(2026, 8, 4, 17, 0, 5, tzinfo=UTC)
PUBLISHED_AT = datetime(2026, 8, 4, 17, 0, 8, tzinfo=UTC)


def commitment() -> HypothesisCommitment:
    return create_commitment(
        event_id="btc-cpi-001",
        headline="Scheduled macro release",
        source="official-source",
        asset="BTC-USD",
        direction=Direction.DOWN,
        horizon_minutes=30,
        minimum_move_pct=0.5,
        registered_at=REGISTERED_AT,
        scheduled_event_at=SCHEDULED_AT,
    )


def test_commitment_hash_is_deterministic_and_sensitive() -> None:
    original = commitment()
    changed = create_commitment(
        event_id=original.event_id,
        headline=original.headline,
        source=original.source,
        asset=original.reaction.asset,
        direction=Direction.UP,
        horizon_minutes=original.reaction.horizon_minutes,
        minimum_move_pct=original.reaction.minimum_move_pct,
        registered_at=original.registered_at,
        scheduled_event_at=original.scheduled_event_at,
    )

    assert original.commitment_hash_sha256 == commitment().commitment_hash_sha256
    assert original.commitment_hash_sha256 != changed.commitment_hash_sha256


def test_load_commitment_rejects_tampering(tmp_path: Path) -> None:
    path = tmp_path / "commitment.json"
    payload = commitment().as_dict()
    payload["hypothesis"]["reaction"]["direction"] = "up"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="commitment_hash_sha256 mismatch"):
        load_commitment(path)


def test_finalize_and_verify_preregistration() -> None:
    payload = finalize_commitment(
        commitment(),
        occurred_at=OCCURRED_AT,
        published_at=PUBLISHED_AT,
        source_confidence=0.95,
    )
    event = EventRecord.from_mapping(payload["event"])

    verified = verify_optional_preregistration(payload, event)

    assert verified is not None
    assert verified.commitment_hash_sha256 == commitment().commitment_hash_sha256
    assert verified.registered_at == REGISTERED_AT
    assert verified.external_timestamp_verified is False


def test_finalize_rejects_event_before_registration() -> None:
    with pytest.raises(ValueError, match="must not precede preregistration"):
        finalize_commitment(
            commitment(),
            occurred_at=datetime(2026, 8, 4, 15, 59, tzinfo=UTC),
            published_at=PUBLISHED_AT,
            source_confidence=1.0,
        )


def test_verified_event_rejects_changed_hypothesis() -> None:
    payload = finalize_commitment(
        commitment(),
        occurred_at=OCCURRED_AT,
        published_at=PUBLISHED_AT,
        source_confidence=1.0,
    )
    payload["event"]["reaction"]["minimum_move_pct"] = 0.1
    event = EventRecord.from_mapping(payload["event"])

    with pytest.raises(ValueError, match="does not match preregistration"):
        verify_optional_preregistration(payload, event)


def test_cli_create_and_finalize_write_private_new_files(tmp_path: Path) -> None:
    commitment_path = tmp_path / "events" / "btc.commitment.json"
    event_path = tmp_path / "events" / "btc.event.json"

    assert (
        main(
            [
                "create",
                "--event-id",
                "btc-cpi-001",
                "--headline",
                "Scheduled macro release",
                "--source",
                "official-source",
                "--asset",
                "BTC-USD",
                "--direction",
                "down",
                "--horizon-minutes",
                "30",
                "--scheduled-event-at",
                "2026-08-05T17:00:00Z",
                "--output",
                str(commitment_path),
            ]
        )
        == 0
    )
    assert stat.S_IMODE(commitment_path.stat().st_mode) == 0o600

    payload = json.loads(commitment_path.read_text(encoding="utf-8"))
    registered_at = datetime.fromisoformat(payload["registered_at"])
    occurred_at = registered_at + timedelta(days=1)
    published_at = occurred_at + timedelta(seconds=1)

    assert (
        main(
            [
                "finalize",
                str(commitment_path),
                "--occurred-at",
                occurred_at.isoformat(),
                "--published-at",
                published_at.isoformat(),
                "--output",
                str(event_path),
            ]
        )
        == 0
    )
    assert stat.S_IMODE(event_path.stat().st_mode) == 0o600

    finalized = json.loads(event_path.read_text(encoding="utf-8"))
    event = EventRecord.from_mapping(finalized["event"])
    assert verify_optional_preregistration(finalized, event) is not None

    with pytest.raises(SystemExit, match="File exists"):
        main(
            [
                "create",
                "--event-id",
                "duplicate",
                "--headline",
                "Duplicate",
                "--source",
                "official-source",
                "--asset",
                "BTC-USD",
                "--direction",
                "down",
                "--horizon-minutes",
                "30",
                "--output",
                str(commitment_path),
            ]
        )
