import { Agent, type AgentEvent, type AgentTool } from '@mariozechner/pi-agent-core'
import type { AssistantMessage, Model } from '@mariozechner/pi-ai'

export interface PiUsage {
  readonly inputTokens: number
  readonly outputTokens: number
  readonly cacheReadTokens: number
  readonly cacheWriteTokens: number
  readonly totalTokens: number
}

export interface PiToolArguments {
  readonly toolCallId: string
  readonly toolName: string
  readonly arguments: unknown
}

export interface PiRunRecord {
  readonly provider: string
  readonly modelId: string
  readonly latencyMs: number
  readonly promptCount: number
  readonly events: readonly AgentEvent[]
  readonly assistantMessages: readonly AssistantMessage[]
  readonly toolArguments: readonly PiToolArguments[]
  readonly usage: PiUsage
}

export class PiRunError extends Error {
  run?: PiRunRecord
}

export class PiTimeoutError extends PiRunError {}
export class PiAbortedError extends PiRunError {}
export class PiProviderError extends PiRunError {}
export class PiSubmissionError extends PiRunError {}

export interface RunAgentSubmissionOptions<T> {
  readonly model: Model<string>
  readonly systemPrompt: string
  readonly prompt: string
  readonly repairPrompt: string
  readonly tools: readonly AgentTool[]
  readonly getSubmission: () => T | undefined
  readonly getSubmissionError?: () => string | undefined
  readonly timeoutMs: number
  readonly signal?: AbortSignal
  readonly sessionId?: string
}

export interface AgentSubmissionResult<T> {
  readonly submission: T
  readonly run: PiRunRecord
}

function summarizeUsage(messages: readonly AssistantMessage[]): PiUsage {
  return Object.freeze(
    messages.reduce(
      (usage, message) => ({
        inputTokens: usage.inputTokens + message.usage.input,
        outputTokens: usage.outputTokens + message.usage.output,
        cacheReadTokens: usage.cacheReadTokens + message.usage.cacheRead,
        cacheWriteTokens: usage.cacheWriteTokens + message.usage.cacheWrite,
        totalTokens: usage.totalTokens + message.usage.totalTokens,
      }),
      {
        inputTokens: 0,
        outputTokens: 0,
        cacheReadTokens: 0,
        cacheWriteTokens: 0,
        totalTokens: 0,
      },
    ),
  )
}

export async function runAgentSubmission<T>(
  options: RunAgentSubmissionOptions<T>,
): Promise<AgentSubmissionResult<T>> {
  if (!Number.isSafeInteger(options.timeoutMs) || options.timeoutMs <= 0) {
    throw new TypeError('timeoutMs must be a positive integer')
  }
  if (options.signal?.aborted) {
    throw new PiAbortedError('Pi run aborted before start')
  }

  const startedAt = performance.now()
  const events: AgentEvent[] = []
  const assistantMessages: AssistantMessage[] = []
  const toolArguments: PiToolArguments[] = []
  let promptCount = 0

  const agent = new Agent({
    initialState: {
      systemPrompt: options.systemPrompt,
      model: options.model,
      thinkingLevel: 'off',
      tools: [...options.tools],
      messages: [],
    },
    toolExecution: 'sequential',
    ...(options.sessionId ? { sessionId: options.sessionId } : {}),
  })
  const unsubscribe = agent.subscribe((event) => {
    events.push(structuredClone(event))
    if (event.type === 'message_end' && event.message.role === 'assistant') {
      assistantMessages.push(structuredClone(event.message))
    }
    if (event.type === 'tool_execution_start') {
      toolArguments.push(
        Object.freeze({
          toolCallId: event.toolCallId,
          toolName: event.toolName,
          arguments: structuredClone(event.args),
        }),
      )
    }
  })
  const abortFromCaller = () => agent.abort()
  options.signal?.addEventListener('abort', abortFromCaller, { once: true })

  const buildRecord = (): PiRunRecord =>
    Object.freeze({
      provider: options.model.provider,
      modelId: options.model.id,
      latencyMs: Math.max(0, performance.now() - startedAt),
      promptCount,
      events: Object.freeze([...events]),
      assistantMessages: Object.freeze([...assistantMessages]),
      toolArguments: Object.freeze([...toolArguments]),
      usage: summarizeUsage(assistantMessages),
    })

  const prompt = async (text: string): Promise<void> => {
    promptCount += 1
    let timedOut = false
    const timer = setTimeout(() => {
      timedOut = true
      agent.abort()
    }, options.timeoutMs)
    const messageOffset = assistantMessages.length
    try {
      await agent.prompt(text)
    } finally {
      clearTimeout(timer)
    }
    if (timedOut) {
      throw new PiTimeoutError(`Pi run exceeded ${options.timeoutMs}ms`)
    }
    if (options.signal?.aborted) {
      throw new PiAbortedError('Pi run aborted')
    }
    const failed = assistantMessages
      .slice(messageOffset)
      .find(({ stopReason }) => stopReason === 'error')
    if (failed) {
      throw new PiProviderError(failed.errorMessage ?? 'Pi provider failed')
    }
    const aborted = assistantMessages
      .slice(messageOffset)
      .find(({ stopReason }) => stopReason === 'aborted')
    if (aborted) {
      throw new PiAbortedError(aborted.errorMessage ?? 'Pi run aborted')
    }
  }

  try {
    await prompt(options.prompt)
    if (options.getSubmission() === undefined) {
      await prompt(options.repairPrompt)
    }
    const submission = options.getSubmission()
    if (submission === undefined) {
      const detail = options.getSubmissionError?.()
      throw new PiSubmissionError(
        detail ? `Pi did not submit a valid result: ${detail}` : 'Pi did not submit a result',
      )
    }
    return Object.freeze({ submission, run: buildRecord() })
  } catch (error) {
    if (error instanceof PiRunError) {
      error.run = buildRecord()
      throw error
    }
    const wrapped = new PiProviderError(
      error instanceof Error ? error.message : 'Pi provider failed',
      { cause: error },
    )
    wrapped.run = buildRecord()
    throw wrapped
  } finally {
    agent.abort()
    await agent.waitForIdle()
    unsubscribe()
    options.signal?.removeEventListener('abort', abortFromCaller)
  }
}
