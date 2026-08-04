from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

from tmi.adapters import RecordedSmartMarketDataGateway
from tmi.cli import analyze_gateway_recording
from tmi.evidence_anchor import (
    build_evidence_anchor_payload,
    prepare_evidence_anchor,
    verify_evidence_anchor,
)
from tmi.receipt import build_recording_manifest

IDENTITY = "researcher@example.com"
ISSUER = "https://accounts.example.com"
RECORDING = Path("examples/gateway_quotes.jsonl")
EVENT = Path("examples/btc_gateway_event.json")


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


def manifest():
    gateway = RecordedSmartMarketDataGateway.from_jsonl(RECORDING)
    return build_recording_manifest(RECORDING, verified_gateway=gateway)


def test_evidence_anchor_payload_contains_only_ledger_fingerprint() -> None:
    payload = build_evidence_anchor_payload(manifest())

    assert payload == {
        "anchor_schema_version": "1.0",
        "anchor_type": "tmi_market_evidence_ledger",
        "anchor_algorithm": "sha256",
        "fingerprint_kind": "ledger_head",
        "evidence_fingerprint_sha256": manifest().ledger_head_hash,
    }
    serialized = json.dumps(payload)
    assert "price" not in serialized
    assert "volume" not in serialized
    assert "bid" not in serialized
    assert "ask" not in serialized


def test_prepare_evidence_anchor_writes_private_new_file(tmp_path: Path) -> None:
    output = tmp_path / "anchors" / "market.anchor.json"

    prepare_evidence_anchor(RECORDING, output)

    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(output.read_text(encoding="utf-8"))[
        "evidence_fingerprint_sha256"
    ] == manifest().ledger_head_hash
    with pytest.raises(FileExistsError):
        prepare_evidence_anchor(RECORDING, output)


def test_verify_evidence_anchor_invokes_cosign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor_path = tmp_path / "anchors" / "market.anchor.json"
    bundle_path = tmp_path / "anchors" / "market.sigstore.json"
    prepare_evidence_anchor(RECORDING, anchor_path)
    bundle_path.write_text('{"mediaType":"sigstore-bundle"}\n', encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", fake_cosign_success)

    verified = verify_evidence_anchor(
        manifest(),
        anchor_path,
        bundle_path,
        certificate_identity=IDENTITY,
        certificate_oidc_issuer=ISSUER,
    )

    assert verified.evidence_fingerprint_sha256 == manifest().ledger_head_hash
    assert verified.fingerprint_kind == "ledger_head"
    assert verified.certificate_identity == IDENTITY
    assert verified.transparency_log_verified is True
    assert len(verified.anchor_payload_sha256) == 64
    assert len(verified.bundle_sha256) == 64


def test_verify_rejects_payload_mismatch_before_cosign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor_path = tmp_path / "anchors" / "market.anchor.json"
    anchor_path.parent.mkdir(parents=True)
    anchor_path.write_text(
        json.dumps(
            {
                **build_evidence_anchor_payload(manifest()),
                "evidence_fingerprint_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    bundle_path = tmp_path / "anchors" / "market.sigstore.json"
    bundle_path.write_text("{}\n", encoding="utf-8")

    def fail_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("cosign must not run for mismatched evidence")

    monkeypatch.setattr(subprocess, "run", fail_run)

    with pytest.raises(ValueError, match="does not match recording manifest"):
        verify_evidence_anchor(
            manifest(),
            anchor_path,
            bundle_path,
            certificate_identity=IDENTITY,
            certificate_oidc_issuer=ISSUER,
        )


def test_replay_binds_verified_evidence_anchor_into_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor_path = tmp_path / "anchors" / "market.anchor.json"
    bundle_path = tmp_path / "anchors" / "market.sigstore.json"
    prepare_evidence_anchor(RECORDING, anchor_path)
    bundle_path.write_text('{"mediaType":"sigstore-bundle"}\n', encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", fake_cosign_success)

    report = analyze_gateway_recording(
        EVENT,
        RECORDING,
        evidence_anchor_payload=anchor_path,
        evidence_anchor_bundle=bundle_path,
        evidence_certificate_identity=IDENTITY,
        evidence_certificate_oidc_issuer=ISSUER,
    )

    external = report["recording_manifest"]["external_anchor"]
    assert report["verdict"] == "confirmed"
    assert external["evidence_fingerprint_sha256"] == manifest().ledger_head_hash
    assert external["certificate_identity"] == IDENTITY
    assert external["signed_timestamp_verified"] is True


def test_replay_requires_complete_evidence_anchor_arguments(tmp_path: Path) -> None:
    anchor_path = tmp_path / "anchors" / "market.anchor.json"
    prepare_evidence_anchor(RECORDING, anchor_path)

    with pytest.raises(ValueError, match="evidence anchoring requires"):
        analyze_gateway_recording(
            EVENT,
            RECORDING,
            evidence_anchor_payload=anchor_path,
        )
