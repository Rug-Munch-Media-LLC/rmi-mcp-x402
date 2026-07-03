# rmi-mcp-x402 — Roadmap

**Next 90 days.**

## Week 1 — Consolidation decision
- Decide canonical repo for x402/MCP: rmi-mcp-x402, x402-gateway, or rmi-backend
- Archive or merge the duplicates

## Week 2 — Type the tools
- Add Pydantic input/output schemas for every MCP tool
- Register tools in a typed registry

## Week 3 — Health and deploy
- Add /health endpoint
- Create docker-compose for standalone deploy
- Add CI

## Week 4 — Cleanup
- Remove duplicate code from rmi-backend and x402-gateway
- Add Makefile and pre-commit

## Acceptance
- MCP server starts standalone
-  green
- CI green
- No duplicated x402 code
