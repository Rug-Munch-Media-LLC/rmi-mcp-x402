# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Rug Munch Media LLC
# --- settlement_normalize.py: normalize a facilitator settle body ---
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def normalize_settlement(
    data: dict[str, Any],
    payment_id: str,
    tx_hash: str,
    network: str,
    facilitator: str,
) -> dict[str, Any]:
    # M2 honesty: do not fabricate success on any HTTP-200 body -> require
    # an explicit settlement/verification signal from the facilitator.
    success = bool(
        data.get("success") is True
        or data.get("isValid") is True
        or data.get("verified") is True
        or data.get("settled") is True
        or bool(data.get("transactionHash") or data.get("transaction"))
    )
    return {
        "success": success,
        "transaction": data.get("transactionHash", data.get("transaction", tx_hash)),
        "network": network,
        "payment_id": payment_id,
        "facilitator": facilitator,
        "settled_at": datetime.now(UTC).isoformat(),
        "block_number": data.get("blockNumber", data.get("block_number", "")),
        "block_hash": data.get("blockHash", data.get("block_hash", "")),
        "confirmations": data.get("confirmations", 0),
        "amount_paid": data.get("amountPaid", data.get("amount_paid", "")),
        "reason": ""
        if success
        else (data.get("error", data.get("reason", "facilitator returned no settlement signal"))),
        "receipt": data,
    }
