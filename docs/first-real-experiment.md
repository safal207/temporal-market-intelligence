# First real experiment: BLS Employment Situation, 7 August 2026

This protocol targets the U.S. Bureau of Labor Statistics Employment Situation release for July 2026, scheduled for **2026-08-07 08:30 Eastern / 12:30 UTC**.

Official schedule:

- https://www.bls.gov/cps/publications/release-calendar.htm
- https://www.bls.gov/ces/

The committed plan is:

```text
experiments/bls-employment-2026-08-07-btc-down.plan.json
```

The test hypothesis is deliberately simple and falsifiable:

```text
BTC-USD moves down by at least 0.5% within 30 minutes of the release.
```

This is a research hypothesis, not investment advice. `CONTRADICTED`, `NO_REACTION`, `NO_SIGNAL`, or any other negative result is a valid experiment outcome.

## 1. Run preflight before the deadline

```bash
python -m tmi.experiment \
  experiments/bls-employment-2026-08-07-btc-down.plan.json
```

The plan fails closed after **2026-08-07 11:30 UTC**. It prints exact operator commands and timing for:

1. hypothesis commitment;
2. hypothesis Sigstore anchor;
3. Coinbase capture;
4. market-ledger anchor;
5. event finalization;
6. deterministic replay receipt.

Set the identity values used by the real OIDC flow:

```bash
export TMI_CERT_IDENTITY='your verified identity'
export TMI_CERT_OIDC_ISSUER='your exact OIDC issuer'
```

## 2. Anchor the hypothesis before observation

Create the commitment, prepare its minimal payload, and sign it with Cosign. The public payload contains only the commitment digest and schema metadata. Keep the commitment, payload, and bundle locally.

Do not continue if the hypothesis bundle cannot be verified with the exact identity and issuer.

## 3. Capture the complete market window

Start the Coinbase research capture at **2026-08-07 11:45 UTC**. The plan records for 5,400 seconds, ending at **13:15 UTC**.

The capture must use the gateway's explicit personal-research terms acknowledgement. Real market files remain under `recordings/` and are ignored by Git.

## 4. Anchor the market ledger head

After capture completes:

```bash
python -m tmi.evidence_anchor prepare \
  recordings/bls-employment-2026-08-07-btc-usd.jsonl \
  --output anchors/bls-employment-2026-08-07.evidence.anchor.json

cosign sign-blob \
  anchors/bls-employment-2026-08-07.evidence.anchor.json \
  --bundle anchors/bls-employment-2026-08-07.evidence.sigstore.json
```

The evidence payload contains only:

- anchor schema metadata;
- `fingerprint_kind=ledger_head`;
- the verified ledger-head SHA-256.

It contains no price, volume, order-book value, trade flow, event text, or verdict.

## 5. Finalize and replay

Use the actual official publication timestamps when finalizing the event. Do not substitute the scheduled time if the release was delayed.

Replay requires both original Sigstore payloads and bundles. TMI verifies the hypothesis anchor before loading evidence, verifies the market-ledger anchor against the validated recording manifest, and then computes the deterministic verdict.

The private receipt binds:

```text
hypothesis commitment hash
+ hypothesis Sigstore bundle fingerprint
+ finalized event fingerprint
+ verified ledger head
+ evidence Sigstore bundle fingerprint
+ deterministic verdict and features
```

## Completion criterion for 93%

The project reaches the defined 93% research-MVP target when one private receipt exists for this protocol and all of the following are true:

- the hypothesis bundle was created before the preregistration deadline;
- the capture covers the complete planned window;
- the recording ledger verifies end to end;
- the market-ledger bundle verifies against the exact ledger head;
- replay re-verifies both external anchors;
- the receipt is created without modifying the hypothesis, evidence, scoring rules, or result after observation.

A positive verdict is not required. Integrity and reproducibility are the completion criteria.
