# MVP Architecture

## Decision boundary

Temporal Market Intelligence is the analytical layer between timestamped events and normalized market evidence.

```text
Event sources
    -> event normalization and pre-registered expectation
    -> market-data adapter
    -> deterministic feature calculation
    -> realization scorer
    -> reviewable verdict and evidence
```

The MVP deliberately separates three concerns:

1. **Data delivery** belongs to `smart-market-data-gateway`.
2. **Market realization analysis** belongs to this repository.
3. **Causal evidence lineage** may later be exported to `Causal-Memory-Layer`.

## Core records

### EventRecord

Stores the event identity, timestamps, source, source confidence, and the expected market reaction.

### ExpectedReaction

Pre-registers:

- asset;
- direction;
- reaction horizon;
- minimum meaningful price move.

This record must exist before outcome evaluation. Otherwise the system risks inventing an explanation after observing the chart.

### MarketSnapshot

Provider-neutral snapshot containing:

- price;
- event-window volume;
- aggressive buy and sell volume;
- bid and ask depth;
- spread in basis points.

### RealizationResult

Returns:

- verdict;
- normalized score;
- human-readable reasons;
- exact calculated features.

## MVP verdict semantics

| Verdict | Meaning |
|---|---|
| `CONFIRMED` | Multiple independent market features align with the registered expectation. |
| `PARTIALLY_CONFIRMED` | Some evidence aligns, but the confirmation threshold is not reached. |
| `CONTRADICTED` | Price moves materially against the expected direction. |
| `PRICED_IN` | The expected move starts before publication and is not renewed afterward. |
| `NO_REACTION` | Price and volume remain materially quiet. |
| `NO_SIGNAL` | Evidence is mixed, weak, or the expectation is non-directional. |
| `INSUFFICIENT_DATA` | The evaluation window or baseline is invalid. |

## Scoring philosophy

The scorer is deterministic and intentionally simple. Price direction carries the highest weight, while volume, aggressive flow, order-book imbalance, and spread expansion provide independent confirmation.

Thresholds are configuration, not universal truths. Every future empirical change must be versioned and evaluated against a frozen out-of-sample dataset.

## Leakage controls

The MVP establishes several invariants:

- event expectation is explicit before evaluation;
- the before snapshot cannot occur after publication;
- the after snapshot cannot occur before publication;
- the after snapshot must remain inside the registered horizon;
- pre-publication movement is evaluated separately;
- uncertain cases may return `NO_SIGNAL`.

## Next architecture increments

1. Add a real adapter for `smart-market-data-gateway`.
2. Represent event windows as time series rather than two snapshots.
3. Calculate abnormal return against market and sector baselines.
4. Add concurrent-event and confounder records.
5. Persist immutable evaluation runs with model and threshold versions.
6. Export evidence bundles to CML-compatible causal records.
