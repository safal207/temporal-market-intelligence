"""Command-line interface for one-event realization analysis."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from tmi.models import EventRecord, MarketSnapshot
from tmi.scoring import RealizationScorer


def _mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be a JSON object")
    return cast(Mapping[str, Any], value)


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


def analyze_file(path: Path) -> dict[str, Any]:
    """Analyze one JSON fixture and return a serializable report."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("input JSON must be an object")
    payload = cast(dict[str, Any], raw)

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
    return {
        "event_id": event.event_id,
        "asset": event.reaction.asset,
        "verdict": result.verdict.value,
        "score": round(result.score, 4),
        "reasons": list(result.reasons),
        "features": {key: round(value, 6) for key, value in result.features.items()},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tmi",
        description="Evaluate whether a timestamped event was realized in market evidence.",
    )
    parser.add_argument("input", type=Path, help="Path to a TMI event JSON file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = analyze_file(args.input)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"tmi: {exc}") from exc
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
