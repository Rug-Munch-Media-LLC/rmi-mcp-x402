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

Crypto (EIP-2612 path): the sponsor path now performs REAL signature
verification — the EIP-712 typed-data domain separator (name/version/chainId/
verifyingContract), the ``Permit`` struct hash, the ``\\x19\\x01`` digest, and
``ecrecover`` (via ``eth_keys``, mirroring the canonical
``x402/self_facilitator.py`` ecrecover pattern) so a forged or tampered permit
fails closed. ``build_permit_calldata`` emits the real ``permit(...)`` calldata
for the facilitator to broadcast.

Honest status: the sponsor path either delegates to a configured ``sponsor_impl``
(a real sponsor key) or reports ``scaffolded:needs_sponsor_key`` — it NEVER
returns a fabricated tx_hash and NEVER claims ``verified:true`` for the
on-chain settlement step it did not execute. On-chain replay protection for the
permit is the token's ``nonces(owner)`` counter — an RPC check the scaffold
cannot perform, so it is flagged [REVIEW] and remains a facilitator duty.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.domain.x402.service import VerifyResult, normalize_network_id, register_facilitator

log = logging.getLogger(__name__)

EIP2612_KEY = "eip2612GasSponsoring"
ERC20_APPROVAL_KEY = "erc20ApprovalGasSponsoring"
MAX_UINT256 = str(2**256 - 1)  # ERC-20 approval to Permit2

# Canonical UniversalPermit2 contract (same address on every EVM chain) — the
# ``spender`` of the EIP-2612 permit and the target of the ERC-20 approval.
PERMIT2_ADDRESS = "0x000000000022D473030F116dDEE9F6B43aC78BA3"

# EIP-712 domain + Permit typehashes (EIP-2612). Mirrors self_facilitator.py's
# EIP-3009 approach: the domain structure is the SAME; only the struct differs.
_EIP712_DOMAIN_STRING = (
    "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)
_PERMIT_STRING = "Permit(address owner,address spender,uint256 value,uint256 nonce,uint256 deadline)"
_PERMIT_SELECTOR = "0xd505accf"  # permit(address,address,uint256,uint256,uint8,bytes32,bytes32)

# Optional ecrecover-aware crypto layer (eth_keys/eth_utils are pyproject deps).
try:  # pragma: no cover - guarded optional import (mirrors domain service.py)
    from eth_keys import keys as _eth_keys  # type: ignore[import-untyped]
    from eth_utils.address import (  # type: ignore[import-untyped]
        to_canonical_address,
        to_checksum_address,
    )
    from eth_utils.crypto import keccak  # type: ignore[import-untyped]

    _ECRECOVER_AVAILABLE = True
except ImportError:  # pragma: no cover - defensive
    _eth_keys = None  # type: ignore[assignment]
    to_canonical_address = None  # type: ignore[assignment]
    to_checksum_address = None  # type: ignore[assignment]
    keccak = None  # type: ignore[assignment]
    _ECRECOVER_AVAILABLE = False


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


# ── EIP-2612 crypto (EIP-712 domain separator + Permit struct + ecrecover) ────


def _eip2612_available() -> bool:
    """True when the crypto backend (eth_keys/eth_utils) is importable."""
    return _ECRECOVER_AVAILABLE


def _enc_address(addr: str) -> bytes:
    return b"\x00" * 12 + to_canonical_address(addr)


def _enc_uint(v: int) -> bytes:
    return int(v).to_bytes(32, "big")


def _to_int(value: Any) -> int:
    """Parse an int from int / decimal str / 0x-hex str (EVM fields)."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.startswith(("0x", "0X")):
            return int(s, 16)
        return int(s, 10)
    raise ValueError(f"cannot parse {value!r} as integer")


def _chain_id_for(network: str) -> int | None:
    """Resolve the EVM chain id for a legacy name or CAIP-2 network id."""
    nid = normalize_network_id(network)
    if nid.startswith("eip155:"):
        try:
            return int(nid.split(":", 1)[1])
        except ValueError:
            return None
    return None


def _eip2612_domain_separator(
    *, name: str, version: str, chain_id: int, token_address: str
) -> bytes:
    """EIP-712 domain separator for an EIP-2612 token (verifyingContract = token)."""
    return keccak(
        keccak(text=_EIP712_DOMAIN_STRING)
        + keccak(text=name)
        + keccak(text=version)
        + _enc_uint(chain_id)
        + _enc_address(token_address)
    )


def _permit_struct_hash(
    *, owner: str, spender: str, value: int, nonce: int, deadline: int
) -> bytes:
    """EIP-712 struct hash of ``Permit(address owner,address spender,uint256
    value,uint256 nonce,uint256 deadline)`` (EIP-2612)."""
    return keccak(
        keccak(text=_PERMIT_STRING)
        + _enc_address(owner)
        + _enc_address(spender)
        + _enc_uint(value)
        + _enc_uint(nonce)
        + _enc_uint(deadline)
    )


def eip2612_digest(
    *,
    owner: str,
    spender: str,
    value: int,
    nonce: int,
    deadline: int,
    chain_id: int,
    token_address: str,
    name: str = "USD Coin",
    version: str = "1",
) -> bytes:
    """Compute the EIP-712 signed digest for an EIP-2612 permit.

    ``keccak("\\x19\\x01" || domainSeparator || structHash)`` per EIP-712 — the
    bytes the payer actually signed.
    """
    domain = _eip2612_domain_separator(
        name=name, version=version, chain_id=chain_id, token_address=token_address
    )
    struct = _permit_struct_hash(
        owner=owner, spender=spender, value=value, nonce=nonce, deadline=deadline
    )
    return keccak(b"\x19\x01" + domain + struct)


def _parse_signature(signature: Any) -> tuple[int, bytes, bytes] | None:
    """Parse a 65-byte (r||s||v) EIP-2612 signature -> (v, r, s). v in {27,28}."""
    if isinstance(signature, (bytes, bytearray)):
        raw = bytes(signature)
    elif isinstance(signature, str):
        try:
            raw = bytes.fromhex(signature.removeprefix("0x"))
        except ValueError:
            return None
    else:
        return None
    if len(raw) != 65:
        return None
    return int(raw[64]), raw[:32], raw[32:64]


def verify_permit_signature(
    payload: dict[str, Any], *, chain_id: int, name: str = "", version: str = ""
) -> tuple[bool, str, str]:
    """Verify an EIP-2612 permit signature, mirroring self_facilitator ecrecover.

    Returns ``(ok, reason, recovered_owner)``. The permit is verified when the
    recovered signer matches the payload ``owner`` AND the domain binds
    ``chain_id`` + ``token`` (verifyingContract). Fails closed when the crypto
    backend is unavailable, fields are malformed, or the signer mismatches.

    [REVIEW] This proves the permit is authentically signed by ``owner`` for
    THAT token/chain. It does NOT check the on-chain ``nonces(owner)`` counter,
    the token's actual ``name``/``version`` domain values, or the current block
    deadline — those are RPC reads the scaffold cannot perform and remain a
    facilitator duty before broadcast.
    """
    if not _ECRECOVER_AVAILABLE or _eth_keys is None:
        return False, "eip2612 crypto unavailable: eth_keys not installed", ""
    try:
        owner = to_checksum_address(str(payload.get("owner") or ""))
    except (ValueError, TypeError):
        return False, "missing/invalid permit 'owner'", ""
    try:
        spender = to_checksum_address(str(payload.get("spender") or PERMIT2_ADDRESS))
    except (ValueError, TypeError):
        return False, "missing/invalid permit 'spender'", ""
    try:
        token = to_checksum_address(str(payload.get("token") or ""))
    except (ValueError, TypeError):
        return False, "missing/invalid permit 'token' (verifyingContract)", ""
    try:
        value = _to_int(payload.get("amount") if payload.get("amount") is not None else payload.get("value") or 0)
        nonce = _to_int(payload.get("nonce") or 0)
        deadline = _to_int(payload.get("deadline") or 0)
    except ValueError:
        return False, "unparseable permit value/nonce/deadline", ""

    # EIP-2612 domain: the ERC-20 token's name + version (EIP-712 "version" is
    # the EIP-2612 version string, typically "1"; the x402 payload may carry it
    # explicitly via tokenName/tokenVersion, else fall back to env / default).
    name = (name or str(payload.get("tokenName") or "") or os.getenv("X402_EIP712_NAME", "USD Coin")).strip()
    version = (version or str(payload.get("tokenVersion") or "") or os.getenv("X402_EIP712_VERSION", "1")).strip()

    sig = _parse_signature(payload.get("signature"))
    if sig is None:
        return False, "malformed EIP-2612 signature (expected 65-byte r||s||v)", ""
    v, r, s = sig
    v_norm = v if v in (0, 1) else (v - 27 if v in (27, 28) else -1)
    if v_norm not in (0, 1):
        return False, "invalid EIP-2612 signature recovery id (v)", ""

    digest = eip2612_digest(
        owner=owner,
        spender=spender,
        value=value,
        nonce=nonce,
        deadline=deadline,
        chain_id=chain_id,
        token_address=token,
        name=name,
        version=version,
    )
    try:
        signature_obj = _eth_keys.Signature(
            vrs=(v_norm, int.from_bytes(r, "big"), int.from_bytes(s, "big"))
        )
        recovered_addr = to_checksum_address(
            _eth_keys.PublicKey.recover_from_msg_hash(digest, signature_obj).to_address()
        )
    except Exception as e:  # noqa: BLE001 - fail closed on any recover error
        return False, f"cannot recover permit signer: {e}", ""
    if recovered_addr != owner:
        return False, (
            f"permit signer {recovered_addr} != owner {owner}"
            f" (forged or tampered permit — fields changed the digest)"
        ), ""
    return True, "", recovered_addr


def build_permit_calldata(
    payload: dict[str, Any], *, chain_id: int, name: str = "", version: str = ""
) -> str:
    """Build the real ERC-20 ``permit(owner,spender,value,deadline,v,r,s)`` calldata.

    Dense ABI encoding of the permit so a sponsor key can broadcast it
    (funding gas + atomic ``settleWithPermit``). Returns ``0x``-prefixed hex,
    never a fabricated tx hash. Raises if the payload is not a valid,
    locally-verified permit.
    """
    ok, reason, _owner = verify_permit_signature(
        payload, chain_id=chain_id, name=name, version=version
    )
    if not ok:
        raise ValueError(f"refusing to build permit calldata: {reason}")
    owner = to_checksum_address(str(payload["owner"]))
    spender = to_checksum_address(str(payload.get("spender") or PERMIT2_ADDRESS))
    value = _to_int(payload.get("amount") if payload.get("amount") is not None else payload.get("value") or 0)
    deadline = _to_int(payload.get("deadline") or 0)
    parsed = _parse_signature(payload.get("signature"))
    if parsed is None:
        raise ValueError("malformed EIP-2612 signature for permit calldata")
    v, r, s = parsed
    # ABI: (address,address,uint256,uint256,uint8,bytes32,bytes32)
    # NOTE: the EIP-2612 permit nonce is NOT a calldata field — it is read
    # on-chain from token.nonces(owner) at permit() execution time.
    calldata = (
        bytes.fromhex(_PERMIT_SELECTOR[2:])
        + _enc_address(owner)
        + _enc_address(spender)
        + _enc_uint(value)
        + _enc_uint(deadline)
        + _enc_uint(v)
        + r.rjust(32, b"\x00")
        + s.rjust(32, b"\x00")
    )
    return "0x" + calldata.hex()


def _validate_permit2_approval_payload(info: dict[str, Any], key: str) -> list[str]:
    """Shared payload-shape validation for the two gas-sponsoring paths."""
    errors: list[str] = []
    if not isinstance(info, dict):
        return ["extension info must be an object"]
    if key == EIP2612_KEY:
        # Presence check tolerant of falsy-but-valid values: nonce/deadline/amount
        # may legitimately be 0 (a fresh owner's EIP-2612 nonce IS 0). We require
        # the key to be present and non-empty, not merely truthy.
        for f in ("token", "owner", "spender", "amount", "nonce", "deadline", "signature"):
            if f not in info or info.get(f) is None or str(info.get(f)).strip() == "":
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
    """Facilitator-side gas sponsoring with REAL EIP-2612 permit verification.

    The facilitator resolves the correct sponsor flow from the payment payload's
    extension info, VERIFIES the EIP-2612 permit signature locally (ecrecover),
    and returns an honest pre-settle payment object.

    On-chain settlement is NOT executed here:

    * with a configured ``sponsor_impl`` (a real sponsor key) the batch is
      delegated to it and its tx_hash returned;
    * WITHOUT one, the scaffold reports ``scaffolded:needs_sponsor_key`` and
      ``apply_gas_batch`` raises ``NotImplementedError`` — so no caller can
      mistake the scaffold for a verified settlement or record a fabricated hash.
    """

    key: str  # network id (CAIP-2 or legacy)
    payment: dict[str, Any]
    sponsor_impl: Callable[..., Awaitable[str]] | None = field(default=None)

    def _resolve_flow(self) -> str:
        exts: Any = (self.payment or {}).get("extensions") or {}
        if not isinstance(exts, dict):
            return "none"
        if EIP2612_KEY in exts:
            return EIP2612_KEY
        if ERC20_APPROVAL_KEY in exts:
            return ERC20_APPROVAL_KEY
        return "none"

    async def resolve_sponsor_flow(self) -> str:
        """Pick the sponsor flow (FULL extension key) from the payment payload.

        Returns ``EIP2612_KEY``, ``ERC20_APPROVAL_KEY``, or ``"none"`` — the
        full extension keys, not short labels, so callers (and this module's
        own validation) can address the correct payload member. (This fixes a
        scaffold bug where the short flow label was used as the extension key,
        so the EIP-2612 branch silently never validated its own permit.)
        """
        return self._resolve_flow()

    def _sponsor_ready(self) -> bool:
        """True only when a real sponsor implementation (sponsor key) is set."""
        return self.sponsor_impl is not None

    async def prepare_payment(self) -> dict[str, Any]:
        """Validate the payload *and* verify the EIP-2612 permit signature.

        Honest: structure + validation + (EIP-2612) REAL ecrecover implemented.
        A forged/tampered permit fails closed here (raises ValueError). The
        returned payment carries NO fabricated tx_hash — that is only provided
        by a real ``sponsor_impl``. Status is ``needs_sponsor_key`` when no
        sponsor key is configured; the on-chain step is never claimed verified.
        """
        flow = self._resolve_flow()
        exts: Any = (self.payment or {}).get("extensions") or {}
        info = exts.get(flow, {}) if (isinstance(exts, dict) and flow != "none") else {}
        kind = "eip2612" if flow == EIP2612_KEY else "erc20-approval" if flow == ERC20_APPROVAL_KEY else "none"
        errors = _validate_permit2_approval_payload(info, flow) if flow != "none" else []
        if errors:
            raise ValueError(f"invalid {flow} gas-sponsoring payload: {'; '.join(errors)}")

        chain_id = _chain_id_for(self.key or "")
        sponsored = self._sponsor_ready()

        if flow == EIP2612_KEY:
            if chain_id is None:
                raise ValueError(f"no EVM chain id known for {self.key}")
            if not _eip2612_available():
                raise ValueError("eip2612 crypto unavailable: eth_keys not installed")
            ok, reason, owner = verify_permit_signature(info, chain_id=chain_id)
            if not ok:
                raise ValueError(f"failed EIP-2612 permit verification: {reason}")
            status = "ready" if sponsored else "needs_sponsor_key"
            return {
                "sponsor_flow": kind,
                "permit2_method": "settleWithPermit",
                "scaffolded": status,
                "verified": True,  # signature-authentic only; on-chain still sponsor's duty
                "signer": owner,
                "permit_calldata": build_permit_calldata(info, chain_id=chain_id),
                "tx_hash": None,  # set by the on-chain sponsor only — never fabricated
            }

        # ERC-20 approval path: structure validated; the raw signed tx replay /
        # broadcast is the sponsor's job (no local ecrecover of a raw tx).
        status = "ready" if sponsored else "needs_sponsor_key"
        return {
            "sponsor_flow": kind,
            "permit2_method": "settle",
            "scaffolded": status,
            "tx_hash": None,  # set by the on-chain sponsor only — never fabricated
        }

    async def apply_gas_batch(self) -> str:
        """On-chain step — NEVER fabricated.

        With a configured ``sponsor_impl`` (a real sponsor key) the batch is
        delegated there and its returned tx_hash used. WITHOUT one, we raise
        so no caller can record a non-existent settlement; the honest status is
        ``scaffolded:needs_sponsor_key``.
        """
        if self.sponsor_impl is not None:
            return await self.sponsor_impl(self.payment)
        raise NotImplementedError(
            "gas sponsoring on-chain settlement is scaffolded:needs_sponsor_key "
            "(no sponsor_impl / sponsor key configured — nothing was broadcast)"
        )


def register_gas_sponsoring_facilitator(
    network: str, sponsor_impl: Callable[..., Awaitable[str]] | None = None
) -> GasSponsoringFacilitator:
    """Integrate gas sponsoring into the FacilitatorRouter.

    Wires a SponsoringFacilitator wrapper ``impl`` onto ``network`` via the
    router's ``register_facilitator``, exposing ``async verify(payment)`` and
    ``async settle(payment)`` (the contract documented on
    ``FacilitatorRouter.register_facilitator``).

    ``verify`` runs honest payload validation + (EIP-2612) local ecrecover;
    ``settle`` calls ``apply_gas_batch`` which delegates to a real sponsor or
    raises until one is configured. [REVIEW] The VerifyResult-as-verdict shape
    is the only facilitator contract exposed here; the extension spec does not
    itself define a verify step (assumption mapped onto the router contract).
    """

    async def _verify(payment: dict[str, Any]) -> VerifyResult:
        fac = GasSponsoringFacilitator(
            key=network, payment=payment, sponsor_impl=sponsor_impl
        )
        try:
            prepped = await fac.prepare_payment()
            return VerifyResult(
                verified=True,
                reason=f"scaffolded:{prepped['scaffolded']} flow={prepped['sponsor_flow']}",
                asset=(payment or {}).get("asset") or "",
                network=network,
            )
        except ValueError as e:
            return VerifyResult(
                verified=False,
                reason=str(e),
                asset=(payment or {}).get("asset") or "",
                network=network,
            )

    async def _settle(payment: dict[str, Any]) -> str | None:
        fac = GasSponsoringFacilitator(
            key=network, payment=payment, sponsor_impl=sponsor_impl
        )
        return await fac.apply_gas_batch()  # delegates to sponsor or raises honestly

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
    "PERMIT2_ADDRESS",
    "GasSponsoringFacilitator",
    "build_permit_calldata",
    "declare_eip2612_gas_sponsoring_extension",
    "declare_erc20_approval_gas_sponsoring_extension",
    "eip2612_digest",
    "register_gas_sponsoring_facilitator",
    "verify_permit_signature",
]
