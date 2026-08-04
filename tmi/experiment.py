"""Fail-closed operator plans for a real preregistered market experiment."""

from __future__ import annotations

import argparse
import json
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from tmi.models import Direction

EXPERIMENT_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class ExperimentPaths:
    commitment: Path
    hypothesis_anchor_payload: Path
    hypothesis_anchor_bundle: Path
    event: Path
    recording: Path
    evidence_anchor_payload: Path
    evidence_anchor_bundle: Path
    receipt: Path


@dataclass(frozen=True, slots=True)
class ExperimentPlan:
    experiment_id: str
    headline: str
    source: str
    official_source_url: str
    asset: str
    symbol: str
    direction: Direction
    horizon_minutes: int
    minimum_move_pct: float
    scheduled_event_at: datetime
    preregistration_deadline: datetime
    capture_start_at: datetime
    capture_end_at: datetime
    paths: ExperimentPaths

    def __post_init__(self) -> None:
        for field_name, value in (
            ("scheduled_event_at", self.scheduled_event_at),
            ("preregistration_deadline", self.preregistration_deadline),
            ("capture_start_at", self.capture_start_at),
            ("capture_end_at", self.capture_end_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must include a timezone")
        if not self.experiment_id.strip():
            raise ValueError("experiment_id must not be empty")
        if not self.headline.strip() or not self.source.strip():
            raise ValueError("headline and source must not be empty")
        if not self.official_source_url.startswith("https://"):
            raise ValueError("official_source_url must use https")
        if not self.asset.strip() or not self.symbol.strip():
            raise ValueError("asset and symbol must not be empty")
        if self.horizon_minutes <= 0:
            raise ValueError("horizon_minutes must be positive")
        if self.minimum_move_pct <= 0:
            raise ValueError("minimum_move_pct must be positive")
        if self.preregistration_deadline >= self.capture_start_at:
            raise ValueError("preregistration_deadline must precede capture_start_at")
        if not (
            self.capture_start_at
            < self.scheduled_event_at
            < self.capture_end_at
        ):
            raise ValueError("capture window must contain scheduled_event_at")
        if (self.capture_end_at - self.capture_start_at).total_seconds() > 7200:
            raise ValueError("capture window must not exceed two hours")
        _validate_paths(self.paths)

    @property
    def capture_seconds(self) -> int:
        return int((self.capture_end_at - self.capture_start_at).total_seconds())


def load_experiment_plan(path: Path) -> ExperimentPlan:
    """Load and validate one operator experiment plan."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("experiment plan must contain a JSON object")
    payload = cast(Mapping[str, Any], raw)
    if payload.get("experiment_schema_version") != EXPERIMENT_SCHEMA_VERSION:
        raise ValueError("unsupported experiment_schema_version")
    event = _mapping(payload, "event")
    capture = _mapping(payload, "capture")
    paths = _mapping(payload, "paths")
    return ExperimentPlan(
        experiment_id=_text(payload.get("experiment_id"), "experiment_id"),
        headline=_text(event.get("headline"), "event.headline"),
        source=_text(event.get("source"), "event.source"),
        official_source_url=_text(
            event.get("official_source_url"),
            "event.official_source_url",
        ),
        asset=_text(event.get("asset"), "event.asset"),
        symbol=_text(capture.get("symbol"), "capture.symbol"),
        direction=Direction(_text(event.get("direction"), "event.direction")),
        horizon_minutes=_positive_int(
            event.get("horizon_minutes"),
            "event.horizon_minutes",
        ),
        minimum_move_pct=_positive_float(
            event.get("minimum_move_pct"),
            "event.minimum_move_pct",
        ),
        scheduled_event_at=_datetime(
            event.get("scheduled_event_at"),
            "event.scheduled_event_at",
        ),
        preregistration_deadline=_datetime(
            payload.get("preregistration_deadline"),
            "preregistration_deadline",
        ),
        capture_start_at=_datetime(
            capture.get("start_at"),
            "capture.start_at",
        ),
        capture_end_at=_datetime(capture.get("end_at"), "capture.end_at"),
        paths=ExperimentPaths(
            commitment=Path(_text(paths.get("commitment"), "paths.commitment")),
            hypothesis_anchor_payload=Path(
                _text(
                    paths.get("hypothesis_anchor_payload"),
                    "paths.hypothesis_anchor_payload",
                )
            ),
            hypothesis_anchor_bundle=Path(
                _text(
                    paths.get("hypothesis_anchor_bundle"),
                    "paths.hypothesis_anchor_bundle",
                )
            ),
            event=Path(_text(paths.get("event"), "paths.event")),
            recording=Path(_text(paths.get("recording"), "paths.recording")),
            evidence_anchor_payload=Path(
                _text(
                    paths.get("evidence_anchor_payload"),
                    "paths.evidence_anchor_payload",
                )
            ),
            evidence_anchor_bundle=Path(
                _text(
                    paths.get("evidence_anchor_bundle"),
                    "paths.evidence_anchor_bundle",
                )
            ),
            receipt=Path(_text(paths.get("receipt"), "paths.receipt")),
        ),
    )


def build_preflight_report(
    plan: ExperimentPlan,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate timing and return exact operator actions without side effects."""

    observed_now = now or datetime.now(UTC)
    if observed_now.tzinfo is None or observed_now.utcoffset() is None:
        raise ValueError("now must include a timezone")
    if observed_now >= plan.preregistration_deadline:
        raise ValueError("preregistration deadline has passed")
    if observed_now >= plan.capture_start_at:
        raise ValueError("capture window has already started")

    commands = _operator_commands(plan)
    return {
        "experiment_schema_version": EXPERIMENT_SCHEMA_VERSION,
        "experiment_id": plan.experiment_id,
        "status": "ready_for_preregistration",
        "test_hypothesis_not_investment_advice": True,
        "official_source_url": plan.official_source_url,
        "scheduled_event_at": plan.scheduled_event_at.isoformat(),
        "preregistration_deadline": plan.preregistration_deadline.isoformat(),
        "capture_start_at": plan.capture_start_at.isoformat(),
        "capture_end_at": plan.capture_end_at.isoformat(),
        "capture_seconds": plan.capture_seconds,
        "seconds_until_preregistration_deadline": int(
            (plan.preregistration_deadline - observed_now).total_seconds()
        ),
        "seconds_until_capture_start": int(
            (plan.capture_start_at - observed_now).total_seconds()
        ),
        "hypothesis": {
            "asset": plan.asset,
            "direction": plan.direction.value,
            "horizon_minutes": plan.horizon_minutes,
            "minimum_move_pct": plan.minimum_move_pct,
        },
        "operator_actions": [
            {
                "order": index,
                "name": name,
                "run_at": run_at,
                "command": command,
            }
            for index, (name, run_at, command) in enumerate(commands, 1)
        ],
        "manual_inputs": [
            "TMI_CERT_IDENTITY",
            "TMI_CERT_OIDC_ISSUER",
            "actual occurred_at UTC after the official release",
            "actual published_at UTC after the official release",
        ],
        "success_criteria": [
            "hypothesis commitment is anchored before the deadline",
            "Coinbase ledger covers the complete capture window",
            "ledger head is anchored after capture",
            "both Sigstore bundles verify during replay",
            "private replay receipt is created without overwriting files",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tmi-experiment",
        description="Validate a real experiment plan and print exact operator steps.",
    )
    parser.add_argument("plan", type=Path)
    parser.add_argument(
        "--now",
        type=_parse_datetime,
        help="Override the current time for deterministic validation",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = load_experiment_plan(args.plan)
        report = build_preflight_report(plan, now=args.now)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"tmi-experiment: {exc}") from exc
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _operator_commands(plan: ExperimentPlan) -> tuple[tuple[str, str, str], ...]:
    paths = plan.paths
    identity = "${TMI_CERT_IDENTITY}"
    issuer = "${TMI_CERT_OIDC_ISSUER}"
    create_commitment = shlex.join(
        [
            "python",
            "-m",
            "tmi.preregister",
            "create",
            "--event-id",
            plan.experiment_id,
            "--headline",
            plan.headline,
            "--source",
            plan.source,
            "--asset",
            plan.asset,
            "--direction",
            plan.direction.value,
            "--horizon-minutes",
            str(plan.horizon_minutes),
            "--minimum-move-pct",
            str(plan.minimum_move_pct),
            "--scheduled-event-at",
            plan.scheduled_event_at.isoformat(),
            "--output",
            str(paths.commitment),
        ]
    )
    prepare_hypothesis_anchor = shlex.join(
        [
            "python",
            "-m",
            "tmi.anchor",
            "prepare",
            str(paths.commitment),
            "--output",
            str(paths.hypothesis_anchor_payload),
        ]
    )
    sign_hypothesis = shlex.join(
        [
            "cosign",
            "sign-blob",
            str(paths.hypothesis_anchor_payload),
            "--bundle",
            str(paths.hypothesis_anchor_bundle),
        ]
    )
    capture = shlex.join(
        [
            "python",
            "-m",
            "smart_market_data_gateway.research_capture",
            "--symbol",
            plan.symbol,
            "--output",
            str(paths.recording),
            "--max-records",
            "20000",
            "--max-seconds",
            str(plan.capture_seconds),
            "--accept-current-market-data-terms",
        ]
    )
    prepare_evidence_anchor = shlex.join(
        [
            "python",
            "-m",
            "tmi.evidence_anchor",
            "prepare",
            str(paths.recording),
            "--output",
            str(paths.evidence_anchor_payload),
        ]
    )
    sign_evidence = shlex.join(
        [
            "cosign",
            "sign-blob",
            str(paths.evidence_anchor_payload),
            "--bundle",
            str(paths.evidence_anchor_bundle),
        ]
    )
    finalize = shlex.join(
        [
            "python",
            "-m",
            "tmi.preregister",
            "finalize",
            str(paths.commitment),
            "--occurred-at",
            "<ACTUAL_OCCURRED_AT_UTC>",
            "--published-at",
            "<ACTUAL_PUBLISHED_AT_UTC>",
            "--source-confidence",
            "1.0",
            "--anchor-payload",
            str(paths.hypothesis_anchor_payload),
            "--anchor-bundle",
            str(paths.hypothesis_anchor_bundle),
            "--certificate-identity",
            identity,
            "--certificate-oidc-issuer",
            issuer,
            "--output",
            str(paths.event),
        ]
    )
    replay = shlex.join(
        [
            "python",
            "-m",
            "tmi",
            str(paths.event),
            "--gateway-recording",
            str(paths.recording),
            "--anchor-payload",
            str(paths.hypothesis_anchor_payload),
            "--anchor-bundle",
            str(paths.hypothesis_anchor_bundle),
            "--evidence-anchor-payload",
            str(paths.evidence_anchor_payload),
            "--evidence-anchor-bundle",
            str(paths.evidence_anchor_bundle),
            "--evidence-certificate-identity",
            identity,
            "--evidence-certificate-oidc-issuer",
            issuer,
            "--receipt-output",
            str(paths.receipt),
        ]
    )
    return (
        ("create hypothesis commitment", "now", create_commitment),
        ("prepare hypothesis anchor", "now", prepare_hypothesis_anchor),
        ("sign hypothesis anchor", "before preregistration deadline", sign_hypothesis),
        ("start Coinbase capture", plan.capture_start_at.isoformat(), capture),
        ("prepare evidence anchor", plan.capture_end_at.isoformat(), prepare_evidence_anchor),
        ("sign evidence anchor", "immediately after capture", sign_evidence),
        ("finalize official event", "after official publication", finalize),
        ("run deterministic replay", "after both anchors verify", replay),
    )


def _validate_paths(paths: ExperimentPaths) -> None:
    expected = (
        (paths.commitment, "events", ".json"),
        (paths.hypothesis_anchor_payload, "anchors", ".json"),
        (paths.hypothesis_anchor_bundle, "anchors", ".json"),
        (paths.event, "events", ".json"),
        (paths.recording, "recordings", ".jsonl"),
        (paths.evidence_anchor_payload, "anchors", ".json"),
        (paths.evidence_anchor_bundle, "anchors", ".json"),
        (paths.receipt, "recordings", ".json"),
    )
    values: list[Path] = []
    for path, directory, suffix in expected:
        if directory not in path.parts:
            raise ValueError(f"{path} must be inside {directory}/")
        if path.suffix.lower() != suffix:
            raise ValueError(f"{path} must use the {suffix} suffix")
        if path.is_absolute():
            raise ValueError("experiment paths must be repository-relative")
        values.append(path)
    if len(set(values)) != len(values):
        raise ValueError("experiment output paths must be unique")


def _mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be a JSON object")
    return cast(Mapping[str, Any], value)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value.strip()


def _positive_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _positive_float(value: object, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    parsed = float(value)
    if parsed <= 0:
        raise ValueError(f"{field} must be positive")
    return parsed


def _parse_datetime(value: str) -> datetime:
    return _datetime(value, "timestamp")


def _datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
