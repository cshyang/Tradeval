import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { Value } from '@sinclair/typebox/value'

import type { AgentTraderConfig } from '../config.js'
import { canonicalHash, canonicalJson } from '../contracts/hash.js'
import { MandateSpecSchema, type MandateSpec } from '../contracts/mandate.js'
import { AgentProtocolSchema, type AgentProtocol } from '../contracts/protocol.js'
import { JobEventLog } from '../jobs/events.js'
import { runHindsightJob } from '../jobs/hindsight.js'
import type { JobStore } from '../jobs/store.js'
import { resolveModel } from '../pi/model.js'
import { RetailTraderClient } from '../retailtrader/client.js'
import { generatePhilosophy } from '../workers/philosophy.js'
import { generateProposal } from '../workers/proposal.js'
import type { ApiJobExecutor } from './routes/experiments.js'

function immutableJson(path: string, value: unknown): void {
  const content = `${canonicalJson(value)}\n`
  if (existsSync(path)) {
    if (readFileSync(path, 'utf8') !== content) throw new Error(`immutable artifact conflict: ${path}`)
    return
  }
  writeFileSync(path, content, { flag: 'wx', mode: 0o600 })
}

export function createRuntimeExecutor(config: AgentTraderConfig, store: JobStore): ApiJobExecutor {
  const model = resolveModel(config.modelProvider, config.modelId)
  return async (request, signal) => {
    if (request.operation === 'philosophy.generate') {
      store.update(request.jobId, { status: 'running', stage: 'interpreting_philosophy' })
      const description = request.body.description
      if (typeof description !== 'string') throw new TypeError('description is required')
      const generated = await generatePhilosophy({ description, model, timeoutMs: config.jobTimeoutMs, signal })
      const resultPath = join(request.workspace, 'philosophy.json')
      immutableJson(resultPath, generated)
      new JobEventLog(join(request.workspace, 'events.jsonl')).append({
        type: 'artifact', stage: 'completed', session: null, artifact: resultPath, detail: null,
      })
      store.update(request.jobId, { status: 'completed', stage: 'completed', resultPath })
      return
    }

    if (!Value.Check(MandateSpecSchema, request.body.mandate)) throw new TypeError('invalid mandate')
    if (!Value.Check(AgentProtocolSchema, request.body.protocol)) throw new TypeError('invalid agent protocol')
    const mandate = structuredClone(request.body.mandate) as MandateSpec
    const protocol = structuredClone(request.body.protocol) as AgentProtocol
    const retailTrader = new RetailTraderClient({
      executable: config.retailTraderExecutable,
      executableArgs: ['run', 'retailtrader'],
      cwd: config.retailTraderRoot,
      codeRevision: process.env.RETAILTRADER_CODE_REVISION?.trim() || 'working-tree',
      timeoutMs: config.jobTimeoutMs,
    })
    await runHindsightJob({
      jobId: request.jobId,
      workspace: request.workspace,
      mandate,
      protocol,
      retailTrader,
      store,
      signal,
      proposalWorker: {
        generate: async ({ candidateSetPath, outputPath, signal: proposalSignal }) => {
          const generated = await generateProposal({
            candidateSet: JSON.parse(readFileSync(candidateSetPath, 'utf8')),
            agentProtocolHash: canonicalHash(protocol),
            model,
            timeoutMs: protocol.timeout_ms,
            ...(proposalSignal ? { signal: proposalSignal } : {}),
          })
          immutableJson(outputPath, generated.proposal)
          return { artifactPath: outputPath, contentHash: canonicalHash(generated.proposal) }
        },
      },
    })
  }
}
