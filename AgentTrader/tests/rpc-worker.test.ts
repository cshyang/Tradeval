import { PassThrough } from 'node:stream'
import { describe, expect, test } from 'vitest'

import {
  parseWorkerInput,
  parseWorkerOutput,
  serializeProtocolMessage,
  type WorkerRequest,
} from '../src/rpc/protocol.js'
import {
  RpcWorker,
  WorkerProcessError,
  WorkerProtocolError,
  launchDirectWorker,
  runNdjsonWorker,
} from '../src/rpc/worker.js'

const HASH = `sha256:${'a'.repeat(64)}`

function request(jobId = 'job-1'): WorkerRequest {
  return {
    type: 'request',
    job_id: jobId,
    operation: 'proposal.generate',
    payload: { fixture: true },
  }
}

function collect(stream: PassThrough): { read: () => string } {
  let value = ''
  stream.setEncoding('utf8')
  stream.on('data', (chunk: string) => {
    value += chunk
  })
  return { read: () => value }
}

function outputLines(value: string) {
  return value
    .trim()
    .split('\n')
    .filter(Boolean)
    .map((line) => parseWorkerOutput(line))
}

describe('NDJSON worker runtime', () => {
  test('deep-freezes requests and rejects inconsistent terminal fields', () => {
    const parsed = parseWorkerInput(serializeProtocolMessage(request()).trim())

    expect(Object.isFrozen(parsed)).toBe(true)
    expect(Object.isFrozen(parsed.type === 'request' ? parsed.payload : {})).toBe(true)
    expect(() =>
      parseWorkerOutput(
        JSON.stringify({
          type: 'result',
          job_id: 'job-1',
          status: 'ok',
          artifact_path: null,
          content_hash: null,
          error: 'not actually successful',
        }),
      ),
    ).toThrow('fields do not match')
  })

  test('reports malformed JSON without invoking a handler', async () => {
    const input = new PassThrough()
    const output = new PassThrough()
    const error = new PassThrough()
    const captured = collect(output)
    let called = false
    input.end('{bad json}\n')

    const exitCode = await runNdjsonWorker({
      input,
      output,
      error,
      mode: 'one-shot',
      jobTimeoutMs: 100,
      handler: async () => {
        called = true
        return { artifactPath: '/tmp/never', contentHash: HASH }
      },
    })

    expect(exitCode).toBe(2)
    expect(called).toBe(false)
    expect(outputLines(captured.read())).toEqual([
      expect.objectContaining({ type: 'error', code: 'malformed_message', job_id: null }),
    ])
  })

  test('rejects duplicate job IDs and emits one terminal result', async () => {
    const input = new PassThrough()
    const output = new PassThrough()
    const error = new PassThrough()
    const captured = collect(output)
    input.end(
      serializeProtocolMessage(request()) + serializeProtocolMessage(request()),
    )

    const exitCode = await runNdjsonWorker({
      input,
      output,
      error,
      mode: 'persistent',
      jobTimeoutMs: 100,
      handler: async (_request, context) => {
        context.progress('agent_evaluating', 1, 1)
        return { artifactPath: '/tmp/artifact.json', contentHash: HASH }
      },
    })
    const messages = outputLines(captured.read())

    expect(exitCode).toBe(0)
    expect(messages.filter(({ type }) => type === 'result')).toHaveLength(1)
    expect(messages).toContainEqual(
      expect.objectContaining({ type: 'error', code: 'duplicate_job', job_id: 'job-1' }),
    )
  })

  test('times out a cooperative handler', async () => {
    const worker = new RpcWorker(
      async (_request, context) =>
        new Promise((_resolve, reject) => {
          context.signal.addEventListener('abort', () => reject(context.signal.reason), {
            once: true,
          })
        }),
      { jobTimeoutMs: 5 },
    )
    const messages: unknown[] = []

    await worker.accept(request(), (message) => messages.push(message))
    await worker.waitForIdle()

    expect(messages).toContainEqual(
      expect.objectContaining({
        type: 'result',
        status: 'error',
        error: 'job timed out after 5ms',
      }),
    )
  })

  test('cancels an active job and ignores progress after its terminal result', async () => {
    let lateProgress: (() => void) | undefined
    const worker = new RpcWorker(
      async (_request, context) => {
        lateProgress = () => context.progress('too_late', 1, 1)
        return new Promise((_resolve, reject) => {
          context.signal.addEventListener('abort', () => reject(context.signal.reason), {
            once: true,
          })
        })
      },
      { jobTimeoutMs: 1_000 },
    )
    const messages: Array<{ type: string; stage?: string }> = []
    void worker.accept(request(), (message) => messages.push(message))
    await worker.cancel('job-1', (message) => messages.push(message))
    await worker.waitForIdle()
    lateProgress?.()

    expect(messages.filter(({ type }) => type === 'result')).toEqual([
      expect.objectContaining({ status: 'cancelled' }),
    ])
    expect(messages.some(({ stage }) => stage === 'too_late')).toBe(false)
  })

  test('returns an early-EOF error when no request arrives', async () => {
    const input = new PassThrough()
    const output = new PassThrough()
    const error = new PassThrough()
    const logs = collect(error)
    input.end()

    const exitCode = await runNdjsonWorker({
      input,
      output,
      error,
      mode: 'one-shot',
      jobTimeoutMs: 100,
      handler: async () => ({ artifactPath: '/tmp/never', contentHash: HASH }),
    })

    expect(exitCode).toBe(2)
    expect(logs.read()).toContain('stdin closed before a request')
  })

  test('aborts active work during process-style shutdown', async () => {
    const input = new PassThrough()
    const output = new PassThrough()
    const error = new PassThrough()
    const captured = collect(output)
    const shutdown = new AbortController()
    const running = runNdjsonWorker({
      input,
      output,
      error,
      mode: 'persistent',
      jobTimeoutMs: 1_000,
      signal: shutdown.signal,
      handler: async (_request, context) =>
        new Promise((_resolve, reject) => {
          context.signal.addEventListener('abort', () => reject(context.signal.reason), {
            once: true,
          })
        }),
    })
    input.write(serializeProtocolMessage(request()))
    await new Promise((resolve) => setTimeout(resolve, 5))
    shutdown.abort(new Error('SIGTERM'))

    const exitCode = await running

    expect(exitCode).toBe(1)
    expect(outputLines(captured.read())).toContainEqual(
      expect.objectContaining({ type: 'result', status: 'cancelled' }),
    )
  })
})

describe('direct worker launcher', () => {
  test('uses argument arrays, captures logs, and returns one result', async () => {
    const script = `
      let body = '';
      process.stdin.setEncoding('utf8');
      process.stdin.on('data', chunk => body += chunk);
      process.stdin.on('end', () => {
        const request = JSON.parse(body.trim());
        console.error('worker log');
        console.log(JSON.stringify({type:'progress',job_id:request.job_id,stage:'done',completed:1,total:1}));
        console.log(JSON.stringify({type:'result',job_id:request.job_id,status:'ok',artifact_path:'/tmp/a.json',content_hash:'${HASH}',error:null}));
      });
    `
    const logs: string[] = []

    const result = await launchDirectWorker(process.execPath, ['-e', script], request(), {
      timeoutMs: 1_000,
      onLog: (line) => logs.push(line),
    })

    expect(result.status).toBe('ok')
    expect(result.artifact_path).toBe('/tmp/a.json')
    expect(logs).toContain('worker log')
  })

  test('rejects non-zero child exits', async () => {
    await expect(
      launchDirectWorker(process.execPath, ['-e', 'process.exit(7)'], request(), {
        timeoutMs: 1_000,
      }),
    ).rejects.toMatchObject<WorkerProcessError>({ exitCode: 7 })
  })

  test('terminates a child on timeout or cancellation', async () => {
    const script = `process.stdin.resume(); setInterval(() => {}, 1000);`
    await expect(
      launchDirectWorker(process.execPath, ['-e', script], request('timeout-job'), {
        timeoutMs: 10,
      }),
    ).rejects.toThrow('worker timed out')

    const controller = new AbortController()
    const launched = launchDirectWorker(process.execPath, ['-e', script], request('cancel-job'), {
      timeoutMs: 1_000,
      signal: controller.signal,
    })
    setTimeout(() => controller.abort(), 10)
    await expect(launched).rejects.toThrow('worker launch aborted')
  })

  test('rejects output after a terminal result', async () => {
    const terminal = JSON.stringify({
      type: 'result',
      job_id: 'job-1',
      status: 'ok',
      artifact_path: '/tmp/a.json',
      content_hash: HASH,
      error: null,
    })
    const progress = JSON.stringify({
      type: 'progress',
      job_id: 'job-1',
      stage: 'late',
      completed: 1,
      total: 1,
    })
    const script = `console.log(${JSON.stringify(terminal)}); console.log(${JSON.stringify(progress)});`

    await expect(
      launchDirectWorker(process.execPath, ['-e', script], request(), { timeoutMs: 1_000 }),
    ).rejects.toBeInstanceOf(WorkerProtocolError)
  })
})
