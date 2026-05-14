# Supply-chain controls (SLSA, container signing)

Framework §12.3 / F-13 — SLSA ≥ Level 2 for builds, signed container
images, MCP server binaries verified by hash. This document captures
the current posture and the L3 graduation plan.

## Posture

| Control | Current | Target | Owner |
|---|---|---|---|
| SLSA level for FinOps hub image | None published | Level 2 (L2 graduation) | Platform |
| Cosign signing for container images | Not enabled | Sigstore keyless (OIDC) | Platform |
| Admission verification | Not enforced | GKE Binary Authorization / ECR signature attestation | SRE |
| MCP server binaries pinned by hash | Yes (`.mcp.json`) | Yes — verified on session start | Platform |
| LLM SDK pin | `pyproject.toml` exact versions | Same | Platform |
| Dependabot dependency review | Enabled (PR-gated) | Same | Platform |

## L2 graduation plan

1. Wire `slsa-framework/slsa-github-generator` into `.github/workflows/release.yml`.
2. Switch container build to `docker/build-push-action@v6` with provenance.
3. Sign the resulting image with cosign keyless (OIDC from GitHub Actions).
4. Configure admission verification in the deployment env (GKE Binary Authorization on GCP; ECR signature attestation on AWS).
5. Update this document with the SLSA attestation URL.

## L3 graduation plan

1. Two-builder reproducible builds (SLSA Level 3 requirement).
2. Cosign signing keys backed by a KMS HSM.
3. SBOM emission per image (`syft` or `trivy sbom`).

## MCP server hash pinning

`.mcp.json` already pins MCP server URLs. To pin by hash:

```jsonc
{
  "servers": {
    "my-mcp": {
      "url": "https://example.com/mcp",
      "expected_sha256": "0123abcd..."   // verified at session start
    }
  }
}
```

The verification logic lives in `mcp_server/server.py` (not yet wired —
tracked in the L2 graduation issue).
