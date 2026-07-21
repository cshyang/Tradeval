import { type Static, Type } from '@sinclair/typebox'

const strictObject = <T extends Parameters<typeof Type.Object>[0]>(properties: T) =>
  Type.Object(properties, { additionalProperties: false })

export const MandateSpecSchema = Object.freeze(
  strictObject({
    schema_version: Type.Literal(1),
    experiment_id: Type.String({ minLength: 1 }),
    capital: strictObject({
      currency: Type.Literal('USD'),
      initial_cash: Type.String({ pattern: '^[0-9]+(?:\\.[0-9]{1,2})?$' }),
    }),
    market: Type.Literal('US'),
    universe: strictObject({
      symbols: Type.Array(Type.String({ minLength: 1 }), { minItems: 1 }),
      screener: Type.Literal('price_quality_v1'),
      max_candidates: Type.Integer({ minimum: 1 }),
      minimum_history_sessions: Type.Integer({ minimum: 1 }),
      minimum_average_dollar_volume: Type.String({ pattern: '^[0-9]+(?:\\.[0-9]+)?$' }),
      minimum_evidence_coverage: Type.Number({ minimum: 0, maximum: 1 }),
      pinned_symbols: Type.Array(Type.String({ minLength: 1 })),
      excluded_symbols: Type.Array(Type.String({ minLength: 1 })),
    }),
    cadence: Type.Union([Type.Literal('weekly'), Type.Literal('monthly')]),
    horizon: strictObject({
      kind: Type.Union([Type.Literal('hindsight'), Type.Literal('forward')]),
      start: Type.String({ minLength: 1 }),
      end: Type.Union([Type.String({ minLength: 1 }), Type.Null()]),
    }),
    limits: strictObject({
      minimum_cash_weight: Type.Number({ minimum: 0, maximum: 1 }),
      maximum_position_weight: Type.Number({ exclusiveMinimum: 0, maximum: 1 }),
      maximum_turnover: Type.Number({ minimum: 0, maximum: 1 }),
      maximum_drawdown: Type.Number({ minimum: 0, maximum: 1 }),
    }),
  }),
)

export type MandateSpec = Static<typeof MandateSpecSchema>
