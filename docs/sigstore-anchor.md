# Sigstore anchor for a preregistered hypothesis

TMI can create a privacy-minimized artifact that contains only the SHA-256 digest of a verified hypothesis commitment. The artifact can then be signed with Cosign and recorded in the Sigstore transparency infrastructure without publishing the event text, expected direction, market evidence, or verdict.

## Prerequisites

- a commitment created with `python -m tmi.preregister create`;
- a current Cosign installation;
- an OIDC identity supported by the configured Sigstore instance.

## 1. Prepare the minimal payload

```bash
python -m tmi.anchor prepare \
  events/btc-cpi-001.commitment.json \
  --output anchors/btc-cpi-001.anchor.json
```

The output contains exactly:

```json
{
  "anchor_algorithm": "sha256",
  "anchor_schema_version": "1.0",
  "anchor_type": "tmi_hypothesis_commitment",
  "commitment_hash_sha256": "..."
}
```

It contains no event ID, headline, source, asset, direction, threshold, market value, or analytical result.

## 2. Sign and publish the proof

Use the official Cosign blob workflow:

```bash
cosign sign-blob \
  anchors/btc-cpi-001.anchor.json \
  --bundle anchors/btc-cpi-001.sigstore.json
```

Cosign performs the configured OIDC flow and writes a bundle containing the verification material and transparency-log inclusion proof. Review the identity presented during authentication before approving it.

## 3. Verify locally for audit

```bash
python -m tmi.anchor verify \
  events/btc-cpi-001.commitment.json \
  --anchor-payload anchors/btc-cpi-001.anchor.json \
  --bundle anchors/btc-cpi-001.sigstore.json \
  --certificate-identity researcher@example.com \
  --certificate-oidc-issuer https://accounts.example.com \
  --output anchors/btc-cpi-001.anchor-verification.json
```

The standalone verification receipt is useful for inspection, but finalization and replay do not trust that receipt as a substitute for the original bundle. They run Cosign again against the original payload and bundle.

## 4. Finalize the event with the verified anchor

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

Finalization re-runs `cosign verify-blob`. Only after it succeeds does the finalized event embed safe external-anchor metadata:

- commitment hash;
- anchor-payload hash;
- bundle hash;
- exact certificate identity;
- exact OIDC issuer;
- transparency-log and signed-timestamp verification flags.

The finalized event contains no path to the local bundle and does not copy the bundle itself.

## 5. Reverify before market scoring

```bash
python -m tmi \
  events/btc-cpi-001.event.json \
  --gateway-recording recordings/coinbase-btc-usd.jsonl \
  --anchor-payload anchors/btc-cpi-001.anchor.json \
  --anchor-bundle anchors/btc-cpi-001.sigstore.json \
  --receipt-output recordings/btc-cpi-001.receipt.json
```

When an event claims an external timestamp, TMI requires the original anchor payload and bundle. Before loading market evidence or calculating a verdict, it:

1. reconstructs the preregistration commitment from the finalized event;
2. checks that the embedded anchor refers to that exact commitment;
3. runs Cosign using the embedded identity and issuer;
4. recomputes the payload and bundle fingerprints;
5. requires exact agreement with the metadata embedded during finalization.

A different but otherwise valid bundle, identity, issuer, payload, or commitment is rejected. The replay receipt includes the verified external-anchor metadata under `preregistration.external_anchor`.

## Failure conditions

Verification fails closed when:

- the commitment file is invalid or was altered;
- the anchor payload contains any unexpected field;
- the payload commitment hash differs from the commitment file;
- the bundle is not valid JSON;
- Cosign is unavailable;
- the signature, certificate identity, issuer, signed timestamp, or transparency-log proof fails Cosign verification;
- anchored finalization omits any required anchor argument;
- an externally anchored event is replayed without its original payload and bundle;
- reverified payload or bundle fingerprints differ from the embedded metadata;
- an output file already exists.

Files under `anchors/` are ignored by Git and created with mode `0600` by the TMI commands.

## Security boundary

The Sigstore bundle is an external proof for the exact minimal payload. TMI verifies it during finalization and verifies it again before scoring. The complete audit set remains the commitment, anchor payload, Sigstore bundle, optional standalone verification receipt, finalized event, market ledger, and replay receipt.

A transparency-log entry strengthens evidence that the signed commitment digest existed by the logged time. It does not prove that the prediction was correct, that the event caused a market move, or that the local machine and operator were uncompromised.
