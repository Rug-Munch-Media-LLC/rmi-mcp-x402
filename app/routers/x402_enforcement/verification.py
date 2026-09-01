"""x402_enforcement.verification — payment payload verification (router + self-verify)."""

from __future__ import annotations

import json
import logging
import time

from app.routers.x402_enforcement.config import CHAIN_USDC, EVM_PAY_TO, get_redis
from app.routers.x402_enforcement.pricing import TOOL_PRICES, _ensure_tool_prices

logger = logging.getLogger("x402_enforcement_v2")


async def verify_payment_via_router(payload: dict) -> dict:
    """Verify x402 payment payload through the multi-facilitator smart router.

    Flow:
    1. Parse payload → extract network (chain) and asset (token)
    2. Map network to chain_key (e.g. 'eip155:8453' → 'base', 'tron:mainnet' → 'tron')
    3. Determine token symbol from asset address
    4. Route to best facilitator via FacilitatorRouter.verify()
    5. Fall back to old verify_payment() if router not available
    """
    accepted = payload.get("accepted", {})
    network = accepted.get("network", "")
    asset_address = accepted.get("asset", "")

    # Map network → chain_key
    chain_key = None
    token_symbol = "USDC"

    for ck, cfg in CHAIN_USDC.items():
        if cfg["network"] == network:
            chain_key = ck
            # Detect token from asset address
            if asset_address:
                token_symbol = _detect_token_from_asset(asset_address, cfg)
            break

    if not chain_key:
        # Unknown network — fall back to old verifier
        from app.routers.x402_middleware import PaymentVerifier

        verifier = PaymentVerifier()
        return await verifier.verify_payment(
            json.dumps(payload),
            network_key="base",
        )

    # Try the smart router first
    try:
        from app.facilitators.router import get_facilitator_router

        router = get_facilitator_router()

        # Build requirements from payload
        requirements = {
            "x402Version": payload.get("x402Version", 2),
            "resource": payload.get("resource", {}),
            "accepts": [
                {
                    "scheme": accepted.get("scheme", "exact"),
                    "network": network,
                    "asset": asset_address,
                    "amount": accepted.get("amount", ""),
                    "payTo": accepted.get("payTo", ""),
                    "maxTimeoutSeconds": accepted.get("maxTimeoutSeconds", 180),
                    "extra": accepted.get("extra", {}),
                }
            ],
        }

        result = await router.verify(
            payload=payload,
            chain_key=chain_key,
            token_symbol=token_symbol,
            requirements=requirements,
        )

        if result.verified:
            logger.info(
                f"Router verified payment via {result.facilitator}: "
                f"chain={chain_key} token={token_symbol} amount={result.amount}"
            )
            return {
                "verified": True,
                "reason": result.reason,
                "tx_hash": result.tx_hash,
                "payer": result.payer,
                "amount": result.amount,
                "chain": chain_key,
                "token": token_symbol,
                "facilitator": result.facilitator,
                "method": f"router:{result.facilitator}",
            }
        else:
            logger.warning(
                f"Router rejected payment for {chain_key}/{token_symbol}: {result.reason}"
            )
            return {
                "verified": False,
                "reason": result.reason,
                "chain": chain_key,
                "token": token_symbol,
            }

    except ImportError:
        logger.debug("Facilitator router not available — falling back to old verifier")
    except Exception as e:
        logger.error(f"Router verification error: {e} — falling back to old verifier")

    # Fallback: old verification logic
    return await verify_payment(payload)


def _detect_token_from_asset(asset_address: str, chain_cfg: dict) -> str:
    """Detect token symbol from asset address using chain config."""
    asset_lower = asset_address.lower()

    # Check USDC
    if chain_cfg.get("usdc", "").lower() == asset_lower:
        return "USDC"

    # Check additional tokens
    tokens = chain_cfg.get("tokens", {})
    for symbol, addr in tokens.items():
        if isinstance(addr, str) and addr.lower() == asset_lower:
            return symbol

    # Heuristic detection
    if "TR7NH" in asset_lower:
        return "USDT"  # USDT on TRC20
    if "TEkxi" in asset_lower:
        return "USDC"  # USDC on TRC20
    if "TPYm" in asset_lower:
        return "USDD"  # USDD on TRC20
    if asset_lower == "native" or asset_address == "BTC":
        return "BTC"

    return "USDC"  # Default


# ── Original verification logic (fallback) ──


async def verify_payment(payload: dict) -> dict:
    """Verify x402 payment using the official SDK.

    Uses x402ResourceServer + HTTPFacilitatorClient for cryptographic verification.
    Falls back to on-chain self-verification for chains without facilitator support.
    """
    accepted = payload.get("accepted", {})
    network = accepted.get("network", "")

    # Step 1: SDK verification (EIP-712 via facilitator)
    try:
        from app.routers.x402_verifier import get_verifier

        v = get_verifier()
        result = await v.verify_payment(payload, network=network)

        if result.get("verified"):
            logger.info(
                "sdk_verification_success",
                extra={
                    "network": network,
                    "payer": str(result.get("payer", ""))[:16],
                },
            )
            return result

        logger.warning(
            "sdk_verification_failed",
            extra={"network": network, "reason": result.get("reason", "unknown")},
        )
    except ImportError:
        logger.error("x402_verifier_not_available")
        return {"verified": False, "reason": "x402 SDK verifier not available"}
    except Exception as e:
        logger.error("sdk_verification_error", extra={"error": str(e)[:200]})

    # Step 2: Self-verification fallback (on-chain tx receipt check)
    chain_cfg = None
    for ck, cc in CHAIN_USDC.items():
        if cc["network"] == network:
            chain_cfg = cc
            break

    if chain_cfg and chain_cfg.get("verify") == "self":
        return await self_verify_evm_usdc(payload, chain_cfg.get("name", "").lower(), chain_cfg)

    return {"verified": False, "reason": f"Verification failed for network: {network}"}


async def self_verify_evm_usdc(payload: dict, chain_key: str, chain_cfg: dict) -> dict:
    """Self-verify a USDC transfer on EVM chains without a facilitator.
    _ensure_pay_addresses()

    How it works:
    1. Extract tx hash, network, sender from the x402 payload
    2. Look up the transaction receipt on Etherscan/BSCScan/etc via eth_getTransactionReceipt
    3. Decode ERC-20 Transfer events from receipt logs (topic0 = keccak256("Transfer(address,address,uint256)"))
    4. Verify: correct USDC contract, 'to' matches PAY_TO, amount matches expected price in atoms
    5. Mark the tx as spent in Redis with 86400s TTL (prevent double-use, 24h window)
    """
    # ERC-20 Transfer event signature: keccak256("Transfer(address,address,uint256)")
    transfer_event_topic0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

    try:
        accepted = payload.get("accepted", {})
        tx_hash = payload.get("txHash") or payload.get("signature") or accepted.get("transaction")
        payer = payload.get("payer") or payload.get("from") or accepted.get("payer")
        amount_atoms = accepted.get("amount") or payload.get("amount")

        if not tx_hash:
            return {"verified": False, "reason": "No tx hash in payment payload"}

        # Prevent double-spend: check Redis first
        r = get_redis()
        if r:
            spent_key = f"x402:spent_tx:{tx_hash}"
            if r.get(spent_key):
                return {"verified": False, "reason": f"Transaction {tx_hash[:16]}... already used"}

        # Resolve expected amount from tool pricing if not in payload
        tool_id = (
            accepted.get("extra", {}).get("tool")
            or payload.get("tool")
            or accepted.get("resource", "").split("/")[-1]
            if accepted.get("resource")
            else None
        )
        _ensure_tool_prices()
        if not amount_atoms and tool_id and tool_id in TOOL_PRICES:
            amount_atoms = TOOL_PRICES[tool_id].get("price_atoms")

        # ── Blockscout API v2 (free, no API key needed) ──
        # Maps chain_id → Blockscout base URL
        chain_id = chain_cfg.get("chain_id")
        if not isinstance(chain_id, int):
            return {
                "verified": False,
                "reason": f"No Blockscout URL for chain_id {chain_id}",
            }
        blockscout_urls = {
            1: "https://eth.blockscout.com",
            42161: "https://arbitrum.blockscout.com",
            10: "https://optimism.blockscout.com",
            137: "https://polygon.blockscout.com",
            8453: "https://base.blockscout.com",
            100: "https://gnosis.blockscout.com",
            43114: "https://avalanche.blockscout.com",
            250: "https://fantom.blockscout.com",
            56: "https://bsc.blockscout.com",
        }

        blockscout_base = blockscout_urls.get(chain_id)
        if not blockscout_base:
            return {"verified": False, "reason": f"No Blockscout URL for chain_id {chain_id}"}

        import httpx

        # Blockscout v2 API: /api/v2/transactions/{tx_hash}
        tx_url = f"{blockscout_base}/api/v2/transactions/{tx_hash}"

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(tx_url)
            if resp.status_code != 200:
                return {
                    "verified": False,
                    "reason": f"TX {tx_hash[:16]}... not found on {chain_key} (HTTP {resp.status_code})",
                }
            tx_data = resp.json()

        # Check transaction status
        tx_status = tx_data.get("status") or tx_data.get("result")
        if tx_status != "ok" and tx_status != "1":
            return {
                "verified": False,
                "reason": f"TX {tx_hash[:16]}... failed on-chain (status={tx_status})",
            }

        # Decode ERC-20 Transfer events from receipt logs
        our_address = (EVM_PAY_TO or "").lower()
        usdc_address = chain_cfg["usdc"].lower()
        expected_amount = str(amount_atoms) if amount_atoms else None

        logs = tx_data.get("decoded_input", {}).get("logs") or tx_data.get("logs") or []
        if not logs:
            # Try alternate: fetch receipt directly
            receipt_url = f"{blockscout_base}/api?module=transaction&action=gettxreceiptstatus&txhash={tx_hash}"
            async with httpx.AsyncClient(timeout=10) as client2:
                r2 = await client2.get(receipt_url)
                if r2.status_code == 200:
                    receipt_data = r2.json()
                    logs = receipt_data.get("result", {}).get("logs", []) or receipt_data.get(
                        "logs", []
                    )

        transfer_found = False
        amount_match = False
        actual_amount = None

        for log in logs:
            topics = log.get("topics", [])

            if len(topics) < 3:
                continue
            if topics[0].lower() != transfer_event_topic0:
                continue
            if log.get("address", "").lower() != usdc_address:
                continue

            # Decode 'to' address from topic2
            try:
                to_address = "0x" + topics[2][-40:].lower()
            except (IndexError, ValueError):
                continue

            if to_address != our_address:
                continue

            # Decode amount from data field
            transfer_found = True
            log_data = log.get("data", "0x")
            try:
                if log_data and log_data.startswith("0x") and len(log_data) >= 66:
                    actual_amount = str(int(log_data[:66], 16))
                elif log_data:
                    actual_amount = str(int(log_data, 16))
            except (ValueError, TypeError):
                logger.warning(
                    f"Could not decode Transfer data for {tx_hash[:16]}...: {str(log_data)[:20]}"
                )

            # Verify amount matches
            if expected_amount and actual_amount:
                if actual_amount == expected_amount:
                    amount_match = True
                else:
                    logger.warning(
                        f"Amount mismatch for {tx_hash[:16]}...: "
                        f"expected {expected_amount} atoms, got {actual_amount} atoms on {chain_key}"
                    )
            elif actual_amount:
                # Check if amount matches any tool price
                for _tid, pricing in TOOL_PRICES.items():
                    if str(pricing.get("price_atoms")) == actual_amount:
                        amount_match = True
                        break

            break  # Found Transfer to our address

        if not transfer_found:
            return {
                "verified": False,
                "reason": f"No USDC Transfer to {our_address[:10]}... found in {tx_hash[:16]}...",
            }

        if not amount_match and expected_amount and actual_amount:
            return {
                "verified": False,
                "reason": f"Amount mismatch: expected {expected_amount} atoms, got {actual_amount} atoms",
            }

        # Mark as spent in Redis with 86400s (24h) TTL
        tool_name = tool_id or "unknown"
        if r:
            spent_key = f"x402:spent_tx:{tx_hash}"
            payment_data = {
                "chain": chain_key,
                "payer": payer or "unknown",
                "amount": actual_amount or amount_atoms or "0",
                "tool": tool_name,
                "timestamp": time.time(),
            }
            r.setex(spent_key, 86400, json.dumps(payment_data))

        logger.info(
            f"Verified USDC payment: {tx_hash[:16]}... on {chain_key} "
            f"from {payer[:10] if payer else 'unknown'}... amount={actual_amount or 'unknown'} tool={tool_name}"
        )
        return {
            "verified": True,
            "chain": chain_key,
            "tx_hash": tx_hash,
            "payer": payer,
            "amount": actual_amount or amount_atoms,
            "method": "blockscout-verify",
        }

    except Exception as e:
        logger.error(f"Self-verify error: {e}")
        return {"verified": False, "reason": f"Verification error: {str(e)[:100]}"}


# ── Refund policy helpers ──
