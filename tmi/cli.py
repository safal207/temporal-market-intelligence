"""Command-line interface for one-event realization analysis."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from tmi.adapters import RecordedSmartMarketDataGateway
from tmi.anchor_binding import reverify_sigstore_anchor
from tmi.evidence_anchor import EvidenceAnchorVerification, verify_evidence_anchor
from tmi.models import EventRecord, MarketSnapshot, RealizationResult
from tmi.preregister import VerifiedPreregistration, verify_optional_preregistration
from tmi.receipt import RecordingManifest, build_recording_manifest, event_fingerprint_sha256
from tmi.scoring import RealizationScorer
from tmi.service import RealizationService

REPLAY_RECEIPT_SCHEMA_VERSION = "1.0"


def _mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be a JSON object")
    return cast(Mapping[str, Any], value)


def _read_object(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("input JSON must be an object")
    return cast(dict[str, Any], raw)


def _optional_snapshot(
    payload: Mapping[str, Any],
    key: str,
) -> MarketSnapshot | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be a JSON object")
    return MarketSnapshot.from_mapping(cast(Mapping[str, Any], value))


def _event_from_payload(payload: Mapping[str, Any]) -> EventRecord:
    wrapped = payload.get("event")
    if wrapped is None:
        return EventRecord.from_mapping(payload)
    if not isinstance(wrapped, Mapping):
        raise ValueError("event must be a JSON object")
    return EventRecord.from_mapping(cast(Mapping[str, Any], wrapped))


def _report(event: EventRecord, result: RealizationResult) -> dict[str, Any]:
    return {
        "replay_receipt_schema_version": REPLAY_RECEIPT_SCHEMA_VERSION,
        "event_id": event.event_id,
        "event_fingerprint_sha256": event_fingerprint_sha256(event),
        "asset": event.reaction.asset,
        "verdict": result.verdict.value,
        "score": round(result.score, 4),
        "reasons": list(result.reasons),
        "features": {key: round(value, 6) for key, value in result.features.items()},
    }


def _attach_preregistration(
    report: dict[str, Any],
    preregistration: VerifiedPreregistration | None,
) -> None:
    if preregistration is not None:
        report["preregistration"] = preregistration.as_dict()


def _reverify_external_anchor(
    preregistration: VerifiedPreregistration | None,
    anchor_payload: Path | None,
    anchor_bundle: Path | None,
    *,
    cosign_binary: str,
) -> None:
    expected = None if preregistration is None else preregistration.external_anchor
    if expected is None:
        if anchor_payload is not None or anchor_bundle is not None:
            raise ValueError(
                "anchor files require an externally anchored preregistration"
            )
        return
    if anchor_payload is None or anchor_bundle is None:
        raise ValueError(
            "externally anchored preregistration requires --anchor-payload and "
            "--anchor-bundle"
        )
    reverify_sigstore_anchor(
        expected,
        anchor_payload,
        anchor_bundle,
        cosign_binary=cosign_binary,
    )


def _verify_optional_evidence_anchor(
    manifest: RecordingManifest,
    anchor_payload: Path | None,
    anchor_bundle: Path | None,
    certificate_identity: str | None,
    certificate_oidc_issuer: str | None,
    *,
    cosign_binary: str,
) -> EvidenceAnchorVerification | None:
    supplied = (
        anchor_payload,
        anchor_bundle,
        certificate_identity,
        certificate_oidc_issuer,
    )
    if not any(value is not None for value in supplied):
        return None
    if any(value is None for value in supplied):
        raise ValueError(
            "evidence anchoring requires --evidence-anchor-payload, "
            "--evidence-anchor-bundle, --evidence-certificate-identity, and "
            "--evidence-certificate-oidc-issuer"
        )
    if anchor_payload is None or anchor_bundle is None:
        raise ValueError("evidence anchor paths are required")
    if certificate_identity is None or certificate_oidc_issuer is None:
        raise ValueError("evidence anchor identity and issuer are required")
    return verify_evidence_anchor(
        manifest,
        anchor_payload,
        anchor_bundle,
        certificate_identity=certificate_identity,
        certificate_oidc_issuer=certificate_oidc_issuer,
        cosign_binary=cosign_binary,
    )


def analyze_file(
    path: Path,
    *,
    anchor_payload: Path | None = None,
    anchor_bundle: Path | None = None,
    cosign_binary: str = "cosign",
) -> dict[str, Any]:
    """Analyze one self-contained JSON fixture."""

    payload = _read_object(path)
    event = EventRecord.from_mapping(_mapping(payload, "event"))
    preregistration = verify_optional_preregistration(payload, event)
    _reverify_external_anchor(
        preregistration,
        anchor_payload,
        anchor_bundle,
        cosign_binary=cosign_binary,
    )
    before = MarketSnapshot.from_mapping(_mapping(payload, "before"))
    after = MarketSnapshot.from_mapping(_mapping(payload, "after"))
    baseline_volume = float(payload["baseline_volume"])

    result = RealizationScorer().evaluate(
        event,
        before,
        after,
        baseline_volume,
        pre_before=_optional_snapshot(payload, "pre_before"),
        pre_after=_optional_snapshot(payload, "pre_after"),
    )
    report = _report(event, result)
    _attach_preregistration(report, preregistration)
    return report


def analyze_gateway_recording(
    event_path: Path,
    recording_path: Path,
    *,
    anchor_payload: Path | None = None,
    anchor_bundle: Path | None = None,
    evidence_anchor_payload: Path | None = None,
    evidence_anchor_bundle: Path | None = None,
    evidence_certificate_identity: str | None = None,
    evidence_certificate_oidc_issuer: str | None = None,
    cosign_binary: str = "cosign",
) -> dict[str, Any]:
    """Evaluate an event against verified Smart Market Data Gateway JSONL."""

    payload = _read_object(event_path)
    event = _event_from_payload(payload)
    preregistration = verify_optional_preregistration(payload, event)
    _reverify_external_anchor(
        preregistration,
        anchor_payload,
        anchor_bundle,
        cosign_binary=cosign_binary,
    )
    gateway = RecordedSmartMarketDataGateway.from_jsonl(recording_path)
    manifest = build_recording_manifest(
        recording_path,
        verified_gateway=gateway,
    )
    evidence_anchor = _verify_optional_evidence_anchor(
        manifest,
        evidence_anchor_payload,
        evidence_anchor_bundle,
        evidence_certificate_identity,
        evidence_certificate_oidc_issuer,
        cosign_binary=cosign_binary,
    )
    result = RealizationService().evaluate(event, gateway)
    report = _report(event, result)
    manifest_payload = manifest.as_dict()
    if evidence_anchor is not None:
        manifest_payload["external_anchor"] = evidence_anchor.as_dict()
    report["recording_manifest"] = manifest_payload
    _attach_preregistration(report, preregistration)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tmi",
        description="Evaluate whether a timestamped event was realized in market evidence.",
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to a self-contained scenario or pre-registered event JSON file",
    )
    parser.add_argument(
        "--gateway-recording",
        type=Path,
        help="Normalized Smart Market Data Gateway JSONL recording",
    )
    parser.add_argument(
        "--receipt-output",
        type=Path,
        help="Write the complete replay receipt as a new private JSON file under recordings/",
    )
    parser.add_argument(
        "--anchor-payload",
        type=Path,
        help="Minimal Sigstore anchor payload for an externally anchored event",
    )
    parser.add_argument(
        "--anchor-bundle",
        type=Path,
        help="Sigstore bundle to reverify before scoring an externally anchored event",
    )
    parser.add_argument(
        "--evidence-anchor-payload",
        type=Path,
        help="Minimal Sigstore payload for the verified market-ledger head",
    )
    parser.add_argument(
        "--evidence-anchor-bundle",
        type=Path,
        help="Sigstore bundle for the verified market-ledger head",
    )
    parser.add_argument("--evidence-certificate-identity")
    parser.add_argument("--evidence-certificate-oidc-issuer")
    parser.add_argument(
        "--cosign-binary",
        default="cosign",
        help="Cosign executable used for anchor re-verification",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.gateway_recording is None:
            if args.receipt_output is not None:
                raise ValueError("--receipt-output requires --gateway-recording")
            if args.evidence_anchor_payload is not None:
                raise ValueError("evidence anchoring requires --gateway-recording")
            report = analyze_file(
                args.input,
                anchor_payload=args.anchor_payload,
                anchor_bundle=args.anchor_bundle,
                cosign_binary=args.cosign_binary,
            )
        else:
            report = analyze_gateway_recording(
                args.input,
                args.gateway_recording,
                anchor_payload=args.anchor_payload,
                anchor_bundle=args.anchor_bundle,
                evidence_anchor_payload=args.evidence_anchor_payload,
                evidence_anchor_bundle=args.evidence_anchor_bundle,
                evidence_certificate_identity=args.evidence_certificate_identity,
                evidence_certificate_oidc_issuer=(
                    args.evidence_certificate_oidc_issuer
                ),
                cosign_binary=args.cosign_binary,
            )
            if args.receipt_output is not None:
                _write_private_receipt(args.receipt_output, report)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"tmi: {exc}") from exc
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _write_private_receipt(path: Path, report: Mapping[str, Any]) -> None:
    if path.suffix.lower() != ".json":
        raise ValueError("replay receipt output must use the .json suffix")
    if "recordings" not in path.parts:
        raise ValueError("replay receipt output must be inside a recordings/ directory")

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(path, flags, 0o600)
    try:
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("replay receipt write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
