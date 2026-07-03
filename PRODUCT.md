# rmi-mcp-x402 — Product Charter

**Product:** RMI MCP + x402 Gateway
**Repo:** git.rugmunch.io/RugMunchMedia/rmi-mcp-x402
**Status:** Active development

## Problem
AI agents need typed, pay-per-call access to RMI crypto security tools without learning a bespoke API.

## User
- Claude Code / opencode / Cursor users
- Autonomous AI agents
- Developers building agent workflows

## Scope
- Model Context Protocol server exposing RMI tools
- x402 payment verification for paid tools
- Verified badge program logic

## Anti-scope
- No token scanning engine (lives in rmi-backend)
- No wallet generation (lives in walletpress)
- No web frontend (lives in rmi-frontend)

## Success criteria
- MCP server starts and exposes typed tools
-  returns ok
- x402 payments settle correctly
- No file >500 lines

## Current reality
- README exists but markets aspirational features
- Code overlaps heavily with x402-gateway
- x402 capability is also duplicated in rmi-backend
- No CI, no Makefile, no pre-commit

## Fleet dependencies
- RMI backend for actual security data
- Qdrant, Redis, Postgres (via rmi-backend)
