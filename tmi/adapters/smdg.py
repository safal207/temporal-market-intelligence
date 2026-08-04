"""Adapter for normalized Smart Market Data Gateway quote recordings.

TMI consumes reproducible point-in-time JSONL evidence rather than inventing a
historical API. Ledger integrity and rich evidence semantics are verified before any
market feature or realization verdict is calculated.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
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
LEVEL1_QUOTE = "level1_quote"
VOLUME = "volume"
AGGRESSOR_FLOW = "aggressor_flow"
TRADE_COUNT = "trade_count"
TOP_OF_BOOK_DEPTH = "top_of_book_depth"
KNOWN_CAPABILITIES = frozenset(
    {LEVEL1_QUOTE, VOLUME, AGGRESSOR_FLOW, TRADE_COUNT, TOP_OF_BOOK_DEPTH}
)
KNOWN_ORIGINS = frozenset({"native", "provider_aggregated", "gateway_derived"})
KNOWN_QUANTITY_UNITS = frozenset({"base_asset", "quote_notional", "contracts"})
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
class VolumeSemantics:
    """Comparable meaning of one volume-like observation."""

    kind: str
    unit: str
    aggregation_window_ms: int | None
    currency: str | None
    origin: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> VolumeSemantics:
        kind = str(data.get("kind", ""))
        unit = str(data.get("unit", ""))
        origin = str(data.get("origin", ""))
        if kind not in {"interval", "cumulative"}:
            raise GatewayContractError("gateway volume_semantics kind is unsupported")
        if unit not in KNOWN_QUANTITY_UNITS:
            raise GatewayContractError("gateway volume_semantics unit is unsupported")
        if origin not in KNOWN_ORIGINS:
            raise GatewayContractError("gateway volume_semantics origin is unsupported")

        window_value = data.get("aggregation_window_ms")
        window = None if window_value is None else _positive_integer(window_value, "aggregation_window_ms")
        currency_value = data.get("currency")
        currency = None if currency_value is None else str(currency_value).strip().upper()

        if kind == "interval" and window is None:
            raise GatewayContractError("interval volume requires aggregation_window_ms")
        if kind == "cumulative" and window is not None:
            raise GatewayContractError("cumulative volume must not declare aggregation_window_ms")
        if unit == "quote_notional" and not currency:
            raise GatewayContractError("quote_notional volume requires currency")
        if unit != "quote_notional" and currency is not None:
            raise GatewayContractError("currency is only valid for quote_notional volume")

        return cls(
            kind=kind,
            unit=unit,
            aggregation_window_ms=window,
            currency=currency,
            origin=origin,
        )

    @property
    def comparison_key(self) -> tuple[str, str, int | None, str | None]:
        return (self.kind, self.unit, self.aggregation_window_ms, self.currency)


@dataclass(frozen=True, slots=True)
class DepthSemantics:
    """Meaning of one paired bid/ask depth observation."""

    unit: str
    levels: int
    currency: str | None
    origin: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> DepthSemantics:
        unit = str(data.get("unit", ""))
        origin = str(data.get("origin", ""))
        if unit not in KNOWN_QUANTITY_UNITS:
            raise GatewayContractError("gateway depth_semantics unit is unsupported")
        if origin not in KNOWN_ORIGINS:
            raise GatewayContractError("gateway depth_semantics origin is unsupported")
        levels = _positive_integer(data.get("levels", 1), "levels")
        if levels != 1:
            raise GatewayContractError("TMI supports exactly one top-of-book depth level")
        currency_value = data.get("currency")
        currency = None if currency_value is None else str(currency_value).strip().upper()
        if unit == "quote_notional" and not currency:
            raise GatewayContractError("quote_notional depth requires currency")
        if unit != "quote_notional" and currency is not None:
            raise GatewayContractError("currency is only valid for quote_notional depth")
        return cls(unit=unit, levels=levels, currency=currency, origin=origin)


@dataclass(frozen=True, slots=True)
class SmartMarketQuote:
    """Provider-neutral quote event accepted from the market-data gateway."""

    symbol: str
    timestamp: datetime
    price: float
    schema_version: str = "1.0"
    capabilities: frozenset[str] = frozenset({LEVEL1_QUOTE})
    volume: float | None = None
    buy_volume: float | None = None
    sell_volume: float | None = None
    trade_count: int | None = None
    bid_depth: float | None = None
    ask_depth: float | None = None
    spread_bps: float | None = None
    volume_semantics: VolumeSemantics | None = None
    depth_semantics: DepthSemantics | None = None
    provider: str | None = None
    sequence: int | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> SmartMarketQuote:
        """Normalize a direct quote, a ``data`` wrapper, or live ``data.quote``."""

        data = _unwrap_payload(payload)
        symbol = str(data.get("symbol", "")).strip().upper()
        if not symbol:
            raise GatewayContractError("gateway quote is missing symbol")

        schema_version, schema_minor = _schema_version(data)
        capabilities = _capabilities(data)
        timestamp = _timestamp(data)
        bid = _optional_number(data, "bid")
        ask = _optional_number(data, "ask")
        if (bid is None) is not (ask is None):
            raise GatewayContractError("gateway quote bid and ask must be provided together")
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
            spread_bps = None

        volume = _optional_number(data, "volume")
        buy_volume = _optional_number(data, "buy_volume")
        sell_volume = _optional_number(data, "sell_volume")
        trade_count = _optional_integer(data, "trade_count")
        bid_depth = _optional_number(data, "bid_depth")
        ask_depth = _optional_number(data, "ask_depth")
        volume_semantics = _optional_volume_semantics(data)
        depth_semantics = _optional_depth_semantics(data)

        rich_values = (
            volume,
            buy_volume,
            sell_volume,
            trade_count,
            bid_depth,
            ask_depth,
            volume_semantics,
            depth_semantics,
        )
        if any(value is not None for value in rich_values) and schema_minor < 1:
            raise GatewayContractError("rich market evidence requires schema_version 1.1 or newer")

        volume_values_present = any(
            value is not None for value in (volume, buy_volume, sell_volume, trade_count)
        )
        if volume_values_present and volume_semantics is None:
            raise GatewayContractError("volume-like evidence requires volume_semantics")
        if volume_semantics is not None and not volume_values_present:
            raise GatewayContractError("volume_semantics requires volume-like evidence")
        if volume_semantics is not None and (
            volume_semantics.kind != "interval" or volume_semantics.unit != "base_asset"
        ):
            raise GatewayContractError(
                "TMI realization scoring supports interval base_asset volume only"
            )

        if volume is not None and VOLUME not in capabilities:
            raise GatewayContractError("volume evidence requires volume capability")
        if (buy_volume is None) is not (sell_volume is None):
            raise GatewayContractError("buy_volume and sell_volume must be provided together")
        if buy_volume is not None and sell_volume is not None:
            if AGGRESSOR_FLOW not in capabilities:
                raise GatewayContractError("aggressor flow requires aggressor_flow capability")
            if volume is not None and buy_volume + sell_volume > volume:
                raise GatewayContractError("classified flow must not exceed total volume")
        if trade_count is not None and TRADE_COUNT not in capabilities:
            raise GatewayContractError("trade_count evidence requires trade_count capability")

        if (bid_depth is None) is not (ask_depth is None):
            raise GatewayContractError("bid_depth and ask_depth must be provided together")
        depth_present = bid_depth is not None and ask_depth is not None
        if depth_present:
            if TOP_OF_BOOK_DEPTH not in capabilities:
                raise GatewayContractError("depth evidence requires top_of_book_depth capability")
            if depth_semantics is None:
                raise GatewayContractError("depth evidence requires depth_semantics")
        elif depth_semantics is not None:
            raise GatewayContractError("depth_semantics requires paired depth evidence")

        sequence_value = data.get("sequence")
        sequence = None if sequence_value is None else _non_negative_integer(sequence_value, "sequence")
        provider_value = data.get("provider")
        provider = str(provider_value) if provider_value is not None else None

        return cls(
            symbol=symbol,
            timestamp=timestamp,
            price=price,
            schema_version=schema_version,
            capabilities=capabilities,
            volume=volume,
            buy_volume=buy_volume,
            sell_volume=sell_volume,
            trade_count=trade_count,
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            spread_bps=spread_bps,
            volume_semantics=volume_semantics,
            depth_semantics=depth_semantics,
            provider=provider,
            sequence=sequence,
        )

    @property
    def volume_basis(self) -> tuple[str, str, int | None, str | None] | None:
        if self.volume is None or self.volume_semantics is None:
            return None
        return self.volume_semantics.comparison_key

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
        for symbol, items in by_symbol.items():
            volume_bases = {item.volume_basis for item in items if item.volume_basis is not None}
            if len(volume_bases) > 1:
                raise GatewayContractError(
                    f"gateway recording mixes incomparable volume semantics for {symbol}"
                )
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
            if start <= quote.timestamp < before and quote.volume is not None
        ]
        if not volumes:
            raise GatewayContractError(
                f"no baseline volume for {symbol} in lookback window"
            )
        baseline = fmean(volumes)
        if baseline <= 0:
            raise GatewayContractError(
                f"baseline volume for {symbol} must be greater than zero"
            )
        return baseline


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


def _schema_version(data: Mapping[str, Any]) -> tuple[str, int]:
    version = str(data.get("schema_version", "1.0"))
    parts = version.split(".")
    if len(parts) != 2 or parts[0] != "1" or not parts[1].isdigit():
        raise GatewayContractError("gateway schema_version must be a supported 1.x version")
    return version, int(parts[1])


def _capabilities(data: Mapping[str, Any]) -> frozenset[str]:
    value = data.get("capabilities")
    if value is None:
        return frozenset({LEVEL1_QUOTE})
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise GatewayContractError("gateway capabilities must be an array")
    capabilities = frozenset(str(item) for item in value)
    unknown = capabilities.difference(KNOWN_CAPABILITIES)
    if unknown:
        raise GatewayContractError(
            f"gateway capabilities contain unsupported values: {', '.join(sorted(unknown))}"
        )
    if LEVEL1_QUOTE not in capabilities:
        raise GatewayContractError("gateway level1_quote capability is required")
    return capabilities


def _optional_volume_semantics(data: Mapping[str, Any]) -> VolumeSemantics | None:
    value = data.get("volume_semantics")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise GatewayContractError("gateway volume_semantics must be an object")
    return VolumeSemantics.from_mapping(cast(Mapping[str, Any], value))


def _optional_depth_semantics(data: Mapping[str, Any]) -> DepthSemantics | None:
    value = data.get("depth_semantics")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise GatewayContractError("gateway depth_semantics must be an object")
    return DepthSemantics.from_mapping(cast(Mapping[str, Any], value))


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
    quote = wrapped.get("quote")
    if quote is None:
        return cast(Mapping[str, Any], wrapped)
    if not isinstance(quote, Mapping):
        raise GatewayContractError("gateway quote wrapper must contain a JSON object")
    return cast(Mapping[str, Any], quote)


def _timestamp(data: Mapping[str, Any]) -> datetime:
    for key in ("provider_timestamp", "timestamp", "received_at"):
        value = data.get(key)
        if value is None:
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise GatewayContractError(f"gateway {key} is not ISO-8601") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
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
    if not math.isfinite(number):
        raise GatewayContractError(f"gateway {key} must be finite")
    if number < 0:
        raise GatewayContractError(f"gateway {key} must not be negative")
    return number


def _optional_integer(data: Mapping[str, Any], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    return _non_negative_integer(value, key)


def _non_negative_integer(value: Any, key: str) -> int:
    if isinstance(value, bool):
        raise GatewayContractError(f"gateway {key} must be an integer")
    try:
        integer = int(value)
    except (TypeError, ValueError) as exc:
        raise GatewayContractError(f"gateway {key} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise GatewayContractError(f"gateway {key} must be an integer")
    if integer < 0:
        raise GatewayContractError(f"gateway {key} must not be negative")
    return integer


def _positive_integer(value: Any, key: str) -> int:
    integer = _non_negative_integer(value, key)
    if integer <= 0:
        raise GatewayContractError(f"gateway {key} must be positive")
    return integer
