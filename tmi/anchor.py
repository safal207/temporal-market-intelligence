"""External Sigstore anchoring for hypothesis commitment hashes.

Only a minimal payload containing the preregistration commitment digest is signed.
The hypothesis text, market evidence, and analytical result are never included.
"""

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

ANCHOR_SCHEMA_VERSION = "1.0"
ANCHOR_TYPE = "tmi_hypothesis_commitment"
ANCHOR_ALGORITHM = "sha256"
VERIFIER = "cosign verify-blob"


@dataclass(frozen=True, slots=True)
class SigstoreAnchorVerification:
    """Safe metadata produced after Cosign verifies a Sigstore bundle."""

    commitment_hash_sha256: str
    anchor_payload_sha256: str
    bundle_sha256: str
    certificate_identity: str
    certificate_oidc_issuer: str
    transparency_log_verified: bool = True
    signed_timestamp_verified: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "anchor_schema_version": ANCHOR_SCHEMA_VERSION,
            "anchor_type": ANCHOR_TYPE,
            "anchor_algorithm": ANCHOR_ALGORITHM,
            "commitment_hash_sha256": self.commitment_hash_sha256,
            "anchor_payload_sha256": self.anchor_payload_sha256,
            "bundle_sha256": self.bundle_sha256,
            "certificate_identity": self.certificate_identity,
            "certificate_oidc_issuer": self.certificate_oidc_issuer,
            "verification_tool": VERIFIER,
            "transparency_log_verified": self.transparency_log_verified,
            "signed_timestamp_verified": self.signed_timestamp_verified,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> SigstoreAnchorVerification:
        if payload.get("anchor_schema_version") != ANCHOR_SCHEMA_VERSION:
            raise ValueError("unsupported anchor_schema_version")
        if payload.get("anchor_type") != ANCHOR_TYPE:
            raise ValueError("unsupported anchor_type")
        if payload.get("anchor_algorithm") != ANCHOR_ALGORITHM:
            raise ValueError("unsupported anchor_algorithm")
        if payload.get("verification_tool") != VERIFIER:
            raise ValueError("unsupported verification_tool")
        transparency = payload.get("transparency_log_verified")
        timestamp = payload.get("signed_timestamp_verified")
        if transparency is not True or timestamp is not True:
            raise ValueError("anchor verification must prove log inclusion and signed timestamp")
        return cls(
            commitment_hash_sha256=_sha256(
                payload.get("commitment_hash_sha256"),
                "commitment_hash_sha256",
            ),
            anchor_payload_sha256=_sha256(
                payload.get("anchor_payload_sha256"),
                "anchor_payload_sha256",
            ),
            bundle_sha256=_sha256(payload.get("bundle_sha256"), "bundle_sha256"),
            certificate_identity=_required_text(
                payload.get("certificate_identity"),
                "certificate_identity",
            ),
            certificate_oidc_issuer=_required_text(
                payload.get("certificate_oidc_issuer"),
                "certificate_oidc_issuer",
            ),
        )


def build_anchor_payload(commitment_hash_sha256: str) -> dict[str, str]:
    """Create the minimal public payload committed to the hypothesis digest."""

    commitment_hash = _sha256(
        commitment_hash_sha256,
        "commitment_hash_sha256",
    )
    return {
        "anchor_schema_version": ANCHOR_SCHEMA_VERSION,
        "anchor_type": ANCHOR_TYPE,
        "anchor_algorithm": ANCHOR_ALGORITHM,
        "commitment_hash_sha256": commitment_hash,
    }


def prepare_anchor_payload(
    commitment_hash_sha256: str,
    output: Path,
) -> dict[str, str]:
    """Write a new minimal anchor payload under an anchors directory."""

    payload = build_anchor_payload(commitment_hash_sha256)
    _write_new_json(output, payload, required_directory="anchors")
    return payload


def verify_sigstore_anchor(
    commitment_hash_sha256: str,
    anchor_payload: Path,
    bundle: Path,
    *,
    certificate_identity: str,
    certificate_oidc_issuer: str,
    cosign_binary: str = "cosign",
) -> SigstoreAnchorVerification:
    """Verify payload semantics and the Cosign bundle before returning metadata."""

    expected_hash = _sha256(
        commitment_hash_sha256,
        "commitment_hash_sha256",
    )
    identity = _required_text(certificate_identity, "certificate_identity")
    issuer = _required_text(certificate_oidc_issuer, "certificate_oidc_issuer")
    binary = _required_text(cosign_binary, "cosign_binary")

    anchor_bytes = anchor_payload.read_bytes()
    raw_anchor = _read_json_object(anchor_bytes, "anchor payload")
    expected_payload = build_anchor_payload(expected_hash)
    if dict(raw_anchor) != expected_payload:
        raise ValueError("anchor payload does not match the hypothesis commitment")

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

    return SigstoreAnchorVerification(
        commitment_hash_sha256=expected_hash,
        anchor_payload_sha256=hashlib.sha256(anchor_bytes).hexdigest(),
        bundle_sha256=hashlib.sha256(bundle_bytes).hexdigest(),
        certificate_identity=identity,
        certificate_oidc_issuer=issuer,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tmi-anchor",
        description="Prepare and verify a privacy-minimized Sigstore anchor.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Create a minimal anchor payload")
    prepare.add_argument("commitment", type=Path)
    prepare.add_argument("--output", type=Path, required=True)

    verify = subparsers.add_parser("verify", help="Verify a Cosign Sigstore bundle")
    verify.add_argument("commitment", type=Path)
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
        from tmi.preregister import load_commitment

        commitment = load_commitment(args.commitment)
        if args.command == "prepare":
            payload = prepare_anchor_payload(
                commitment.commitment_hash_sha256,
                args.output,
            )
            report: dict[str, Any] = {
                "prepared": True,
                "path": str(args.output),
                "commitment_hash_sha256": payload["commitment_hash_sha256"],
                "contains_hypothesis_text": False,
            }
        else:
            verified = verify_sigstore_anchor(
                commitment.commitment_hash_sha256,
                args.anchor_payload,
                args.bundle,
                certificate_identity=args.certificate_identity,
                certificate_oidc_issuer=args.certificate_oidc_issuer,
                cosign_binary=args.cosign_binary,
            )
            report = verified.as_dict()
            report["verified"] = True
            if args.output is not None:
                _write_new_json(
                    args.output,
                    report,
                    required_directory="anchors",
                )
                report["path"] = str(args.output)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"tmi-anchor: {exc}") from exc
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
                raise OSError("anchor write made no progress")
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
