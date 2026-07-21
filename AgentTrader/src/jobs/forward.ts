import { readFileSync } from 'node:fs'

import type { ScheduledExperiment } from './scheduler.js'

export interface ForwardPipeline {
  screen(payload: Record<string, unknown>, dueAt: string, signal?: AbortSignal): Promise<string>
  propose(candidateSetPath: string, signal?: AbortSignal): Promise<string>
  commit(proposalPath: string, signal?: AbortSignal): Promise<'committed' | 'no_op' | 'deferred'>
}

export async function runForwardDecision(
  job: ScheduledExperiment,
  pipeline: ForwardPipeline,
  signal?: AbortSignal,
): Promise<'committed' | 'no_op' | 'deferred'> {
  const payload = JSON.parse(readFileSync(job.payloadPath, 'utf8')) as Record<string, unknown>
  const mandate = payload.mandate as { horizon?: { kind?: string } } | undefined
  if (mandate?.horizon?.kind !== 'forward') throw new TypeError('forward scheduler requires a forward mandate')
  const candidateSet = await pipeline.screen(payload, job.dueAt, signal)
  const proposal = await pipeline.propose(candidateSet, signal)
  return pipeline.commit(proposal, signal)
}
