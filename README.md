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

## First real experiment

The committed operator plan for the first real experiment targets the BLS Employment Situation release on 7 August 2026:

```bash
python -m tmi.experiment \
  experiments/bls-employment-2026-08-07-btc-down.plan.json
```

Preflight fails closed after the preregistration deadline and prints the exact sequence for hypothesis anchoring, Coinbase capture, market-ledger anchoring, event finalization, and replay. See `docs/first-real-experiment.md`.

The hypothesis is a falsifiable research test, not investment advice. A negative or inconclusive verdict is a valid outcome; integrity and reproducibility define completion.

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

### Hypothesis preregistration

Commit the expected reaction before observing the event or its market response:

```bash
python -m tmi.preregister create \
  --event-id btc-cpi-001 \
  --headline "Scheduled CPI release" \
  --source official-source \
  --asset BTC-USD \
  --direction down \
  --horizon-minutes 30 \
  --minimum-move-pct 0.5 \
  --scheduled-event-at 2026-08-05T12:30:00Z \
  --output events/btc-cpi-001.commitment.json
```

The commitment fixes the event identity, headline, source, asset, direction, horizon, threshold, registration time, and optional scheduled-event time under one canonical SHA-256 digest. The command creates a new file with mode `0600` and never overwrites an existing commitment.

A local timestamp is useful audit evidence but is not an independently trusted timestamp. TMI can publish only the commitment digest through a privacy-minimized Sigstore/Cosign anchor. See `docs/sigstore-anchor.md` for the complete workflow.

After the event occurs, finalize it without changing the committed hypothesis. For an externally anchored commitment, finalization re-runs Cosign against the original payload and bundle:

```bash
python -m tmi.preregister finalize \
  events/btc-cpi-001.commitment.json \
  --occurred-at 2026-08-05T12:30:02Z \
  --published-at 2026-08-05T12:30:04Z \
  --source-confidence 0.98 \
  --anchor-payload anchors/btc-cpi-001.anchor.json \
  --anchor-bundle anchors/btc-cpi-001.sigstore.json \
  --certificate-identity researcher@example.com \
  --certificate-oidc-issuer https://accounts.example.com \
  --output events/btc-cpi-001.event.json
```

Finalization verifies the commitment hash, rejects an event timestamp before registration, verifies the Sigstore proof when supplied, and embeds only safe anchor metadata. A boolean `external_timestamp_verified=true` is rejected unless a complete verified anchor contract is present.

### Market-evidence ledger anchor

After a verified gateway capture, TMI can anchor only the ledger-head digest:

```bash
python -m tmi.evidence_anchor prepare \
  recordings/coinbase-btc-usd.jsonl \
  --output anchors/coinbase-btc-usd.evidence.anchor.json

cosign sign-blob \
  anchors/coinbase-btc-usd.evidence.anchor.json \
  --bundle anchors/coinbase-btc-usd.evidence.sigstore.json
```

The public payload contains the anchor schema, `fingerprint_kind=ledger_head`, and the ledger-head SHA-256. It contains no prices, volumes, trade flow, order-book values, hypothesis text, or verdict. Legacy non-ledger JSONL is rejected for external evidence anchoring.

### Private replay receipt

Prepare the event JSON before reviewing the captured market response. TMI will not generate a post-hoc hypothesis from the recording.

A dual-anchor replay re-verifies the hypothesis proof and the evidence-ledger proof before scoring:

```bash
python -m tmi \
  events/btc-cpi-001.event.json \
  --gateway-recording recordings/coinbase-btc-usd.jsonl \
  --anchor-payload anchors/btc-cpi-001.anchor.json \
  --anchor-bundle anchors/btc-cpi-001.sigstore.json \
  --evidence-anchor-payload anchors/coinbase-btc-usd.evidence.anchor.json \
  --evidence-anchor-bundle anchors/coinbase-btc-usd.evidence.sigstore.json \
  --evidence-certificate-identity researcher@example.com \
  --evidence-certificate-oidc-issuer https://accounts.example.com \
  --receipt-output recordings/coinbase-btc-usd.receipt.json
```

The receipt can bind the result to six deterministic commitments:

- `preregistration.commitment_hash_sha256` commits to the hypothesis fixed before observation;
- `preregistration.external_anchor` records the exact identity, issuer, anchor-payload hash, and bundle hash reverified before scoring;
- `event_fingerprint_sha256` commits to the finalized normalized event and expected reaction;
- `recording_manifest.evidence_fingerprint_sha256` commits to the verified ledger head;
- `recording_manifest.external_anchor` records the externally verified ledger-head payload and bundle fingerprints;
- the verdict, reasons, and features are deterministically derived from the bound event and evidence.

TMI rejects incomplete anchor arguments, a changed payload, a failed signature or inclusion proof, a different hypothesis bundle than the one embedded at finalization, and any evidence payload that differs from the validated ledger head.

The embedded recording manifest contains only coverage and identity metadata: record counts, symbols, providers, time range, schema versions, capabilities, evidence semantics, session IDs, file size, and the evidence fingerprint. It deliberately excludes prices, volumes, order-book values, and calculated features.

The complete receipt still contains the analytical result and derived features, so it remains private by default. TMI requires receipt output under `recordings/`, creates the file with mode `0600`, refuses to overwrite an existing receipt, and ignores `recordings/`, `anchors/`, and `events/` in Git.

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
- does not claim that a valid external timestamp proves a correct hypothesis or causal market impact.

Its narrow goal is to make event-driven market hypotheses explicit, testable, and reproducible.

## Next milestones

1. Execute the committed BLS Employment Situation protocol and retain the first private dual-anchor receipt.
2. Add a cross-repository contract fixture generated directly by the released gateway package.
3. Represent event windows as time series rather than two snapshots.
4. Calculate abnormal return against market and sector baselines.
5. Store 100-200 timestamped event records.
6. Compare TMI with sentiment-only and price-only baselines.
7. Add confounder detection and walk-forward evaluation.

## License

Apache-2.0.