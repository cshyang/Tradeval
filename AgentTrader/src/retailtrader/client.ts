import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'

export interface RetailTraderClientOptions {
  readonly executable: string
  readonly executableArgs?: readonly string[]
  readonly cwd: string
  readonly codeRevision: string
  readonly timeoutMs: number
}

export interface RetailTraderExecution<T> {
  readonly result: T
  readonly executable: string
  readonly args: readonly string[]
  readonly codeRevision: string
  readonly durationMs: number
}

export class RetailTraderProcessError extends Error {
  constructor(
    message: string,
    readonly exitCode: number | null,
    readonly errorCode?: string,
  ) {
    super(message)
  }
}

export class RetailTraderClient {
  constructor(private readonly options: RetailTraderClientOptions) {
    if (!options.executable.trim() || !options.codeRevision.trim()) {
      throw new TypeError('RetailTrader executable and code revision are required')
    }
    if (!Number.isSafeInteger(options.timeoutMs) || options.timeoutMs <= 0) {
      throw new TypeError('RetailTrader timeout must be a positive integer')
    }
  }

  async execute<T extends Record<string, unknown>>(
    args: readonly string[],
    options: { readonly signal?: AbortSignal; readonly onLog?: (line: string) => void } = {},
  ): Promise<RetailTraderExecution<T>> {
    if (options.signal?.aborted) {
      throw new RetailTraderProcessError('RetailTrader command aborted', null)
    }
    const fullArgs = [...(this.options.executableArgs ?? []), ...args]
    const started = performance.now()
    const child = spawn(this.options.executable, fullArgs, {
      cwd: this.options.cwd,
      shell: false,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    const stdout = createInterface({ input: child.stdout, crlfDelay: Infinity })
    const stderr = createInterface({ input: child.stderr, crlfDelay: Infinity })
    const stdoutLines: string[] = []
    const logs = (async () => {
      for await (const line of stderr) {
        options.onLog?.(line)
      }
    })()
    const output = (async () => {
      for await (const line of stdout) {
        if (line.trim()) stdoutLines.push(line)
      }
    })()
    let interruption: Error | undefined
    const terminate = (error: Error) => {
      if (!interruption) {
        interruption = error
        child.kill('SIGTERM')
      }
    }
    const timer = setTimeout(
      () => terminate(new Error(`RetailTrader command timed out after ${this.options.timeoutMs}ms`)),
      this.options.timeoutMs,
    )
    const abort = () => terminate(new Error('RetailTrader command aborted'))
    options.signal?.addEventListener('abort', abort, { once: true })
    const exited = new Promise<{ code: number | null; signal: NodeJS.Signals | null }>(
      (resolve, reject) => {
        child.once('error', reject)
        child.once('exit', (code, signal) => resolve({ code, signal }))
      },
    )

    try {
      const exit = await exited
      await Promise.all([output, logs])
      if (interruption) throw interruption
      if (stdoutLines.length !== 1) {
        throw new RetailTraderProcessError(
          `RetailTrader emitted ${stdoutLines.length} JSON envelopes`,
          exit.code,
        )
      }
      let envelope: unknown
      try {
        envelope = JSON.parse(stdoutLines[0]!)
      } catch (error) {
        throw new RetailTraderProcessError(
          `RetailTrader emitted invalid JSON: ${error instanceof Error ? error.message : String(error)}`,
          exit.code,
        )
      }
      if (!envelope || typeof envelope !== 'object') {
        throw new RetailTraderProcessError('RetailTrader emitted an invalid envelope', exit.code)
      }
      const value = envelope as Record<string, unknown>
      if (exit.code !== 0 || value.status !== 'ok') {
        const detail = value.error as { code?: string; message?: string } | undefined
        throw new RetailTraderProcessError(
          detail?.message ?? `RetailTrader exited with code ${exit.code}`,
          exit.code,
          detail?.code,
        )
      }
      if (!value.result || typeof value.result !== 'object') {
        throw new RetailTraderProcessError('RetailTrader success envelope has no result', exit.code)
      }
      return Object.freeze({
        result: structuredClone(value.result) as T,
        executable: this.options.executable,
        args: Object.freeze(fullArgs),
        codeRevision: this.options.codeRevision,
        durationMs: Math.max(0, performance.now() - started),
      })
    } finally {
      clearTimeout(timer)
      options.signal?.removeEventListener('abort', abort)
    }
  }
}
