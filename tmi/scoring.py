"""Deterministic scoring for event-to-market realization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from tmi.features import MarketFeatures, calculate_market_features, percentage_change
from tmi.models import (
    Direction,
    EventRecord,
    MarketSnapshot,
    RealizationResult,
    Verdict,
)


@dataclass(frozen=True, slots=True)
class RealizationConfig:
    """Transparent thresholds for the MVP scorer."""

    volume_multiple: float = 1.5
    order_book_imbalance: float = 0.15
    aggressive_flow_ratio: float = 0.60
    spread_expansion_ratio: float = 1.25
    priced_in_move_pct: float = 0.40
    confirmed_score: float = 0.70
    partial_score: float = 0.40


class RealizationScorer:
    """Evaluate whether observed market evidence matches an expected reaction."""

    def __init__(self, config: RealizationConfig | None = None) -> None:
        self.config = config or RealizationConfig()

    def evaluate(
        self,
        event: EventRecord,
        before: MarketSnapshot,
        after: MarketSnapshot,
        baseline_volume: float | None,
        *,
        pre_before: MarketSnapshot | None = None,
        pre_after: MarketSnapshot | None = None,
    ) -> RealizationResult:
        invalid_reason = self._validate_window(event, before, after, baseline_volume)
        if invalid_reason is not None:
            return RealizationResult(
                verdict=Verdict.INSUFFICIENT_DATA,
                score=0.0,
                reasons=(invalid_reason,),
            )

        if event.reaction.direction in {Direction.MIXED, Direction.NONE}:
            return RealizationResult(
                verdict=Verdict.NO_SIGNAL,
                score=0.0,
                reasons=("MVP scorer requires a directional expectation",),
            )

        features = calculate_market_features(before, after, baseline_volume)
        signed_move = self._signed(features.price_change_pct, event.reaction.direction)
        minimum_move = event.reaction.minimum_move_pct

        pre_signed_move = self._pre_event_signed_move(
            event.reaction.direction,
            pre_before,
            pre_after,
        )
        if (
            pre_signed_move is not None
            and pre_signed_move >= self.config.priced_in_move_pct
            and signed_move < minimum_move
        ):
            return RealizationResult(
                verdict=Verdict.PRICED_IN,
                score=min(1.0, 0.60 + min(pre_signed_move, 2.0) * 0.10),
                reasons=(
                    "Expected directional movement started before publication",
                    "Post-publication move did not reach the registered threshold",
                ),
                features={**features.as_dict(), "pre_event_signed_move_pct": pre_signed_move},
            )

        if signed_move <= -minimum_move:
            return RealizationResult(
                verdict=Verdict.CONTRADICTED,
                score=min(1.0, abs(signed_move) / max(minimum_move, 0.01) * 0.50),
                reasons=("Price moved materially against the expected direction",),
                features=features.as_dict(),
            )

        score, reasons = self._evidence_score(features, event.reaction.direction, minimum_move)
        score *= 0.70 + (0.30 * event.source_confidence)
        score = min(1.0, score)

        if score >= self.config.confirmed_score:
            verdict = Verdict.CONFIRMED
        elif score >= self.config.partial_score:
            verdict = Verdict.PARTIALLY_CONFIRMED
        elif (
            abs(features.price_change_pct) < max(0.10, minimum_move * 0.25)
            and (features.volume_available == 0.0 or features.relative_volume < 1.10)
        ):
            verdict = Verdict.NO_REACTION
            reasons.append("No material price response was observed")
        else:
            verdict = Verdict.NO_SIGNAL
            reasons.append("Evidence was mixed or below deterministic thresholds")

        return RealizationResult(
            verdict=verdict,
            score=score,
            reasons=tuple(reasons),
            features=features.as_dict(),
        )

    def _validate_window(
        self,
        event: EventRecord,
        before: MarketSnapshot,
        after: MarketSnapshot,
        baseline_volume: float | None,
    ) -> str | None:
        if baseline_volume is not None and baseline_volume <= 0:
            return "baseline_volume must be greater than zero when provided"
        if before.timestamp > event.published_at:
            return "before snapshot must not be later than event publication"
        if after.timestamp < event.published_at:
            return "after snapshot must not be earlier than event publication"
        deadline = event.published_at + timedelta(minutes=event.reaction.horizon_minutes)
        if after.timestamp > deadline:
            return "after snapshot is outside the registered reaction horizon"
        if after.timestamp <= before.timestamp:
            return "after snapshot must be later than before snapshot"
        return None

    def _evidence_score(
        self,
        features: MarketFeatures,
        direction: Direction,
        minimum_move: float,
    ) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        signed_move = self._signed(features.price_change_pct, direction)

        if signed_move >= minimum_move:
            score += 0.45
            reasons.append("Price reached the expected directional threshold")
        elif signed_move >= minimum_move * 0.50:
            score += 0.25
            reasons.append("Price moved in the expected direction but below threshold")

        if features.volume_available == 1.0:
            if features.relative_volume >= self.config.volume_multiple:
                score += 0.20
                reasons.append("Volume exceeded the event-window baseline")
        else:
            reasons.append("Volume baseline unavailable; volume confirmation was omitted")

        if features.order_book_available == 1.0:
            book_aligned = (
                direction is Direction.UP
                and features.order_book_imbalance >= self.config.order_book_imbalance
            ) or (
                direction is Direction.DOWN
                and features.order_book_imbalance <= -self.config.order_book_imbalance
            )
            if book_aligned:
                score += 0.15
                reasons.append("Order-book depth aligned with the expected direction")
        else:
            reasons.append("Order-book depth unavailable; book confirmation was omitted")

        if features.aggressive_flow_available == 1.0:
            flow_aligned = (
                direction is Direction.UP
                and features.aggressive_sell_ratio <= 1.0 - self.config.aggressive_flow_ratio
            ) or (
                direction is Direction.DOWN
                and features.aggressive_sell_ratio >= self.config.aggressive_flow_ratio
            )
            if flow_aligned:
                score += 0.15
                reasons.append("Aggressive trade flow aligned with the expected direction")
        else:
            reasons.append("Aggressive trade flow unavailable; flow confirmation was omitted")

        if features.spread_available == 1.0:
            if features.spread_change_ratio >= self.config.spread_expansion_ratio:
                score += 0.05
                reasons.append("Spread expansion confirmed elevated execution uncertainty")
        else:
            reasons.append("Bid/ask spread unavailable; spread confirmation was omitted")

        return score, reasons

    @staticmethod
    def _signed(value: float, direction: Direction) -> float:
        return value if direction is Direction.UP else -value

    def _pre_event_signed_move(
        self,
        direction: Direction,
        pre_before: MarketSnapshot | None,
        pre_after: MarketSnapshot | None,
    ) -> float | None:
        if pre_before is None or pre_after is None:
            return None
        if pre_after.timestamp <= pre_before.timestamp:
            return None
        move = percentage_change(pre_before.price, pre_after.price)
        return self._signed(move, direction)
