from __future__ import annotations

import json
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tmi import anchor
from tmi.anchor import (
    SigstoreAnchorVerification,
    build_anchor_payload,
    main,
    prepare_anchor_payload,
    verify_sigstore_anchor,
)
from tmi.models import Direction
from tmi.preregister import create_commitment

COMMITMENT_HASH = "a" * 64
IDENTITY = "researcher@example.com"
ISSUER = "https://accounts.example.com"


def test_anchor_payload_contains_only_commitment_metadata() -> None:
    payload = build_anchor_payload(COMMITMENT_HASH)

    assert payload == {
        "anchor_schema_version": "1.0",
        "anchor_type": "tmi_hypothesis_commitment",
        "anchor_algorithm": "sha256",
        "commitment_hash_sha256": COMMITMENT_HASH,
    }
    serialized = json.dumps(payload)
    assert "headline" not in serialized
    assert "direction" not in serialized
    assert "asset" not in serialized


def test_prepare_anchor_writes_private_new_file(tmp_path: Path) -> None:
    output = tmp_path / "anchors" / "event.anchor.json"

    prepare_anchor_payload(COMMITMENT_HASH, output)

    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(output.read_text(encoding="utf-8"))[
        "commitment_hash_sha256"
    ] == COMMITMENT_HASH
    with pytest.raises(FileExistsError):
        prepare_anchor_payload(COMMITMENT_HASH, output)


def test_verify_sigstore_anchor_invokes_cosign_and_fingerprints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor_path = tmp_path / "anchors" / "event.anchor.json"
    bundle_path = tmp_path / "anchors" / "event.sigstore.json"
    prepare_anchor_payload(COMMITMENT_HASH, anchor_path)
    bundle_path.write_text('{"mediaType":"application/vnd.dev.sigstore.bundle+json"}\n')
    observed: list[str] = []

    def fake_run(
        command: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output is True
        assert text is True
        assert check is False
        observed.extend(command)
        return subprocess.CompletedProcess(command, 0, "Verified OK\n", "")

    monkeypatch.setattr(anchor.subprocess, "run", fake_run)

    verified = verify_sigstore_anchor(
        COMMITMENT_HASH,
        anchor_path,
        bundle_path,
        certificate_identity=IDENTITY,
        certificate_oidc_issuer=ISSUER,
    )

    assert observed == [
        "cosign",
        "verify-blob",
        str(anchor_path),
        "--bundle",
        str(bundle_path),
        f"--certificate-identity={IDENTITY}",
        f"--certificate-oidc-issuer={ISSUER}",
    ]
    assert verified.commitment_hash_sha256 == COMMITMENT_HASH
    assert len(verified.anchor_payload_sha256) == 64
    assert len(verified.bundle_sha256) == 64
    assert verified.transparency_log_verified is True
    assert verified.signed_timestamp_verified is True


def test_verify_rejects_payload_mismatch_before_cosign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor_path = tmp_path / "anchors" / "event.anchor.json"
    bundle_path = tmp_path / "anchors" / "event.sigstore.json"
    prepare_anchor_payload("b" * 64, anchor_path)
    bundle_path.write_text("{}\n", encoding="utf-8")

    def fail_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("cosign must not run for a mismatched payload")

    monkeypatch.setattr(anchor.subprocess, "run", fail_run)

    with pytest.raises(ValueError, match="does not match"):
        verify_sigstore_anchor(
            COMMITMENT_HASH,
            anchor_path,
            bundle_path,
            certificate_identity=IDENTITY,
            certificate_oidc_issuer=ISSUER,
        )


def test_verify_rejects_failed_cosign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor_path = tmp_path / "anchors" / "event.anchor.json"
    bundle_path = tmp_path / "anchors" / "event.sigstore.json"
    prepare_anchor_payload(COMMITMENT_HASH, anchor_path)
    bundle_path.write_text("{}\n", encoding="utf-8")

    def fake_run(
        command: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "invalid inclusion proof")

    monkeypatch.setattr(anchor.subprocess, "run", fake_run)

    with pytest.raises(ValueError, match="invalid inclusion proof"):
        verify_sigstore_anchor(
            COMMITMENT_HASH,
            anchor_path,
            bundle_path,
            certificate_identity=IDENTITY,
            certificate_oidc_issuer=ISSUER,
        )


def test_verification_round_trips_safe_metadata() -> None:
    original = SigstoreAnchorVerification(
        commitment_hash_sha256=COMMITMENT_HASH,
        anchor_payload_sha256="b" * 64,
        bundle_sha256="c" * 64,
        certificate_identity=IDENTITY,
        certificate_oidc_issuer=ISSUER,
    )

    restored = SigstoreAnchorVerification.from_mapping(original.as_dict())

    assert restored == original


def test_cli_prepares_payload_from_verified_commitment(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    commitment = create_commitment(
        event_id="btc-event-001",
        headline="Scheduled macro event",
        source="official-source",
        asset="BTC-USD",
        direction=Direction.DOWN,
        horizon_minutes=30,
        minimum_move_pct=0.5,
        registered_at=datetime(2026, 8, 4, 18, 0, tzinfo=UTC),
        scheduled_event_at=datetime(2026, 8, 5, 12, 30, tzinfo=UTC),
    )
    commitment_path = tmp_path / "events" / "event.commitment.json"
    commitment_path.parent.mkdir(parents=True)
    commitment_path.write_text(
        json.dumps(commitment.as_dict()),
        encoding="utf-8",
    )
    output = tmp_path / "anchors" / "event.anchor.json"

    result = main(
        [
            "prepare",
            str(commitment_path),
            "--output",
            str(output),
        ]
    )

    assert result == 0
    report = json.loads(capsys.readouterr().out)
    assert report["contains_hypothesis_text"] is False
    assert report["commitment_hash_sha256"] == commitment.commitment_hash_sha256
    assert output.exists()
