import type { AgentTool } from '@mariozechner/pi-agent-core'
import { Type, type Model, type Static } from '@mariozechner/pi-ai'

import { canonicalHash } from '../contracts/hash.js'
import {
  PHILOSOPHY_REPAIR_PROMPT,
  PHILOSOPHY_SYSTEM_PROMPT,
  philosophyPrompt,
} from '../prompts/philosophy.js'
import { type PiRunRecord, runAgentSubmission } from '../pi/run-agent.js'

const strictObject = <T extends Parameters<typeof Type.Object>[0]>(properties: T) =>
  Type.Object(properties, { additionalProperties: false })

export const PhilosophySubmissionSchema = strictObject({
  schema_version: Type.Literal(1),
  classification: Type.Literal('AI-INTERPRETED'),
  status: Type.Union([Type.Literal('ready'), Type.Literal('clarification_required')]),
  name: Type.String({ minLength: 1 }),
  principles: Type.Array(Type.String({ minLength: 1 })),
  screener: Type.Literal('price_quality_v1'),
  cadence: Type.Union([Type.Literal('weekly'), Type.Literal('monthly')]),
  defaults: strictObject({
    max_candidates: Type.Integer({ minimum: 1, maximum: 30 }),
    minimum_evidence_coverage: Type.Number({ minimum: 0, maximum: 1 }),
    minimum_cash_weight: Type.Number({ minimum: 0, maximum: 1 }),
    maximum_position_weight: Type.Number({ exclusiveMinimum: 0, maximum: 1 }),
    maximum_turnover: Type.Number({ minimum: 0, maximum: 1 }),
    maximum_drawdown: Type.Number({ minimum: 0, maximum: 1 }),
  }),
  assumptions: Type.Array(Type.String({ minLength: 1 })),
  unsupported_capabilities: Type.Array(Type.String({ minLength: 1 })),
  clarification_question: Type.Union([Type.String({ minLength: 1 }), Type.Null()]),
})

type PhilosophySubmission = Static<typeof PhilosophySubmissionSchema>
export type PhilosophySpec = Readonly<PhilosophySubmission & { spec_hash: string }>

function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) {
      deepFreeze(child)
    }
    Object.freeze(value)
  }
  return value
}

export interface GeneratePhilosophyOptions {
  readonly description: string
  readonly model: Model<string>
  readonly timeoutMs: number
  readonly signal?: AbortSignal
  readonly sessionId?: string
}

export interface PhilosophyWorkerResult {
  readonly philosophy: PhilosophySpec
  readonly run: PiRunRecord
}

function validateSubmission(
  value: PhilosophySubmission,
  description: string,
): string | undefined {
  if (
    /^jpmorgan(?: chase)?[.!]?$/i.test(description.trim()) &&
    value.status !== 'clarification_required'
  ) {
    return 'JPMorgan without a named investment style requires clarification'
  }
  if (value.status === 'ready') {
    if (value.principles.length === 0) {
      return 'a ready philosophy requires at least one principle'
    }
    if (value.clarification_question !== null) {
      return 'a ready philosophy cannot contain a clarification question'
    }
  } else {
    if (value.clarification_question === null) {
      return 'clarification_required requires a question'
    }
    if (value.principles.length !== 0) {
      return 'clarification_required cannot freeze invented principles'
    }
  }
  return undefined
}

export async function generatePhilosophy(
  options: GeneratePhilosophyOptions,
): Promise<PhilosophyWorkerResult> {
  if (!options.description.trim()) {
    throw new TypeError('philosophy description is required')
  }

  let submission: PhilosophySpec | undefined
  let submissionError: string | undefined
  const submitTool: AgentTool<typeof PhilosophySubmissionSchema> = {
    name: 'submit_philosophy',
    label: 'Submit philosophy',
    description: 'Submit the complete AI-interpreted philosophy specification.',
    parameters: PhilosophySubmissionSchema,
    executionMode: 'sequential',
    execute: async (_toolCallId, params, signal) => {
      if (signal?.aborted) {
        throw new Error('submit_philosophy aborted')
      }
      submissionError = validateSubmission(params, options.description)
      if (!submissionError) {
        submission = deepFreeze({ ...structuredClone(params), spec_hash: canonicalHash(params) })
      }
      return {
        content: [
          {
            type: 'text',
            text: submissionError
              ? `Rejected philosophy: ${submissionError}`
              : 'Philosophy accepted.',
          },
        ],
        details: { accepted: submissionError === undefined },
        terminate: true,
      }
    },
  }

  const result = await runAgentSubmission({
    model: options.model,
    systemPrompt: PHILOSOPHY_SYSTEM_PROMPT,
    prompt: philosophyPrompt(options.description),
    repairPrompt: PHILOSOPHY_REPAIR_PROMPT,
    tools: [submitTool],
    getSubmission: () => submission,
    getSubmissionError: () => submissionError,
    timeoutMs: options.timeoutMs,
    ...(options.signal ? { signal: options.signal } : {}),
    ...(options.sessionId ? { sessionId: options.sessionId } : {}),
  })
  return Object.freeze({ philosophy: result.submission, run: result.run })
}
