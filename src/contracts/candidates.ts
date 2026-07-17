import { type Static, Type } from '@sinclair/typebox'

const EvidenceMetricSchema = Type.Object(
  {
    name: Type.String({ minLength: 1 }),
    value: Type.Union([Type.Number(), Type.Null()]),
    unavailable_reason: Type.Union([Type.String({ minLength: 1 }), Type.Null()]),
    evidence_refs: Type.Array(Type.String({ minLength: 1 })),
  },
  { additionalProperties: false },
)

const CandidateSchema = Type.Object(
  {
    symbol: Type.String({ minLength: 1 }),
    score: Type.Number(),
    evidence_coverage: Type.Number({ minimum: 0, maximum: 1 }),
    metrics: Type.Array(EvidenceMetricSchema),
  },
  { additionalProperties: false },
)

export const CandidateSetSchema = Object.freeze(
  Type.Object(
    {
      schema_version: Type.Literal(1),
      experiment_id: Type.String({ minLength: 1 }),
      decision_at: Type.String({
        pattern: '^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$',
      }),
      market_data_hash: Type.String({ pattern: '^sha256:[a-f0-9]{64}$' }),
      candidates: Type.Array(CandidateSchema),
      exclusions: Type.Array(
        Type.Object(
          {
            symbol: Type.String({ minLength: 1 }),
            reason: Type.String({ minLength: 1 }),
          },
          { additionalProperties: false },
        ),
      ),
      candidate_set_hash: Type.String({ pattern: '^sha256:[a-f0-9]{64}$' }),
    },
    { additionalProperties: false },
  ),
)

export type CandidateSet = Static<typeof CandidateSetSchema>
