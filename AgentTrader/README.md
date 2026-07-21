# AgentTrader

AgentTrader is the untrusted AI and orchestration side of Tradeval. It uses Pi to interpret
natural-language philosophies and propose desired portfolio weights from a frozen candidate
set. It never calculates executable orders, fills, cash, positions, returns, or risk metrics.

## Run

```bash
npm ci
AGENTTRADER_MODEL_ID=<model-id> npm run build
AGENTTRADER_MODEL_ID=<model-id> npm run serve
```

Configuration is explicit through `AGENTTRADER_MODEL_PROVIDER`, `AGENTTRADER_MODEL_ID`,
`RETAILTRADER_ROOT`, `RETAILTRADER_EXECUTABLE`, `AGENTTRADER_WORKSPACE_ROOT`, and
`AGENTTRADER_API_PORT`. Provider credentials remain environment variables and never enter
artifacts, logs, hashes, or API responses.

Long-running endpoints return `202` with a job ID. `GET /experiments/:id/events` emits
monotonic SSE IDs and accepts `Last-Event-ID` for reconnection. The local SQLite database is
an index; immutable JSON and JSONL artifacts are the source of audit truth.

## Verify

```bash
npm test
npm run typecheck
npm run build
```
