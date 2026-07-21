# AgentTrader Artifacts

Each job owns one workspace. `api-request.json` freezes the accepted request and idempotency
identity. `events.jsonl` records monotonic stage, command, log, artifact, and error events.
`inputs/mandate.json` and `inputs/agent-protocol.json` freeze financial and model constraints.

Each decision session stores its candidate set, raw Pi proposal, deterministic adjudication,
and the hashes linking those records to RetailTrader's transition journal. Pi run records retain
model identity, raw assistant messages, tool arguments, usage, and latency. They contain no API
keys. A restart trusts only terminal Pi artifacts and committed RetailTrader transitions.

SQLite indexes job ID, experiment ID, status, stage, timestamps, and result paths. Deleting the
index must not change financial truth; it can be reconstructed from versioned artifacts.
