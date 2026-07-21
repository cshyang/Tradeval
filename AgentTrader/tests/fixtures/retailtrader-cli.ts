import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

const args = process.argv.slice(2)
const option = (name: string) => args[args.indexOf(name) + 1]
const command = args.slice(0, 2).join('.')
const workspace = option('--workspace')

console.error(`fake RetailTrader: ${command}`)

let result: Record<string, unknown>
if (command === 'agent.prepare-hindsight') {
  const frames = ['2025-02-03', '2025-03-03'].map((session) => {
    const stepDirectory = join(workspace, 'prepared', session)
    mkdirSync(stepDirectory, { recursive: true })
    writeFileSync(join(stepDirectory, 'mandate.json'), readFileSync(option('--experiment')))
    writeFileSync(
      join(stepDirectory, 'prepared-frame.json'),
      JSON.stringify({ session, fixture: true }) + '\n',
    )
    return {
      session,
      decision_at: session === '2025-02-03' ? '2025-01-31T20:00:00Z' : '2025-02-28T20:00:00Z',
      step_directory: stepDirectory,
    }
  })
  result = { frames }
} else if (command === 'agent.candidates') {
  const out = option('--out')
  mkdirSync(dirname(out), { recursive: true })
  const candidate = {
    schema_version: 1,
    experiment_id: 'exp-hindsight',
    screener: 'price_quality_v1',
    decision_at: option('--decision-at'),
    market_data_hash: `sha256:${'c'.repeat(64)}`,
    candidates: [],
    exclusions: [],
    candidate_set_hash: `sha256:${'a'.repeat(64)}`,
  }
  writeFileSync(out, JSON.stringify(candidate) + '\n')
  result = { out, candidate_set_hash: candidate.candidate_set_hash }
} else if (command === 'agent.step') {
  const proposal = JSON.parse(readFileSync(option('--proposal'), 'utf8'))
  const session = proposal.decision_at.startsWith('2025-01') ? '2025-02-03' : '2025-03-03'
  const audit = join(workspace, 'audit', session)
  mkdirSync(audit, { recursive: true })
  const proposalPath = join(audit, 'proposal.json')
  const adjudicationPath = join(audit, 'adjudication.json')
  writeFileSync(proposalPath, JSON.stringify(proposal) + '\n')
  writeFileSync(adjudicationPath, JSON.stringify({ session, status: 'accepted' }) + '\n')
  result = {
    status: 'committed',
    session,
    proposal_path: proposalPath,
    adjudication_path: adjudicationPath,
  }
} else if (command === 'agent.prepare-frame') {
  writeFileSync(option('--out'), JSON.stringify({ fixture: true }) + '\n')
  result = { out: option('--out') }
} else if (command === 'agent.finalize-hindsight') {
  const evaluationPath = join(workspace, 'evaluation.json')
  const comparisonPath = join(workspace, 'comparison.json')
  writeFileSync(evaluationPath, JSON.stringify({ classification: 'HINDSIGHT SCENARIO' }) + '\n')
  writeFileSync(comparisonPath, JSON.stringify({ controls: ['cash', 'equal_weight', 'quality_value'] }) + '\n')
  result = { evaluation_path: evaluationPath, comparison_path: comparisonPath }
} else {
  console.log(JSON.stringify({ schema_version: 1, command, status: 'error', error: { code: 'unknown', message: command } }))
  process.exit(3)
}

console.log(JSON.stringify({ schema_version: 1, command, status: 'ok', result }))
