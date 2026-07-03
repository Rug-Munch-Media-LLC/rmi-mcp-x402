# rmi-mcp-x402 — MCP Server + x402 Gateway

MCP server and x402 payment surface for Rug Munch Intelligence.

## Scope
- Model Context Protocol server exposing RMI security tools
- x402 payment verification for paid tools
- Verified badge program domain logic

## Status
Active development. Not yet deployed as a standalone container. Code overlaps with x402-gateway and rmi-backend x402 routers; consolidation in progress.

## Fleet dependencies
- RMI backend for security data
- Qdrant, Redis, Postgres via rmi-backend

## Structure
app/mcp/          MCP server and manifest
app/domain/x402/  payment middleware, models, service, router
app/domain/verified/  badge domain
app/api/v1/       HTTP API routes

## Standards
This repo follows Rug Munch Media standards:
https://git.rugmunch.io/RugMunchMedia/standards
