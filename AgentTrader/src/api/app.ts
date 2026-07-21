import { Hono } from 'hono'
import type { AgentTraderConfig } from '../config.js'
import { registerEventRoutes } from './routes/events.js'
import { ExperimentService, registerExperimentRoutes } from './routes/experiments.js'

export function createApiApp(config: AgentTraderConfig, service: ExperimentService): Hono {
  const app = new Hono()
  app.get('/health', (c) => c.json({ status: 'ok', model_provider: config.modelProvider, model_id: config.modelId }))
  registerExperimentRoutes(app, service)
  registerEventRoutes(app, service)
  return app
}
