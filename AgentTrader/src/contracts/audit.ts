import { type Static, Type } from '@sinclair/typebox'

export const AgentAuditSchema = Object.freeze(
  Type.Object(
    {
      schema_version: Type.Literal(1),
      job_id: Type.String({ minLength: 1 }),
      experiment_id: Type.String({ minLength: 1 }),
      operation: Type.Union([
        Type.Literal('philosophy.generate'),
        Type.Literal('proposal.generate'),
      ]),
      provider: Type.String({ minLength: 1 }),
      model_id: Type.String({ minLength: 1 }),
      started_at: Type.String({
        pattern: '^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$',
      }),
      finished_at: Type.String({
        pattern: '^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$',
      }),
      input_hash: Type.String({ pattern: '^sha256:[a-f0-9]{64}$' }),
      output_hash: Type.String({ pattern: '^sha256:[a-f0-9]{64}$' }),
      usage: Type.Object(
        {
          input_tokens: Type.Integer({ minimum: 0 }),
          output_tokens: Type.Integer({ minimum: 0 }),
        },
        { additionalProperties: false },
      ),
    },
    { additionalProperties: false },
  ),
)

export type AgentAudit = Static<typeof AgentAuditSchema>
