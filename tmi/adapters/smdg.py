"""Adapter for normalized Smart Market Data Gateway quote recordings.

The gateway currently exposes latest REST quotes and a live WebSocket stream. TMI
needs reproducible point-in-time evidence, so the first integration consumes a JSONL
recording of those normalized quote events rather than inventing a historical API.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import hmac
import json
from pathlib import Path
from statistics import fmean
from typing import Any, cast

from tmi.models import MarketSnapshot

LEDGER_VERSION = "1.0"
LEDGER_ALGORITHM = "sha256"
GENESIS_HASH = "0" * 64
PROVENANCE_SYSTEM = "smart-market-data-gateway"
PROVENANCE_COMPONENT = "websocket-jsonl-recorder"
PROVENANCE_TRANSPORT = "websocket"
LEDGER_FIELDS = frozenset(
    {
        "ledger_version",
        "ledger_algorithm",
        "ledger_index",
        "previous_record_hash",
        "record_hash",
        "recorder_session_id",
        "provenance_system",
        "provenance_component",
        "provenance_transport",
    }
)


class GatewayContractError(ValueError):
    """Raised when a gateway payload cannot be normalized safely."""


@dataclass(frozen=True, slots=True)
class SmartMarketQuote:
    """Provider-neutral quote event accepted from the market-data gateway."""

    symbol: str
    timestamp: datetime
    price: float
    volume: float = 0.0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    bid_depth: float = 0.0
    ask_depth: float = 0.0
    spread_bps: float = 0.0
    provider: str | None = None
    sequence: int | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> SmartMarketQuote:
        """Normalize a direct quote object or a ``{"data": quote}`` wrapper."""

        data = _unwrap_payload(payload)
        symbol = str(data.get("symbol", "")).strip().upper()
        if not symbol:
            raise GatewayContractError("gateway quote is missing symbol")

        timestamp = _timestamp(data)
        bid = _optional_number(data, "bid")
        ask = _optional_number(data, "ask")
        if bid is not None and ask is not None and bid > ask:
            raise GatewayContractError("gateway quote bid must not exceed ask")

        price_value = data.get("price")
        if price_value is None:
            if bid is None or ask is None:
                raise GatewayContractError("gateway quote needs price or both bid and ask")
            price = (bid + ask) / 2.0
        else:
            price = _number(price_value, "price")
        if price <= 0:
            raise GatewayContractError("gateway quote price must be greater than zero")

        spread_value = data.get("spread_bps")
        if spread_value is not None:
            spread_bps = _number(spread_value, "spread_bps")
        elif bid is not None and ask is not None:
            midpoint = (bid + ask) / 2.0
            spread_bps = ((ask - bid) / midpoint) * 10_000.0
        else:
            spread_bps = 0.0

        sequence_value = data.get("sequence")
        sequence = int(sequence_value) if sequence_value is not None else None
        provider_value = data.get("provider")
        provider = str(provider_value) if provider_value is not None else None

        return cls(
            symbol=symbol,
            timestamp=timestamp,
            price=price,
            volume=_optional_number(data, "volume") or 0.0,
            buy_volume=_optional_number(data, "buy_volume") or 0.0,
            sell_volume=_optional_number(data, "sell_volume") or 0.0,
            bid_depth=_optional_number(data, "bid_depth") or 0.0,
            ask_depth=_optional_number(data, "ask_depth") or 0.0,
            spread_bps=spread_bps,
            provider=provider,
            sequence=sequence,
        )

    def to_snapshot(self) -> MarketSnapshot:
        """Convert the gateway event into TMI's evidence model."""

        return MarketSnapshot(
            timestamp=self.timestamp,
            price=self.price,
            volume=self.volume,
            buy_volume=self.buy_volume,
            sell_volume=self.sell_volume,
            bid_depth=self.bid_depth,
            ask_depth=self.ask_depth,
            spread_bps=self.spread_bps,
        )


class RecordedSmartMarketDataGateway:
    """Point-in-time adapter backed by normalized gateway JSONL events."""

    def __init__(
        self,
        quotes: Sequence[SmartMarketQuote],
        *,
        max_distance_seconds: float = 90.0,
    ) -> None:
        if max_distance_seconds < 0:
            raise ValueError("max_distance_seconds must not be negative")
        self._max_distance_seconds = max_distance_seconds
        by_symbol: dict[str, list[SmartMarketQuote]] = {}
        for quote in quotes:
            by_symbol.setdefault(quote.symbol, []).append(quote)
        self._quotes = {
            symbol: tuple(sorted(items, key=lambda item: item.timestamp))
            for symbol, items in by_symbol.items()
        }

    @classmethod
    def from_jsonl(
        cls,
        path: Path,
        *,
        max_distance_seconds: float = 90.0,
    ) -> RecordedSmartMarketDataGateway:
        """Load normalized gateway events and verify a ledger when metadata is present."""

        rows: list[Mapping[str, Any]] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise GatewayContractError(
                    f"invalid JSON on gateway recording line {line_number}"
                ) from exc
            if not isinstance(raw, Mapping):
                raise GatewayContractError(
                    f"gateway recording line {line_number} must be a JSON object"
                )
            rows.append(cast(Mapping[str, Any], raw))

        if not rows:
            raise GatewayContractError("gateway recording contains no quote events")

        _verify_optional_evidence_ledger(rows)
        quotes = [SmartMarketQuote.from_mapping(row) for row in rows]
        return cls(quotes, max_distance_seconds=max_distance_seconds)

    def snapshot(self, asset: str, at: datetime) -> MarketSnapshot:
        """Return the nearest quote, rejecting evidence that is too far away."""

        symbol = asset.strip().upper()
        candidates = self._quotes.get(symbol)
        if not candidates:
            raise GatewayContractError(f"no gateway quotes recorded for {symbol}")
        nearest = min(candidates, key=lambda quote: abs((quote.timestamp - at).total_seconds()))
        distance = abs((nearest.timestamp - at).total_seconds())
        if distance > self._max_distance_seconds:
            raise GatewayContractError(
                f"nearest {symbol} quote is {distance:.1f}s from requested time"
            )
        return nearest.to_snapshot()

    def baseline_volume(
        self,
        asset: str,
        *,
        before: datetime,
        lookback_minutes: int,
    ) -> float:
        """Return mean comparable interval volume before an event."""

        if lookback_minutes <= 0:
            raise ValueError("lookback_minutes must be positive")
        symbol = asset.strip().upper()
        candidates = self._quotes.get(symbol)
        if not candidates:
            raise GatewayContractError(f"no gateway quotes recorded for {symbol}")
        start = before - timedelta(minutes=lookback_minutes)
        volumes = [
            quote.volume
            for quote in candidates
            if start <= quote.timestamp < before and quote.volume > 0
        ]
        if not volumes:
            raise GatewayContractError(
                f"no positive baseline volume for {symbol} in lookback window"
            )
        return fmean(volumes)


def _verify_optional_evidence_ledger(rows: Sequence[Mapping[str, Any]]) -> None:
    ledger_presence = [bool(LEDGER_FIELDS.intersection(row)) for row in rows]
    if not any(ledger_presence):
        return
    if not all(ledger_presence):
        raise GatewayContractError(
            "gateway recording mixes legacy rows with evidence-ledger rows"
        )

    expected_previous_hash = GENESIS_HASH
    for expected_index, row in enumerate(rows):
        line_number = expected_index + 1
        missing = LEDGER_FIELDS.difference(row)
        if missing:
            names = ", ".join(sorted(missing))
            raise GatewayContractError(
                f"gateway ledger line {line_number} is missing fields: {names}"
            )
        if row.get("ledger_version") != LEDGER_VERSION:
            raise GatewayContractError(
                f"gateway ledger line {line_number} has unsupported ledger_version"
            )
        if row.get("ledger_algorithm") != LEDGER_ALGORITHM:
            raise GatewayContractError(
                f"gateway ledger line {line_number} has unsupported ledger_algorithm"
            )
        if row.get("ledger_index") != expected_index:
            raise GatewayContractError(
                f"gateway ledger line {line_number} has non-contiguous ledger_index"
            )
        if row.get("provenance_system") != PROVENANCE_SYSTEM:
            raise GatewayContractError(
                f"gateway ledger line {line_number} has invalid provenance_system"
            )
        if row.get("provenance_component") != PROVENANCE_COMPONENT:
            raise GatewayContractError(
                f"gateway ledger line {line_number} has invalid provenance_component"
            )
        if row.get("provenance_transport") != PROVENANCE_TRANSPORT:
            raise GatewayContractError(
                f"gateway ledger line {line_number} has invalid provenance_transport"
            )

        session_id = row.get("recorder_session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise GatewayContractError(
                f"gateway ledger line {line_number} has invalid recorder_session_id"
            )

        previous_hash = _sha256_value(
            row.get("previous_record_hash"),
            field="previous_record_hash",
            line_number=line_number,
        )
        if not hmac.compare_digest(previous_hash, expected_previous_hash):
            raise GatewayContractError(
                f"gateway ledger line {line_number} breaks previous_record_hash linkage"
            )

        stored_hash = _sha256_value(
            row.get("record_hash"),
            field="record_hash",
            line_number=line_number,
        )
        canonical = dict(row)
        canonical.pop("record_hash", None)
        computed_hash = hashlib.sha256(
            json.dumps(
                canonical,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        if not hmac.compare_digest(stored_hash, computed_hash):
            raise GatewayContractError(
                f"gateway ledger line {line_number} has record_hash mismatch"
            )
        expected_previous_hash = stored_hash


def _sha256_value(value: object, *, field: str, line_number: int) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise GatewayContractError(
            f"gateway ledger line {line_number} has invalid {field}"
        )
    return value


def _unwrap_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    wrapped = payload.get("data")
    if wrapped is None:
        return payload
    if not isinstance(wrapped, Mapping):
        raise GatewayContractError("gateway data wrapper must contain a JSON object")
    return cast(Mapping[str, Any], wrapped)


def _timestamp(data: Mapping[str, Any]) -> datetime:
    for key in ("provider_timestamp", "timestamp", "received_at"):
        value = data.get(key)
        if value is None:
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise GatewayContractError(f"gateway {key} is not ISO-8601") from exc
        if parsed.tzinfo is None:
            raise GatewayContractError(f"gateway {key} must include a timezone")
        return parsed
    raise GatewayContractError("gateway quote is missing a timestamp")


def _optional_number(data: Mapping[str, Any], key: str) -> float | None:
    value = data.get(key)
    if value is None:
        return None
    return _number(value, key)


def _number(value: Any, key: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GatewayContractError(f"gateway {key} must be numeric") from exc
    if number < 0:
        raise GatewayContractError(f"gateway {key} must not be negative")
    return number
