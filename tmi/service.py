"""Application service connecting event hypotheses to market-data evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from tmi.gateway import MarketDataGateway
from tmi.models import EventRecord, MarketSnapshot, RealizationResult
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
        """Resolve required snapshots and omit unavailable optional evidence."""

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
        pre_before = self._optional_snapshot(
            gateway,
            asset,
            published_at - timedelta(minutes=policy.priced_in_start_minutes),
        )
        pre_after = self._optional_snapshot(
            gateway,
            asset,
            published_at - timedelta(minutes=policy.priced_in_end_minutes),
        )
        baseline_volume = self._optional_baseline(
            gateway,
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

    @staticmethod
    def _optional_snapshot(
        gateway: MarketDataGateway,
        asset: str,
        at: datetime,
    ) -> MarketSnapshot | None:
        try:
            return gateway.snapshot(asset, at)
        except ValueError:
            return None

    @staticmethod
    def _optional_baseline(
        gateway: MarketDataGateway,
        asset: str,
        *,
        before: datetime,
        lookback_minutes: int,
    ) -> float | None:
        try:
            return gateway.baseline_volume(
                asset,
                before=before,
                lookback_minutes=lookback_minutes,
            )
        except ValueError:
            return None
