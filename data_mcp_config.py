#!/usr/bin/env python3
"""
RugMunch Data MCP — configuration.

Env loading (.env.helius), API keys, RPC endpoint tables, Postgres/HF settings.
"""

import os

# ── Config ────────────────────────────────────────────────────────
_env_file = os.path.join(os.path.dirname(__file__), ".env.helius")
if os.path.exists(_env_file):
    with open(_env_file) as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k, v)

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "")
PRY_API_URL = os.getenv("PRY_API_URL", "http://127.0.0.1:8005")
HELIUS_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}" if HELIUS_KEY else None

# ── RPC Endpoints ─────────────────────────────────────────────────
SOLANA_RPC = [
    {"name": "helius", "url": HELIUS_URL, "rate": 50, "cooldown": 0},
    {
        "name": "solana-labs",
        "url": "https://api.mainnet-beta.solana.com",
        "rate": 10,
        "cooldown": 0,
    },
    {"name": "lava", "url": "https://solana.lava.build", "rate": 20, "cooldown": 0},
    {"name": "zan", "url": "https://api.zan.top/solana-mainnet", "rate": 20, "cooldown": 0},
]

EVM_RPC = {
    "ethereum": [
        {"name": "drpc", "url": "https://eth.drpc.org", "rate": 30, "cooldown": 0},
        {
            "name": "publicnode",
            "url": "https://ethereum-rpc.publicnode.com",
            "rate": 20,
            "cooldown": 0,
        },
        {"name": "llamarpc", "url": "https://eth.llamarpc.com", "rate": 20, "cooldown": 0},
    ],
    "base": [
        {"name": "drpc", "url": "https://base.drpc.org", "rate": 30, "cooldown": 0},
        {"name": "publicnode", "url": "https://base-rpc.publicnode.com", "rate": 20, "cooldown": 0},
        {"name": "llamarpc", "url": "https://base.llamarpc.com", "rate": 20, "cooldown": 0},
    ],
    "arbitrum": [
        {"name": "drpc", "url": "https://arbitrum.drpc.org", "rate": 30, "cooldown": 0},
        {
            "name": "publicnode",
            "url": "https://arbitrum-one-rpc.publicnode.com",
            "rate": 20,
            "cooldown": 0,
        },
        {"name": "llamarpc", "url": "https://arbitrum.llamarpc.com", "rate": 20, "cooldown": 0},
    ],
}

SOL_RPC = [e for e in SOLANA_RPC if e["url"]]
EVM_CHAINS = list(EVM_RPC.keys())

# ── Archive / Postgres configuration ─────────────────────────────
HF_HUB_TOKEN = os.getenv("HF_HUB_TOKEN", "")
HF_REPO = os.getenv("HF_REPO_ID", "rugmunchmedia/solana-archives")
HF_BASE_URL = f"https://huggingface.co/datasets/{HF_REPO}/resolve/main"

PG_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
PG_PORT = int(os.getenv("POSTGRES_PORT", "5433"))
PG_DB = os.getenv("POSTGRES_DB", "entity_labels")
PG_USER = os.getenv("POSTGRES_USER", "labeler")
PG_PASS = os.getenv("POSTGRES_PASSWORD", "")
