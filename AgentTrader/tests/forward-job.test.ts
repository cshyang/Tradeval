import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, test } from 'vitest'

import { runForwardDecision } from '../src/jobs/forward.js'
import { LocalJobScheduler, nextMonthlyClose } from '../src/jobs/scheduler.js'

describe('forward scheduler', () => {
  test('calculates the next monthly weekday close', () => {
    expect(nextMonthlyClose(new Date('2026-01-31T21:00:00Z')).toISOString()).toBe('2026-02-27T21:00:00.000Z')
  })

  test('claims one decision per due close and survives restart', async () => {
    const root = mkdtempSync(join(tmpdir(), 'agenttrader-forward-'))
    const path = join(root, 'scheduler.sqlite')
    const payload = join(root, 'forward.json')
    writeFileSync(payload, JSON.stringify({ mandate: { horizon: { kind: 'forward' } } }))
    let now = new Date('2026-01-30T22:00:00Z')
    const runs: string[] = []
    const runner = async (job: { dueAt: string }) => { runs.push(job.dueAt) }
    try {
      const first = new LocalJobScheduler(path, runner, () => now)
      first.schedule('exp-forward', new Date('2026-01-30T21:00:00Z'), payload)
      expect(await first.tick()).toBe(1)
      expect(await first.tick()).toBe(0)
      first.close()
      now = new Date('2026-02-27T22:00:00Z')
      const restarted = new LocalJobScheduler(path, runner, () => now)
      expect(await restarted.tick()).toBe(1)
      expect(runs).toEqual(['2026-01-30T21:00:00.000Z', '2026-02-27T21:00:00.000Z'])
      restarted.close()
    } finally { rmSync(root, { recursive: true, force: true }) }
  })

  test('retries failures without weakening the pipeline contract', async () => {
    const root = mkdtempSync(join(tmpdir(), 'agenttrader-forward-retry-'))
    const payload = join(root, 'forward.json')
    writeFileSync(payload, JSON.stringify({ mandate: { horizon: { kind: 'forward' } } }))
    let attempts = 0
    const scheduler = new LocalJobScheduler(join(root, 'scheduler.sqlite'), async () => {
      attempts += 1
      if (attempts === 1) throw new Error('temporary failure')
    }, () => new Date('2026-01-30T22:00:00Z'))
    scheduler.schedule('exp-forward', new Date('2026-01-30T21:00:00Z'), payload)
    await expect(scheduler.tick()).rejects.toThrow('temporary failure')
    expect(await scheduler.tick()).toBe(1)
    scheduler.close(); rmSync(root, { recursive: true, force: true })
  })

  test('uses candidate, proposal, and deterministic step in order', async () => {
    const root = mkdtempSync(join(tmpdir(), 'agenttrader-forward-pipeline-'))
    const payload = join(root, 'forward.json')
    writeFileSync(payload, JSON.stringify({ mandate: { horizon: { kind: 'forward' } } }))
    const calls: string[] = []
    const result = await runForwardDecision(
      { experimentId: 'exp', dueAt: '2026-01-30T21:00:00Z', payloadPath: payload, attempt: 1 },
      {
        screen: async () => { calls.push('screen'); return 'candidates.json' },
        propose: async () => { calls.push('propose'); return 'proposal.json' },
        commit: async () => { calls.push('commit'); return 'deferred' },
      },
    )
    expect(calls).toEqual(['screen', 'propose', 'commit'])
    expect(result).toBe('deferred')
    rmSync(root, { recursive: true, force: true })
  })
})
