import { afterEach, describe, expect, test } from 'vitest'

import { loadConfig, serializeConfig } from '../src/config.js'

const ORIGINAL_ENV = { ...process.env }

afterEach(() => {
  process.env = { ...ORIGINAL_ENV }
})

describe('loadConfig', () => {
  test('requires an explicit model ID', () => {
    delete process.env.AGENTTRADER_MODEL_ID

    expect(() => loadConfig()).toThrow('AGENTTRADER_MODEL_ID is required')
  })

  test('loads explicit service boundaries and validated runtime limits', () => {
    process.env.AGENTTRADER_MODEL_ID = 'claude-sonnet-4-20250514'
    process.env.AGENTTRADER_MODEL_PROVIDER = 'anthropic'
    process.env.RETAILTRADER_EXECUTABLE = '/opt/bin/uv'
    process.env.RETAILTRADER_ROOT = '/srv/retailtrader'
    process.env.AGENTTRADER_WORKSPACE_ROOT = '/srv/agent-workspaces'
    process.env.AGENTTRADER_API_PORT = '4317'
    process.env.AGENTTRADER_JOB_TIMEOUT_MS = '45000'

    expect(loadConfig()).toEqual({
      modelProvider: 'anthropic',
      modelId: 'claude-sonnet-4-20250514',
      retailTraderExecutable: '/opt/bin/uv',
      retailTraderRoot: '/srv/retailtrader',
      workspaceRoot: '/srv/agent-workspaces',
      apiPort: 4317,
      jobTimeoutMs: 45000,
    })
  })

  test('defaults to Anthropic and local sibling paths', () => {
    process.env.AGENTTRADER_MODEL_ID = 'claude-sonnet-4-20250514'
    delete process.env.AGENTTRADER_MODEL_PROVIDER
    delete process.env.RETAILTRADER_EXECUTABLE
    delete process.env.RETAILTRADER_ROOT
    delete process.env.AGENTTRADER_WORKSPACE_ROOT
    delete process.env.AGENTTRADER_API_PORT
    delete process.env.AGENTTRADER_JOB_TIMEOUT_MS

    const config = loadConfig('/srv/agenttrader')

    expect(config.modelProvider).toBe('anthropic')
    expect(config.retailTraderExecutable).toBe('uv')
    expect(config.retailTraderRoot).toBe('/srv/RetailTrader')
    expect(config.workspaceRoot).toBe('/srv/agenttrader/workspaces')
    expect(config.apiPort).toBe(4317)
    expect(config.jobTimeoutMs).toBe(300_000)
  })

  test.each([
    ['AGENTTRADER_API_PORT', '0'],
    ['AGENTTRADER_API_PORT', '65536'],
    ['AGENTTRADER_API_PORT', 'abc'],
    ['AGENTTRADER_JOB_TIMEOUT_MS', '0'],
    ['AGENTTRADER_JOB_TIMEOUT_MS', '1.5'],
  ])('rejects invalid %s values', (name, value) => {
    process.env.AGENTTRADER_MODEL_ID = 'claude-sonnet-4-20250514'
    process.env[name] = value

    expect(() => loadConfig()).toThrow(name)
  })

  test('never serializes provider credentials', () => {
    process.env.AGENTTRADER_MODEL_ID = 'claude-sonnet-4-20250514'
    process.env.ANTHROPIC_API_KEY = 'super-secret-key'
    process.env.OPENAI_API_KEY = 'another-secret-key'

    const serialized = serializeConfig(loadConfig('/srv/agenttrader'))

    expect(serialized).not.toContain('super-secret-key')
    expect(serialized).not.toContain('another-secret-key')
    expect(serialized).not.toContain('API_KEY')
  })
})
