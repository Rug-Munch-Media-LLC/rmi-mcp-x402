# Data MCP — tool contracts & capability map

Canonical contract for the tools served by `data_mcp.py` (FastMCP). One
chain-data server; new market data is added as **tools on this server**, never as
a second MCP.

## 1. Response envelope (every `dex_*` tool)

```json
{"data": <provider payload>, "source": "dexscreener|geckoterminal",
 "fetched_at": 1792904400.123, "stale": false, "age_s": 0.4}
```

| field | meaning |
| --- | --- |
| `data` | provider payload, trimmed to the tool's `limit`; never `null` on success |
| `source` | provider id (`dexscreener`, `geckoterminal`) |
| `fetched_at` | epoch seconds when the payload was fetched upstream (not served) |
| `stale` | `true` only when served from last-known-good after an upstream failure |
| `age_s` | `fetched_at` → now, seconds |

A tool **never** returns an empty success. No data ⇒ typed failure.

## 2. Failure contract

Provider down, throttled, or schema drift raises `data_mcp_dex_core.DataUnavailable`:

| attribute | meaning |
| --- | --- |
| `source` | provider id |
| `reason` | `rate limited by upstream`, `http 503`, `not found upstream`, `transport: ConnectError`, `unexpected response schema` |
| `status` | upstream HTTP status when one exists (`None` for transport errors) |
| `payload()` | `{"type": "DataUnavailable", "source", "reason", "status"?}` for structured callers |

FastMCP surfaces the raise as a tool error — callers branch on it instead of
guessing whether an empty list meant "no data" or "provider died".

## 3. Cache, rate limit, retry

| provider | TTL | stale grace | gate | backoff |
| --- | --- | --- | --- | --- |
| DexScreener | 30 s (60 s for boosts/profiles) | 300 s | 5 req/s | 3 attempts, `Retry-After` → 0.5 s, 1 s |
| GeckoTerminal | 60 s | 900 s | 1 req/s, 2 s min interval | 3 attempts, `Retry-After` → 0.5 s, 1 s |

4xx other than 429 is not retried (the request itself is wrong). `429` and `5xx`
retry with exponential backoff. Concurrent callers pass through a per-source gate
(`data_mcp_dex_core.Gate`), so a burst queues instead of tripping the provider limit.

## 4. Tool surface

### DexScreener (`data_mcp_dex.py`, api.dexscreener.com, no key)

| tool | upstream | notes |
| --- | --- | --- |
| `dex_search(query, limit=20)` | `GET /latest/dex/search` | free-text symbol/name/address |
| `dex_pairs(chain, pair)` | `GET /latest/dex/pairs/{chainId}/{pairId}` | address normalized per chain |
| `dex_token(address, chain="")` | `GET /latest/dex/tokens/{addrs}` | comma-separated, max 30 |
| `dex_boosts(kind="latest", limit=20)` | `GET /token-boosts/{latest|top}/v1` | paid-promotion signal |
| `dex_profiles(limit=20)` | `GET /token-profiles/latest/v1` | team-submitted metadata |

### GeckoTerminal (`data_mcp_gecko.py`, api.geckoterminal.com/api/v2, no key)

| tool | upstream | notes |
| --- | --- | --- |
| `dex_pools(network, token="", query="", page=1, limit=20)` | `/networks/{net}/pools`, `/networks/{net}/tokens/{addr}/pools`, `/search/pools` | token takes precedence over query |
| `dex_ohlcv(network, pool, resolution="hour", limit=100, before_timestamp=0)` | `/networks/{net}/pools/{pool}/ohlcv/{tf}` | `resolution` = `<timeframe>[:<aggregate>]`; minute 1/5/15, hour 1/4/12, day 1 (e.g. `hour:4`) |

### Existing surface (unchanged)

`sol_*` (Solana RPC), `evm_*`/`xchain_*` (EVM RPC + cross-chain profile),
`intel_*` (wallet intel, DuckDB/Postgres archives), `web_scrape` (Pry engine).

## 5. Normalization (`data_mcp_dex_chains.py`)

- Canonical chains: `ethereum solana bsc base arbitrum polygon optimism avalanche`;
  aliases accepted (`eth`, `sol`, `bnb`, `arb`, `matic`, `avax`, …).
- Provider ids: DexScreener `ethereum` / GeckoTerminal `eth`; `polygon` →
  `polygon_pos`, `avalanche` → `avax`. Unknown lowercase ids pass through.
- Addresses: EVM must match `^0x[0-9a-fA-F]{40}$` and is lowercased; Solana must
  match base58 32-44 and is **never** lowercased (case-sensitive); anything else
  raises `ValueError` before a request is made.

## 6. Non-goals (explicit)

- **No Dextools, no Arkham** — separate lanes own them; do not add tools here.
- **No HTML proxying** — these tools speak provider JSON only; page scraping stays
  in `web_scrape` (Pry).
- **No second chain server** — tools register on the existing FastMCP instance.

## 7. Verify

```bash
ruff check data_mcp_dex*.py data_mcp_gecko.py tests/test_dex_*.py tests/test_gecko_tools.py
python -m pytest tests/test_dex_contract.py tests/test_dex_normalize.py tests/test_dex_tools.py tests/test_gecko_tools.py -q
```

Tests replay recorded fixtures (`tests/fixtures/dex/`) through `respx`; they never
touch the network.
