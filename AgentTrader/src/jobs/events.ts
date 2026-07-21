import { closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync, writeSync } from 'node:fs'
import { dirname } from 'node:path'

import { canonicalJson } from '../contracts/hash.js'

export interface JobEvent {
  readonly sequence: number
  readonly timestamp: string
  readonly type: 'stage' | 'artifact' | 'command' | 'log' | 'error'
  readonly stage: string
  readonly session: string | null
  readonly artifact: string | null
  readonly detail: string | null
}

export class JobEventLog {
  constructor(
    readonly path: string,
    private readonly now: () => Date = () => new Date(),
  ) {}

  read(): JobEvent[] {
    if (!existsSync(this.path)) return []
    return readFileSync(this.path, 'utf8')
      .split('\n')
      .filter(Boolean)
      .map((line) => JSON.parse(line) as JobEvent)
  }

  append(event: Omit<JobEvent, 'sequence' | 'timestamp'>): JobEvent {
    const previous = this.read()
    const value: JobEvent = Object.freeze({
      sequence: (previous.at(-1)?.sequence ?? 0) + 1,
      timestamp: this.now().toISOString(),
      ...event,
    })
    mkdirSync(dirname(this.path), { recursive: true })
    const descriptor = openSync(this.path, 'a', 0o600)
    try {
      writeSync(descriptor, `${canonicalJson(value)}\n`)
      fsyncSync(descriptor)
    } finally {
      closeSync(descriptor)
    }
    return value
  }
}
