import { type Static, Type } from '@sinclair/typebox'
import { Value } from '@sinclair/typebox/value'

import { canonicalJson } from '../contracts/hash.js'

const strictObject = <T extends Parameters<typeof Type.Object>[0]>(properties: T) =>
  Type.Object(properties, { additionalProperties: false })
const jobId = () => Type.String({ minLength: 1 })
const hash = () => Type.String({ pattern: '^sha256:[a-f0-9]{64}$' })

export const WorkerRequestSchema = strictObject({
  type: Type.Literal('request'),
  job_id: jobId(),
  operation: Type.Union([
    Type.Literal('philosophy.generate'),
    Type.Literal('proposal.generate'),
  ]),
  payload: Type.Record(Type.String(), Type.Unknown()),
})

export const WorkerCancelSchema = strictObject({
  type: Type.Literal('cancel'),
  job_id: jobId(),
})

export const WorkerProgressSchema = strictObject({
  type: Type.Literal('progress'),
  job_id: jobId(),
  stage: Type.String({ minLength: 1 }),
  completed: Type.Integer({ minimum: 0 }),
  total: Type.Integer({ minimum: 0 }),
})

export const WorkerResultSchema = strictObject({
  type: Type.Literal('result'),
  job_id: jobId(),
  status: Type.Union([
    Type.Literal('ok'),
    Type.Literal('error'),
    Type.Literal('cancelled'),
  ]),
  artifact_path: Type.Union([Type.String({ minLength: 1 }), Type.Null()]),
  content_hash: Type.Union([hash(), Type.Null()]),
  error: Type.Union([Type.String({ minLength: 1 }), Type.Null()]),
})

export const WorkerProtocolErrorSchema = strictObject({
  type: Type.Literal('error'),
  job_id: Type.Union([jobId(), Type.Null()]),
  code: Type.Union([
    Type.Literal('malformed_message'),
    Type.Literal('invalid_message'),
    Type.Literal('duplicate_job'),
    Type.Literal('unknown_job'),
  ]),
  message: Type.String({ minLength: 1 }),
})

export const WorkerInputSchema = Type.Union([WorkerRequestSchema, WorkerCancelSchema])
export const WorkerOutputSchema = Type.Union([
  WorkerProgressSchema,
  WorkerResultSchema,
  WorkerProtocolErrorSchema,
])

export type WorkerRequest = Static<typeof WorkerRequestSchema>
export type WorkerCancel = Static<typeof WorkerCancelSchema>
export type WorkerInput = Static<typeof WorkerInputSchema>
export type WorkerProgress = Static<typeof WorkerProgressSchema>
export type WorkerResult = Static<typeof WorkerResultSchema>
export type WorkerProtocolErrorMessage = Static<typeof WorkerProtocolErrorSchema>
export type WorkerOutput = Static<typeof WorkerOutputSchema>
export type ProtocolMessage = WorkerInput | WorkerOutput

function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) {
      deepFreeze(child)
    }
    Object.freeze(value)
  }
  return value
}

function parseJson(line: string): unknown {
  try {
    return JSON.parse(line)
  } catch (error) {
    throw new TypeError('message is not valid JSON', { cause: error })
  }
}

export function parseWorkerInput(line: string): WorkerInput {
  const value = parseJson(line)
  if (!Value.Check(WorkerInputSchema, value)) {
    const issue = Value.Errors(WorkerInputSchema, value).First()
    throw new TypeError(issue?.message ?? 'invalid worker input')
  }
  return deepFreeze(structuredClone(value) as WorkerInput)
}

export function parseWorkerOutput(line: string): WorkerOutput {
  const value = parseJson(line)
  if (!Value.Check(WorkerOutputSchema, value)) {
    const issue = Value.Errors(WorkerOutputSchema, value).First()
    throw new TypeError(issue?.message ?? 'invalid worker output')
  }
  const output = value as WorkerOutput
  if (output.type === 'progress' && output.completed > output.total) {
    throw new TypeError('worker progress completed cannot exceed total')
  }
  if (output.type === 'result') {
    const validSuccess =
      output.status === 'ok' &&
      output.artifact_path !== null &&
      output.content_hash !== null &&
      output.error === null
    const validFailure =
      output.status !== 'ok' &&
      output.artifact_path === null &&
      output.content_hash === null &&
      output.error !== null
    if (!validSuccess && !validFailure) {
      throw new TypeError('worker result fields do not match its status')
    }
  }
  return deepFreeze(structuredClone(output))
}

export function serializeProtocolMessage(message: ProtocolMessage): string {
  return `${canonicalJson(message)}\n`
}
