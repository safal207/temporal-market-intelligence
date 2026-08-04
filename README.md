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

The gateway provides latest REST quotes and a normalized live WebSocket stream. It does not promise arbitrary historical snapshots, so TMI does not invent a historical endpoint. The integration consumes a reproducible JSONL recording of normalized gateway quote events.

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
- calculates pre-event baseline volume when comparable volume evidence exists;
- supports quote-only replay without inventing missing volume, trade flow, or order-book depth;
- automatically verifies the Smart Market Data Gateway SHA-256 evidence ledger when ledger metadata is present;
- rejects altered rows, broken chain links, non-contiguous indexes, invalid provenance, and files that mix legacy and ledger rows.

Legacy JSONL recordings without ledger metadata remain supported for existing fixtures and migration. Once any ledger field is present, every row must belong to one valid chain; TMI fails closed before market scoring if integrity verification fails.

### Evidence availability

Every result reports explicit numeric availability flags:

- `volume_available`;
- `aggressive_flow_available`;
- `order_book_available`;
- `spread_available`.

Unavailable evidence contributes no score and produces a human-readable omission reason. A live Level-1 quote recording containing only price and bid/ask can therefore support a price-and-spread assessment, but it cannot masquerade as volume or order-flow confirmation. In the normal quote-only case, aligned price evidence is limited to `PARTIALLY_CONFIRMED` rather than being promoted to a fully corroborated result.

This gives us a real integration boundary while preserving deterministic replay for backtests and investor-facing evidence.

## Repository boundaries

| System | Responsibility |
|---|---|
| `temporal-market-intelligence` | Event hypotheses, evidence verification, market realization scoring, attribution, backtesting |
| `smart-market-data-gateway` | Reliable normalized market-data delivery, provenance, and tamper-evident stream recording |
| `Causal-Memory-Layer` | Optional causal evidence and lineage protocol |
| `finanalytics-core` | Portfolio-level impact and risk analytics |

The gateway remains a data plane. TMI remains the analytical decision layer.

## Non-claims

This MVP:

- is not a trading bot;
- does not provide investment advice;
- does not prove causality from observational market data;
- does not hide uncertain or negative outcomes;
- does not use an LLM in the scoring path;
- does not treat missing market layers as zero-valued evidence;
- does not treat a local hash chain as a digital signature or external timestamp.

Its narrow goal is to make event-driven market hypotheses explicit, testable, and reproducible.

## Next milestones

1. Extend the gateway evidence schema with licensed volume, aggressive-flow, and order-book-depth fields.
2. Capture a longer real-provider evidence ledger under an approved data-rights profile.
3. Anchor or sign ledger head hashes outside the recording file.
4. Represent event windows as time series rather than two snapshots.
5. Calculate abnormal return against market and sector baselines.
6. Store 100-200 timestamped event records.
7. Compare TMI with sentiment-only and price-only baselines.
8. Add confounder detection and walk-forward evaluation.

## License

Apache-2.0.
