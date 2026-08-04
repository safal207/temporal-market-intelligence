"""Bind embedded Sigstore anchor metadata to the original bundle at replay time."""

from __future__ import annotations

from pathlib import Path

from tmi.anchor import SigstoreAnchorVerification, verify_sigstore_anchor


def reverify_sigstore_anchor(
    expected: SigstoreAnchorVerification,
    anchor_payload: Path,
    bundle: Path,
    *,
    cosign_binary: str = "cosign",
) -> SigstoreAnchorVerification:
    """Re-run Cosign and require exact agreement with embedded anchor metadata."""

    verified = verify_sigstore_anchor(
        expected.commitment_hash_sha256,
        anchor_payload,
        bundle,
        certificate_identity=expected.certificate_identity,
        certificate_oidc_issuer=expected.certificate_oidc_issuer,
        cosign_binary=cosign_binary,
    )
    if verified != expected:
        raise ValueError(
            "reverified Sigstore anchor does not match embedded preregistration metadata"
        )
    return verified
