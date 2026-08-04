from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tmi.anchor import SigstoreAnchorVerification, prepare_anchor_payload
from tmi.cli import analyze_gateway_recording
from tmi.models import Direction, EventRecord
from tmi.preregister import (
    create_commitment,
    finalize_commitment,
    main as preregister_main,
    verify_optional_preregistration,
)

IDENTITY = "researcher@example.com"
ISSUER = "https://accounts.example.com"
REGISTERED_AT = datetime(2026, 8, 1, 11, 0, tzinfo=UTC)
OCCURRED_AT = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def commitment():
    return create_commitment(
        event_id="btc-policy-shock-001",
        headline="Unexpected policy announcement pressures risk assets",
        source="official-source",
        asset="BTC/USDT",
        direction=Direction.DOWN,
        horizon_minutes=30,
        minimum_move_pct=0.5,
        registered_at=REGISTERED_AT,
        scheduled_event_at=OCCURRED_AT,
    )


def write_anchor_files(
    tmp_path: Path,
) -> tuple[Path, Path, SigstoreAnchorVerification]:
    anchor_path = tmp_path / "anchors" / "event.anchor.json"
    bundle_path = tmp_path / "anchors" / "event.sigstore.json"
    prepare_anchor_payload(commitment().commitment_hash_sha256, anchor_path)
    bundle_path.write_text(
        '{"mediaType":"application/vnd.dev.sigstore.bundle+json"}\n',
        encoding="utf-8",
    )
    verification = SigstoreAnchorVerification(
        commitment_hash_sha256=commitment().commitment_hash_sha256,
        anchor_payload_sha256=hashlib.sha256(anchor_path.read_bytes()).hexdigest(),
        bundle_sha256=hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        certificate_identity=IDENTITY,
        certificate_oidc_issuer=ISSUER,
    )
    return anchor_path, bundle_path, verification


def fake_cosign_success(
    command: list[str],
    *,
    capture_output: bool,
    text: bool,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    assert command[:2] == ["cosign", "verify-blob"]
    assert capture_output is True
    assert text is True
    assert check is False
    return subprocess.CompletedProcess(command, 0, "Verified OK\n", "")


def test_finalize_embeds_verified_anchor_metadata(tmp_path: Path) -> None:
    _, _, verification = write_anchor_files(tmp_path)

    payload = finalize_commitment(
        commitment(),
        occurred_at=OCCURRED_AT,
        published_at=OCCURRED_AT,
        source_confidence=0.98,
        anchor_verification=verification,
    )
    event = EventRecord.from_mapping(payload["event"])
    verified = verify_optional_preregistration(payload, event)

    assert verified is not None
    assert verified.external_timestamp_verified is True
    assert verified.external_anchor == verification
    assert payload["preregistration"]["external_anchor"][
        "certificate_identity"
    ] == IDENTITY


def test_preregistration_rejects_forged_external_timestamp_flag() -> None:
    payload = finalize_commitment(
        commitment(),
        occurred_at=OCCURRED_AT,
        published_at=OCCURRED_AT,
        source_confidence=1.0,
    )
    payload["preregistration"]["external_timestamp_verified"] = True
    event = EventRecord.from_mapping(payload["event"])

    with pytest.raises(ValueError, match="requires verified external_anchor"):
        verify_optional_preregistration(payload, event)


def test_anchored_finalize_cli_reverifies_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commitment_path = tmp_path / "events" / "event.commitment.json"
    commitment_path.parent.mkdir(parents=True)
    commitment_path.write_text(
        json.dumps(commitment().as_dict()),
        encoding="utf-8",
    )
    anchor_path, bundle_path, _ = write_anchor_files(tmp_path)
    output = tmp_path / "events" / "event.json"
    monkeypatch.setattr(subprocess, "run", fake_cosign_success)

    result = preregister_main(
        [
            "finalize",
            str(commitment_path),
            "--occurred-at",
            OCCURRED_AT.isoformat(),
            "--published-at",
            OCCURRED_AT.isoformat(),
            "--source-confidence",
            "0.98",
            "--anchor-payload",
            str(anchor_path),
            "--anchor-bundle",
            str(bundle_path),
            "--certificate-identity",
            IDENTITY,
            "--certificate-oidc-issuer",
            ISSUER,
            "--output",
            str(output),
        ]
    )

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["preregistration"]["external_timestamp_verified"] is True
    assert payload["preregistration"]["external_anchor"]["bundle_sha256"]


def test_replay_reverifies_anchor_and_embeds_it_in_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor_path, bundle_path, verification = write_anchor_files(tmp_path)
    event_payload = finalize_commitment(
        commitment(),
        occurred_at=OCCURRED_AT,
        published_at=OCCURRED_AT,
        source_confidence=0.98,
        anchor_verification=verification,
    )
    event_path = tmp_path / "events" / "event.json"
    event_path.parent.mkdir(parents=True)
    event_path.write_text(json.dumps(event_payload), encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", fake_cosign_success)

    report = analyze_gateway_recording(
        event_path,
        Path("examples/gateway_quotes.jsonl"),
        anchor_payload=anchor_path,
        anchor_bundle=bundle_path,
    )

    assert report["verdict"] == "confirmed"
    preregistration = report["preregistration"]
    assert preregistration["external_timestamp_verified"] is True
    assert preregistration["external_anchor"]["certificate_identity"] == IDENTITY
    assert preregistration["external_anchor"]["bundle_sha256"] == verification.bundle_sha256


def test_replay_requires_bundle_for_externally_anchored_event(tmp_path: Path) -> None:
    _, _, verification = write_anchor_files(tmp_path)
    event_payload = finalize_commitment(
        commitment(),
        occurred_at=OCCURRED_AT,
        published_at=OCCURRED_AT,
        source_confidence=0.98,
        anchor_verification=verification,
    )
    event_path = tmp_path / "events" / "event.json"
    event_path.parent.mkdir(parents=True)
    event_path.write_text(json.dumps(event_payload), encoding="utf-8")

    with pytest.raises(ValueError, match="requires --anchor-payload"):
        analyze_gateway_recording(
            event_path,
            Path("examples/gateway_quotes.jsonl"),
        )


def test_replay_rejects_different_bundle_even_when_cosign_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor_path, bundle_path, verification = write_anchor_files(tmp_path)
    event_payload = finalize_commitment(
        commitment(),
        occurred_at=OCCURRED_AT,
        published_at=OCCURRED_AT,
        source_confidence=0.98,
        anchor_verification=verification,
    )
    event_path = tmp_path / "events" / "event.json"
    event_path.parent.mkdir(parents=True)
    event_path.write_text(json.dumps(event_payload), encoding="utf-8")
    bundle_path.write_text('{"changed":true}\n', encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", fake_cosign_success)

    with pytest.raises(ValueError, match="does not match embedded"):
        analyze_gateway_recording(
            event_path,
            Path("examples/gateway_quotes.jsonl"),
            anchor_payload=anchor_path,
            anchor_bundle=bundle_path,
        )
