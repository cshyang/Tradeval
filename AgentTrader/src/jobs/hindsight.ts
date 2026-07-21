import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import type { AgentProtocol } from '../contracts/protocol.js'
import { canonicalHash, canonicalJson } from '../contracts/hash.js'
import type { MandateSpec } from '../contracts/mandate.js'
import type { RetailTraderClient } from '../retailtrader/client.js'
import { JobEventLog } from './events.js'
import type { JobStore } from './store.js'

interface PreparedFrameReference {
  readonly session: string
  readonly decision_at: string
  readonly step_directory: string
}

export interface ProposalWorkerClient {
  generate(options: {
    readonly jobId: string
    readonly candidateSetPath: string
    readonly protocolPath: string
    readonly outputPath: string
    readonly signal?: AbortSignal
  }): Promise<{ readonly artifactPath: string; readonly contentHash: string }>
}

export interface RunHindsightJobOptions {
  readonly jobId: string
  readonly workspace: string
  readonly mandate: MandateSpec
  readonly protocol: AgentProtocol
  readonly retailTrader: RetailTraderClient
  readonly proposalWorker: ProposalWorkerClient
  readonly store: JobStore
  readonly signal?: AbortSignal
}

export interface HindsightSessionResult {
  readonly session: string
  readonly candidateSetPath: string
  readonly proposalPath: string
  readonly adjudicationPath: string
}

export interface HindsightJobResult {
  readonly classification: 'HINDSIGHT SCENARIO'
  readonly jobId: string
  readonly experimentId: string
  readonly sessions: readonly HindsightSessionResult[]
  readonly evaluationPath: string
  readonly comparisonPath: string
}

function writeImmutable(path: string, value: unknown): void {
  const content = `${canonicalJson(value)}\n`
  if (existsSync(path)) {
    if (readFileSync(path, 'utf8') !== content) throw new Error(`immutable artifact conflict: ${path}`)
    return
  }
  writeFileSync(path, content, { flag: 'wx', mode: 0o600 })
}

function requireString(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value) throw new Error(`RetailTrader result missing ${field}`)
  return value
}

export async function runHindsightJob(
  options: RunHindsightJobOptions,
): Promise<HindsightJobResult> {
  if (options.mandate.horizon.kind !== 'hindsight') {
    throw new TypeError('hindsight job requires a hindsight mandate')
  }
  mkdirSync(options.workspace, { recursive: true })
  const inputs = join(options.workspace, 'inputs')
  mkdirSync(inputs, { recursive: true })
  const mandatePath = join(inputs, 'mandate.json')
  const protocolPath = join(inputs, 'agent-protocol.json')
  const resultPath = join(options.workspace, 'result.json')
  const events = new JobEventLog(join(options.workspace, 'events.jsonl'))
  const job = options.store.create(options.jobId, options.mandate.experiment_id, options.workspace)
  if (job.status === 'completed' && job.resultPath && existsSync(job.resultPath)) {
    return JSON.parse(readFileSync(job.resultPath, 'utf8')) as HindsightJobResult
  }
  let activeStage = 'queued'
  const stage = (name: string, session: string | null = null) => {
    activeStage = name
    options.store.update(options.jobId, { status: 'running', stage: name, error: null })
    events.append({ type: 'stage', stage: name, session, artifact: null, detail: null })
  }
  const artifact = (name: string, path: string, session: string | null = null) => {
    events.append({ type: 'artifact', stage: name, session, artifact: path, detail: null })
  }
  const runRetailTrader = async <T extends Record<string, unknown>>(args: readonly string[]) => {
    const execution = await options.retailTrader.execute<T>(args, {
      ...(options.signal ? { signal: options.signal } : {}),
      onLog: (line) => {
        events.append({
          type: 'log',
          stage: activeStage,
          session: null,
          artifact: null,
          detail: line,
        })
      },
    })
    events.append({
      type: 'command',
      stage: activeStage,
      session: null,
      artifact: null,
      detail: canonicalJson({
        executable: execution.executable,
        args: execution.args,
        code_revision: execution.codeRevision,
        duration_ms: execution.durationMs,
      }),
    })
    return execution
  }

  try {
    stage('freezing_inputs')
    writeImmutable(mandatePath, options.mandate)
    writeImmutable(protocolPath, options.protocol)
    artifact('mandate', mandatePath)
    artifact('agent_protocol', protocolPath)

    stage('preparing_hindsight')
    const prepared = await runRetailTrader<{ frames: PreparedFrameReference[] }>(
      [
        'agent',
        'prepare-hindsight',
        '--experiment',
        mandatePath,
        '--workspace',
        options.workspace,
        '--format',
        'json',
      ],
    )
    if (!Array.isArray(prepared.result.frames) || prepared.result.frames.length === 0) {
      throw new Error('RetailTrader prepared no hindsight frames')
    }

    const sessions: HindsightSessionResult[] = []
    for (const frame of prepared.result.frames) {
      const candidateSetPath = join(frame.step_directory, 'candidate-set.json')
      const proposalPath = join(frame.step_directory, 'decision-proposal.json')
      stage('screening_candidates', frame.session)
      await runRetailTrader(
        [
          'agent',
          'candidates',
          '--experiment',
          mandatePath,
          '--decision-at',
          frame.decision_at,
          '--out',
          candidateSetPath,
          '--format',
          'json',
        ],
      )
      artifact('candidate_set', candidateSetPath, frame.session)

      await runRetailTrader([
        'agent', 'prepare-frame',
        '--source', join(frame.step_directory, 'frame-source.json'),
        '--candidate-set', candidateSetPath,
        '--out', join(frame.step_directory, 'prepared-frame.json'),
        '--format', 'json',
      ])

      stage('generating_proposal', frame.session)
      if (!existsSync(proposalPath)) {
        await options.proposalWorker.generate({
          jobId: `${options.jobId}:${frame.session}`,
          candidateSetPath,
          protocolPath,
          outputPath: proposalPath,
          ...(options.signal ? { signal: options.signal } : {}),
        })
      }
      artifact('proposal', proposalPath, frame.session)

      stage('adjudicating_proposal', frame.session)
      const stepped = await runRetailTrader<Record<string, unknown>>(
        [
          'agent',
          'step',
          '--workspace',
          options.workspace,
          '--proposal',
          proposalPath,
          '--format',
          'json',
        ],
      )
      const adjudicationPath = requireString(stepped.result.adjudication_path, 'adjudication_path')
      const persistedProposal = requireString(stepped.result.proposal_path, 'proposal_path')
      artifact('persisted_proposal', persistedProposal, frame.session)
      artifact('adjudication', adjudicationPath, frame.session)
      sessions.push(Object.freeze({
        session: frame.session,
        candidateSetPath,
        proposalPath: persistedProposal,
        adjudicationPath,
      }))
    }

    stage('evaluating_controls')
    const finalized = await runRetailTrader<Record<string, unknown>>(
      ['agent', 'finalize-hindsight', '--workspace', options.workspace, '--format', 'json'],
    )
    const result: HindsightJobResult = Object.freeze({
      classification: 'HINDSIGHT SCENARIO',
      jobId: options.jobId,
      experimentId: options.mandate.experiment_id,
      sessions: Object.freeze(sessions),
      evaluationPath: requireString(finalized.result.evaluation_path, 'evaluation_path'),
      comparisonPath: requireString(finalized.result.comparison_path, 'comparison_path'),
    })
    writeImmutable(resultPath, result)
    artifact('evaluation', result.evaluationPath)
    artifact('comparison', result.comparisonPath)
    stage('completed')
    options.store.update(options.jobId, {
      status: 'completed',
      stage: 'completed',
      resultPath,
      error: null,
    })
    return result
  } catch (error) {
    const message = error instanceof Error ? error.message : 'hindsight job failed'
    options.store.update(options.jobId, { status: 'failed', stage: 'failed', error: message })
    events.append({ type: 'error', stage: 'failed', session: null, artifact: null, detail: message })
    throw error
  }
}
