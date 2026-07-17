import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, test } from 'vitest'

import { canonicalHash } from '../src/contracts/hash.js'
import { parseDecisionProposal } from '../src/contracts/proposals.js'

const fixture = JSON.parse(
  readFileSync(resolve('tests/fixtures/decision-proposal-v1.json'), 'utf8'),
) as Record<string, unknown>

describe('DecisionProposal v1', () => {
  test('accepts and freezes the shared wire fixture', () => {
    const proposal = parseDecisionProposal(fixture)

    expect(proposal.experiment_id).toBe('exp-buffett-001')
    expect(Object.isFrozen(proposal)).toBe(true)
    expect(Object.isFrozen(proposal.decisions)).toBe(true)
    expect(Object.isFrozen(proposal.decisions[0])).toBe(true)
  })

  test('rejects unknown fields at every object boundary', () => {
    expect(() => parseDecisionProposal({ ...fixture, unexpected: true })).toThrow()
    const decisions = fixture.decisions as Array<Record<string, unknown>>
    expect(() =>
      parseDecisionProposal({
        ...fixture,
        decisions: [{ ...decisions[0], unexpected: true }],
      }),
    ).toThrow()
  })

  test('rejects duplicate symbols', () => {
    const decisions = fixture.decisions as Array<Record<string, unknown>>

    expect(() =>
      parseDecisionProposal({ ...fixture, decisions: [decisions[0], decisions[0]] }),
    ).toThrow('duplicate symbol: AAPL')
  })

  test.each([
    ['confidence', -0.01],
    ['confidence', 1.01],
    ['desired_weight', -0.01],
    ['desired_weight', 1.01],
  ])('rejects %s outside [0, 1]', (field, value) => {
    const decisions = fixture.decisions as Array<Record<string, unknown>>

    expect(() =>
      parseDecisionProposal({
        ...fixture,
        decisions: [{ ...decisions[0], [field]: value }],
      }),
    ).toThrow()
  })

  test('computes the frozen cross-language canonical hash', () => {
    const proposal = parseDecisionProposal(fixture)

    expect(canonicalHash(proposal)).toBe(
      'sha256:e2ea7033cd2f4e073df346239c713baa3db662a5dc3ed61e549d7305c829b3df',
    )
  })
})
