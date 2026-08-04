"""Command-line interface for one-event realization analysis."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from tmi.adapters import RecordedSmartMarketDataGateway
from tmi.models import EventRecord, MarketSnapshot, RealizationResult
from tmi.preregister import verify_optional_preregistration
from tmi.receipt import build_recording_manifest, event_fingerprint_sha256
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
    payload: Mapping[str, Any],
    event: EventRecord,
) -> None:
    verified = verify_optional_preregistration(payload, event)
    if verified is not None:
        report["preregistration"] = verified.as_dict()


def analyze_file(path: Path) -> dict[str, Any]:
    """Analyze one self-contained JSON fixture."""

    payload = _read_object(path)
    event = EventRecord.from_mapping(_mapping(payload, "event"))
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
    _attach_preregistration(report, payload, event)
    return report


def analyze_gateway_recording(event_path: Path, recording_path: Path) -> dict[str, Any]:
    """Evaluate an event against verified Smart Market Data Gateway JSONL."""

    payload = _read_object(event_path)
    event = _event_from_payload(payload)
    preregistration = verify_optional_preregistration(payload, event)
    gateway = RecordedSmartMarketDataGateway.from_jsonl(recording_path)
    manifest = build_recording_manifest(
        recording_path,
        verified_gateway=gateway,
    )
    result = RealizationService().evaluate(event, gateway)
    report = _report(event, result)
    report["recording_manifest"] = manifest.as_dict()
    if preregistration is not None:
        report["preregistration"] = preregistration.as_dict()
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.gateway_recording is None:
            if args.receipt_output is not None:
                raise ValueError("--receipt-output requires --gateway-recording")
            report = analyze_file(args.input)
        else:
            report = analyze_gateway_recording(args.input, args.gateway_recording)
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
