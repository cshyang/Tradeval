import { serve } from '@hono/node-server'
import { pathToFileURL } from 'node:url'
import { Hono } from 'hono'

import { type AgentTraderConfig, loadConfig } from './config.js'

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
  const app = createApp(config)
  serve(
    {
      fetch: app.fetch,
      port: config.apiPort,
    },
    ({ port }) => {
      console.error(`AgentTrader listening on http://localhost:${port}`)
    },
  )
}

const entryPoint = process.argv[1]
if (entryPoint && import.meta.url === pathToFileURL(entryPoint).href) {
  start()
}
