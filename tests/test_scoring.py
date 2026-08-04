from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tmi import (
    Direction,
    EventRecord,
    ExpectedReaction,
    MarketSnapshot,
    RealizationScorer,
    Verdict,
)

PUBLISHED_AT = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def event(direction: Direction = Direction.DOWN) -> EventRecord:
    return EventRecord(
        event_id="event-1",
        headline="Unexpected event",
        source="official-source",
        occurred_at=PUBLISHED_AT,
        published_at=PUBLISHED_AT,
        source_confidence=1.0,
        reaction=ExpectedReaction(
            asset="BTC/USDT",
            direction=direction,
            horizon_minutes=30,
            minimum_move_pct=0.5,
        ),
    )


def snapshot(
    minute: int,
    price: float,
    volume: float,
    *,
    buy_volume: float = 0.0,
    sell_volume: float = 0.0,
    bid_depth: float = 0.0,
    ask_depth: float = 0.0,
    spread_bps: float = 2.0,
) -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=PUBLISHED_AT + timedelta(minutes=minute),
        price=price,
        volume=volume,
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        bid_depth=bid_depth,
        ask_depth=ask_depth,
        spread_bps=spread_bps,
    )


def test_confirms_aligned_price_volume_book_and_flow() -> None:
    result = RealizationScorer().evaluate(
        event(),
        snapshot(-1, 100.0, 90.0, spread_bps=2.0),
        snapshot(
            5,
            98.5,
            220.0,
            buy_volume=50.0,
            sell_volume=170.0,
            bid_depth=300.0,
            ask_depth=700.0,
            spread_bps=3.0,
        ),
        baseline_volume=100.0,
    )

    assert result.verdict is Verdict.CONFIRMED
    assert result.score >= 0.70
    assert result.features["price_change_pct"] == -1.5


def test_contradicts_material_move_against_expectation() -> None:
    result = RealizationScorer().evaluate(
        event(),
        snapshot(-1, 100.0, 100.0),
        snapshot(5, 101.0, 180.0),
        baseline_volume=100.0,
    )

    assert result.verdict is Verdict.CONTRADICTED


def test_detects_priced_in_pre_event_move() -> None:
    result = RealizationScorer().evaluate(
        event(),
        snapshot(-1, 99.0, 100.0),
        snapshot(5, 98.9, 100.0),
        baseline_volume=100.0,
        pre_before=snapshot(-10, 100.0, 80.0),
        pre_after=snapshot(-2, 99.0, 90.0),
    )

    assert result.verdict is Verdict.PRICED_IN
    assert result.features["pre_event_signed_move_pct"] == 1.0


def test_returns_no_reaction_for_flat_quiet_window() -> None:
    result = RealizationScorer().evaluate(
        event(),
        snapshot(-1, 100.0, 100.0),
        snapshot(5, 100.05, 105.0),
        baseline_volume=100.0,
    )

    assert result.verdict is Verdict.NO_REACTION
    assert result.score == 0.0
