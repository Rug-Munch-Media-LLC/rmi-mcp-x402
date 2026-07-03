# rmi-mcp-x402 — Architecture

**Status:** canonical
**Last updated:** 2026-07-03

## Components
- app/mcp/ — MCP server and manifest
- app/domain/x402/ — payment middleware, models, service, router
- app/domain/verified/ — verified badge domain
- app/api/v1/ — HTTP API routes

## Data flow


## Current issues
- Duplicates x402-gateway backend/app code
- README claims 8+ tools; actual implementation may differ
- Largest file is 100 KB (x402_enforcement.py copy)
- No CI

## Deployment
Not currently deployed as a standalone container.
