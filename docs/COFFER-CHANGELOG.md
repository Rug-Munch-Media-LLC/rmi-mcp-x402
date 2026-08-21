# Coffer — Build Record & Change Log

> **What we built** across the Coffer workstream, and the honesty fixes that
> make the docs above accurate. Mirrors the source of truth in the code
> (`app/domain/x402/`, `vendor/x402/`, `coffer/`). Last updated 2026-08-21.

## Summary

Coffer is now a spec-compliant, self-hosted x402 payment facilitator with a
brand facade (`coffer`), a vendored x402 reference SDK, packaged 2026 protocol
extensions, real verified settlement paths, and honest reporting of what is and
isn't live. The headline of the build: **we stopped overclaiming.** Nothing is
reported verified or settled unless a real facilitator proved it.

## 1. Spec compliance (G1–G10) — closed

The `x402` protocol layer was brought into line with the official 2026 spec
(`docs/x402-official-spec/`, v1 + v2) and the internal conformance contract
(`docs/X402-SPEC-CONTRACT.md`):

| Gap | Fix |
| --- | --- |
| **G1** | Non-spec 402 body → spec-canonical `PaymentRequirement` / `PaymentOption` (`accepts[]`, `x402Version`) |
| **G2** | Non-spec `X-Payment` header → spec headers (`PAYMENT-REQUIRED` / `X-Payment-Required` / `X-Payment-Options`) |
| **G3** | Placeholder invoice + HMAC → server-authoritative `payTo` / atomic `maxAmountRequired` |
| **G4** | Hand-rolled scanner → facilitator verify/settle contract |
| **G5** | Bare network names → **CAIP-2** (`eip155:*`, `solana:mainnet`) with legacy normalisation |
| **G6** | No pricing enforcement → `resolve_price_usd` + `validate_client_amount` (atomic compare, underpayment rejected) |
| **G7** | Single-asset surface → multi-asset `accepts[]` via `build_multi_asset_accepts` |
| **G8** | tx_hash-only replay → caller-bound, TTL'd `X-Payment-ID` (atomic nonce reuse) |
| **G9** | Opaque health → honest `VERIFICATION_STATUS` on `/v1/x402/health` (live vs scaffolded) |
| **G10** | Reimplemented protocol → **vendored `x402-py`** reference SDK (`vendor/x402/`) |

## 2. Vendored x402-py reference SDK

`vendor/x402/` now carries the official reference implementation (`scheme.py`,
`facilitator.py`, `self_facilitator.py`, `solana_verifier.py`, `handler.py`,
`middleware.py`, `batch.py`) so the domain delegates protocol logic instead of
reimplementing it — exposed via `coffer.spec`.

## 3. Rebrand: pry → Coffer

The product surfaced under a single brand: `coffer/` is the public facade
(`require_payment`, `get_facilitator_router`, `register_facilitator`,
`coffer.mcp` manifest); the python package is `coffer` (v5.0.0). Legacy
`pry-*` names removed from the codebase.

## 4. Honesty fixes (C1 / C2 / H1 / H2 / H3 / M1 / M2)

This was the security-honesty pass. Details:

| ID | Fix |
| --- | --- |
| **C1** | EIP-7702 self-verify reads on-chain receipts but does **not** broadcast a settlement → **fail closed**: `success:false`, `reason:"needs_facilitator … verify-only"`. A fabricated settlement record is impossible. |
| **C2** | Solana SPL RPC verifies on-chain receipts but does **not** broadcast → **fail closed** on settle, same guarantee. |
| **H1** | Gas-sponsoring (EIP-2612 / ERC-20 approval) without a `sponsor_impl` **never reports verified** and never fabricates a `tx_hash`; settlement raises `not implemented — needs_facilitator`. |
| **H2** | Atomic nonce: in-memory replay store serializes check-then-add under `asyncio.Lock`; Redis `SETNX` remains canonical for production. |
| **H3** | **No bare-success verify:** a bare `"success":true` is not proof of payment — require an explicit `isValid`/`verified` signal. |
| **M1** | Gas-sponsoring economics stated honestly: **buyer pays payment gas; sponsor fronts approval gas only.** Un-sponsored sponsoring is never reported verified. |
| **M2** | No fabricated success on any HTTP-200 body — require an explicit settlement/verification signal (`success`/`isValid`/`verified`/`settled` true, or a transaction reference) before reporting `success`. |

E2E proof: `tests/test_x402_e2e.py` (9) + `tests/test_extensions_e2e.py` (10) =
**19 end-to-end tests** covering round-trip 402 → verify → settle, replay
rejection, and extension hooks (once-only side effects across two-phase settle).

## 5. Modularisation

`service.py` decomposed into six cohesive concerns — **replay** (ReplayStore),
**pricing** (atomic usd/amount), **facilitator_config** (FacilitatorConfig +
env), **self_facilitator** (SelfFacilitator), **evm_verify** (EIP-7702
receipt-only), **router** (FacilitatorRouter). Layering is strict: facade →
domain → vendored spec, with extensions on top and the MCP manifest exported at
`coffer.mcp`.

## 6. Brand docs

`README.md` (SEO hook, live/scaffolded payment matrix, honesty section,
comparison with citation flags, quickstart), `ARCHITECTURE.md` (layering +
402 flow + honest status), and this changelog. Examples in `examples/` are the
runnable proof: `fastapi_paid_endpoint.py`, `mcp_server_paid.py`,
`self_hosted_facilitator.py`.

---

### Open / still honest

- **TRON**, **`upto`**, **batch-settlement**, and un-sponsored **gas-sponsoring**
  remain scaffolded — never reported verified until a facilitator/sponsor impl
  lands.
- Competitor-prowess claims in the README table are curated from the official
  facilitator catalog and carry `[REVIEW]` flags pending publish-time citation.
