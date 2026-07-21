import type { AgentTool } from '@mariozechner/pi-agent-core'
import { Type, type Model } from '@mariozechner/pi-ai'

import { parseCandidateSet, type CandidateSet } from '../contracts/candidates.js'
import { parseDecisionProposal, type DecisionProposal } from '../contracts/proposals.js'
import { PROPOSAL_REPAIR_PROMPT, PROPOSAL_SYSTEM_PROMPT, proposalPrompt } from '../prompts/proposal.js'
import {
  PiTimeoutError,
  type PiRunRecord,
  runAgentSubmission,
} from '../pi/run-agent.js'
import { createCandidateDataTool } from '../tools/candidate-data.js'

const strictObject = <T extends Parameters<typeof Type.Object>[0]>(properties: T) =>
  Type.Object(properties, { additionalProperties: false })
const hash = () => Type.String({ pattern: '^sha256:[a-f0-9]{64}$' })

const DecisionProposalParameters = strictObject({
  schema_version: Type.Literal(1),
  experiment_id: Type.String({ minLength: 1 }),
  decision_at: Type.String({ pattern: '^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$' }),
  candidate_set_hash: hash(),
  agent_protocol_hash: hash(),
  decisions: Type.Array(
    strictObject({
      symbol: Type.String({ minLength: 1, maxLength: 32 }),
      stance: Type.Union([Type.Literal('buy'), Type.Literal('hold'), Type.Literal('sell')]),
      confidence: Type.Number({ minimum: 0, maximum: 1 }),
      desired_weight: Type.Number({ minimum: 0, maximum: 1 }),
      thesis: Type.String({ minLength: 1 }),
      evidence_refs: Type.Array(Type.String({ minLength: 1 })),
      risks: Type.Array(Type.String({ minLength: 1 })),
      invalidating_conditions: Type.Array(Type.String({ minLength: 1 })),
      intended_holding_period: Type.String({ minLength: 1 }),
    }),
  ),
  abstentions: Type.Array(
    strictObject({
      symbol: Type.String({ minLength: 1, maxLength: 32 }),
      reason: Type.String({ minLength: 1 }),
      evidence_refs: Type.Array(Type.String({ minLength: 1 })),
    }),
  ),
})

export interface GenerateProposalOptions {
  readonly candidateSet: unknown
  readonly agentProtocolHash: string
  readonly model: Model<string>
  readonly timeoutMs: number
  readonly signal?: AbortSignal
  readonly sessionId?: string
}

export interface ProposalWorkerResult {
  readonly status: 'submitted' | 'abstained_timeout'
  readonly proposal: DecisionProposal
  readonly runs: readonly PiRunRecord[]
}

function validateProposal(
  value: unknown,
  candidateSet: CandidateSet,
  agentProtocolHash: string,
): DecisionProposal {
  const proposal = parseDecisionProposal(value)
  if (proposal.experiment_id !== candidateSet.experiment_id) {
    throw new TypeError('proposal experiment_id does not match the candidate set')
  }
  if (proposal.decision_at !== candidateSet.decision_at) {
    throw new TypeError('proposal decision_at does not match the candidate set')
  }
  if (proposal.candidate_set_hash !== candidateSet.candidate_set_hash) {
    throw new TypeError('proposal candidate_set_hash does not match the candidate set')
  }
  if (proposal.agent_protocol_hash !== agentProtocolHash) {
    throw new TypeError('proposal agent_protocol_hash does not match the frozen protocol')
  }

  const evidenceBySymbol = new Map(
    candidateSet.candidates.map((candidate) => [
      candidate.symbol,
      new Set(candidate.metrics.flatMap(({ evidence_refs }) => evidence_refs)),
    ]),
  )
  for (const item of [...proposal.decisions, ...proposal.abstentions]) {
    const allowed = evidenceBySymbol.get(item.symbol)
    if (!allowed) {
      throw new TypeError(`proposal symbol is outside the candidate set: ${item.symbol}`)
    }
    const unknown = item.evidence_refs.filter((reference) => !allowed.has(reference))
    if (unknown.length > 0) {
      throw new TypeError(`unknown evidence for ${item.symbol}: ${unknown.join(', ')}`)
    }
  }
  return proposal
}

function timeoutProposal(candidateSet: CandidateSet, agentProtocolHash: string): DecisionProposal {
  return parseDecisionProposal({
    schema_version: 1,
    experiment_id: candidateSet.experiment_id,
    decision_at: candidateSet.decision_at,
    candidate_set_hash: candidateSet.candidate_set_hash,
    agent_protocol_hash: agentProtocolHash,
    decisions: [],
    abstentions: candidateSet.candidates.map(({ symbol }) => ({
      symbol,
      reason: 'model timeout after one retry',
      evidence_refs: [],
    })),
  })
}

export async function generateProposal(
  options: GenerateProposalOptions,
): Promise<ProposalWorkerResult> {
  const candidateSet = parseCandidateSet(options.candidateSet)
  if (!/^sha256:[a-f0-9]{64}$/.test(options.agentProtocolHash)) {
    throw new TypeError('agentProtocolHash must be a canonical SHA-256 hash')
  }
  const runs: PiRunRecord[] = []

  const runOnce = async () => {
    let submission: DecisionProposal | undefined
    let submissionError: string | undefined
    let candidateDataRead = false
    const submitTool: AgentTool<typeof DecisionProposalParameters> = {
      name: 'submit_proposals',
      label: 'Submit proposals',
      description: 'Submit the complete evidence-bound decision proposal.',
      parameters: DecisionProposalParameters,
      executionMode: 'sequential',
      execute: async (_toolCallId, params, signal) => {
        if (signal?.aborted) {
          throw new Error('submit_proposals aborted')
        }
        try {
          if (!candidateDataRead) {
            throw new TypeError('candidate data must be read before submitting proposals')
          }
          submission = validateProposal(params, candidateSet, options.agentProtocolHash)
          submissionError = undefined
        } catch (error) {
          submissionError = error instanceof Error ? error.message : 'invalid proposal'
        }
        return {
          content: [
            {
              type: 'text',
              text: submissionError ? `Rejected proposal: ${submissionError}` : 'Proposal accepted.',
            },
          ],
          details: { accepted: submissionError === undefined },
          terminate: true,
        }
      },
    }
    return runAgentSubmission({
      model: options.model,
      systemPrompt: PROPOSAL_SYSTEM_PROMPT,
      prompt: proposalPrompt(candidateSet, options.agentProtocolHash),
      repairPrompt: PROPOSAL_REPAIR_PROMPT,
      tools: [
        createCandidateDataTool(candidateSet, () => {
          candidateDataRead = true
        }),
        submitTool,
      ],
      getSubmission: () => submission,
      getSubmissionError: () => submissionError,
      timeoutMs: options.timeoutMs,
      ...(options.signal ? { signal: options.signal } : {}),
      ...(options.sessionId ? { sessionId: options.sessionId } : {}),
    })
  }

  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const result = await runOnce()
      runs.push(result.run)
      return Object.freeze({
        status: 'submitted',
        proposal: result.submission,
        runs: Object.freeze(runs),
      })
    } catch (error) {
      if (!(error instanceof PiTimeoutError)) {
        throw error
      }
      if (error.run) {
        runs.push(error.run)
      }
    }
  }

  return Object.freeze({
    status: 'abstained_timeout',
    proposal: timeoutProposal(candidateSet, options.agentProtocolHash),
    runs: Object.freeze(runs),
  })
}
