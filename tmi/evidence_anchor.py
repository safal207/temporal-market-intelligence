"""Privacy-minimized Sigstore anchoring for verified market-evidence ledgers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from tmi.adapters import RecordedSmartMarketDataGateway
from tmi.receipt import RecordingManifest, build_recording_manifest

EVIDENCE_ANCHOR_SCHEMA_VERSION = "1.0"
EVIDENCE_ANCHOR_TYPE = "tmi_market_evidence_ledger"
EVIDENCE_ANCHOR_ALGORITHM = "sha256"
EVIDENCE_VERIFIER = "cosign verify-blob"


@dataclass(frozen=True, slots=True)
class EvidenceAnchorVerification:
    """Safe metadata returned after verifying a ledger anchor with Cosign."""

    evidence_fingerprint_sha256: str
    fingerprint_kind: str
    anchor_payload_sha256: str
    bundle_sha256: str
    certificate_identity: str
    certificate_oidc_issuer: str
    transparency_log_verified: bool = True
    signed_timestamp_verified: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "anchor_schema_version": EVIDENCE_ANCHOR_SCHEMA_VERSION,
            "anchor_type": EVIDENCE_ANCHOR_TYPE,
            "anchor_algorithm": EVIDENCE_ANCHOR_ALGORITHM,
            "evidence_fingerprint_sha256": self.evidence_fingerprint_sha256,
            "fingerprint_kind": self.fingerprint_kind,
            "anchor_payload_sha256": self.anchor_payload_sha256,
            "bundle_sha256": self.bundle_sha256,
            "certificate_identity": self.certificate_identity,
            "certificate_oidc_issuer": self.certificate_oidc_issuer,
            "verification_tool": EVIDENCE_VERIFIER,
            "transparency_log_verified": self.transparency_log_verified,
            "signed_timestamp_verified": self.signed_timestamp_verified,
        }


def build_evidence_anchor_payload(manifest: RecordingManifest) -> dict[str, str]:
    """Build a minimal payload for one verified ledger head."""

    if not manifest.ledger_verified:
        raise ValueError("external evidence anchoring requires a verified ledger")
    if manifest.fingerprint_kind != "ledger_head":
        raise ValueError("external evidence anchoring requires fingerprint_kind=ledger_head")
    if manifest.ledger_head_hash is None:
        raise ValueError("verified ledger is missing ledger_head_hash")
    fingerprint = _sha256(
        manifest.evidence_fingerprint_sha256,
        "evidence_fingerprint_sha256",
    )
    if fingerprint != manifest.ledger_head_hash:
        raise ValueError("evidence fingerprint does not match ledger head")
    return {
        "anchor_schema_version": EVIDENCE_ANCHOR_SCHEMA_VERSION,
        "anchor_type": EVIDENCE_ANCHOR_TYPE,
        "anchor_algorithm": EVIDENCE_ANCHOR_ALGORITHM,
        "fingerprint_kind": "ledger_head",
        "evidence_fingerprint_sha256": fingerprint,
    }


def prepare_evidence_anchor(recording: Path, output: Path) -> dict[str, str]:
    """Verify a recording and write a new private anchor payload."""

    gateway = RecordedSmartMarketDataGateway.from_jsonl(recording)
    manifest = build_recording_manifest(recording, verified_gateway=gateway)
    payload = build_evidence_anchor_payload(manifest)
    _write_new_json(output, payload, required_directory="anchors")
    return payload


def verify_evidence_anchor(
    manifest: RecordingManifest,
    anchor_payload: Path,
    bundle: Path,
    *,
    certificate_identity: str,
    certificate_oidc_issuer: str,
    cosign_binary: str = "cosign",
) -> EvidenceAnchorVerification:
    """Verify the payload semantics and Sigstore bundle for a manifest."""

    expected_payload = build_evidence_anchor_payload(manifest)
    identity = _required_text(certificate_identity, "certificate_identity")
    issuer = _required_text(certificate_oidc_issuer, "certificate_oidc_issuer")
    binary = _required_text(cosign_binary, "cosign_binary")

    anchor_bytes = anchor_payload.read_bytes()
    raw_anchor = _read_json_object(anchor_bytes, "evidence anchor payload")
    if dict(raw_anchor) != expected_payload:
        raise ValueError("evidence anchor payload does not match recording manifest")

    bundle_bytes = bundle.read_bytes()
    _read_json_object(bundle_bytes, "Sigstore bundle")
    command = [
        binary,
        "verify-blob",
        str(anchor_payload),
        "--bundle",
        str(bundle),
        f"--certificate-identity={identity}",
        f"--certificate-oidc-issuer={issuer}",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ValueError(f"Cosign executable not found: {binary}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        if not detail:
            detail = f"exit status {completed.returncode}"
        raise ValueError(f"cosign verify-blob failed: {detail[:400]}")

    return EvidenceAnchorVerification(
        evidence_fingerprint_sha256=expected_payload[
            "evidence_fingerprint_sha256"
        ],
        fingerprint_kind="ledger_head",
        anchor_payload_sha256=hashlib.sha256(anchor_bytes).hexdigest(),
        bundle_sha256=hashlib.sha256(bundle_bytes).hexdigest(),
        certificate_identity=identity,
        certificate_oidc_issuer=issuer,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tmi-evidence-anchor",
        description="Prepare and verify a privacy-minimized market-ledger anchor.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Create a ledger-head payload")
    prepare.add_argument("recording", type=Path)
    prepare.add_argument("--output", type=Path, required=True)

    verify = subparsers.add_parser("verify", help="Verify an evidence Sigstore bundle")
    verify.add_argument("recording", type=Path)
    verify.add_argument("--anchor-payload", type=Path, required=True)
    verify.add_argument("--bundle", type=Path, required=True)
    verify.add_argument("--certificate-identity", required=True)
    verify.add_argument("--certificate-oidc-issuer", required=True)
    verify.add_argument("--cosign-binary", default="cosign")
    verify.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            payload = prepare_evidence_anchor(args.recording, args.output)
            report: dict[str, Any] = {
                "prepared": True,
                "path": str(args.output),
                "contains_market_values": False,
                **payload,
            }
        else:
            gateway = RecordedSmartMarketDataGateway.from_jsonl(args.recording)
            manifest = build_recording_manifest(
                args.recording,
                verified_gateway=gateway,
            )
            verified = verify_evidence_anchor(
                manifest,
                args.anchor_payload,
                args.bundle,
                certificate_identity=args.certificate_identity,
                certificate_oidc_issuer=args.certificate_oidc_issuer,
                cosign_binary=args.cosign_binary,
            )
            report = {"verified": True, **verified.as_dict()}
            if args.output is not None:
                _write_new_json(
                    args.output,
                    report,
                    required_directory="anchors",
                )
                report["path"] = str(args.output)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"tmi-evidence-anchor: {exc}") from exc
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _read_json_object(raw_bytes: bytes, label: str) -> Mapping[str, Any]:
    try:
        decoded = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be UTF-8") from exc
    raw = json.loads(decoded)
    if not isinstance(raw, Mapping):
        raise ValueError(f"{label} must contain a JSON object")
    return cast(Mapping[str, Any], raw)


def _write_new_json(
    path: Path,
    payload: Mapping[str, Any],
    *,
    required_directory: str,
) -> None:
    if path.suffix.lower() != ".json":
        raise ValueError("output must use the .json suffix")
    if required_directory not in path.parts:
        raise ValueError(f"output must be inside an {required_directory}/ directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(path, flags, 0o600)
    try:
        remaining = memoryview(content)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("evidence anchor write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    os.close(descriptor)


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value.strip()


def _sha256(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
