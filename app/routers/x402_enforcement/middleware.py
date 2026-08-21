"""x402_enforcement.middleware — the x402 enforcement ASGI/Starlette middleware."""

from __future__ import annotations

import base64 as _base64
import json
import logging
import os
import re

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app.core.redis import get_redis
from app.routers.protection import get_client_id

from app.routers.x402_enforcement.config import CHAIN_USDC, SECURITY_HEADERS
from app.routers.x402_enforcement.policy import check_trial, _record_refundable_payment
from app.routers.x402_enforcement.spec import build_402_response, parse_x_pay_header
from app.routers.x402_enforcement.verification import verify_payment_via_router

logger = logging.getLogger("x402_enforcement_v2")


async def x402_enforcement_middleware(request: Request, call_next) -> Response:
    """
    x402 payment enforcement with Chain of Responsibility pattern.
    Delegates early-exit checks (free paths, bot detection) to the
    enforcement chain, then handles payment/trial/DataBus routing.
    """
    path = request.url.path

    # Only intercept x402-tools
    if not path.startswith("/api/v1/x402-tools/"):
        return await call_next(request)

    # ── Chain of Responsibility ──────────────────────────────────
    # Early-exit checks: free paths, bot detection
    try:
        from app.routers.enforcement_chain import (
            SECURITY_HEADERS as CHAIN_SECURITY_HEADERS,
            BotDetector,
            EnforcementChain,
            EnforcementContext,
            FreeEndpointRouter,
        )

        chain = EnforcementChain().add(FreeEndpointRouter()).add(BotDetector())

        ctx = EnforcementContext(request)
        ctx.path = path
        ctx.method = request.method
        ctx.user_agent = (request.headers.get("User-Agent", "") or "").lower()

        for step in chain._steps:
            result = await step.process(request, ctx)
            if result is not None:
                return result

    except ImportError:
        pass  # Chain module not available
    except Exception as e:
        logger.error(f"enforcement_chain_error: {e}")

    # ── Internal bypass: trial GET-to-POST re-dispatch ──────────────
    if request.headers.get("X-RMI-Internal") == "trial-granted":
        response = await call_next(request)
        response.headers["X-RMI-Trial"] = "true"
        remaining_hdr = request.headers.get("X-RMI-Trial-Remaining", "0")
        response.headers["X-RMI-Trial-Remaining"] = remaining_hdr
        for k, v in SECURITY_HEADERS.items():
            if k not in response.headers:
                response.headers[k] = v
        return response

    # ── Developer API Key Check ─────────────────────────────────
    try:
        from app.routers.developer_tier import check_developer_key

        dev_result = await check_developer_key(request)
        if dev_result is not None:
            if dev_result.get("deny"):
                status_code = dev_result.get("status_code", 429)
                return JSONResponse(
                    status_code=status_code,
                    content={
                        "error": dev_result.get("error", "rate_limit_exceeded"),
                        "tier": dev_result.get("tier", "free"),
                        "message": dev_result.get("error", "Rate limit exceeded"),
                        "daily_limit": dev_result.get("daily_limit"),
                        "calls_today": dev_result.get("calls_today"),
                        "upgrade_url": "https://rugmunch.io/mcp-docs#pricing",
                    },
                    headers={
                        **SECURITY_HEADERS,
                        "X-RMI-Tier": dev_result.get("tier", "free"),
                        "X-RMI-Remaining": str(dev_result.get("remaining_today", 0)),
                        "Retry-After": str(dev_result.get("retry_after", 60)),
                    },
                )
            request.state.developer_key = True
            request.state.developer_tier = dev_result.get("tier", "free")
            request.state.developer_email = dev_result.get("email", "")
            response = await call_next(request)
            response.headers["X-RMI-Tier"] = dev_result.get("tier", "free")
            response.headers["X-RMI-Remaining"] = str(dev_result.get("remaining_today", 0))
            return response
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Developer key check failed: {e}")

    # ── Burst protection: rate limit rapid requests ───────────────
    client_id = get_client_id(request)
    # Extract tool name early for burst tracking
    path_tool_id = path.rstrip("/").split("/")[-1] if path.count("/") >= 4 else "unknown"
    r = get_redis()
    if r and re.match(r"^[a-zA-Z0-9_]+$", path_tool_id):
        burst_key = f"x402:burst:{client_id}:{path_tool_id}"
        try:
            burst_count = int(r.get(burst_key) or 0)
            BURST_LIMIT = int(os.getenv("X402_BURST_LIMIT", "5"))
            BURST_WINDOW = int(os.getenv("X402_BURST_WINDOW", "60"))
            if burst_count >= BURST_LIMIT:
                logger.warning(
                    f"Burst limit: {client_id} hit {burst_count} requests in {BURST_WINDOW}s"
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": f"Rate limited. Max {BURST_LIMIT} requests per {BURST_WINDOW}s per tool.",
                        "retry_after": BURST_WINDOW,
                    },
                    headers={**SECURITY_HEADERS, "Retry-After": str(BURST_WINDOW)},
                )
            pipe = r.pipeline()
            pipe.incr(burst_key)
            if burst_count == 0:
                pipe.expire(burst_key, BURST_WINDOW)
            pipe.execute()
        except Exception:
            pass  # Don't block on burst tracking errors

    # Free paths already checked above — flow continues to paid tool enforcement
    # Extract tool name
    tool_id = path.rstrip("/").split("/")[-1]

    # Input validation: reject tool IDs with suspicious characters
    if not re.match(r"^[a-zA-Z0-9_]+$", tool_id):
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid tool identifier"},
            headers=SECURITY_HEADERS,
        )

    client_id = get_client_id(request)

    # ── Payment header parsing: support both v1 (x-pay) and v2 (PAYMENT-SIGNATURE) ──
    # v2 specifies PAYMENT-SIGNATURE header (base64 JSON), v1 uses X-Pay or x-pay
    pay_sig = request.headers.get("PAYMENT-SIGNATURE", "") or request.headers.get(
        "Payment-Signature", ""
    )
    x_pay = request.headers.get("x-pay", "") or request.headers.get("X-Pay", "")

    # Reject oversized payment headers (DoS protection)
    if pay_sig and len(pay_sig) > 16384:
        return JSONResponse(
            status_code=400,
            content={"error": "Payment header too large"},
            headers=SECURITY_HEADERS,
        )
    if x_pay and len(x_pay) > 8192:
        return JSONResponse(
            status_code=400,
            content={"error": "Payment header too large"},
            headers=SECURITY_HEADERS,
        )

    # Parse payment payload from whichever header is present
    # v2 PAYMENT-SIGNATURE takes priority over v1 x-pay
    payload = None
    if pay_sig:
        payload = parse_x_pay_header(pay_sig)  # parse_x_pay handles base64 too
        if payload:
            # v2 spec: payload should have top-level x402Version, accepted, payload, resource
            # Normalize: make sure "accepted" is available at top level for our verifier
            if "accepted" not in payload and "payload" in payload:
                # This is a v2 PaymentPayload format — extract payment info
                pass  # The verify_payment_via_router already handles this format
    elif x_pay:
        payload = parse_x_pay_header(x_pay)

        if payload:
            # Validate payment payload structure
            if not isinstance(payload, dict):
                return JSONResponse(
                    status_code=400,
                    content={"error": "Invalid payment payload structure"},
                    headers=SECURITY_HEADERS,
                )
            accepted = payload.get("accepted", {})
            if not isinstance(accepted, dict):
                return JSONResponse(
                    status_code=400,
                    content={"error": "Invalid accepted payment structure"},
                    headers=SECURITY_HEADERS,
                )
            result = await verify_payment_via_router(payload)
            if result.get("verified"):
                # Payment OK — attach info and proceed
                request.state.x402_verified = True
                request.state.x402_payer = result.get("payer", "")
                request.state.x402_chain = result.get("chain", "")
                request.state.x402_tx_hash = result.get("tx_hash", "")
                request.state.x402_amount = result.get("amount", "")
                request.state.x402_method = result.get("method", "")

                # Track facilitator payment success for health monitoring
                facilitator = result.get("method", result.get("facilitator", ""))
                if facilitator:
                    try:
                        from app.routers.facilitator_health import record_payment_success

                        record_payment_success(facilitator, True)
                    except Exception:
                        pass

                response = await call_next(request)
                response.headers["X-RMI-Payment"] = "verified"

                # ── x402 v2: PAYMENT-RESPONSE header (base64 SettlementResponse) ──
                # The v2 spec requires a PAYMENT-RESPONSE header on 200 responses
                # after successful settlement, containing base64 JSON:
                # {success: true, transaction: "0x...", network: "eip155:8453", payer: "0x..."}
                settlement_response = {
                    "success": True,
                    "transaction": result.get("tx_hash", ""),
                    "network": CHAIN_USDC.get(result.get("chain", ""), {}).get(
                        "network", result.get("chain", "")
                    ),
                    "payer": result.get("payer", ""),
                }
                if result.get("amount"):
                    settlement_response["amount"] = result.get("amount")
                try:
                    payment_response_b64 = _base64.b64encode(
                        json.dumps(settlement_response, separators=(",", ":")).encode()
                    ).decode("ascii")
                    response.headers["PAYMENT-RESPONSE"] = payment_response_b64
                except Exception:
                    pass  # Don't fail on settlement header encoding errors

                # Add security headers to successful responses too
                for k, v in SECURITY_HEADERS.items():
                    if k not in response.headers:
                        response.headers[k] = v

                # ── Lightweight refund check (no body reading) ──
                # Body-parsing for empty data detection was removed because it:
                #   a) Breaks streaming responses
                #   b) Reads entire response into memory
                #   c) Duplicates logic that belongs in tool handlers
                # Instead: flag HTTP errors for refund. Empty-data detection
                # is handled by tool handlers setting X-RMI-Result header.
                if response.status_code >= 400:
                    _record_refundable_payment(
                        tx_hash=request.state.x402_tx_hash or "unknown",
                        chain=request.state.x402_chain,
                        payer=request.state.x402_payer,
                        amount=request.state.x402_amount,
                        tool=tool_id,
                        reason=f"HTTP {response.status_code} error response",
                    )
                    response.headers["X-RMI-Refund-Flagged"] = "true"
                    logger.info(
                        f"Flagged payment for refund: tx={request.state.x402_tx_hash} "
                        f"tool={tool_id} reason=HTTP_{response.status_code}"
                    )

                return response

            # Payment verification failed — track failure for health monitoring
            facilitator = result.get("method", result.get("facilitator", ""))
            if facilitator:
                try:
                    from app.routers.facilitator_health import record_payment_success

                    record_payment_success(facilitator, False)
                except Exception:
                    pass

    # No valid payment — check free trials
    try:
        can_trial, remaining = check_trial(tool_id, client_id, consume=False)
    except Exception as e:
        logger.error(f"Trial check failed for {tool_id}: {e}")
        can_trial, remaining = False, 0

    if can_trial:
        # ── Consume the trial now (atomic INCR in Redis) ──
        # The pre-check above used consume=False; now we actually consume it
        try:
            _, remaining_after = check_trial(tool_id, client_id, consume=True)
            remaining = remaining_after
        except Exception as e:
            logger.error(f"Trial consumption failed for {tool_id}/{client_id}: {e}")

        # Allow free trial execution
        # ── Execute the tool directly via DataBus caching shield ──
        # This eliminates the fragile httpx GET-to-POST roundtrip.
        # All 127 tools are available through the DataBus — no need for
        # internal HTTP bouncing through POST-only route handlers.
        try:
            from app.caching_shield.tool_data import td

            # Build params from request (query params for GET, JSON body for POST)
            params = {}
            if request.method == "GET" and request.query_params:
                params = dict(request.query_params)
            elif request.method == "POST":
                try:
                    params = await request.json()
                except Exception:
                    params = {}

            result = await td.call_tool(tool_id, params)

            if result is not None:
                # Trial execution succeeded — return the result
                # Use raw Response (not JSONResponse) to avoid BaseHTTPMiddleware
                # body_iterator consumption bug when returning from middleware directly.
                response_body = json.dumps(result, default=str)
                response = Response(
                    content=response_body,
                    status_code=200,
                    media_type="application/json",
                    headers={
                        "X-RMI-Trial": "true",
                        "X-RMI-Trial-Remaining": str(remaining),
                        **SECURITY_HEADERS,
                    },
                )
                return response
            else:
                # DataBus returned None — tool not available via DataBus.
                # For GET requests: try httpx GET-to-POST conversion.
                # For POST requests: fall through to original route handler.
                if request.method == "GET" and request.query_params:
                    import httpx

                    query_params = dict(request.query_params)
                    base_url = str(request.url).split("?")[0]
                    try:
                        async with httpx.AsyncClient(timeout=30) as client:
                            resp = await client.post(
                                base_url,
                                json=query_params,
                                headers={
                                    "Content-Type": "application/json",
                                    "X-RMI-Internal": "trial-granted",
                                    "X-RMI-Trial": "true",
                                    "X-RMI-Trial-Remaining": str(remaining),
                                    "X-Device-Id": request.headers.get("x-device-id", ""),
                                },
                            )
                            if resp.status_code == 404:
                                # Tool has no POST route handler (DataBus-only tool that failed).
                                # Trial was consumed — return 200 with "no data" result.
                                # The client got their free call, data just wasn't available.
                                return Response(
                                    content=json.dumps(
                                        {
                                            "tool": tool_id,
                                            "result": "no_data_available",
                                            "trial": True,
                                            "trial_remaining": remaining,
                                            "message": "Free trial used. Data not available for this address — try a real token address.",
                                        }
                                    ),
                                    status_code=200,
                                    media_type="application/json",
                                    headers={
                                        "X-RMI-Trial": "true",
                                        "X-RMI-Trial-Remaining": str(remaining),
                                        **SECURITY_HEADERS,
                                    },
                                )
                            response = Response(
                                content=resp.content,
                                status_code=resp.status_code,
                                media_type=resp.headers.get("content-type", "application/json"),
                                headers={
                                    k: v
                                    for k, v in resp.headers.items()
                                    if k.lower() not in ("content-length", "transfer-encoding")
                                },
                            )
                    except Exception:
                        # httpx failed — return "no data" response (trial was consumed)
                        return Response(
                            content=json.dumps(
                                {
                                    "tool": tool_id,
                                    "result": "no_data_available",
                                    "trial": True,
                                    "trial_remaining": remaining,
                                }
                            ),
                            status_code=200,
                            media_type="application/json",
                            headers={
                                "X-RMI-Trial": "true",
                                "X-RMI-Trial-Remaining": str(remaining),
                                **SECURITY_HEADERS,
                            },
                        )
                else:
                    # POST request or GET without params — try call_next
                    response = await call_next(request)
                    # If call_next returns 404, the tool has no POST route handler.
                    # Trial was consumed — return "no data" response instead of 404.
                    if response.status_code == 404:
                        return Response(
                            content=json.dumps(
                                {
                                    "tool": tool_id,
                                    "result": "no_data_available",
                                    "trial": True,
                                    "trial_remaining": remaining,
                                    "message": "Free trial used. Data not available for this address — try a real token address.",
                                }
                            ),
                            status_code=200,
                            media_type="application/json",
                            headers={
                                "X-RMI-Trial": "true",
                                "X-RMI-Trial-Remaining": str(remaining),
                                **SECURITY_HEADERS,
                            },
                        )
                    response.headers["X-RMI-Trial"] = "true"
                    response.headers["X-RMI-Trial-Remaining"] = str(remaining)
                    for k, v in SECURITY_HEADERS.items():
                        if k not in response.headers:
                            response.headers[k] = v
                    return response

        except ImportError:
            logger.warning("DataBus caching shield not available, falling back to call_next")
            response = await call_next(request)
            response.headers["X-RMI-Trial"] = "true"
            response.headers["X-RMI-Trial-Remaining"] = str(remaining)
            for k, v in SECURITY_HEADERS.items():
                if k not in response.headers:
                    response.headers[k] = v
            return response
        except Exception as e:
            logger.error(f"Trial execution via DataBus failed for {tool_id}: {e}")
            # Trial consumed but execution failed — refund the trial
            try:
                r = get_redis()
                if r:
                    key = f"x402:trial:{client_id}:{tool_id}"
                    r.decr(key)  # Roll back the consumption
            except Exception:
                pass
            # Return 402 — trial should be refunded since we couldn't execute
            return build_402_response(tool_id, client_id=client_id)

    # No trials left — return 402
    return build_402_response(tool_id, client_id=client_id)
