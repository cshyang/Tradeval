import { type Static, Type } from '@sinclair/typebox'

export const AgentProtocolSchema = Object.freeze(
  Type.Object(
    {
      schema_version: Type.Literal(1),
      provider: Type.String({ minLength: 1 }),
      model_id: Type.String({ minLength: 1 }),
      system_prompt_hash: Type.String({ pattern: '^sha256:[a-f0-9]{64}$' }),
      recipe: Type.String({ minLength: 1 }),
      tools: Type.Array(Type.String({ minLength: 1 })),
      sampling: Type.Object(
        {
          temperature: Type.Number({ minimum: 0 }),
          max_tokens: Type.Integer({ minimum: 1 }),
        },
        { additionalProperties: false },
      ),
      timeout_ms: Type.Integer({ minimum: 1 }),
      retry_count: Type.Integer({ minimum: 0 }),
    },
    { additionalProperties: false },
  ),
)

export type AgentProtocol = Static<typeof AgentProtocolSchema>
