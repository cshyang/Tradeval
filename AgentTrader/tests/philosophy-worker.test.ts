import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import {
  fauxAssistantMessage,
  fauxText,
  fauxToolCall,
  registerFauxProvider,
} from '@mariozechner/pi-ai'
import { describe, expect, test } from 'vitest'

import {
  PiProviderError,
  PiSubmissionError,
  PiTimeoutError,
} from '../src/pi/run-agent.js'
import { resolveModel } from '../src/pi/model.js'
import { generatePhilosophy } from '../src/workers/philosophy.js'

const fixtures = readFileSync(resolve('tests/fixtures/philosophy-events.jsonl'), 'utf8')
  .trim()
  .split('\n')
  .map((line) => JSON.parse(line) as { scenario: string; arguments: Record<string, unknown> })

const ready = fixtures.find(({ scenario }) => scenario === 'ready')!.arguments
const clarification = fixtures.find(({ scenario }) => scenario === 'clarification')!.arguments

describe('philosophy worker', () => {
  test('uses only submit_philosophy and captures an AI-interpreted result', async () => {
    let toolNames: string[] = []
    const faux = registerFauxProvider({ provider: 'faux-philosophy-ready' })
    faux.setResponses([
      (context) => {
        toolNames = context.tools?.map(({ name }) => name) ?? []
        return fauxAssistantMessage(fauxToolCall('submit_philosophy', ready), {
          stopReason: 'toolUse',
        })
      },
    ])

    try {
      const result = await generatePhilosophy({
        description: 'Buffett-inspired long-term quality value',
        model: faux.getModel(),
        timeoutMs: 1_000,
      })

      expect(toolNames).toEqual(['submit_philosophy'])
      expect(result.philosophy.classification).toBe('AI-INTERPRETED')
      expect(result.philosophy.status).toBe('ready')
      expect(result.philosophy.spec_hash).toMatch(/^sha256:[a-f0-9]{64}$/)
      expect(result.run.provider).toBe('faux-philosophy-ready')
      expect(result.run.modelId).toBe(faux.getModel().id)
      expect(result.run.latencyMs).toBeGreaterThanOrEqual(0)
      expect(result.run.events.length).toBeGreaterThan(0)
      expect(result.run.assistantMessages).toHaveLength(1)
      expect(result.run.toolArguments).toEqual([
        { toolCallId: expect.any(String), toolName: 'submit_philosophy', arguments: ready },
      ])
      const exactInput = result.run.assistantMessages.reduce(
        (total, message) => total + message.usage.input,
        0,
      )
      expect(result.run.usage.inputTokens).toBe(exactInput)
    } finally {
      faux.unregister()
    }
  })

  test('requires clarification for an institution without a style', async () => {
    const faux = registerFauxProvider({ provider: 'faux-philosophy-clarification' })
    faux.setResponses([
      fauxAssistantMessage(fauxToolCall('submit_philosophy', ready), {
        stopReason: 'toolUse',
      }),
      fauxAssistantMessage(fauxToolCall('submit_philosophy', clarification), {
        stopReason: 'toolUse',
      }),
    ])

    try {
      const result = await generatePhilosophy({
        description: 'JPMorgan',
        model: faux.getModel(),
        timeoutMs: 1_000,
      })

      expect(result.philosophy.status).toBe('clarification_required')
      expect(result.philosophy.clarification_question).toContain('Which JPMorgan')
      expect(result.philosophy.principles).toEqual([])
      expect(result.run.promptCount).toBe(2)
    } finally {
      faux.unregister()
    }
  })

  test('uses exactly one explicit repair prompt after a missing submission', async () => {
    const faux = registerFauxProvider({ provider: 'faux-philosophy-repair' })
    faux.setResponses([
      fauxAssistantMessage(fauxText('I will describe it in prose.')),
      fauxAssistantMessage(fauxToolCall('submit_philosophy', ready), {
        stopReason: 'toolUse',
      }),
    ])

    try {
      const result = await generatePhilosophy({
        description: 'Quality value',
        model: faux.getModel(),
        timeoutMs: 1_000,
      })

      expect(result.run.promptCount).toBe(2)
      expect(faux.state.callCount).toBe(2)
    } finally {
      faux.unregister()
    }
  })

  test('lets Pi repair one invalid tool payload', async () => {
    const faux = registerFauxProvider({ provider: 'faux-philosophy-invalid-tool' })
    faux.setResponses([
      fauxAssistantMessage(fauxToolCall('submit_philosophy', { schema_version: 1 }), {
        stopReason: 'toolUse',
      }),
      fauxAssistantMessage(fauxToolCall('submit_philosophy', ready), {
        stopReason: 'toolUse',
      }),
    ])

    try {
      const result = await generatePhilosophy({
        description: 'Quality value',
        model: faux.getModel(),
        timeoutMs: 1_000,
      })

      expect(result.philosophy.status).toBe('ready')
      expect(faux.state.callCount).toBe(2)
    } finally {
      faux.unregister()
    }
  })

  test('fails after one repair prompt without a valid submission', async () => {
    const faux = registerFauxProvider({ provider: 'faux-philosophy-no-submit' })
    faux.setResponses([
      fauxAssistantMessage(fauxText('prose only')),
      fauxAssistantMessage(fauxText('still prose only')),
    ])

    try {
      await expect(
        generatePhilosophy({
          description: 'Quality value',
          model: faux.getModel(),
          timeoutMs: 1_000,
        }),
      ).rejects.toMatchObject<PiSubmissionError>({
        name: 'Error',
        run: expect.objectContaining({ promptCount: 2 }),
      })
    } finally {
      faux.unregister()
    }
  })

  test('surfaces provider failures without attempting a repair', async () => {
    const faux = registerFauxProvider({ provider: 'faux-philosophy-failure' })
    faux.setResponses([
      fauxAssistantMessage('provider unavailable', {
        stopReason: 'error',
        errorMessage: 'provider unavailable',
      }),
    ])

    try {
      await expect(
        generatePhilosophy({
          description: 'Quality value',
          model: faux.getModel(),
          timeoutMs: 1_000,
        }),
      ).rejects.toBeInstanceOf(PiProviderError)
      expect(faux.state.callCount).toBe(1)
    } finally {
      faux.unregister()
    }
  })

  test('cleans up a timed-out Pi run', async () => {
    const faux = registerFauxProvider({
      provider: 'faux-philosophy-timeout',
      tokensPerSecond: 100,
    })
    faux.setResponses([fauxAssistantMessage(fauxText('slow response '.repeat(1_000)))])

    try {
      await expect(
        generatePhilosophy({
          description: 'Quality value',
          model: faux.getModel(),
          timeoutMs: 1,
        }),
      ).rejects.toBeInstanceOf(PiTimeoutError)
    } finally {
      faux.unregister()
    }
  })

  test('honors an external abort signal', async () => {
    const faux = registerFauxProvider({
      provider: 'faux-philosophy-abort',
      tokensPerSecond: 100,
    })
    faux.setResponses([fauxAssistantMessage(fauxText('slow response '.repeat(1_000)))])
    const controller = new AbortController()
    controller.abort()

    try {
      await expect(
        generatePhilosophy({
          description: 'Quality value',
          model: faux.getModel(),
          timeoutMs: 1_000,
          signal: controller.signal,
        }),
      ).rejects.toThrow('aborted before start')
      expect(faux.state.callCount).toBe(0)
    } finally {
      faux.unregister()
    }
  })

  test('rejects unknown configured models', () => {
    expect(() => resolveModel('not-a-provider', 'not-a-model')).toThrow(
      'unknown Pi model not-a-provider/not-a-model',
    )
  })
})
