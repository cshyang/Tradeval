import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import {
  fauxAssistantMessage,
  fauxText,
  fauxToolCall,
  registerFauxProvider,
} from '@mariozechner/pi-ai'
import { describe, expect, test } from 'vitest'

import { generateProposal } from '../src/workers/proposal.js'

const HASH_A = `sha256:${'a'.repeat(64)}`
const HASH_B = `sha256:${'b'.repeat(64)}`

const candidateSet = {
  schema_version: 1 as const,
  experiment_id: 'exp-1',
  screener: 'price_quality_v1' as const,
  decision_at: '2025-01-31T21:00:00Z',
  market_data_hash: HASH_B,
  candidates: [
    {
      symbol: 'AAPL',
      score: 0.9,
      evidence_coverage: 1,
      price_history_sessions: 500,
      average_dollar_volume: '100000000',
      latest_price: '200',
      metrics: [
        {
          name: 'roic',
          value: 0.31,
          unavailable_reason: null,
          evidence_refs: ['aapl-roic'],
          formula_version: 'v1',
          decision_cutoff: '2025-01-31T21:00:00Z',
        },
      ],
    },
  ],
  exclusions: [
    {
      symbol: 'MSFT',
      reason: 'fixture exclusion',
      evidence_coverage: 0,
      evidence_refs: ['future-msft'],
    },
  ],
  candidate_set_hash: HASH_A,
}

const fixtures = readFileSync(resolve('tests/fixtures/proposal-events.jsonl'), 'utf8')
  .trim()
  .split('\n')
  .map((line) => JSON.parse(line) as { scenario: string; tool: string; arguments: Record<string, unknown> })
const candidateRead = fixtures.find(({ scenario }) => scenario === 'candidate-read')!
const submission = fixtures.find(({ scenario }) => scenario === 'submit')!

describe('proposal worker', () => {
  test('exposes exactly two tools and only frozen candidate evidence', async () => {
    let initialContext = ''
    let candidateResult = ''
    const faux = registerFauxProvider({ provider: 'faux-proposal-ready' })
    faux.setResponses([
      (context) => {
        initialContext = JSON.stringify(context)
        return fauxAssistantMessage(fauxToolCall(candidateRead.tool, candidateRead.arguments), {
          stopReason: 'toolUse',
        })
      },
      (context) => {
        candidateResult = JSON.stringify(context.messages.at(-1))
        return fauxAssistantMessage(fauxToolCall(submission.tool, submission.arguments), {
          stopReason: 'toolUse',
        })
      },
    ])

    try {
      const result = await generateProposal({
        candidateSet,
        agentProtocolHash: HASH_B,
        model: faux.getModel(),
        timeoutMs: 1_000,
      })

      expect(result.status).toBe('submitted')
      expect(result.proposal.decisions[0]?.evidence_refs).toEqual(['aapl-roic'])
      expect(initialContext).not.toContain('aapl-roic')
      expect(initialContext).not.toContain('future-msft')
      expect(candidateResult).toContain('aapl-roic')
      expect(candidateResult).not.toContain('future-msft')
      expect(result.runs[0]?.toolArguments.map(({ toolName }) => toolName)).toEqual([
        'get_candidate_data',
        'submit_proposals',
      ])
    } finally {
      faux.unregister()
    }
  })

  test('rejects unknown evidence and accepts one structure-only repair', async () => {
    const invalid = structuredClone(submission.arguments)
    const decisions = invalid.decisions as Array<Record<string, unknown>>
    decisions[0] = { ...decisions[0], evidence_refs: ['unknown-evidence'] }
    const faux = registerFauxProvider({ provider: 'faux-proposal-repair' })
    faux.setResponses([
      fauxAssistantMessage(fauxToolCall(candidateRead.tool, candidateRead.arguments), {
        stopReason: 'toolUse',
      }),
      fauxAssistantMessage(fauxToolCall(submission.tool, invalid), { stopReason: 'toolUse' }),
      fauxAssistantMessage(fauxToolCall(submission.tool, submission.arguments), {
        stopReason: 'toolUse',
      }),
    ])

    try {
      const result = await generateProposal({
        candidateSet,
        agentProtocolHash: HASH_B,
        model: faux.getModel(),
        timeoutMs: 1_000,
      })

      expect(result.status).toBe('submitted')
      expect(result.runs[0]?.promptCount).toBe(2)
      expect(result.proposal.decisions[0]?.evidence_refs).toEqual(['aapl-roic'])
    } finally {
      faux.unregister()
    }
  })

  test('rejects a submission made before reading candidate data', async () => {
    const faux = registerFauxProvider({ provider: 'faux-proposal-read-first' })
    faux.setResponses([
      fauxAssistantMessage(fauxToolCall(submission.tool, submission.arguments), {
        stopReason: 'toolUse',
      }),
      fauxAssistantMessage(fauxToolCall(candidateRead.tool, candidateRead.arguments), {
        stopReason: 'toolUse',
      }),
      fauxAssistantMessage(fauxToolCall(submission.tool, submission.arguments), {
        stopReason: 'toolUse',
      }),
    ])

    try {
      const result = await generateProposal({
        candidateSet,
        agentProtocolHash: HASH_B,
        model: faux.getModel(),
        timeoutMs: 1_000,
      })

      expect(result.status).toBe('submitted')
      expect(result.runs[0]?.promptCount).toBe(2)
      expect(result.runs[0]?.toolArguments.map(({ toolName }) => toolName)).toEqual([
        'submit_proposals',
        'get_candidate_data',
        'submit_proposals',
      ])
    } finally {
      faux.unregister()
    }
  })

  test('turns two model timeouts into explicit abstentions', async () => {
    const faux = registerFauxProvider({
      provider: 'faux-proposal-timeout',
      tokensPerSecond: 100,
    })
    faux.setResponses([
      fauxAssistantMessage(fauxText('first slow response '.repeat(1_000))),
      fauxAssistantMessage(fauxText('second slow response '.repeat(1_000))),
    ])

    try {
      const result = await generateProposal({
        candidateSet,
        agentProtocolHash: HASH_B,
        model: faux.getModel(),
        timeoutMs: 1,
      })

      expect(result.status).toBe('abstained_timeout')
      expect(result.proposal.decisions).toEqual([])
      expect(result.proposal.abstentions).toEqual([
        {
          symbol: 'AAPL',
          reason: 'model timeout after one retry',
          evidence_refs: [],
        },
      ])
      expect(result.runs).toHaveLength(2)
    } finally {
      faux.unregister()
    }
  })
})
