import type { AgentTool } from '@mariozechner/pi-agent-core'
import { Type } from '@mariozechner/pi-ai'

import type { CandidateSet } from '../contracts/candidates.js'
import { canonicalJson } from '../contracts/hash.js'

const CandidateDataParameters = Type.Object(
  {
    symbols: Type.Array(Type.String({ minLength: 1 }), {
      minItems: 1,
      uniqueItems: true,
    }),
  },
  { additionalProperties: false },
)

export function createCandidateDataTool(
  candidateSet: CandidateSet,
  onRead?: () => void,
): AgentTool<typeof CandidateDataParameters> {
  const candidates = new Map(
    candidateSet.candidates.map((candidate) => [candidate.symbol, candidate] as const),
  )
  const frozenSymbols = [...candidates.keys()].sort()
  let used = false
  return {
    name: 'get_candidate_data',
    label: 'Get candidate data',
    description: 'Read a batch of records from the frozen candidate set.',
    parameters: CandidateDataParameters,
    executionMode: 'sequential',
    execute: async (_toolCallId, params, signal) => {
      if (signal?.aborted) {
        throw new Error('get_candidate_data aborted')
      }
      const symbols = [...params.symbols].sort()
      if (used) {
        throw new Error('get_candidate_data may be called only once')
      }
      if (JSON.stringify(symbols) !== JSON.stringify(frozenSymbols)) {
        throw new Error('get_candidate_data must request every frozen candidate in one batch')
      }
      used = true
      onRead?.()
      const records = symbols.map((symbol) => candidates.get(symbol)!)
      return {
        content: [{ type: 'text', text: canonicalJson({ candidates: records }) }],
        details: { candidateCount: records.length },
      }
    },
  }
}
