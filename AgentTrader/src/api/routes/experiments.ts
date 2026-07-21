import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import type { Context, Hono } from 'hono'
import { Value } from '@sinclair/typebox/value'

import { canonicalJson } from '../../contracts/hash.js'
import { MandateSpecSchema } from '../../contracts/mandate.js'
import { AgentProtocolSchema } from '../../contracts/protocol.js'
import type { JobRecord, JobStore } from '../../jobs/store.js'

export type ApiOperation = 'philosophy.generate' | 'experiment.run' | 'experiment.forward'
export interface ApiJobRequest {
  readonly operation: ApiOperation
  readonly jobId: string
  readonly experimentId: string
  readonly workspace: string
  readonly body: Record<string, unknown>
}
export type ApiJobExecutor = (request: ApiJobRequest, signal: AbortSignal) => Promise<void>

export class ExperimentService {
  private readonly active = new Map<string, AbortController>()

  constructor(
    private readonly root: string,
    readonly store: JobStore,
    private readonly execute: ApiJobExecutor,
  ) {}

  create(operation: ApiOperation, idempotencyKey: string, body: Record<string, unknown>): JobRecord {
    if (!idempotencyKey.trim()) throw new TypeError('Idempotency-Key is required')
    const suffix = createHash('sha256').update(`${operation}\0${idempotencyKey}`).digest('hex').slice(0, 24)
    const jobId = `job-${suffix}`
    const experimentId =
      typeof body.experiment_id === 'string' && body.experiment_id
        ? body.experiment_id
        : `philosophy-${suffix}`
    const workspace = join(this.root, jobId)
    const requestPath = join(workspace, 'api-request.json')
    mkdirSync(workspace, { recursive: true })
    const request = { schema_version: 1, operation, job_id: jobId, experiment_id: experimentId, body }
    const content = `${canonicalJson(request)}\n`
    if (existsSync(requestPath) && readFileSync(requestPath, 'utf8') !== content) {
      throw new Error('idempotency key was already used with different content')
    }
    if (!existsSync(requestPath)) writeFileSync(requestPath, content, { flag: 'wx', mode: 0o600 })
    const job = this.store.create(jobId, experimentId, workspace)
    if (!['completed', 'failed', 'cancelled'].includes(job.status) && !this.active.has(jobId)) {
      const controller = new AbortController()
      this.active.set(jobId, controller)
      queueMicrotask(() => {
        void this.execute({ operation, jobId, experimentId, workspace, body }, controller.signal)
          .catch((error) => {
            if (this.store.get(jobId)?.status !== 'cancelled') {
              this.store.update(jobId, {
                status: 'failed',
                stage: 'failed',
                error: error instanceof Error ? error.message : 'job failed',
              })
            }
          })
          .finally(() => this.active.delete(jobId))
      })
    }
    return job
  }

  get(id: string): JobRecord | undefined {
    return this.store.get(id) ?? this.store.getByExperiment(id)
  }

  fork(id: string, idempotencyKey: string, body: Record<string, unknown>): JobRecord {
    const source = this.get(id)
    if (!source) throw new RangeError('experiment not found')
    if (typeof body.experiment_id !== 'string' || !body.experiment_id) {
      throw new TypeError('fork requires a new experiment_id')
    }
    const original = JSON.parse(readFileSync(join(source.workspace, 'api-request.json'), 'utf8')) as {
      body: Record<string, unknown>
    }
    const mandate = original.body.mandate as Record<string, unknown> | undefined
    return this.create('experiment.run', idempotencyKey, {
      ...original.body,
      ...body,
      mandate: mandate ? { ...mandate, experiment_id: body.experiment_id } : mandate,
      forked_from: source.experimentId,
    })
  }

  cancel(id: string): JobRecord {
    const job = this.get(id)
    if (!job) throw new RangeError('experiment not found')
    if (['completed', 'failed', 'cancelled'].includes(job.status)) return job
    this.active.get(job.jobId)?.abort(new Error('cancelled by user'))
    return this.store.update(job.jobId, { status: 'cancelled', stage: 'cancelled', error: null })
  }

  async shutdown(): Promise<void> {
    for (const controller of this.active.values()) controller.abort(new Error('server shutting down'))
    while (this.active.size > 0) await new Promise((resolve) => setTimeout(resolve, 10))
  }
}

function errorResponse(context: Context, error: unknown) {
  const status: 400 | 404 | 409 = error instanceof RangeError ? 404 : error instanceof TypeError ? 400 : 409
  return context.json({ error: error instanceof Error ? error.message : 'request failed' }, status)
}

export function registerExperimentRoutes(app: Hono, service: ExperimentService): void {
  app.post('/experiments/philosophy', async (c) => {
    try {
      const body = await c.req.json<Record<string, unknown>>()
      if (typeof body.description !== 'string' || !body.description.trim()) throw new TypeError('description is required')
      const job = service.create('philosophy.generate', c.req.header('Idempotency-Key') ?? '', body)
      return c.json({ job_id: job.jobId, experiment_id: job.experimentId }, 202)
    } catch (error) { return errorResponse(c, error) }
  })
  app.post('/experiments', async (c) => {
    try {
      const body = await c.req.json<Record<string, unknown>>()
      if (typeof body.experiment_id !== 'string' || !body.experiment_id) throw new TypeError('experiment_id is required')
      if (!Value.Check(MandateSpecSchema, body.mandate)) throw new TypeError('valid mandate is required')
      if (!Value.Check(AgentProtocolSchema, body.protocol)) throw new TypeError('valid agent protocol is required')
      if (body.mandate.experiment_id !== body.experiment_id) throw new TypeError('mandate experiment_id must match')
      const horizon = body.mandate.horizon.kind
      const job = service.create(horizon === 'forward' ? 'experiment.forward' : 'experiment.run', c.req.header('Idempotency-Key') ?? '', body)
      return c.json({ job_id: job.jobId, experiment_id: job.experimentId }, 202)
    } catch (error) { return errorResponse(c, error) }
  })
  app.get('/experiments/:id', (c) => {
    const job = service.get(c.req.param('id'))
    return job ? c.json(job) : c.json({ error: 'experiment not found' }, 404)
  })
  app.post('/experiments/:id/fork', async (c) => {
    try {
      const job = service.fork(c.req.param('id'), c.req.header('Idempotency-Key') ?? '', await c.req.json())
      return c.json({ job_id: job.jobId, experiment_id: job.experimentId }, 202)
    } catch (error) { return errorResponse(c, error) }
  })
  app.post('/experiments/:id/cancel', (c) => {
    try { return c.json(service.cancel(c.req.param('id'))) }
    catch (error) { return errorResponse(c, error) }
  })
}
