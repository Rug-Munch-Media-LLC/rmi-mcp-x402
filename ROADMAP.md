# rmi-mcp-x402 — Roadmap (post-spec-compliance)

**Next 90 days.** The protocol core is spec-conformant in target; this roadmap
is about reconciling the five workstreams and finishing chain coverage.

## Immediate — Reconcile interfaces at merge (blocking)
- Land unified `PaymentRequired`/`PaymentRequirement` models; drop `PaymentRequest`.
- Unify the x402 header convention (verified-id `X-Payment-ID` vs official
  `PAYMENT-SIGNATURE`/`PAYMENT-RESPONSE`) across middleware, verifier, settle-api.
- Contract-align `FacilitatorRouter.verify/settle`; settle the verify response key.
- One shared test-assertion contract (verify key, 402 vs 410, CAIP-2 ids).

## Sprint 2 — Close G1–G10 in `app/domain/x402/*`
- Delegate middleware + models to `x402-py` (verifier already does).
- CAIP-2 throughout; server-authoritative pricing; `/v1/x402/health`.
- Remove legacy tool-count / platform duplication from routers.

## Sprint 3 — Chain completeness
- **TRON facilitator impl** (TRC-20). Removes `scaffolded` from TRON; health
  reports `live` only after a real verifier.
- Solana SPL + EVM self-host verification hardened (EIP-3009 / EIP-7702),
  nonce/replay TTL'd caller-binding.

## Future
- `upto` scheme; **`batch-settlement`** scheme (fleet-approved future).
- Discovery resources/search + `/supported`.

## Acceptance
- All five workstreams merged with zero `[REVIEW]` interface mismatches.
- `/v1/x402/health` truthful: EVM+Solana `live`, TRON `scaffolded:needs_facilitator` until real.
- No duplicated x402 code across `rmi-backend` / archived `x402-gateway`.
