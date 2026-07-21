import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, test } from 'vitest'

import { createApiApp } from '../src/api/app.js'
import { gracefulShutdown } from '../src/api/server.js'
import { ExperimentService, type ApiJobExecutor } from '../src/api/routes/experiments.js'
import type { AgentTraderConfig } from '../src/config.js'
import { JobEventLog } from '../src/jobs/events.js'
import { JobStore } from '../src/jobs/store.js'

const roots: string[] = []
const config: AgentTraderConfig = {
  modelProvider: 'faux', modelId: 'fixture', retailTraderExecutable: 'uv',
  retailTraderRoot: '/tmp/retail', workspaceRoot: '/tmp/jobs', apiPort: 4317, jobTimeoutMs: 1000,
}
const experimentBody = {
  experiment_id: 'exp-1',
  mandate: {
    schema_version: 1, experiment_id: 'exp-1', capital: { currency: 'USD', initial_cash: '100000.00' }, market: 'US',
    universe: { symbols: ['AAPL'], screener: 'price_quality_v1', max_candidates: 12, minimum_history_sessions: 250, minimum_average_dollar_volume: '1000000', minimum_evidence_coverage: 0.7, pinned_symbols: [], excluded_symbols: [] },
    cadence: 'monthly', horizon: { kind: 'hindsight', start: '2025-01-01', end: '2025-12-31' },
    limits: { minimum_cash_weight: 0.1, maximum_position_weight: 0.12, maximum_turnover: 0.2, maximum_drawdown: 0.25 },
  },
  protocol: { schema_version: 1, provider: 'faux', model_id: 'fixture', system_prompt_hash: `sha256:${'a'.repeat(64)}`, recipe: 'proposal-v1', tools: ['get_candidate_data', 'submit_proposals'], sampling: { temperature: 0, max_tokens: 1000 }, timeout_ms: 1000, retry_count: 1 },
}
const experiment = (id: string) => ({
  ...experimentBody,
  experiment_id: id,
  mandate: { ...experimentBody.mandate, experiment_id: id },
})

function harness(executor?: ApiJobExecutor) {
  const root = mkdtempSync(join(tmpdir(), 'agenttrader-api-'))
  roots.push(root)
  const store = new JobStore(join(root, 'jobs.sqlite'))
  const run: ApiJobExecutor = executor ?? (async (request) => {
    store.update(request.jobId, { status: 'running', stage: 'running' })
    const events = new JobEventLog(join(request.workspace, 'events.jsonl'))
    events.append({ type: 'stage', stage: 'running', session: null, artifact: null, detail: null })
    events.append({ type: 'stage', stage: 'completed', session: null, artifact: null, detail: null })
    store.update(request.jobId, { status: 'completed', stage: 'completed' })
  })
  const service = new ExperimentService(root, store, run)
  return { root, store, service, app: createApiApp(config, service) }
}

async function waitFor(predicate: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return
    await new Promise((resolve) => setTimeout(resolve, 5))
  }
  throw new Error('condition not reached')
}

afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true })
})

describe('experiment API', () => {
  test('validates requests and keeps idempotency stable', async () => {
    const { app, store } = harness()
    expect((await app.request('/experiments', { method: 'POST', body: '{}' })).status).toBe(400)
    const request = { method: 'POST', headers: { 'content-type': 'application/json', 'Idempotency-Key': 'same' }, body: JSON.stringify(experiment('exp-1')) }
    const first = await app.request('/experiments', request)
    const duplicate = await app.request('/experiments', request)
    expect(first.status).toBe(202)
    expect(await duplicate.json()).toEqual(await first.clone().json())
    const conflict = await app.request('/experiments', { ...request, body: JSON.stringify(experiment('exp-other')) })
    expect(conflict.status).toBe(409)
    await waitFor(() => store.getByExperiment('exp-1')?.status === 'completed')
    store.close()
  })

  test('creates philosophies, returns status, forks immutably, and handles missing jobs', async () => {
    const { app, store, root } = harness()
    const headers = { 'content-type': 'application/json', 'Idempotency-Key': 'philosophy' }
    const created = await app.request('/experiments/philosophy', { method: 'POST', headers, body: JSON.stringify({ description: 'Quality value' }) })
    expect(created.status).toBe(202)
    const source = await created.json() as { job_id: string; experiment_id: string }
    expect((await app.request('/experiments/missing')).status).toBe(404)
    const experimentResponse = await app.request('/experiments', { method: 'POST', headers: { ...headers, 'Idempotency-Key': 'original' }, body: JSON.stringify(experiment('exp-1')) })
    const originalJob = await experimentResponse.json() as { job_id: string }
    const forked = await app.request(`/experiments/${originalJob.job_id}/fork`, {
      method: 'POST', headers: { ...headers, 'Idempotency-Key': 'fork' }, body: JSON.stringify({ experiment_id: 'exp-fork' }),
    })
    expect(forked.status).toBe(202)
    const original = readFileSync(join(root, originalJob.job_id, 'api-request.json'), 'utf8')
    expect(original).not.toContain('exp-fork')
    store.close()
  })

  test('cancels active jobs and records terminal executor errors', async () => {
    const pending = harness(async (_request, signal) => new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true })))
    const response = await pending.app.request('/experiments', { method: 'POST', headers: { 'content-type': 'application/json', 'Idempotency-Key': 'cancel' }, body: JSON.stringify(experiment('exp-cancel')) })
    const job = await response.json() as { job_id: string }
    expect((await pending.app.request(`/experiments/${job.job_id}/cancel`, { method: 'POST' })).status).toBe(200)
    expect(pending.store.get(job.job_id)?.status).toBe('cancelled')
    await pending.service.shutdown(); pending.store.close()

    const failed = harness(async () => { throw new Error('provider failed') })
    await failed.app.request('/experiments', { method: 'POST', headers: { 'content-type': 'application/json', 'Idempotency-Key': 'fail' }, body: JSON.stringify(experiment('exp-fail')) })
    await waitFor(() => failed.store.getByExperiment('exp-fail')?.status === 'failed')
    expect(failed.store.getByExperiment('exp-fail')?.error).toBe('provider failed')
    failed.store.close()
  })

  test('streams monotonic reconnectable SSE events', async () => {
    const { app, store } = harness()
    await app.request('/experiments', { method: 'POST', headers: { 'content-type': 'application/json', 'Idempotency-Key': 'events' }, body: JSON.stringify(experiment('exp-events')) })
    await waitFor(() => store.getByExperiment('exp-events')?.status === 'completed')
    const all = await (await app.request('/experiments/exp-events/events')).text()
    expect(all).toContain('id: 1'); expect(all).toContain('id: 2')
    const resumed = await (await app.request('/experiments/exp-events/events', { headers: { 'Last-Event-ID': '1' } })).text()
    expect(resumed).not.toContain('id: 1\n'); expect(resumed).toContain('id: 2')
    store.close()
  })

  test('graceful shutdown stops the server, workers, and store', async () => {
    const { service, store } = harness()
    let closed = false
    await gracefulShutdown({ close: (callback) => { closed = true; callback() } }, service, store)
    expect(closed).toBe(true)
    expect(() => store.get('anything')).toThrow()
  })
})
