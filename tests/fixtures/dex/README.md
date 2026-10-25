# DEX provider fixtures (recorded)

Captured once from the public APIs on 2026-10-25 and trimmed to 2 items per
list so the repo stays small. Tests replay these through `respx` — **no test
touches the network**. Re-record only when a provider changes shape:

```bash
python - <<'PY'   # see tests/test_dex_tools.py header for the recorded URLs
PY
```

| file | upstream |
| --- | --- |
| dexscreener_search.json | GET /latest/dex/search?q=bonk |
| dexscreener_pairs.json | GET /latest/dex/pairs/{chainId}/{pairAddress} |
| dexscreener_token.json | GET /latest/dex/tokens/{token} |
| dexscreener_boosts_latest.json | GET /token-boosts/latest/v1 |
| dexscreener_boosts_top.json | GET /token-boosts/top/v1 |
| dexscreener_profiles.json | GET /token-profiles/latest/v1 |
| geckoterminal_pools.json | GET /api/v2/networks/solana/pools |
| geckoterminal_token_pools.json | GET /api/v2/networks/solana/tokens/{token}/pools |
| geckoterminal_ohlcv.json | GET /api/v2/networks/solana/pools/{pool}/ohlcv/hour |
