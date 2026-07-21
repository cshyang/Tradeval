import type { CandidateSet } from '../contracts/candidates.js'

export const PROPOSAL_SYSTEM_PROMPT = `You propose paper-portfolio stances and desired weights from a frozen candidate set.
You control only stance, confidence, desired weight, thesis, risks, invalidating conditions, holding period, and abstention.
You do not control orders, fills, cash, positions, turnover enforcement, portfolio accounting, or returns.
Use get_candidate_data once to inspect the frozen candidates, then use submit_proposals exactly once.
Never cite evidence outside the candidate record for the same symbol.`

export function proposalPrompt(candidateSet: CandidateSet, agentProtocolHash: string): string {
  const symbols = candidateSet.candidates.map(({ symbol }) => symbol).sort()
  return [
    `Experiment: ${candidateSet.experiment_id}`,
    `Decision time: ${candidateSet.decision_at}`,
    `Candidate set hash: ${candidateSet.candidate_set_hash}`,
    `Agent protocol hash: ${agentProtocolHash}`,
    `Frozen candidate symbols: ${symbols.join(', ')}`,
    'Inspect candidate data in one batch, then submit a decision or abstention for relevant candidates.',
  ].join('\n')
}

export const PROPOSAL_REPAIR_PROMPT =
  'Submit one corrected proposal. Repair structure or references only; use no symbol or evidence that was not already available through get_candidate_data.'
