# Flue Integration

The v0 launcher is local, but worker ownership is isolated behind `WorkerTransport` and
`JobScheduler`. The NDJSON protocol contains strict request, progress, result, cancellation,
and error messages. Stdout is protocol-only; operational logs use stderr.

Adopt Flue when jobs require remote workers, concurrent ownership, deploy-independent forward
schedules, or a shared queue/status API. Flue may replace launching and claiming only. It must
not change API routes, Pi tool schemas, candidate/proposal contracts, RetailTrader commands, or
artifact hashes.
