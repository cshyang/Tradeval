import { createHash } from 'node:crypto'

function canonicalNumber(value: number): string {
  if (!Number.isFinite(value)) {
    throw new TypeError('canonical JSON does not support non-finite numbers')
  }
  return Object.is(value, -0) ? '0' : JSON.stringify(value)
}

export function canonicalJson(value: unknown): string {
  if (value === null) {
    return 'null'
  }
  if (typeof value === 'string' || typeof value === 'boolean') {
    return JSON.stringify(value)
  }
  if (typeof value === 'number') {
    return canonicalNumber(value)
  }
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(',')}]`
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    const entries = Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
    return `{${entries.join(',')}}`
  }
  throw new TypeError(`canonical JSON does not support ${typeof value}`)
}

export function canonicalHash(value: unknown): string {
  return `sha256:${createHash('sha256').update(canonicalJson(value)).digest('hex')}`
}
