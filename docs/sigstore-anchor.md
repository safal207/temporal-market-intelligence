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

## 3. Verify locally

```bash
python -m tmi.anchor verify \
  events/btc-cpi-001.commitment.json \
  --anchor-payload anchors/btc-cpi-001.anchor.json \
  --bundle anchors/btc-cpi-001.sigstore.json \
  --certificate-identity researcher@example.com \
  --certificate-oidc-issuer https://accounts.example.com \
  --output anchors/btc-cpi-001.anchor-verification.json
```

Verification fails closed when:

- the commitment file is invalid or was altered;
- the anchor payload contains any unexpected field;
- the payload commitment hash differs from the commitment file;
- the bundle is not valid JSON;
- Cosign is unavailable;
- the signature, certificate identity, issuer, signed timestamp, or transparency-log proof fails Cosign verification;
- an output file already exists.

The generated verification receipt contains fingerprints and identity metadata but no hypothesis or market values. Files under `anchors/` are ignored by Git and created with mode `0600` by the TMI commands.

## Security boundary

The Sigstore bundle is an external proof for the exact minimal payload. The TMI verification receipt records that proof locally. The current command does not rewrite a commitment or finalized event and does not yet embed the external proof into a replay receipt. Keep the commitment, anchor payload, Sigstore bundle, verification receipt, market ledger, and replay receipt together for audit.

A transparency-log entry strengthens evidence that the signed commitment digest existed by the logged time. It does not prove that the prediction was correct, that the event caused a market move, or that the local machine and operator were uncompromised.
