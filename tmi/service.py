"""Application service connecting event hypotheses to market-data evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from tmi.gateway import MarketDataGateway
from tmi.models import EventRecord, RealizationResult
from tmi.scoring import RealizationScorer


@dataclass(frozen=True, slots=True)
class EvaluationWindow:
    """Time-window policy for one event realization evaluation."""

    before_offset_seconds: int = 60
    baseline_lookback_minutes: int = 60
    priced_in_start_minutes: int = 10
    priced_in_end_minutes: int = 2

    def __post_init__(self) -> None:
        if self.before_offset_seconds <= 0:
            raise ValueError("before_offset_seconds must be positive")
        if self.baseline_lookback_minutes <= 0:
            raise ValueError("baseline_lookback_minutes must be positive")
        if self.priced_in_start_minutes <= self.priced_in_end_minutes:
            raise ValueError("priced_in_start_minutes must exceed priced_in_end_minutes")
        if self.priced_in_end_minutes <= 0:
            raise ValueError("priced_in_end_minutes must be positive")


class RealizationService:
    """Evaluate a pre-registered event against a market-data gateway."""

    def __init__(self, scorer: RealizationScorer | None = None) -> None:
        self._scorer = scorer or RealizationScorer()

    def evaluate(
        self,
        event: EventRecord,
        gateway: MarketDataGateway,
        *,
        window: EvaluationWindow | None = None,
    ) -> RealizationResult:
        """Resolve event-time snapshots and produce a deterministic verdict."""

        policy = window or EvaluationWindow()
        asset = event.reaction.asset
        published_at = event.published_at

        before = gateway.snapshot(
            asset,
            published_at - timedelta(seconds=policy.before_offset_seconds),
        )
        after = gateway.snapshot(
            asset,
            published_at + timedelta(minutes=event.reaction.horizon_minutes),
        )
        pre_before = gateway.snapshot(
            asset,
            published_at - timedelta(minutes=policy.priced_in_start_minutes),
        )
        pre_after = gateway.snapshot(
            asset,
            published_at - timedelta(minutes=policy.priced_in_end_minutes),
        )
        baseline_volume = gateway.baseline_volume(
            asset,
            before=published_at,
            lookback_minutes=policy.baseline_lookback_minutes,
        )

        return self._scorer.evaluate(
            event,
            before,
            after,
            baseline_volume,
            pre_before=pre_before,
            pre_after=pre_after,
        )
