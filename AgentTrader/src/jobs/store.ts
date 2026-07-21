import { mkdirSync } from 'node:fs'
import { dirname } from 'node:path'
import { DatabaseSync } from 'node:sqlite'

export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface JobRecord {
  readonly jobId: string
  readonly experimentId: string
  readonly status: JobStatus
  readonly stage: string
  readonly workspace: string
  readonly resultPath: string | null
  readonly error: string | null
  readonly createdAt: string
  readonly updatedAt: string
}

interface JobRow {
  job_id: string
  experiment_id: string
  status: JobStatus
  stage: string
  workspace: string
  result_path: string | null
  error: string | null
  created_at: string
  updated_at: string
}

function record(row: JobRow): JobRecord {
  return Object.freeze({
    jobId: row.job_id,
    experimentId: row.experiment_id,
    status: row.status,
    stage: row.stage,
    workspace: row.workspace,
    resultPath: row.result_path,
    error: row.error,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  })
}

export class JobStore {
  private readonly database: DatabaseSync

  constructor(
    path: string,
    private readonly now: () => Date = () => new Date(),
  ) {
    mkdirSync(dirname(path), { recursive: true })
    this.database = new DatabaseSync(path, { timeout: 5_000 })
    this.database.exec(`
      PRAGMA journal_mode = WAL;
      CREATE TABLE IF NOT EXISTS jobs (
        job_id TEXT PRIMARY KEY,
        experiment_id TEXT NOT NULL,
        status TEXT NOT NULL,
        stage TEXT NOT NULL,
        workspace TEXT NOT NULL,
        result_path TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      ) STRICT;
    `)
  }

  create(jobId: string, experimentId: string, workspace: string): JobRecord {
    const existing = this.get(jobId)
    if (existing) {
      if (existing.experimentId !== experimentId || existing.workspace !== workspace) {
        throw new Error(`job identity conflict: ${jobId}`)
      }
      return existing
    }
    const timestamp = this.now().toISOString()
    this.database
      .prepare(`INSERT INTO jobs VALUES (?, ?, 'queued', 'queued', ?, NULL, NULL, ?, ?)`)
      .run(jobId, experimentId, workspace, timestamp, timestamp)
    return this.get(jobId)!
  }

  update(
    jobId: string,
    values: { status: JobStatus; stage: string; resultPath?: string | null; error?: string | null },
  ): JobRecord {
    const current = this.get(jobId)
    if (!current) throw new Error(`unknown job: ${jobId}`)
    this.database
      .prepare(
        `UPDATE jobs SET status = ?, stage = ?, result_path = ?, error = ?, updated_at = ? WHERE job_id = ?`,
      )
      .run(
        values.status,
        values.stage,
        values.resultPath === undefined ? current.resultPath : values.resultPath,
        values.error === undefined ? current.error : values.error,
        this.now().toISOString(),
        jobId,
      )
    return this.get(jobId)!
  }

  get(jobId: string): JobRecord | undefined {
    const row = this.database.prepare('SELECT * FROM jobs WHERE job_id = ?').get(jobId) as
      | JobRow
      | undefined
    return row ? record(row) : undefined
  }

  close(): void {
    this.database.close()
  }
}
