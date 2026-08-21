"""Gas-sponsored approval extensions — EIP-2612 + ERC-20 Permit2 onboarding.

Implements two official x402 extensions whose only integration point is the
FACILITATOR side ("Gas sponsoring extensions ... inject batch signing
capabilities into the settlement flow so the facilitator can sponsor gas on
behalf of the payer" — extensions overview):

1. ``extensions__eip2612-gas-sponsoring.md`` — gasless EIP-2612 ``permit()``.
   Client signs a permit authorizing Permit2 off-chain, facilitator submits it
   atomically during settlement (`x402ExactPermit2Proxy.settleWithPermit`).
2. ``extensions__erc20-approval-gas-sponsoring.md`` — universal fallback for
   tokens WITHOUT EIP-2612. Client signs (not broadcasts) a raw
   ``approve(Permit2, MaxUint256)`` tx, facilitator funds gas + broadcasts it
   atomically before settling (`x402ExactPermit2Proxy.settle`).

Scaffold level: the DECLARATION + client payload-shape + facilitator sponsor
internals are wired (structure, signing intent, payload validation). The
ON-CHAIN settlement step (actual ``settleWithPermit`` / atomic gas-funding
batch) is marked ``scaffolded:needs_facilitator`` — it is NEVER claimed to be
verified on a live chain.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.domain.x402.service import VerifyResult, register_facilitator

log = logging.getLogger(__name__)

EIP2612_KEY = "eip2612GasSponsoring"
ERC20_APPROVAL_KEY = "erc20ApprovalGasSponsoring"
MAX_UINT256 = str(2**256 - 1)  # ERC-20 approval to Permit2


def declare_eip2612_gas_sponsoring_extension() -> dict[str, dict[str, Any]]:
    """Advertise EIP-2612 gas sponsoring (spec §Server Usage / How It Works)."""
    return {
        EIP2612_KEY: {
            "type": "eip2612",
            "assetTransferMethod": "permit2",
            "description": "Gasless EIP-2612 permit; facilitator submits on chain",
        }
    }


def declare_erc20_approval_gas_sponsoring_extension() -> dict[str, dict[str, Any]]:
    """Advertise ERC-20 approval gas sponsoring (spec §Server Usage)."""
    return {
        ERC20_APPROVAL_KEY: {
            "type": "erc20-approval",
            "assetTransferMethod": "permit2",
            "sponsorsGas": True,
            "description": "Facilitator funds gas + broadcasts approve(Permit2) then settles",
        }
    }


def _validate_permit2_approval_payload(info: dict[str, Any], key: str) -> list[str]:
    """Shared payload-shape validation for the two gas-sponsoring paths."""
    errors: list[str] = []
    if not isinstance(info, dict):
        return ["extension info must be an object"]
    if key == EIP2612_KEY:
        for f in ("token", "owner", "spender", "amount", "nonce", "deadline", "signature"):
            if not info.get(f):
                errors.append(f"EIP-2612 permit missing '{f}'")
        sig: Any = info.get("signature")
        if isinstance(sig, str) and not sig.startswith("0x"):
            errors.append("EIP-2612 signature must be 0x-prefixed")
    else:  # ERC-20 approval
        if not info.get("token"):
            errors.append("signed approval missing 'token'")
        if not info.get("signedTransaction"):
            errors.append("ERC-20 approval missing 'signedTransaction'")
        # The signed raw approve tx must target the canonical Permit2 (spec: approve(Permit2)).
        tx: Any = info.get("signedTransaction")
        if isinstance(tx, dict) and tx.get("to") and "permit2" not in str(tx.get("to", "")).lower():
            errors.append("approve() 'to' must be the canonical Permit2 contract")
    return errors


@dataclass
class GasSponsoringFacilitator:
    """Facilitator-side scaffold for gas sponsoring (spec §Facilitator).

    The facilitator resolves the correct sponsor flow from the payment
    payload's extension info, then hands a signed, pre-settled batch to the
    chain hook. On-chain settlement is NOT implemented here:

    ``scaffolded:needs_facilitator`` — a real facilitator must perform the
    atomic on-chain step (``settleWithPermit`` for EIP-2612; fund-gas +
    broadcast ``approve`` + ``settle`` for ERC-20) and return a tx_hash.
    Until then ``apply_gas_batch`` raises ``NotImplementedError`` so callers
    can never mistake the scaffold for a verified settlement.
    """

    key: str
    payment: dict[str, Any]
    sponsor_impl: Callable[..., Awaitable[str]] | None = field(default=None)

    async def resolve_sponsor_flow(self) -> str:
        """Pick the sponsor flow from the payment payload's extension info."""
        exts: Any = (self.payment or {}).get("extensions") or {}
        if not isinstance(exts, dict):
            return "none"
        if EIP2612_KEY in exts:
            return "eip2612"
        if ERC20_APPROVAL_KEY in exts:
            return "erc20-approval"
        return "none"

    async def prepare_payment(self) -> dict[str, Any]:
        """Validate payload *and* return a pre-settle payment object.

        This is the honest part: structure + validation implemented. The
        returned payment is missing an on-chain tx_hash — that is provided by
        the real sponsor_impl (marked ``scaffolded:needs_facilitator``).
        """
        flow = await self.resolve_sponsor_flow()
        exts: Any = (self.payment or {}).get("extensions") or {}
        info = exts.get(flow, {}) if isinstance(exts, dict) else {}
        errors = _validate_permit2_approval_payload(info, flow) if flow != "none" else []
        if errors:
            raise ValueError(f"invalid {flow} gas-sponsoring payload: {'; '.join(errors)}")
        return {
            "sponsor_flow": flow,
            "permit2_method": "settleWithPermit" if flow == "eip2612" else "settle",
            "scaffolded": "needs_facilitator",
            "tx_hash": None,  # set by the on-chain facilitator only
        }

    async def apply_gas_batch(self) -> str:
        """On-chain step — NEVER run by this scaffold.

        A real facilitator must implement this. We raise instead of returning
        a fake hash so no caller can record a non-existent settlement.
        """
        if self.sponsor_impl is not None:
            return await self.sponsor_impl(self.payment)
        raise NotImplementedError(
            "gas sponsoring on-chain settlement is scaffolded:needs_facilitator "
            "(no sponsor_impl configured)"
        )


def register_gas_sponsoring_facilitator(
    network: str, sponsor_impl: Callable[..., Awaitable[str]] | None = None
) -> GasSponsoringFacilitator:
    """Integrate gas sponsoring into the FacilitatorRouter.

    Wires a SponsorFacilitator wrapper ``impl`` onto ``network`` via the
    router's ``register_facilitator``, exposing ``async verify(payment)`` and
    ``async settle(payment)`` (the contract documented on
    ``FacilitatorRouter.register_facilitator``).

    ``verify`` runs the honest payload validation; ``settle`` calls
    ``apply_gas_batch`` which raises until a real sponsor_impl is provided.

    [REVIEW] The gas-sponsoring extensions are officially FACILITATOR-side
    extensions injected into settlement. We integrate through the domain's
    ``register_facilitator(network, impl)`` seam (the only facilitator
    registration point that exists here). The VerifyResult-as-verdict shape
    is REUSED, but the extension spec does not itself define a verify step —
    the return type is an assumption mapped onto the router contract.
    """

    async def _verify(payment: dict[str, Any]) -> VerifyResult:
        fac = GasSponsoringFacilitator(key=network, payment=payment, sponsor_impl=sponsor_impl)
        try:
            prepped = await fac.prepare_payment()
            return VerifyResult(
                True,
                reason=f"scaffolded:{prepped['scaffolded']} flow={prepped['sponsor_flow']}",
                asset=(payment or {}).get("asset") or "",
                network=network,
            )
        except ValueError as e:
            return VerifyResult(
                False,
                str(e),
                asset=(payment or {}).get("asset") or "",
                network=network,
            )

    async def _settle(payment: dict[str, Any]) -> str | None:
        fac = GasSponsoringFacilitator(key=network, payment=payment, sponsor_impl=sponsor_impl)
        return await fac.apply_gas_batch()  # raises NotImplementedError until real sponsor

    class _Wrapper:
        async def verify(self, payment: dict[str, Any]) -> VerifyResult:
            return await _verify(payment)

        async def settle(self, payment: dict[str, Any]) -> str | None:
            return await _settle(payment)

    register_facilitator(network, _Wrapper())
    log.info("x402_gas_sponsoring_registered", extra={"network": network})
    return GasSponsoringFacilitator(key=network, payment={}, sponsor_impl=sponsor_impl)


__all__ = [
    "EIP2612_KEY",
    "ERC20_APPROVAL_KEY",
    "MAX_UINT256",
    "GasSponsoringFacilitator",
    "declare_eip2612_gas_sponsoring_extension",
    "declare_erc20_approval_gas_sponsoring_extension",
    "register_gas_sponsoring_facilitator",
]
