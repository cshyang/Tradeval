import { resolve } from 'node:path'

export interface AgentTraderConfig {
  readonly modelProvider: string
  readonly modelId: string
  readonly retailTraderExecutable: string
  readonly retailTraderRoot: string
  readonly workspaceRoot: string
  readonly apiPort: number
  readonly jobTimeoutMs: number
}

function requiredValue(name: string): string {
  const value = process.env[name]?.trim()
  if (!value) {
    throw new Error(`${name} is required`)
  }
  return value
}

function positiveInteger(name: string, defaultValue: number, maximum?: number): number {
  const raw = process.env[name]?.trim()
  if (!raw) {
    return defaultValue
  }
  const value = Number(raw)
  if (!Number.isSafeInteger(value) || value <= 0 || (maximum !== undefined && value > maximum)) {
    throw new Error(`${name} must be a positive integer${maximum ? ` at most ${maximum}` : ''}`)
  }
  return value
}

function absolutePath(value: string | undefined, fallback: string, baseDirectory: string): string {
  return resolve(baseDirectory, value?.trim() || fallback)
}

export function loadConfig(baseDirectory = process.cwd()): AgentTraderConfig {
  return Object.freeze({
    modelProvider: process.env.AGENTTRADER_MODEL_PROVIDER?.trim() || 'anthropic',
    modelId: requiredValue('AGENTTRADER_MODEL_ID'),
    retailTraderExecutable: process.env.RETAILTRADER_EXECUTABLE?.trim() || 'uv',
    retailTraderRoot: absolutePath(
      process.env.RETAILTRADER_ROOT,
      '../RetailTrader',
      baseDirectory,
    ),
    workspaceRoot: absolutePath(
      process.env.AGENTTRADER_WORKSPACE_ROOT,
      'workspaces',
      baseDirectory,
    ),
    apiPort: positiveInteger('AGENTTRADER_API_PORT', 4317, 65_535),
    jobTimeoutMs: positiveInteger('AGENTTRADER_JOB_TIMEOUT_MS', 300_000),
  })
}

export function serializeConfig(config: AgentTraderConfig): string {
  return JSON.stringify(config)
}
