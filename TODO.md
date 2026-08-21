# rmi-mcp-x402 — TODO

Reflects the **post-spec-compliance** state. Writing docs is done; the work is
now reconciling the five parallel workstreams and closing remaining spec gaps.

## Reconcile five workstream interfaces (MERGE BLOCKER)
- [ ] `[REVIEW]` Models: land `PaymentRequired`/`PaymentRequirement`/`PaymentOption`; retire `PaymentRequest`/legacy receipts
- [ ] `[REVIEW]` Middleware: adopt one header convention (X-Payment-ID vs official PAYMENT-SIGNATURE/PAYMENT-REQUIRED/PAYMENT-RESPONSE) and stop parsing `X-Payment: base64({tx_hash,signature})`
- [ ] `[REVIEW]` Service: introduce `FacilitatorRouter.verify/settle(...)` + `register_facilitator(network,impl)`; settle one verify response key (`verified`/`isValid`/`valid`)
- [ ] `[REVIEW]` Router: replace `POST /api/v1/x402/verify` + `receipts/{tx_hash}` with verified-payment-id semantics
- [ ] `[REVIEW]` Tests: one shared assertion contract (verify key, 402 vs 410 status, CAIP-2 network ids)

## Spec conformance (contract G1–G10 closure)
- [ ] Finish delegating `app/domain/x402/*` to the vendors `x402-py` API (G10) — verifier done, middleware/models pending
- [ ] CAIP-2 everywhere — remove bare `base`/`ethereum`/`solana`/`trx` in domain + service defaults (G5)
- [ ] Server-authoritative pricing (`resolve_price_usd`/`validate_client_amount`) replacing HMAC-invoice placeholder `0xRMI_PLATFORM_WALLET` (G3/G6)
- [ ] `VERIFICATION_STATUS` exposed via `/v1/x402/health` (G9); honest `scaffolded` for TRON
- [ ] Prune legacy `app/routers/x402_*` after domain+verifier own the path

## Next
- [ ] Add `/v1/x402/health`, `/supported` and proposal for discovery endpoints
- [ ] Standalone docker-compose + deploy

## Blocked
- [ ] **(dependency)** TRON facilitator impl — real TRC-20 verifier required; scaffold must never report `verified`

## Done
- README / ARCHITECTURE / CONFORMANCE-REVIEW rewritten to spec reality
- CI + Makefile + pre-commit hooks present (previously claimed absent)
