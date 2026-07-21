import { type Static, Type } from '@sinclair/typebox'
import { Value } from '@sinclair/typebox/value'

const HashSchema = Type.String({ pattern: '^sha256:[a-f0-9]{64}$' })
const SymbolSchema = Type.String({ minLength: 1, maxLength: 32 })
const UtcSecondSchema = Type.String({
  pattern: '^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$',
})

export const DecisionSchema = Object.freeze(
  Type.Object(
    {
      symbol: SymbolSchema,
      stance: Type.Union([Type.Literal('buy'), Type.Literal('hold'), Type.Literal('sell')]),
      confidence: Type.Number({ minimum: 0, maximum: 1 }),
      desired_weight: Type.Number({ minimum: 0, maximum: 1 }),
      thesis: Type.String({ minLength: 1 }),
      evidence_refs: Type.Array(Type.String({ minLength: 1 })),
      risks: Type.Array(Type.String({ minLength: 1 })),
      invalidating_conditions: Type.Array(Type.String({ minLength: 1 })),
      intended_holding_period: Type.String({ minLength: 1 }),
    },
    { additionalProperties: false },
  ),
)

export const AbstentionSchema = Object.freeze(
  Type.Object(
    {
      symbol: SymbolSchema,
      reason: Type.String({ minLength: 1 }),
      evidence_refs: Type.Array(Type.String({ minLength: 1 })),
    },
    { additionalProperties: false },
  ),
)

export const DecisionProposalSchema = Object.freeze(
  Type.Object(
    {
      schema_version: Type.Literal(1),
      experiment_id: Type.String({ minLength: 1 }),
      decision_at: UtcSecondSchema,
      candidate_set_hash: HashSchema,
      agent_protocol_hash: HashSchema,
      decisions: Type.Array(DecisionSchema),
      abstentions: Type.Array(AbstentionSchema),
    },
    { additionalProperties: false },
  ),
)

export type DecisionProposal = Static<typeof DecisionProposalSchema>

function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) {
      deepFreeze(child)
    }
    Object.freeze(value)
  }
  return value
}

export function parseDecisionProposal(value: unknown): DecisionProposal {
  if (!Value.Check(DecisionProposalSchema, value)) {
    const issue = Value.Errors(DecisionProposalSchema, value).First()
    throw new TypeError(issue?.message ?? 'invalid DecisionProposal')
  }
  const proposal = structuredClone(value) as DecisionProposal
  if (Number.isNaN(Date.parse(proposal.decision_at))) {
    throw new TypeError('decision_at must be an ISO datetime')
  }
  const symbols = [...proposal.decisions, ...proposal.abstentions].map(({ symbol }) => symbol)
  const duplicate = symbols.find((symbol, index) => symbols.indexOf(symbol) !== index)
  if (duplicate) {
    throw new TypeError(`duplicate symbol: ${duplicate}`)
  }
  return deepFreeze(proposal)
}
