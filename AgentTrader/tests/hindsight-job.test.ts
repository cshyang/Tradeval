import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { describe, expect, test } from 'vitest'

import { canonicalHash } from '../src/contracts/hash.js'
import { JobEventLog } from '../src/jobs/events.js'
import { runHindsightJob } from '../src/jobs/hindsight.js'
import { JobStore } from '../src/jobs/store.js'
import { RetailTraderClient } from '../src/retailtrader/client.js'

const HASH_B = `sha256:${'b'.repeat(64)}`

describe('hindsight job', () => {
  test('processes monthly frames and preserves every audit reference', async () => {
    const root = mkdtempSync(join(tmpdir(), 'agenttrader-hindsight-'))
    const workspace = join(root, 'workspace')
    const mandate = {
      schema_version: 1,
      experiment_id: 'exp-hindsight',
      capital: { currency: 'USD', initial_cash: '100000.00' },
      market: 'US',
      universe: {
        symbols: ['AAPL'],
        screener: 'price_quality_v1',
        max_candidates: 12,
        minimum_history_sessions: 250,
        minimum_average_dollar_volume: '1000000',
        minimum_evidence_coverage: 0.7,
        pinned_symbols: [],
        excluded_symbols: [],
      },
      cadence: 'monthly',
      horizon: { kind: 'hindsight', start: '2025-01-01', end: '2025-03-31' },
      limits: {
        minimum_cash_weight: 0.1,
        maximum_position_weight: 0.12,
        maximum_turnover: 0.2,
        maximum_drawdown: 0.25,
      },
    } as const
    const protocol = {
      schema_version: 1,
      provider: 'faux',
      model_id: 'fixture',
      system_prompt_hash: HASH_B,
      recipe: 'proposal-v1',
      tools: ['get_candidate_data', 'submit_proposals'],
      sampling: { temperature: 0, max_tokens: 1000 },
      timeout_ms: 1000,
      retry_count: 1,
    } as const
    const cli = new RetailTraderClient({
      executable: process.execPath,
      executableArgs: ['--experimental-strip-types', resolve('tests/fixtures/retailtrader-cli.ts')],
      cwd: root,
      codeRevision: 'fixture-revision',
      timeoutMs: 2_000,
    })
    const store = new JobStore(join(root, 'jobs.sqlite'))
    const proposalWorker = {
      generate: async ({ candidateSetPath, outputPath }: { candidateSetPath: string; outputPath: string }) => {
        const candidates = JSON.parse(readFileSync(candidateSetPath, 'utf8'))
        const proposal = {
          schema_version: 1,
          experiment_id: candidates.experiment_id,
          decision_at: candidates.decision_at,
          candidate_set_hash: candidates.candidate_set_hash,
          agent_protocol_hash: canonicalHash(protocol),
          decisions: [],
          abstentions: [],
        }
        writeFileSync(outputPath, JSON.stringify(proposal) + '\n', { flag: 'wx' })
        return { artifactPath: outputPath, contentHash: canonicalHash(proposal) }
      },
    }

    try {
      const result = await runHindsightJob({
        jobId: 'job-1',
        workspace,
        mandate,
        protocol,
        retailTrader: cli,
        proposalWorker,
        store,
      })
      const events = new JobEventLog(join(workspace, 'events.jsonl')).read()

      expect(result.classification).toBe('HINDSIGHT SCENARIO')
      expect(events.filter(({ type }) => type === 'stage').map(({ stage }) => stage)).toEqual([
        'freezing_inputs',
        'preparing_hindsight',
        'screening_candidates',
        'generating_proposal',
        'adjudicating_proposal',
        'screening_candidates',
        'generating_proposal',
        'adjudicating_proposal',
        'evaluating_controls',
        'completed',
      ])
      expect(events.filter(({ type, stage }) => type === 'artifact' && stage === 'proposal')).toHaveLength(2)
      expect(events.filter(({ type, stage }) => type === 'artifact' && stage === 'adjudication')).toHaveLength(2)
      expect(result.sessions).toHaveLength(2)
      expect(events.filter(({ type }) => type === 'command')).toHaveLength(8)
      expect(events.some(({ type, detail }) => type === 'command' && detail?.includes('fixture-revision'))).toBe(true)
      expect(events.some(({ type, detail }) => type === 'log' && detail?.includes('fake RetailTrader'))).toBe(true)
      expect(result.sessions.every(({ proposalPath, adjudicationPath }) =>
        readFileSync(proposalPath).length > 0 && readFileSync(adjudicationPath).length > 0,
      )).toBe(true)
      expect(readFileSync(result.evaluationPath, 'utf8')).toContain('HINDSIGHT SCENARIO')
      expect(store.get('job-1')).toMatchObject({ status: 'completed', stage: 'completed' })
    } finally {
      store.close()
      rmSync(root, { recursive: true, force: true })
    }
  }, 10_000)
})

describe('RetailTrader subprocess client', () => {
  test('terminates timed-out and cancelled commands', async () => {
    const root = mkdtempSync(join(tmpdir(), 'agenttrader-client-'))
    const script = 'process.stdin.resume(); setInterval(() => {}, 1000)'
    try {
      const timed = new RetailTraderClient({
        executable: process.execPath,
        executableArgs: ['-e', script],
        cwd: root,
        codeRevision: 'fixture-revision',
        timeoutMs: 10,
      })
      await expect(timed.execute([])).rejects.toThrow('timed out')

      const controller = new AbortController()
      const cancellable = new RetailTraderClient({
        executable: process.execPath,
        executableArgs: ['-e', script],
        cwd: root,
        codeRevision: 'fixture-revision',
        timeoutMs: 2_000,
      })
      const running = cancellable.execute([], { signal: controller.signal })
      setTimeout(() => controller.abort(), 10)
      await expect(running).rejects.toThrow('aborted')
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })
})
