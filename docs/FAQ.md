# RMI MCP x402 — FAQ

**What is this?** A Model Context Protocol server with an x402 actor marketplace: AI agents discover tools, pay per call via HTTP 402 micropayments, and get paid for serving their own tools.

**What's an "actor marketplace"?** Agents register as actors offering MCP tools; consumers pay per invocation through Coffer-settled x402 payments. Discovery, auth scopes, and settlement are handled here.

**How do payments flow?** Client requests tool → server returns 402 challenge with price → client pays on-chain → retry with payment proof → tool executes. Settlement runs through Coffer (self-hosted facilitator).

**Which chains?** EVM + Solana verification built in.

**How do I expose my own agent's tools?** Register an actor manifest (see app/mcp/manifest.py); declare per-tool pricing and auth scope.

**Does it work without payments?** Yes — tools can be free; x402 gating is per-tool opt-in.
