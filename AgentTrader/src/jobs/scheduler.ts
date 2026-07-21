import { mkdirSync } from 'node:fs'
import { dirname } from 'node:path'
import { DatabaseSync } from 'node:sqlite'

export interface ScheduledExperiment {
  readonly experimentId: string
  readonly dueAt: string
  readonly payloadPath: string
  readonly attempt: number
}
export interface JobScheduler {
  schedule(experimentId: string, dueAt: Date, payloadPath: string): void
  cancel(experimentId: string): void
  tick(signal?: AbortSignal): Promise<number>
  close(): void
}

export function nextMonthlyClose(after: Date): Date {
  const year = after.getUTCFullYear()
  const month = after.getUTCMonth() + 1
  let day = new Date(Date.UTC(year, month + 1, 0, 21))
  while (day.getUTCDay() === 0 || day.getUTCDay() === 6) day = new Date(day.getTime() - 86_400_000)
  return day
}

export class LocalJobScheduler implements JobScheduler {
  private readonly database: DatabaseSync
  constructor(
    path: string,
    private readonly run: (job: ScheduledExperiment, signal?: AbortSignal) => Promise<void>,
    private readonly now: () => Date = () => new Date(),
  ) {
    mkdirSync(dirname(path), { recursive: true })
    this.database = new DatabaseSync(path, { timeout: 5_000 })
    this.database.exec(`CREATE TABLE IF NOT EXISTS schedules (
      experiment_id TEXT PRIMARY KEY, due_at TEXT NOT NULL, payload_path TEXT NOT NULL,
      status TEXT NOT NULL, attempt INTEGER NOT NULL, last_completed_due TEXT
    ) STRICT`)
  }
  schedule(experimentId: string, dueAt: Date, payloadPath: string): void {
    this.database.prepare(`INSERT INTO schedules VALUES (?, ?, ?, 'scheduled', 0, NULL)
      ON CONFLICT(experiment_id) DO UPDATE SET payload_path = excluded.payload_path`).run(
      experimentId, dueAt.toISOString(), payloadPath,
    )
  }
  cancel(experimentId: string): void {
    this.database.prepare(`UPDATE schedules SET status = 'cancelled' WHERE experiment_id = ?`).run(experimentId)
  }
  async tick(signal?: AbortSignal): Promise<number> {
    const rows = this.database.prepare(
      `SELECT experiment_id, due_at, payload_path, attempt FROM schedules
       WHERE status = 'scheduled' AND due_at <= ? ORDER BY due_at, experiment_id`,
    ).all(this.now().toISOString()) as Array<{ experiment_id: string; due_at: string; payload_path: string; attempt: number }>
    let completed = 0
    for (const row of rows) {
      if (signal?.aborted) break
      const claimed = this.database.prepare(
        `UPDATE schedules SET status = 'running', attempt = attempt + 1
         WHERE experiment_id = ? AND status = 'scheduled' AND due_at = ?`,
      ).run(row.experiment_id, row.due_at)
      if (claimed.changes !== 1) continue
      const job = Object.freeze({ experimentId: row.experiment_id, dueAt: row.due_at, payloadPath: row.payload_path, attempt: row.attempt + 1 })
      try {
        await this.run(job, signal)
        this.database.prepare(
          `UPDATE schedules SET status = 'scheduled', due_at = ?, last_completed_due = ? WHERE experiment_id = ?`,
        ).run(nextMonthlyClose(new Date(row.due_at)).toISOString(), row.due_at, row.experiment_id)
        completed += 1
      } catch (error) {
        this.database.prepare(`UPDATE schedules SET status = 'scheduled' WHERE experiment_id = ?`).run(row.experiment_id)
        throw error
      }
    }
    return completed
  }
  close(): void { this.database.close() }
}
