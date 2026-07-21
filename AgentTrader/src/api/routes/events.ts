import { join } from 'node:path'
import type { Hono } from 'hono'
import { streamSSE } from 'hono/streaming'

import { JobEventLog } from '../../jobs/events.js'
import type { ExperimentService } from './experiments.js'

export function registerEventRoutes(app: Hono, service: ExperimentService): void {
  app.get('/experiments/:id/events', (c) => {
    const job = service.get(c.req.param('id'))
    if (!job) return c.json({ error: 'experiment not found' }, 404)
    const after = Number(c.req.header('Last-Event-ID') ?? c.req.query('after') ?? 0)
    return streamSSE(c, async (stream) => {
      let cursor = Number.isSafeInteger(after) && after >= 0 ? after : 0
      let running = true
      stream.onAbort(() => { running = false })
      while (running && !stream.aborted) {
        const events = new JobEventLog(join(job.workspace, 'events.jsonl')).read().filter(({ sequence }) => sequence > cursor)
        for (const event of events) {
          await stream.writeSSE({ id: String(event.sequence), event: event.type, data: JSON.stringify(event) })
          cursor = event.sequence
        }
        const current = service.get(job.jobId)
        if (current && ['completed', 'failed', 'cancelled'].includes(current.status)) break
        await stream.sleep(25)
      }
    })
  })
}
