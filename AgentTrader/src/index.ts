import { pathToFileURL } from 'node:url'
import { Hono } from 'hono'

import { type AgentTraderConfig, loadConfig } from './config.js'
import { createRuntimeExecutor } from './api/runtime.js'
import { startApiServer } from './api/server.js'
import { ExperimentService } from './api/routes/experiments.js'
import { JobStore } from './jobs/store.js'
import { join } from 'node:path'
export { createApiApp } from './api/app.js'
export { gracefulShutdown, startApiServer } from './api/server.js'

export function createApp(config: AgentTraderConfig): Hono {
  const app = new Hono()
  app.get('/health', (context) =>
    context.json({
      status: 'ok',
      model_provider: config.modelProvider,
      model_id: config.modelId,
    }),
  )
  return app
}

function start(): void {
  const config = loadConfig()
  const store = new JobStore(join(config.workspaceRoot, 'jobs.sqlite'))
  const service = new ExperimentService(config.workspaceRoot, store, createRuntimeExecutor(config, store))
  const { server } = startApiServer(config, service, store)
  const address = server.address()
  console.error(`AgentTrader listening on port ${typeof address === 'object' && address ? address.port : config.apiPort}`)
}

const entryPoint = process.argv[1]
if (entryPoint && import.meta.url === pathToFileURL(entryPoint).href) {
  start()
}
