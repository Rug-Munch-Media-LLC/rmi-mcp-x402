"""Chain + address normalization shared by both DEX providers."""

from __future__ import annotations

import pytest

import data_mcp_dex_chains as chains

SOL = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
EVM = "0xAbC0000000000000000000000000000000000001"


@pytest.mark.parametrize(
    ("raw", "canon"),
    [
        ("ETH", "ethereum"),
        ("sol", "solana"),
        ("bnb", "bsc"),
        ("arb", "arbitrum"),
        ("polygon-pos", "polygon"),
        ("avax", "avalanche"),
        ("unichain", "unichain"),
    ],
)
def test_canonical_chain(raw: str, canon: str) -> None:
    assert chains.canonical_chain(raw) == canon


def test_unknown_chain_shape_is_rejected() -> None:
    with pytest.raises(ValueError):
        chains.canonical_chain("not a chain")


def test_provider_ids_differ_per_provider() -> None:
    assert chains.provider_chain("eth", "dexscreener") == "ethereum"
    assert chains.provider_chain("eth", "geckoterminal") == "eth"
    assert chains.provider_chain("polygon", "geckoterminal") == "polygon_pos"
    assert chains.provider_chain("avalanche", "geckoterminal") == "avax"


def test_address_normalization() -> None:
    assert chains.normalize_address("ethereum", EVM) == EVM.lower()
    assert chains.normalize_address("solana", SOL) == SOL  # case-sensitive
    assert chains.normalize_pair_id("solana", SOL) == SOL


def test_invalid_addresses_rejected_before_any_request() -> None:
    with pytest.raises(ValueError):
        chains.normalize_address("ethereum", "0xnot-an-address")
    with pytest.raises(ValueError):
        chains.normalize_address("solana", EVM)
    with pytest.raises(ValueError):
        chains.normalize_address("ethereum", "")
