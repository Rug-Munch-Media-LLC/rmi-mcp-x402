# rmi-mcp-x402 — MCP Server + x402 Payment Protocol

> **Pay-per-call crypto security tools for AI agents.**
> Part of [Rug Munch Intelligence](https://rugmunch.io) — Wyoming-based.

## What Is This?

This is the **MCP (Model Context Protocol) server** and **x402 payment gateway** for Rug Munch Intelligence. It provides:

- **8+ MCP tools** for AI agents (Claude Code, Cursor, opencode, etc.)
- **x402 pay-per-call** — AI agents pay with USDC, no API keys
- **32 token detectors** — honeypot, blacklist, ownership, etc.
- **96 chains** — Ethereum, BSC, Solana, Arbitrum, etc.
- **RAG-powered investigation** — AI-powered threat reports

## Quickstart

```bash
pip install rugmunch-mcp
```

```python
# In your AI agent config (claude_desktop_config.json, etc.)
{
  "mcpServers": {
    "rugmunch": {
      "command": "rugmunch-mcp",
      "args": ["--api-key", "your-key"]
    }
  }
}
```

## MCP Tools

| Tool | Description | Price (x402) |
|------|-------------|-------------|
| `rugmunch_scan_token` | Full token scan (32 detectors) | $0.001/call |
| `rugmunch_wallet_forensics` | Wallet behavior analysis | $0.01/call |
| `rugmunch_rug_probability` | AI rug prediction | $0.005/call |
| `rugmunch_contract_audit` | Smart contract security | $0.05/call |
| `rugmunch_real_time_alert` | Real-time threat monitoring | $0.001/min |
| `rugmunch_threat_intel` | Threat intelligence lookup | $0.002/call |
| `rugmunch_address_labels` | Wallet label lookup | $0.001/call |
| `rugmunch_chain_info` | Multi-chain information | Free |

## Architecture

```
AI Agent (Claude/Cursor/opencode)
    ↓ MCP protocol
rugmunch-mcp server
    ↓ x402 payment (USDC micropayments)
Backend API
    ↓ 32 detectors + 52 data providers
Results back to agent
```

Part of Rug Munch Intelligence (RMI) — the Bloomberg Terminal of Crypto Security.
