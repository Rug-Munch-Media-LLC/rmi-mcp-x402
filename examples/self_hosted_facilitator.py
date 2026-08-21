#!/usr/bin/env python3
"""Coffer: stand up your OWN x402 facilitator — verify/settle with no Coinbase.

Coffer's selling point: you keep the keys and settle on-chain yourself. The
`FacilitatorRouter` verifies and settles payments and defaults to
"self_hosted" mode (EIP-3009 / EIP-7702 on EVM, SPL on Solana), needing NO
hosted facilitator. This wires the router explicitly and exposes self-hosted
`/verify` + `/settle` HTTP endpoints so clients pay YOUR rail.

Run:
    pip install fastapi uvicorn
    X402_SELF_FACILITATE=1 X402_WALLET=0xYourEVMAddr \
        X402_SELF_FACILITATOR_RPC=https://mainnet.base.org \
        PYTHONPATH=. uvicorn examples.self_hosted_facilitator:app --port 8010
    # (config via env; sees the same self-hosted verifier as `@coffer.require_payment`)
"""

from __future__ import annotations

from fastapi import FastAPI, Request

from _coffer_facade import coffer  # brand facade (falls back to app.domain.x402)

router = coffer.get_facilitator_router()
app = FastAPI(title="Coffer self-hosted facilitator", version="1.0.0")


# 1) A custom facilitator impl. Anything exposing
#      async verify(payment) -> VerifyResult
#      async settle(payment) -> str | None
#    is a valid facilitator (this is how TRON gets wired once an impl exists).
class MyVerifier:
    async def verify(self, payment: dict) -> dict:
        return {"verified": True, "reason": "self-hosted", "network": payment.get("network")}

    async def settle(self, payment: dict) -> str | None:
        # On-chain broadcast with YOUR key (EIP-3009/EIP-7702). In production
        # return the resulting tx_hash; None signals not-yet-settled.
        return "0x" + "00" * 32


# 2) Register a self-hosted verifier for the networks you settle. With
#    X402_SELF_FACILITATE=1 (default) the router already self-settles; this
#    makes the mesh explicit and extensible (e.g. plug in TRON later).
for network in ("eip155:8453", "eip155:1", "solana:mainnet"):
    coffer.register_facilitator(network, MyVerifier())


@app.get("/verify")
async def verify(payment_id: str, tx_hash: str, network: str, asset: str, amount: float) -> dict:
    """Self-hosted /verify — read-only, confirms the payment is valid."""
    return await router.verify(payment_id, tx_hash, network, asset, amount)


@app.get("/settle")
async def settle(payment_id: str, tx_hash: str, network: str, asset: str, amount: float) -> dict:
    """Self-hosted /settle — commits funds on-chain with YOUR keys."""
    return await router.settle(payment_id, tx_hash, network, asset, amount)


@app.get("/paid")
@coffer.require_payment(asset="USDC", network="base", amount=0.001)
async def paid_endpoint(request: Request) -> dict[str, str]:
    return {"data": "self-settled, sovereign, open"}


@app.get("/health")
async def health() -> dict:
    return {"mode": router.readiness()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8010)
