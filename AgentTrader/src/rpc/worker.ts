import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import type { Readable, Writable } from 'node:stream'

import {
  parseWorkerInput,
  parseWorkerOutput,
  serializeProtocolMessage,
  type WorkerOutput,
  type WorkerProtocolErrorMessage,
  type WorkerRequest,
  type WorkerResult,
} from './protocol.js'

export interface WorkerHandlerResult {
  readonly artifactPath: string
  readonly contentHash: string
}

export interface WorkerHandlerContext {
  readonly signal: AbortSignal
  readonly progress: (stage: string, completed: number, total: number) => void
}

export type WorkerHandler = (
  request: WorkerRequest,
  context: WorkerHandlerContext,
) => Promise<WorkerHandlerResult>

type Emit = (message: WorkerOutput) => void

class JobTimeoutError extends Error {}

export class WorkerProtocolError extends Error {}

export class WorkerProcessError extends Error {
  readonly exitCode: number | null
  readonly signal: NodeJS.Signals | null

  constructor(exitCode: number | null, signal: NodeJS.Signals | null) {
    super(
      exitCode === null
        ? `worker exited from signal ${signal ?? 'unknown'}`
        : `worker exited with code ${exitCode}`,
    )
    this.exitCode = exitCode
    this.signal = signal
  }
}

export interface RpcWorkerOptions {
  readonly jobTimeoutMs: number
}

function protocolError(
  jobId: string | null,
  code: WorkerProtocolErrorMessage['code'],
  message: string,
): WorkerProtocolErrorMessage {
  return Object.freeze({ type: 'error', job_id: jobId, code, message })
}

export class RpcWorker {
  private readonly seenJobs = new Set<string>()
  private readonly activeJobs = new Map<string, AbortController>()
  private readonly terminalJobs = new Set<string>()
  private readonly tasks = new Set<Promise<void>>()
  private hadFailure = false

  constructor(
    private readonly handler: WorkerHandler,
    private readonly options: RpcWorkerOptions,
  ) {
    if (!Number.isSafeInteger(options.jobTimeoutMs) || options.jobTimeoutMs <= 0) {
      throw new TypeError('jobTimeoutMs must be a positive integer')
    }
  }

  accept(request: WorkerRequest, emit: Emit): Promise<void> {
    if (this.seenJobs.has(request.job_id)) {
      emit(
        protocolError(
          request.job_id,
          'duplicate_job',
          `job ID has already been accepted: ${request.job_id}`,
        ),
      )
      return Promise.resolve()
    }
    this.seenJobs.add(request.job_id)
    const controller = new AbortController()
    this.activeJobs.set(request.job_id, controller)
    const task = this.run(request, controller, emit)
    this.tasks.add(task)
    void task.finally(() => this.tasks.delete(task))
    return task
  }

  cancel(jobId: string, emit: Emit): Promise<void> {
    const controller = this.activeJobs.get(jobId)
    if (!controller) {
      emit(protocolError(jobId, 'unknown_job', `job is not active: ${jobId}`))
      return Promise.resolve()
    }
    controller.abort(new Error('job cancelled'))
    return Promise.resolve()
  }

  async shutdown(reason = new Error('worker shutting down')): Promise<void> {
    for (const controller of this.activeJobs.values()) {
      controller.abort(reason)
    }
    await this.waitForIdle()
  }

  async waitForIdle(): Promise<void> {
    while (this.tasks.size > 0) {
      await Promise.allSettled([...this.tasks])
    }
  }

  get failed(): boolean {
    return this.hadFailure
  }

  private async run(
    request: WorkerRequest,
    controller: AbortController,
    emit: Emit,
  ): Promise<void> {
    const timeout = setTimeout(() => {
      controller.abort(new JobTimeoutError(`job timed out after ${this.options.jobTimeoutMs}ms`))
    }, this.options.jobTimeoutMs)
    const progress = (stage: string, completed: number, total: number) => {
      if (this.terminalJobs.has(request.job_id) || controller.signal.aborted) {
        return
      }
      if (
        !stage.trim() ||
        !Number.isSafeInteger(completed) ||
        !Number.isSafeInteger(total) ||
        completed < 0 ||
        completed > total
      ) {
        throw new TypeError('invalid worker progress')
      }
      emit({ type: 'progress', job_id: request.job_id, stage, completed, total })
    }

    try {
      const result = await this.handler(request, { signal: controller.signal, progress })
      if (controller.signal.aborted) {
        throw controller.signal.reason
      }
      if (
        !result.artifactPath.trim() ||
        !/^sha256:[a-f0-9]{64}$/.test(result.contentHash)
      ) {
        throw new TypeError('worker handler returned an invalid artifact reference')
      }
      this.emitTerminal(
        request.job_id,
        {
          type: 'result',
          job_id: request.job_id,
          status: 'ok',
          artifact_path: result.artifactPath,
          content_hash: result.contentHash,
          error: null,
        },
        emit,
      )
    } catch (error) {
      const reason = controller.signal.aborted ? controller.signal.reason : error
      const timedOut = reason instanceof JobTimeoutError
      if (timedOut || !controller.signal.aborted) {
        this.hadFailure = true
      }
      this.emitTerminal(
        request.job_id,
        {
          type: 'result',
          job_id: request.job_id,
          status: timedOut || !controller.signal.aborted ? 'error' : 'cancelled',
          artifact_path: null,
          content_hash: null,
          error: reason instanceof Error ? reason.message : 'worker operation failed',
        },
        emit,
      )
    } finally {
      clearTimeout(timeout)
      this.activeJobs.delete(request.job_id)
    }
  }

  private emitTerminal(jobId: string, result: WorkerResult, emit: Emit): void {
    if (this.terminalJobs.has(jobId)) {
      return
    }
    this.terminalJobs.add(jobId)
    emit(Object.freeze(result))
  }
}

export interface RunNdjsonWorkerOptions {
  readonly input: Readable
  readonly output: Writable
  readonly error: Writable
  readonly mode: 'one-shot' | 'persistent'
  readonly jobTimeoutMs: number
  readonly handler: WorkerHandler
  readonly signal?: AbortSignal
}

export async function runNdjsonWorker(options: RunNdjsonWorkerOptions): Promise<number> {
  const worker = new RpcWorker(options.handler, { jobTimeoutMs: options.jobTimeoutMs })
  const lines = createInterface({ input: options.input, crlfDelay: Infinity })
  const emit: Emit = (message) => {
    options.output.write(serializeProtocolMessage(message))
  }
  let requestCount = 0
  let malformed = false
  let shuttingDown = options.signal?.aborted ?? false
  const shutdown = () => {
    shuttingDown = true
    void worker.shutdown(options.signal?.reason)
    lines.close()
    options.input.destroy()
  }
  options.signal?.addEventListener('abort', shutdown, { once: true })

  try {
    for await (const line of lines) {
      if (!line.trim()) {
        malformed = true
        emit(protocolError(null, 'malformed_message', 'empty protocol line'))
        continue
      }
      let message
      try {
        message = parseWorkerInput(line)
      } catch (error) {
        malformed = true
        emit(
          protocolError(
            null,
            'malformed_message',
            error instanceof Error ? error.message : 'invalid protocol message',
          ),
        )
        continue
      }
      if (message.type === 'cancel') {
        await worker.cancel(message.job_id, emit)
        continue
      }
      if (options.mode === 'one-shot' && requestCount > 0) {
        emit(protocolError(message.job_id, 'invalid_message', 'one-shot mode accepts one request'))
        continue
      }
      requestCount += 1
      void worker.accept(message, emit)
    }
  } catch (error) {
    if (!shuttingDown) {
      options.error.write(
        `worker input failed: ${error instanceof Error ? error.message : String(error)}\n`,
      )
      malformed = true
    }
  } finally {
    options.signal?.removeEventListener('abort', shutdown)
    lines.close()
  }

  if (requestCount === 0) {
    options.error.write('worker stdin closed before a request\n')
    return 2
  }
  if (shuttingDown) {
    await worker.shutdown(options.signal?.reason)
  } else {
    await worker.waitForIdle()
  }
  if (malformed) {
    return 2
  }
  return shuttingDown || worker.failed ? 1 : 0
}

export interface WorkerExit {
  readonly code: number | null
  readonly signal: NodeJS.Signals | null
}

export interface WorkerTransport {
  readonly lines: AsyncIterable<string>
  readonly logs: AsyncIterable<string>
  readonly exit: Promise<WorkerExit>
  send(line: string): void
  closeInput(): void
  terminate(): void
}

export interface SupervisedWorkerOptions {
  readonly timeoutMs: number
  readonly signal?: AbortSignal
  readonly onProgress?: (message: Extract<WorkerOutput, { type: 'progress' }>) => void
  readonly onLog?: (line: string) => void
}

export async function runSupervisedWorker(
  transport: WorkerTransport,
  request: WorkerRequest,
  options: SupervisedWorkerOptions,
): Promise<WorkerResult> {
  if (!Number.isSafeInteger(options.timeoutMs) || options.timeoutMs <= 0) {
    throw new TypeError('timeoutMs must be a positive integer')
  }
  if (options.signal?.aborted) {
    transport.terminate()
    await Promise.allSettled([transport.exit])
    throw new Error('worker launch aborted')
  }

  let interruption: Error | undefined
  const interrupt = (error: Error) => {
    if (!interruption) {
      interruption = error
      transport.terminate()
    }
  }
  const timer = setTimeout(
    () => interrupt(new Error(`worker timed out after ${options.timeoutMs}ms`)),
    options.timeoutMs,
  )
  const abort = () => interrupt(new Error('worker launch aborted'))
  options.signal?.addEventListener('abort', abort, { once: true })
  const logs = (async () => {
    for await (const line of transport.logs) {
      options.onLog?.(line)
    }
  })()

  let result: WorkerResult | undefined
  try {
    transport.send(serializeProtocolMessage(request))
    transport.closeInput()
    for await (const line of transport.lines) {
      if (!line.trim()) {
        continue
      }
      const message = parseWorkerOutput(line)
      if (result) {
        throw new WorkerProtocolError('worker emitted output after its terminal result')
      }
      if (message.type === 'error') {
        throw new WorkerProtocolError(`${message.code}: ${message.message}`)
      }
      if (message.job_id !== request.job_id) {
        throw new WorkerProtocolError(`worker emitted output for unexpected job ${message.job_id}`)
      }
      if (message.type === 'progress') {
        options.onProgress?.(message)
      } else {
        result = message
      }
    }
    const exited = await transport.exit
    await logs
    if (interruption) {
      throw interruption
    }
    if (exited.code !== 0) {
      throw new WorkerProcessError(exited.code, exited.signal)
    }
    if (!result) {
      throw new WorkerProtocolError('worker exited without a terminal result')
    }
    return result
  } catch (error) {
    transport.terminate()
    await Promise.allSettled([transport.exit, logs])
    if (interruption) {
      throw interruption
    }
    throw error
  } finally {
    clearTimeout(timer)
    options.signal?.removeEventListener('abort', abort)
  }
}

export interface DirectWorkerOptions extends SupervisedWorkerOptions {
  readonly cwd?: string
  readonly env?: NodeJS.ProcessEnv
}

export function launchDirectWorker(
  executable: string,
  args: readonly string[],
  request: WorkerRequest,
  options: DirectWorkerOptions,
): Promise<WorkerResult> {
  const child = spawn(executable, [...args], {
    stdio: ['pipe', 'pipe', 'pipe'],
    shell: false,
    ...(options.cwd ? { cwd: options.cwd } : {}),
    ...(options.env ? { env: options.env } : {}),
  })
  const stdout = createInterface({ input: child.stdout, crlfDelay: Infinity })
  const stderr = createInterface({ input: child.stderr, crlfDelay: Infinity })
  const exit = new Promise<WorkerExit>((resolve, reject) => {
    child.once('error', reject)
    child.once('exit', (code, signal) => resolve({ code, signal }))
  })
  const transport: WorkerTransport = {
    lines: stdout,
    logs: stderr,
    exit,
    send: (line) => child.stdin.write(line),
    closeInput: () => child.stdin.end(),
    terminate: () => {
      if (child.exitCode === null && child.signalCode === null) {
        child.kill('SIGTERM')
      }
    },
  }
  return runSupervisedWorker(transport, request, options)
}

export function bindProcessShutdown(controller: AbortController): () => void {
  const shutdown = (signal: NodeJS.Signals) => controller.abort(new Error(signal))
  process.once('SIGINT', shutdown)
  process.once('SIGTERM', shutdown)
  return () => {
    process.removeListener('SIGINT', shutdown)
    process.removeListener('SIGTERM', shutdown)
  }
}
