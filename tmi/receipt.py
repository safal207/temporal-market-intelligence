"""Safe recording manifests and deterministic replay fingerprints.

The manifest intentionally excludes prices, volumes, and derived market features. It
can identify an evidence file, describe its coverage, and prove which ledger was used
without redistributing the underlying market-data payload.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from tmi.adapters.smdg import LEDGER_FIELDS, RecordedSmartMarketDataGateway, SmartMarketQuote
from tmi.models import EventRecord

MANIFEST_SCHEMA_VERSION = "1.0"
EVENT_FINGERPRINT_ALGORITHM = "sha256"


@dataclass(frozen=True, slots=True)
class RecordingManifest:
    """Non-market-value metadata for one validated gateway recording."""

    records: int
    records_by_symbol: tuple[tuple[str, int], ...]
    symbols: tuple[str, ...]
    providers: tuple[str, ...]
    started_at: datetime
    ended_at: datetime
    duration_seconds: float
    quote_schema_versions: tuple[str, ...]
    capabilities_observed: tuple[str, ...]
    capabilities_on_every_record: tuple[str, ...]
    volume_bases: tuple[str, ...]
    depth_bases: tuple[str, ...]
    ledger_verified: bool
    ledger_head_hash: str | None
    session_ids: tuple[str, ...]
    evidence_fingerprint_sha256: str
    fingerprint_kind: str
    file_size_bytes: int

    def as_dict(self) -> dict[str, Any]:
        """Return a stable JSON-serializable representation."""

        return {
            "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "records": self.records,
            "records_by_symbol": dict(self.records_by_symbol),
            "symbols": list(self.symbols),
            "providers": list(self.providers),
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "duration_seconds": round(self.duration_seconds, 6),
            "quote_schema_versions": list(self.quote_schema_versions),
            "capabilities_observed": list(self.capabilities_observed),
            "capabilities_on_every_record": list(self.capabilities_on_every_record),
            "volume_bases": list(self.volume_bases),
            "depth_bases": list(self.depth_bases),
            "ledger_verified": self.ledger_verified,
            "ledger_head_hash": self.ledger_head_hash,
            "session_ids": list(self.session_ids),
            "evidence_fingerprint_sha256": self.evidence_fingerprint_sha256,
            "fingerprint_kind": self.fingerprint_kind,
            "file_size_bytes": self.file_size_bytes,
        }


def build_recording_manifest(
    path: Path,
    *,
    verified_gateway: RecordedSmartMarketDataGateway | None = None,
) -> RecordingManifest:
    """Validate a recording and describe it without exposing market values."""

    if verified_gateway is None:
        RecordedSmartMarketDataGateway.from_jsonl(path)

    raw_bytes = path.read_bytes()
    rows = _read_rows(raw_bytes)
    quotes = tuple(SmartMarketQuote.from_mapping(row) for row in rows)
    timestamps = tuple(quote.timestamp for quote in quotes)

    counts = Counter(quote.symbol for quote in quotes)
    symbols = tuple(sorted(counts))
    providers = tuple(sorted({quote.provider for quote in quotes if quote.provider}))
    schema_versions = tuple(sorted({quote.schema_version for quote in quotes}))

    observed_capabilities: set[str] = set()
    complete_capabilities = set(quotes[0].capabilities)
    for quote in quotes:
        observed_capabilities.update(quote.capabilities)
        complete_capabilities.intersection_update(quote.capabilities)

    volume_bases = tuple(
        sorted(
            {
                _volume_basis(quote)
                for quote in quotes
                if quote.volume_semantics is not None
            }
        )
    )
    depth_bases = tuple(
        sorted(
            {
                _depth_basis(quote)
                for quote in quotes
                if quote.depth_semantics is not None
            }
        )
    )

    ledger_presence = tuple(bool(LEDGER_FIELDS.intersection(row)) for row in rows)
    ledger_verified = bool(ledger_presence) and all(ledger_presence)
    if ledger_verified:
        head_hash = str(rows[-1]["record_hash"])
        session_ids = tuple(
            sorted({str(row["recorder_session_id"]) for row in rows})
        )
        fingerprint = head_hash
        fingerprint_kind = "ledger_head"
    else:
        head_hash = None
        session_ids = ()
        fingerprint = hashlib.sha256(raw_bytes).hexdigest()
        fingerprint_kind = "file_sha256"

    started_at = min(timestamps)
    ended_at = max(timestamps)
    return RecordingManifest(
        records=len(quotes),
        records_by_symbol=tuple(sorted(counts.items())),
        symbols=symbols,
        providers=providers,
        started_at=started_at,
        ended_at=ended_at,
        duration_seconds=(ended_at - started_at).total_seconds(),
        quote_schema_versions=schema_versions,
        capabilities_observed=tuple(sorted(observed_capabilities)),
        capabilities_on_every_record=tuple(sorted(complete_capabilities)),
        volume_bases=volume_bases,
        depth_bases=depth_bases,
        ledger_verified=ledger_verified,
        ledger_head_hash=head_hash,
        session_ids=session_ids,
        evidence_fingerprint_sha256=fingerprint,
        fingerprint_kind=fingerprint_kind,
        file_size_bytes=len(raw_bytes),
    )


def event_fingerprint_sha256(event: EventRecord) -> str:
    """Commit a replay result to the normalized pre-registered event semantics."""

    canonical = {
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
    }
    payload = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_rows(raw_bytes: bytes) -> tuple[Mapping[str, Any], ...]:
    rows: list[Mapping[str, Any]] = []
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("gateway recording must be UTF-8") from exc
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid JSON on gateway recording line {line_number}"
            ) from exc
        if not isinstance(value, Mapping):
            raise ValueError(
                f"gateway recording line {line_number} must be a JSON object"
            )
        rows.append(cast(Mapping[str, Any], value))
    if not rows:
        raise ValueError("gateway recording contains no quote events")
    return tuple(rows)


def _volume_basis(quote: SmartMarketQuote) -> str:
    semantics = quote.volume_semantics
    if semantics is None:
        raise ValueError("volume semantics are unavailable")
    window = "none" if semantics.aggregation_window_ms is None else str(
        semantics.aggregation_window_ms
    )
    currency = semantics.currency or "none"
    return ":".join((semantics.kind, semantics.unit, window, currency, semantics.origin))


def _depth_basis(quote: SmartMarketQuote) -> str:
    semantics = quote.depth_semantics
    if semantics is None:
        raise ValueError("depth semantics are unavailable")
    currency = semantics.currency or "none"
    return ":".join(
        (semantics.unit, str(semantics.levels), currency, semantics.origin)
    )
