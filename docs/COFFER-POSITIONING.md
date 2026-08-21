# Coffer — Positioning & Market Claim

> **The standard for "accept x402 payments on your own."** Sovereign, modular,
> the broadest payment surface, AI-native.

## Our claim

Coffer is the self-hosted x402 payment facilitator. It is the only option in the
2026 facilitator landscape that is simultaneously:

1. **Self-hosted** — settlement runs in your process on your keys
   (`SelfFacilitator`, EIP-3009 / EIP-7702 on EVM, SPL on Solana). No hosted
   facilitator required. Your treasury, your custody.
2. **Open source** — MIT. Every layer is auditable and forkable.
3. **The broadest payment surface** — [6 live networks × 24 live
   (network, asset) payment options](../README.md#payment-options), plus TRON
   scaffolded and an extension surface for `upto`/`batch-settlement`.
4. **AI-native** — MCP-first. Agents pay over HTTP/MCP with the same `@coffer`
   decorator that gates FastAPI routes. Built for the machine economy.
5. **2026 spec compliant** — exact v1 + v2 wire format, `PaymentRequired` /
   `PaymentPayload` / `SettlementResponse`, replay via `X-Payment-ID`.

## Why this wins

| Competitor class | Their constraint | Where Coffer wins |
| --- | --- | --- |
| Hosted facilitators (Coinbase/CDP, PayAI, Cloudflare) | settlement routes through their service | **self-hosting** — Coffer settles on your keys, no dependency |
| OSS facilitators (Fireblocks, Mogami) | Fireblocks couples to a hosted vault; Mogami is Docker + Base-only | **breadth + zero infra** — 6 live networks, 24 options, in-process |
| In-house DIY x402 | you own the whole protocol | **modular facade** — reuse the protocol, add one decorator |

Source for the competitor catalog:
[`docs/x402-official-spec/dev-tools__facilitators.md`](x402-official-spec/dev-tools__facilitators.md).

## The three sentences

- **Self-hosted** means no counterparty between you and your revenue.
- **OSS** means anyone can audit the rail that moves your money.
- **AI-native** means agents can pay your API without a human in the loop.

That is why Coffer is the no-brainer self-facilitator — for apps, for agents, for
anyone who wants an HTTP treasury they actually own.
