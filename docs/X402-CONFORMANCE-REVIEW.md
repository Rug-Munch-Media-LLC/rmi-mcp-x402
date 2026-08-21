# x402 Conformance Cross-Check — 5-Workstream Interface Report

**Date:** 2026-08-21 · **Scope:** Read-only interface cross-check of the five
parallel x402 workstreams against the official spec (`docs/x402-official-spec/`,
authoritative) and the internal conformance contract
(`docs/X402-SPEC-CONTRACT.md`). Source = actual code in the `feat/x402-docs`
worktree. **Every item below is a `[REVIEW]` that the orchestrator must
reconcile before merge.**

## The five workstreams' intended interfaces (from their code)

1. **models** — `app/domain/x402/models.py` (+ `__init__.py` exports)
2. **middleware** — `app/domain/x402/middleware.py` (402 + headers)
3. **service** — `app/domain/x402/service.py` + `app/routers/x402_verifier.py`
   (`FacilitatorRouter`/`VerifierClient` verify/settle)
4. **router / HTTP surface** — `app/domain/x402/router.py` + `app/routers/x402_settle_api.py`
5. **tests** — assertion contract implied by the four above

---

## [REVIEW] 1 — Header naming: THREE conventions, must pick one (MERGE BLOCKER)

| Workstream | Wire header |
|---|---|
| middleware `require_payment` | client sends `X-Payment: base64({tx_hash, signature})`; no `X-Payment-ID` |
| verifier `verify_payment` docstring | reads `PAYMENT-SIGNATURE` (base64 PaymentPayload) |
| settle-api | reads `x_pay` body field / `X-PAY ` prefix (base64 PaymentPayload) |
| contract G2 | replay = `X-Payment-ID: <verified id>`; 402 carries `X-Payment-Required`/`X-Payment-Options` |
| official spec transports-v2 | `PAYMENT-REQUIRED` (srv→cl), `PAYMENT-SIGNATURE` (cl→srv), `PAYMENT-RESPONSE` (srv→cl) |

**Resolution needed:** adopt the official v2 header set and drop the internal
`X-Payment` / `X-Payment-ID` legacy, **or** _explicitly_ keep `X-Payment-ID` as a
repo-local alias and document the divergence.

## [REVIEW] 2 — VERIFY response key name differs across all sources

- router/middleware: `valid: bool`
- verifier (`x402_verifier`): `verified: bool` (+ `reason,tx_hash,payer,amount`)
- contract §5.1: `{"verified": bool, "reason", ...}`
- official spec `VerifyResponse`: `isValid` + `invalidReason`

Service implementation + tests must assert **one** key.

## [REVIEW] 3 — SETTLE success key name differs

- verifier `settle_payment`: `settled: bool`
- contract §5.2 + official `SettlementResponse`: `success` (+ `errorReason`),
  with non-empty `transaction` when `errorReason == settlement_pending`
- settle-api: `success: True`

Unify to `success` + `errorReason`.

## [REVIEW] 4 — Network identifier: CAIP-2 vs bare names

- verifier + official spec: CAIP-2 (`eip155:8453`, `solana:5eykt4UsFv8P8...`)
- legacy models/middleware/router + `service._default_facilitators`: bare
  `base`, `ethereum`, `arbitrum`, `solana`, `trx` (contract G5)
- settle-api health reports `"eip155:8453 (Base)"` (CAIP-2 ✓)

Legacy bare names must be removed everywhere.

## [REVIEW] 5 — Asset identifier: symbol vs contract address (contract VS official)

- official v2 spec: `asset` = **contract address** / ISO 4217
- contract examples + legacy: symbol `"USDC"`, `"SOL"`, `"TRX"`
- verifier: `asset` from payload but network map keys are the **addresses**
- settle-api: `USDC` symbol constant, address `0x8335...`

The five branches must agree on symbol-vs-address for `accepts[].asset`.

## [REVIEW] 6 — Duration between `amount` (official) and `maxAmountRequired` (contract)

- official spec field: `amount` (atomic string)
- contract §2.2 + `scheme_exact_svm.md`: `maxAmountRequired`
- verifier `_build_requirements`: `amount`

Two field names for one semantic — pick one for the `accepts[]` entries.

## [REVIEW] 7 — Payment identity / replay semantics (tx_hash nonce vs verified id)

- middleware + router `/receipts/{tx_hash}` + nonce-burn key on **tx_hash**;
  410 Gone on replay
- contract G2/G8: verified **payment-id**, caller-bound + TTL'd, header
  `X-Payment-ID`
- X402Handler (contract) records verified ids; **not present in code**
- tests must assert 402 vs 410 and caller-binding — currently unspecified

## [REVIEW] 8 — `FacilitatorRouter` interface is not the verifier's

- contract §5.1/5.2: `verify(payment_id, tx_hash, network, asset, amount_usd)`
  and `settle(...)`; extension `register_facilitator(network, impl)`
- actual: `VerifierClient.verify_payment(payload, network)/settle_payment(...)`
  wrapping the `x402` SDK; `X402Service` has neither; `register_facilitator`
  and `X402_SELF_FACILITATE` are **absent** from code

Contract and vendored-SDK signatures must be harmonised (the SDK's payload-based
signature is likely the real one; the contract params are aspirational).

## [REVIEW] 9 — models `__init__.py` exports conflict with PaymentRequired target

`__init__.py` re-exports legacy `router` + `X402Service` + legacy models while
the models workstream targets `PaymentRequired`/`PaymentRequirement`. After the
models rewrite these exports change; middleware/service/router (and tests)
imports must be co-committed to avoid breaking bindings.

## [REVIEW] 10 — payTo / pricing authority

- middleware `create_invoice`: placeholder `0xRMI_PLATFORM_WALLET` + HMAC
  signature (G3)
- middleware `PAY_TO` + settle `PAY_TO`: `0x1E3AC01d0fdb976179790BDD02823196A92705C9`
  (in-consensus) but verifier uses per-env `X402_EVM_PAY_TO` / `X402_SOL_PAY_TO`
- contract: server-authoritative `resolve_price_usd`/`validate_client_amount`;
  genuinely derived `payToAddress`

Reconcile hardcoded vs env payTo and implement atomic-unit pricing (G6).

---

## Conformance level documented (target)

Protocol **v1+v2**; scheme **exact** (EVP EIP-3009/EIP-7702, SVM SPL) **live**;
networks base/ethereum/arbitrum/optimism/polygon/solana **live**; **TRON
scaffolded** (needs real verifier; never reported live). Batch-settlement = future.

## Verdict

Only `app/routers/x402_verifier.py` reflects the spec-compliant target. The
`app/domain/x402/*` layer still ships the legacy T34/non-spec interfaces
(contract G1–G10 open). **Recommended merge order:** resolve [REVIEW] 1 + 2 + 3
(header + verb keys) first, then 4–6 (schema), then 7–10. Do not merge the
five workstreams until 1–3 are agreed.
