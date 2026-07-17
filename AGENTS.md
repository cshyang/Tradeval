# AgentTrader working conventions

AgentTrader is the AI and experiment-orchestration service for the AgentTrader Lab product.
RetailTrader remains the deterministic trust boundary for market data, screening, risk,
orders, fills, portfolio accounting, and evaluation.

## Hard boundaries

- Pi may generate philosophies and untrusted decision proposals.
- AgentTrader must not calculate executable orders, fills, cash, positions, returns, or risk metrics.
- AgentTrader exchanges versioned immutable JSON artifacts with RetailTrader through CLI JSON envelopes.
- Credentials come from environment variables only and must never enter artifacts, logs, events, or hashes.
- Do not expose coding, filesystem, shell, MCP, or browser tools to a trading agent.
- Hindsight output must be labeled `HINDSIGHT SCENARIO`; model knowledge is never historical evidence.

## Engineering

- Use Node.js 22, TypeScript ESM, strict types, and exact dependency versions.
- Keep worker stdout protocol-only; operational logs go to stderr.
- Prefer immutable values, explicit validation, deterministic serialization, and injected clocks/processes in tests.
- Do not import RetailTrader Python modules. Launch its CLI with argument arrays, never shell strings.
