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

The gateway provides latest REST quotes and a normalized live WebSocket stream. It does not promise arbitrary historical snapshots, so TMI does not invent a historical endpoint. The integration consumes reproducible JSONL recordings produced by the gateway recorder.

```bash
python -m tmi \
  examples/btc_gateway_event.json \
  --gateway-recording examples/gateway_quotes.jsonl
```

The bundled rich example is a valid `QuoteEvent 1.1` SHA-256 evidence ledger. It contains explicit capabilities, interval volume semantics, aggressive trade flow, trade count, top-of-book depth, units, evidence origin, recorder provenance, and linked record hashes. Its deterministic result is fully `CONFIRMED`.

The adapter:

- accepts direct quote objects, `{"data": quote}` wrappers, and live `{"data": {"quote": quote}}` messages;
- uses `provider_timestamp`, then `timestamp`, then `received_at`;
- derives midpoint price when only bid and ask are present;
- calculates spread in basis points when needed;
- selects the nearest point-in-time quote within a strict tolerance;
- verifies `QuoteEvent 1.1` capabilities, units, aggregation windows, origin, paired flow, and paired depth;
- accepts only comparable interval base-asset volume for relative-volume scoring;
- rejects recordings that mix incompatible volume windows or units for one symbol;
- calculates pre-event baseline volume only from comparable observations;
- supports quote-only replay without inventing missing volume, trade flow, or order-book depth;
- automatically verifies the gateway SHA-256 evidence ledger when ledger metadata is present;
- rejects altered rows, broken chain links, non-contiguous indexes, invalid provenance, and files that mix legacy and ledger rows.

Legacy JSONL recordings without ledger metadata remain supported for migration. Once any ledger field is present, every row must belong to one valid chain; TMI fails closed before market scoring if integrity verification fails.

### Private replay receipt

Prepare the event JSON before reviewing the captured market response. TMI will not generate a post-hoc hypothesis from the recording.

After a private gateway capture, verify the ledger, run the deterministic analysis, and write one local replay receipt:

```bash
python -m tmi \
  events/pre_registered_event.json \
  --gateway-recording recordings/coinbase-btc-usd.jsonl \
  --receipt-output recordings/coinbase-btc-usd.receipt.json
```

The receipt binds the result to two deterministic commitments:

- `event_fingerprint_sha256` commits to the normalized pre-registered event and expected reaction;
- `recording_manifest.evidence_fingerprint_sha256` commits to the verified ledger head, or to the complete file hash for a legacy recording.

The embedded recording manifest contains only coverage and identity metadata: record counts, symbols, providers, time range, schema versions, capabilities, evidence semantics, session IDs, file size, and the evidence fingerprint. It deliberately excludes prices, volumes, order-book values, and calculated features.

The complete receipt still contains the analytical result and derived features, so it remains private by default. TMI requires receipt output under `recordings/`, creates the file with mode `0600`, refuses to overwrite an existing receipt, and ignores the directory in Git.

### Evidence availability

Every result reports explicit numeric availability flags:

- `volume_available`;
- `aggressive_flow_available`;
- `order_book_available`;
- `spread_available`.

Availability is based on field presence and validated semantics, not on `value > 0`. An absent field means unavailable evidence; numeric zero remains an observed zero. A live Level-1 recording can therefore support price-and-spread assessment but cannot masquerade as volume or order-flow confirmation. In the normal quote-only case, aligned price evidence is limited to `PARTIALLY_CONFIRMED` rather than being promoted to a fully corroborated result.

## Repository boundaries

| System | Responsibility |
|---|---|
| `temporal-market-intelligence` | Event hypotheses, evidence verification, market realization scoring, attribution, backtesting |
| `smart-market-data-gateway` | Reliable normalized market-data delivery, rich evidence semantics, provenance, and tamper-evident recording |
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
- does not treat synthetic mock evidence as market alpha;
- does not treat a local hash chain as a digital signature or external timestamp.

Its narrow goal is to make event-driven market hypotheses explicit, testable, and reproducible.

## Next milestones

1. Run the first private Coinbase capture against a genuinely pre-registered event and retain its local replay receipt.
2. Add a cross-repository contract fixture generated directly by the released gateway package.
3. Anchor or sign ledger head hashes outside the recording file.
4. Represent event windows as time series rather than two snapshots.
5. Calculate abnormal return against market and sector baselines.
6. Store 100-200 timestamped event records.
7. Compare TMI with sentiment-only and price-only baselines.
8. Add confounder detection and walk-forward evaluation.

## License

Apache-2.0.