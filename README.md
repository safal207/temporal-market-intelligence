# Temporal Market Intelligence

**Temporal Market Intelligence (TMI)** is an evidence-first engine for testing whether a market event was actually realized in price, volume, order flow, and liquidity.

TMI does not claim that a news item caused a price move merely because both occurred close together. It creates a falsifiable expectation before evaluation, measures the observed market response, and returns a reviewable verdict.

```text
Event -> expected mechanism -> expected market reaction
      -> price / volume / order flow / liquidity evidence
      -> CONFIRMED / PARTIALLY_CONFIRMED / PRICED_IN
         / CONTRADICTED / NO_REACTION / NO_SIGNAL
```

## MVP scope

The first vertical slice evaluates one event for one asset using deterministic rules:

- expected direction and time horizon;
- post-event price change;
- relative volume;
- aggressive buy/sell flow;
- order-book imbalance;
- spread expansion;
- pre-event movement detection for `PRICED_IN`.

The initial example uses `BTC/USDT`, but the core models are provider-neutral.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
python -m tmi examples/btc_event.json
```

Expected output is a JSON report containing the verdict, score, reasons, and calculated features.

## Smart Market Data Gateway integration

The existing gateway provides latest REST quotes and a normalized live WebSocket stream. It does not yet promise arbitrary historical snapshots, so TMI does not invent a historical endpoint. The first integration consumes a reproducible JSONL recording of normalized gateway quote events.

```bash
python -m tmi \
  examples/btc_gateway_event.json \
  --gateway-recording examples/gateway_quotes.jsonl
```

The adapter:

- accepts direct quote objects and `{"data": quote}` wrappers;
- uses `provider_timestamp`, then `timestamp`, then `received_at`;
- derives midpoint price when only bid and ask are present;
- calculates spread in basis points when needed;
- selects the nearest point-in-time quote within a strict tolerance;
- calculates pre-event baseline volume from comparable recorded intervals;
- rejects crossed books, missing timestamps, stale evidence, and absent baseline volume.

This gives us a real integration boundary now while preserving deterministic replay for backtests and investor-facing evidence.

## Repository boundaries

| System | Responsibility |
|---|---|
| `temporal-market-intelligence` | Event hypotheses, market realization scoring, attribution, backtesting |
| `smart-market-data-gateway` | Reliable normalized market-data delivery and stream recording |
| `Causal-Memory-Layer` | Optional causal evidence and lineage protocol |
| `finanalytics-core` | Portfolio-level impact and risk analytics |

The gateway remains a data plane. TMI remains the analytical decision layer.

## Non-claims

This MVP:

- is not a trading bot;
- does not provide investment advice;
- does not prove causality from observational market data;
- does not hide uncertain or negative outcomes;
- does not use an LLM in the scoring path.

Its narrow goal is to make event-driven market hypotheses explicit, testable, and reproducible.

## Next milestones

1. Add a gateway WebSocket recorder that writes the normalized JSONL contract.
2. Add event-study baselines and abnormal returns.
3. Store 100-200 timestamped event records.
4. Compare TMI with sentiment-only and price-only baselines.
5. Add confounder detection and walk-forward evaluation.

## License

Apache-2.0.
