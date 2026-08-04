"""Command-line interface for one-event realization analysis."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from tmi.adapters import RecordedSmartMarketDataGateway
from tmi.models import EventRecord, MarketSnapshot, RealizationResult
from tmi.scoring import RealizationScorer
from tmi.service import RealizationService


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
        "event_id": event.event_id,
        "asset": event.reaction.asset,
        "verdict": result.verdict.value,
        "score": round(result.score, 4),
        "reasons": list(result.reasons),
        "features": {key: round(value, 6) for key, value in result.features.items()},
    }


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
    return _report(event, result)


def analyze_gateway_recording(event_path: Path, recording_path: Path) -> dict[str, Any]:
    """Evaluate an event against normalized Smart Market Data Gateway JSONL."""

    event = _event_from_payload(_read_object(event_path))
    gateway = RecordedSmartMarketDataGateway.from_jsonl(recording_path)
    result = RealizationService().evaluate(event, gateway)
    return _report(event, result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tmi",
        description="Evaluate whether a timestamped event was realized in market evidence.",
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to a self-contained scenario or event JSON file",
    )
    parser.add_argument(
        "--gateway-recording",
        type=Path,
        help="Normalized Smart Market Data Gateway JSONL recording",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.gateway_recording is None:
            report = analyze_file(args.input)
        else:
            report = analyze_gateway_recording(args.input, args.gateway_recording)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"tmi: {exc}") from exc
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
