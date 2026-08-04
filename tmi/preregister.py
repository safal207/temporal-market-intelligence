"""Tamper-evident hypothesis preregistration and event finalization.

A preregistration commits to the expected market reaction before the event is
observed. The local timestamp and SHA-256 digest are useful audit evidence, but they
are not an independent trusted timestamp; operators should anchor the commitment in
an external system when stronger proof is required.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from tmi.anchor import SigstoreAnchorVerification, verify_sigstore_anchor
from tmi.models import Direction, EventRecord, ExpectedReaction

COMMITMENT_VERSION = "1.0"
COMMITMENT_ALGORITHM = "sha256"
REGISTRATION_CLOCK = "local_system_utc"


@dataclass(frozen=True, slots=True)
class HypothesisCommitment:
    """Fields fixed before observing the event or market response."""

    registered_at: datetime
    event_id: str
    headline: str
    source: str
    reaction: ExpectedReaction
    scheduled_event_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.registered_at.tzinfo is None or self.registered_at.utcoffset() is None:
            raise ValueError("registered_at must include a timezone")
        if self.scheduled_event_at is not None:
            if (
                self.scheduled_event_at.tzinfo is None
                or self.scheduled_event_at.utcoffset() is None
            ):
                raise ValueError("scheduled_event_at must include a timezone")
            if self.scheduled_event_at < self.registered_at:
                raise ValueError("scheduled_event_at must not precede registration")
        if not self.event_id.strip():
            raise ValueError("event_id must not be empty")
        if not self.headline.strip():
            raise ValueError("headline must not be empty")
        if not self.source.strip():
            raise ValueError("source must not be empty")

    def canonical_body(self) -> dict[str, Any]:
        """Return the stable body covered by the commitment hash."""

        return {
            "commitment_version": COMMITMENT_VERSION,
            "registered_at": self.registered_at.isoformat(),
            "registration_clock": REGISTRATION_CLOCK,
            "scheduled_event_at": (
                None
                if self.scheduled_event_at is None
                else self.scheduled_event_at.isoformat()
            ),
            "hypothesis": {
                "event_id": self.event_id,
                "headline": self.headline,
                "source": self.source,
                "reaction": {
                    "asset": self.reaction.asset,
                    "direction": self.reaction.direction.value,
                    "horizon_minutes": self.reaction.horizon_minutes,
                    "minimum_move_pct": self.reaction.minimum_move_pct,
                },
            },
        }

    @property
    def commitment_hash_sha256(self) -> str:
        return hashlib.sha256(_canonical_json_bytes(self.canonical_body())).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        payload = self.canonical_body()
        payload["commitment_algorithm"] = COMMITMENT_ALGORITHM
        payload["commitment_hash_sha256"] = self.commitment_hash_sha256
        payload["external_timestamp_verified"] = False
        return payload


@dataclass(frozen=True, slots=True)
class VerifiedPreregistration:
    """Verified preregistration metadata safe to bind into a replay receipt."""

    registered_at: datetime
    commitment_hash_sha256: str
    registration_clock: str
    scheduled_event_at: datetime | None
    external_anchor: SigstoreAnchorVerification | None = None

    @property
    def external_timestamp_verified(self) -> bool:
        return self.external_anchor is not None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "verified": True,
            "commitment_version": COMMITMENT_VERSION,
            "commitment_algorithm": COMMITMENT_ALGORITHM,
            "commitment_hash_sha256": self.commitment_hash_sha256,
            "registered_at": self.registered_at.isoformat(),
            "registration_clock": self.registration_clock,
            "scheduled_event_at": (
                None
                if self.scheduled_event_at is None
                else self.scheduled_event_at.isoformat()
            ),
            "external_timestamp_verified": self.external_timestamp_verified,
        }
        if self.external_anchor is not None:
            payload["external_anchor"] = self.external_anchor.as_dict()
        return payload


def create_commitment(
    *,
    event_id: str,
    headline: str,
    source: str,
    asset: str,
    direction: Direction,
    horizon_minutes: int,
    minimum_move_pct: float,
    registered_at: datetime | None = None,
    scheduled_event_at: datetime | None = None,
) -> HypothesisCommitment:
    """Create one normalized commitment using the current UTC clock by default."""

    return HypothesisCommitment(
        registered_at=registered_at or datetime.now(UTC),
        event_id=event_id,
        headline=headline,
        source=source,
        reaction=ExpectedReaction(
            asset=asset,
            direction=direction,
            horizon_minutes=horizon_minutes,
            minimum_move_pct=minimum_move_pct,
        ),
        scheduled_event_at=scheduled_event_at,
    )


def load_commitment(path: Path) -> HypothesisCommitment:
    """Load and cryptographically verify a commitment file."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("commitment file must contain a JSON object")
    payload = cast(Mapping[str, Any], raw)
    commitment = _commitment_from_mapping(payload)

    if payload.get("commitment_version") != COMMITMENT_VERSION:
        raise ValueError("unsupported commitment_version")
    if payload.get("commitment_algorithm") != COMMITMENT_ALGORITHM:
        raise ValueError("unsupported commitment_algorithm")
    stored_hash = _sha256(payload.get("commitment_hash_sha256"), "commitment_hash_sha256")
    if not hmac.compare_digest(stored_hash, commitment.commitment_hash_sha256):
        raise ValueError("commitment_hash_sha256 mismatch")
    return commitment


def finalize_commitment(
    commitment: HypothesisCommitment,
    *,
    occurred_at: datetime,
    published_at: datetime,
    source_confidence: float,
    anchor_verification: SigstoreAnchorVerification | None = None,
) -> dict[str, Any]:
    """Bind observed event timestamps to a previously fixed hypothesis."""

    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise ValueError("occurred_at must include a timezone")
    if published_at.tzinfo is None or published_at.utcoffset() is None:
        raise ValueError("published_at must include a timezone")
    if occurred_at < commitment.registered_at:
        raise ValueError("occurred_at must not precede preregistration")
    if (
        anchor_verification is not None
        and not hmac.compare_digest(
            anchor_verification.commitment_hash_sha256,
            commitment.commitment_hash_sha256,
        )
    ):
        raise ValueError("Sigstore anchor does not match hypothesis commitment")

    event = EventRecord(
        event_id=commitment.event_id,
        headline=commitment.headline,
        source=commitment.source,
        occurred_at=occurred_at,
        published_at=published_at,
        source_confidence=source_confidence,
        reaction=commitment.reaction,
    )
    preregistration: dict[str, Any] = {
        "commitment_version": COMMITMENT_VERSION,
        "commitment_algorithm": COMMITMENT_ALGORITHM,
        "commitment_hash_sha256": commitment.commitment_hash_sha256,
        "registered_at": commitment.registered_at.isoformat(),
        "registration_clock": REGISTRATION_CLOCK,
        "scheduled_event_at": (
            None
            if commitment.scheduled_event_at is None
            else commitment.scheduled_event_at.isoformat()
        ),
        "external_timestamp_verified": anchor_verification is not None,
    }
    if anchor_verification is not None:
        preregistration["external_anchor"] = anchor_verification.as_dict()

    return {
        "event": {
            "event_id": event.event_id,
            "headline": event.headline,
            "source": event.source,
            "occurred_at": event.occurred_at.isoformat(),
            "published_at": event.published_at.isoformat(),
            "source_confidence": event.source_confidence,
            "reaction": {
                "asset": event.reaction.asset,
                "direction": event.reaction.direction.value,
                "horizon_minutes": event.reaction.horizon_minutes,
                "minimum_move_pct": event.reaction.minimum_move_pct,
            },
        },
        "preregistration": preregistration,
    }


def verify_optional_preregistration(
    payload: Mapping[str, Any],
    event: EventRecord,
) -> VerifiedPreregistration | None:
    """Verify preregistration metadata against the finalized event, if present."""

    raw = payload.get("preregistration")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("preregistration must be a JSON object")
    metadata = cast(Mapping[str, Any], raw)

    if metadata.get("commitment_version") != COMMITMENT_VERSION:
        raise ValueError("unsupported preregistration commitment_version")
    if metadata.get("commitment_algorithm") != COMMITMENT_ALGORITHM:
        raise ValueError("unsupported preregistration commitment_algorithm")
    if metadata.get("registration_clock") != REGISTRATION_CLOCK:
        raise ValueError("unsupported preregistration registration_clock")

    registered_at = _datetime(metadata.get("registered_at"), "registered_at")
    scheduled_value = metadata.get("scheduled_event_at")
    scheduled_at = (
        None
        if scheduled_value is None
        else _datetime(scheduled_value, "scheduled_event_at")
    )
    commitment = HypothesisCommitment(
        registered_at=registered_at,
        event_id=event.event_id,
        headline=event.headline,
        source=event.source,
        reaction=event.reaction,
        scheduled_event_at=scheduled_at,
    )
    stored_hash = _sha256(
        metadata.get("commitment_hash_sha256"),
        "commitment_hash_sha256",
    )
    if not hmac.compare_digest(stored_hash, commitment.commitment_hash_sha256):
        raise ValueError("finalized event does not match preregistration commitment")
    if event.occurred_at < registered_at:
        raise ValueError("event occurred before preregistration")

    declared_external = metadata.get("external_timestamp_verified", False)
    if not isinstance(declared_external, bool):
        raise ValueError("external_timestamp_verified must be boolean")
    raw_anchor = metadata.get("external_anchor")
    if raw_anchor is None:
        if declared_external:
            raise ValueError(
                "external_timestamp_verified requires verified external_anchor metadata"
            )
        external_anchor = None
    else:
        if not isinstance(raw_anchor, Mapping):
            raise ValueError("external_anchor must be a JSON object")
        if not declared_external:
            raise ValueError("external_anchor requires external_timestamp_verified=true")
        external_anchor = SigstoreAnchorVerification.from_mapping(
            cast(Mapping[str, Any], raw_anchor)
        )
        if not hmac.compare_digest(
            external_anchor.commitment_hash_sha256,
            stored_hash,
        ):
            raise ValueError("external_anchor does not match preregistration commitment")

    return VerifiedPreregistration(
        registered_at=registered_at,
        commitment_hash_sha256=stored_hash,
        registration_clock=REGISTRATION_CLOCK,
        scheduled_event_at=scheduled_at,
        external_anchor=external_anchor,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tmi-preregister",
        description="Commit a market hypothesis before observation and finalize it later.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create a new hypothesis commitment")
    create.add_argument("--event-id", required=True)
    create.add_argument("--headline", required=True)
    create.add_argument("--source", required=True)
    create.add_argument("--asset", required=True)
    create.add_argument("--direction", choices=[item.value for item in Direction], required=True)
    create.add_argument("--horizon-minutes", type=int, required=True)
    create.add_argument("--minimum-move-pct", type=float, default=0.5)
    create.add_argument("--scheduled-event-at", type=_parse_datetime)
    create.add_argument("--output", type=Path, required=True)

    finalize = subparsers.add_parser("finalize", help="Finalize a committed event")
    finalize.add_argument("commitment", type=Path)
    finalize.add_argument("--occurred-at", type=_parse_datetime, required=True)
    finalize.add_argument("--published-at", type=_parse_datetime, required=True)
    finalize.add_argument("--source-confidence", type=float, default=1.0)
    finalize.add_argument("--anchor-payload", type=Path)
    finalize.add_argument("--anchor-bundle", type=Path)
    finalize.add_argument("--certificate-identity")
    finalize.add_argument("--certificate-oidc-issuer")
    finalize.add_argument("--cosign-binary", default="cosign")
    finalize.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            commitment = create_commitment(
                event_id=args.event_id,
                headline=args.headline,
                source=args.source,
                asset=args.asset,
                direction=Direction(args.direction),
                horizon_minutes=args.horizon_minutes,
                minimum_move_pct=args.minimum_move_pct,
                scheduled_event_at=args.scheduled_event_at,
            )
            _write_new_json(args.output, commitment.as_dict())
            report = {
                "created": True,
                "path": str(args.output),
                "commitment_hash_sha256": commitment.commitment_hash_sha256,
                "registered_at": commitment.registered_at.isoformat(),
                "external_timestamp_verified": False,
            }
        else:
            commitment = load_commitment(args.commitment)
            anchor_verification = _anchor_verification_from_args(args, commitment)
            finalized = finalize_commitment(
                commitment,
                occurred_at=args.occurred_at,
                published_at=args.published_at,
                source_confidence=args.source_confidence,
                anchor_verification=anchor_verification,
            )
            _write_new_json(args.output, finalized)
            report = {
                "finalized": True,
                "path": str(args.output),
                "commitment_hash_sha256": commitment.commitment_hash_sha256,
                "registered_at": commitment.registered_at.isoformat(),
                "external_timestamp_verified": anchor_verification is not None,
            }
            if anchor_verification is not None:
                report["external_anchor"] = anchor_verification.as_dict()
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"tmi-preregister: {exc}") from exc
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _anchor_verification_from_args(
    args: argparse.Namespace,
    commitment: HypothesisCommitment,
) -> SigstoreAnchorVerification | None:
    required = (
        args.anchor_payload,
        args.anchor_bundle,
        args.certificate_identity,
        args.certificate_oidc_issuer,
    )
    if not any(value is not None for value in required):
        return None
    if not all(value is not None for value in required):
        raise ValueError(
            "anchored finalization requires --anchor-payload, --anchor-bundle, "
            "--certificate-identity, and --certificate-oidc-issuer"
        )
    return verify_sigstore_anchor(
        commitment.commitment_hash_sha256,
        args.anchor_payload,
        args.anchor_bundle,
        certificate_identity=args.certificate_identity,
        certificate_oidc_issuer=args.certificate_oidc_issuer,
        cosign_binary=args.cosign_binary,
    )


def _commitment_from_mapping(payload: Mapping[str, Any]) -> HypothesisCommitment:
    hypothesis = payload.get("hypothesis")
    if not isinstance(hypothesis, Mapping):
        raise ValueError("commitment hypothesis must be a JSON object")
    reaction = hypothesis.get("reaction")
    if not isinstance(reaction, Mapping):
        raise ValueError("commitment reaction must be a JSON object")

    scheduled_value = payload.get("scheduled_event_at")
    return HypothesisCommitment(
        registered_at=_datetime(payload.get("registered_at"), "registered_at"),
        event_id=str(hypothesis["event_id"]),
        headline=str(hypothesis["headline"]),
        source=str(hypothesis["source"]),
        reaction=ExpectedReaction.from_mapping(cast(Mapping[str, Any], reaction)),
        scheduled_event_at=(
            None
            if scheduled_value is None
            else _datetime(scheduled_value, "scheduled_event_at")
        ),
    )


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _write_new_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.suffix.lower() != ".json":
        raise ValueError("output must use the .json suffix")
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
                raise OSError("preregistration write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    os.close(descriptor)


def _parse_datetime(value: str) -> datetime:
    return _datetime(value, "timestamp")


def _datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


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
