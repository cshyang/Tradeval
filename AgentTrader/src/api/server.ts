import { serve } from '@hono/node-server'

import type { AgentTraderConfig } from '../config.js'
import type { JobStore } from '../jobs/store.js'
import { createApiApp } from './app.js'
import type { ExperimentService } from './routes/experiments.js'

interface ClosableServer {
  close(callback: (error?: Error) => void): void
}

export async function gracefulShutdown(
  server: ClosableServer,
  service: ExperimentService,
  store: JobStore,
): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()))
  })
  await service.shutdown()
  store.close()
}

export function startApiServer(
  config: AgentTraderConfig,
  service: ExperimentService,
  store: JobStore,
): { server: ReturnType<typeof serve>; shutdown: () => Promise<void> } {
  const app = createApiApp(config, service)
  const server = serve({ fetch: app.fetch, port: config.apiPort })
  let stopping: Promise<void> | undefined
  const shutdown = () => (stopping ??= gracefulShutdown(server, service, store))
  const onSignal = () => {
    void shutdown().catch((error) => {
      console.error('AgentTrader shutdown failed', error)
      process.exitCode = 1
    })
  }
  process.once('SIGINT', onSignal)
  process.once('SIGTERM', onSignal)
  server.once('close', () => {
    process.removeListener('SIGINT', onSignal)
    process.removeListener('SIGTERM', onSignal)
  })
  return { server, shutdown }
}
