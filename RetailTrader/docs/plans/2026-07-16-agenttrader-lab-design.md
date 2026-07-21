# AgentTrader Lab — MECE Architecture Design

**Status:** Approved

## Purpose

AgentTrader Lab evaluates the question:

> What would happen if a specific AI agent helped manage a fixed amount of fake money, at a chosen decision frequency and trade budget, in a defined market?

The product is a configurable, reproducible, cutoff-aware AI investment experiment lab. It is not a brokerage product and cannot place real orders.

RetailTrader remains useful as deterministic simulation and comparison infrastructure. The differentiated product is the experiment protocol around an agent: mandate, model identity, permitted information, immutable proposals, simulated execution, controls, and auditability.

## Goals

- Configure fake capital, market, universe, cadence, horizon, objectives, and risk limits.
- Freeze an agent recipe, model configuration, tools, and declared knowledge cutoff.
- Support honest forward paper experiments, cutoff-safe historical replay, and clearly labeled hindsight scenarios.
- Preserve point-in-time data rules and next-session execution timing.
- Compare agent behavior against deterministic recipes, passive benchmarks, and cash over the same window.
- Record enough provenance to inspect and reproduce each experiment as far as the model provider permits.

## Approved Product Experience

AgentTrader Lab and RetailTrader appear as one product backed by separate
services. A first-time user should be able to describe an investing philosophy,
configure fake capital and a market, run an immediate experiment, inspect the
agent's decisions, and start a forward-paper portfolio.

The canonical user journey is:

```text
Describe -> Configure -> Preview -> Run -> Watch -> Explain -> Compare -> Fork
```

### Describe

The user starts with natural language such as:

```text
I want a Buffett-inspired long-term quality-value agent.
```

The philosophy copilot uses model knowledge to draft plain-language principles,
screening rules, cadence, and risk defaults. Persona and institution references
are always labeled `AI-INTERPRETED`; they are not presented as authentic,
endorsed, or proprietary strategies. Ambiguous institutions such as JPMorgan
require the user to choose a broad style before generation.

### Configure

The experiment builder owns starting fake capital, market, screener rules,
pinned and excluded symbols, historical window, cadence, cash buffer, position
limit, turnover limit, and temporal mode. The first product supports a
deterministic screener plus manual symbol overrides. "All available stocks"
means all supported symbols may enter screening, not that every symbol is sent
to the model.

### Preview And Run

Before execution, the UI previews candidate count, data coverage, exclusions,
estimated model calls, and expected cost. Runtime progress names each stage:

```text
load data -> screen market -> evaluate candidates -> validate proposals
-> apply mandate -> simulate next-open fills -> evaluate
```

### Watch, Explain, Compare, Fork

Equity Replay is a result inspector, not the primary product. It sits beside
agent proposals, evidence, holdings, cash, interventions, model usage, passive
benchmarks, and deterministic controls. Forking creates a new version; active
experiments are immutable.

## Non-goals

- Real-money trading.
- Broker integration.
- Claims that hindsight scenarios are historical track records.
- Fabricated prices, documents, fills, or computed performance.
- Network-dependent default tests.

## MECE Domain Model

Every field has one canonical owner.

### 1. Mandate — what is being managed

Owns initial fake capital, market, universe, base currency, decision frequency, horizon, objective, maximum trade size, position limits, turnover limits, and drawdown constraints. It owns no model configuration or performance results.

### 2. Agent Protocol — how decisions are formed

Owns model provider, model identifier and version, declared knowledge cutoff, system prompt, selected recipe, research tools, sampling configuration, token budget, timeout, and retry policy. It owns no portfolio state.

### 3. Information Environment — what the agent may know

Owns point-in-time prices, fundamentals, filings, news, and research documents. Every observation records `observed_at`, `available_at`, source identity, and content hash. It owns no agent conclusions.

### 4. Decision Event — what the agent proposed

Owns the immutable output of one decision step: symbol, buy/hold/sell stance,
confidence, desired weight, thesis, evidence references, risks, invalidating
conditions, intended holding period, and abstention reason. It owns no simulated
fills or later performance.

### 5. Simulation and Ledger — what happened

Owns deterministic mandate enforcement, bounded target exposure, simulated orders, next-session-open fills, slippage, cash, positions, and close marks. It performs no research or narration.

### 6. Evaluation — how the experiment performed

Owns outcome, risk, behavioral, decision-quality, and operational-cost metrics. It never changes decisions or portfolio state.

### 7. Audit Envelope — why results are inspectable

Owns experiment identity, code revision, configuration hashes, data hashes, timestamps, random seeds where supported, raw model responses, and validity classification. It records provenance but owns no trading logic.

The canonical chain is:

```text
Mandate + Agent Protocol + Information Environment
    -> Decision Event
    -> Simulation and Ledger
    -> Evaluation
```

The Audit Envelope covers the complete run.

## Orthogonal Experiment Dimensions

Temporal validity and decision policy are independent dimensions.

### Temporal mode

Every run has exactly one temporal mode:

1. **Forward paper:** decisions occur after experiment creation using information available at the real decision time. This is the strongest evidence but requires waiting for results.
2. **Cutoff-safe replay:** the period is historical, the declared model cutoff predates the period, and all retrieved information satisfies `available_at <= decision_at`.
3. **Hindsight scenario:** the model cutoff or research access extends beyond at least one simulated decision timestamp. The run is useful as a counterfactual, but must never be presented as a track record.

### Decision policy

Every run has exactly one policy type:

- **AI agent:** researches and produces immutable proposals.
- **Deterministic recipe:** executes an existing versioned factor strategy.
- **Passive control:** follows a buy-and-hold, equal-weight, or cash policy.

The cross-product supports comparisons over identical market windows and execution assumptions without conflating policy type with temporal validity.

## Repository and Governance Boundary

The existing RetailTrader repository explicitly prohibits LLM-generated order decisions. A worktree shares repository governance and cannot override that rule.

Therefore:

- **RetailTrader** remains the deterministic simulator, ledger, evaluation engine, and source of deterministic control policies.
- **AgentTrader Lab** is a companion project that owns model calls, research orchestration, proposal artifacts, and agent-specific evaluation.
- The projects integrate through versioned, immutable artifacts and an explicit simulation API. They do not share mutable state.
- Neither project contains a broker or real-order path.

A RetailTrader feature worktree may add the point-in-time live-price boundary, cache, provider contract tests, and a stable simulation interface. Agent decision code belongs only in the separately governed companion project.

## Components

### Experiment Registry

Validates and freezes the mandate, temporal mode, agent protocol, evaluation window, and comparison policies. Once an experiment starts, changes create a new version instead of mutating the run.

### Point-in-Time Gateway

Retrieves adjusted daily market bars and timestamped research sources. A time gate rejects every observation with `available_at > decision_at`. OpenBB with an explicit Yahoo Finance provider supplies adjusted daily prices in the first version. News, filings, and fundamentals are later, separate adapters.

### Agent Runner

Builds only the permitted context, invokes the frozen model configuration, and stores the complete raw response. It validates the structured proposal but does not calculate fills or mutate a portfolio.

The agent controls stance and desired weight. It does not create executable
share orders. One batched call evaluates the screened candidate set at each
decision date.

### Mandate Adapter and Simulator

A deterministic adapter validates symbols, applies trade-size, concentration, cash, and turnover constraints, and produces bounded exposure. The simulator calculates orders, fills, cash, positions, and marks through one calculation path.

Every proposal receives one deterministic disposition:

```text
accepted   requested weight is valid
capped     reduced by position, turnover, or cash constraints
rejected   unsupported symbol or mandate violation
deferred   next execution bar is unavailable
```

The original proposal is never rewritten. Adjudication, bounded target, orders,
fills, and portfolio state are separate hash-linked artifacts.

### Evaluator and Audit Store

Calculates metrics against identical-window controls and writes append-only artifacts with configuration, data, response, and code hashes.

## Daily Data Flow and Timing

```text
Completed market close at T
-> admit only information available by T
-> invoke the agent and validate its proposal
-> freeze the proposal and evidence references
-> apply the deterministic mandate and risk gate
-> simulate execution at the next session T+1 open
-> mark the portfolio at T+1 close
-> append ledger, metrics, and audit artifacts
```

Historical replay and forward paper mode use this same sequence. Only the clock source changes. The execution bar is never visible to the decision made at the prior close.

## Evaluation Model

Metrics are MECE across five groups:

1. **Outcome:** total return, benchmark-relative return, and ending equity.
2. **Risk:** volatility, maximum drawdown, downside capture, concentration, and cash exposure.
3. **Trading behavior:** turnover, trade count, holding period, abstention rate, and mandate interventions.
4. **Decision quality:** confidence calibration, thesis consistency, evidence coverage, and subsequent return by confidence bucket.
5. **Operational cost:** model calls, tokens, research requests, latency, and simulated transaction costs.

Every agent run uses the same market window and execution assumptions as passive, deterministic-recipe, and cash controls. Because model calls can be stochastic, repeated trials report distributions and decision consistency rather than highlighting only the best run.

## Failure Handling

The system fails closed:

- Future-dated or stale information is rejected.
- Invalid model output becomes a rejected proposal; one bounded repair attempt may be configured.
- A model timeout receives at most one retry, then records an explicit abstention.
- Unsupported symbols are rejected.
- Risk breaches are rejected or deterministically capped and recorded as interventions.
- A missing next-session bar defers execution; no price is invented.
- Interrupted work resumes idempotently from the last completed event.
- Research documents are treated as untrusted data, never as instructions, and retain source hashes for prompt-injection review.
- Partial writes cannot advance the ledger.

## Verification Strategy

Default tests remain offline and deterministic:

- Unit tests for temporal admission, proposal validation, mandate constraints, and next-open timing.
- Fixture-based provider normalization and cache contract tests.
- Replay-versus-forward parity tests.
- Deliberate future-document leakage tests.
- Ledger reconstruction and idempotent-resume tests.
- Prompt-injection boundary tests for untrusted research content.
- Repeated-trial aggregation tests.
- An opt-in live OpenBB smoke test guarded by an explicit environment variable.

## First Vertical Slice

The first end-to-end path is:

> Create a Buffett-inspired agent, give it $100,000, test it on US large
> caps, then start paper trading from today.

### Frozen Defaults

```text
Philosophy          AI-interpreted long-term quality value
Capital             USD 100,000
Market              US equities
Universe            30 liquid large caps in v0
Candidate target    approximately 12 after screening
Cadence             monthly
Maximum position    12%
Maximum turnover    20%
Modes               hindsight sandbox + forward paper
```

The user may edit capital, limits, cadence, screener rules, and symbol overrides.
The narrow initial universe proves the workflow before introducing a broad US
symbol master.

### AI Decision Loop

At each decision date:

```text
deterministic screener
-> one batched candidate-evaluation call
-> buy/hold/sell + confidence + desired weights + thesis + evidence
-> deterministic mandate adjudication
-> next-open fake execution
-> close mark and evaluation
```

The hindsight sandbox is immediate and visibly classified as
`HINDSIGHT SCENARIO`; it is not a track record. The forward-paper experiment
starts from information retrieved today and runs automatically within the
frozen mandate.

### Result Workspace

The first slice displays ending equity, drawdown, holdings, cash, trades,
proposals, original and capped weights, evidence, interventions, model calls,
tokens, latency, estimated cost, and comparisons against cash, equal weight,
and a deterministic quality-value control.

### Delivery Order

1. **Completed in RetailTrader:** integrate the point-in-time adjusted-price
   gateway, immutable cache, provenance, and explicit decision/execution
   `SimulationFrame` contract without changing the frozen domain models.
2. Define `MandateSpec`, `AgentProtocol`, `CandidateSet`, `DecisionProposal`,
   and `AuditEnvelope` contracts for AgentTrader.
3. Implement one model-backed philosophy and proposal worker using fixtures for
   offline tests.
4. Add experiment jobs and server-sent progress events.
5. Build Describe -> Configure -> Preview -> Run in the unified UI.
6. Reuse Equity Replay as the Watch/Explain result view.
7. Add automatic forward scheduling after the hindsight workflow passes end to
   end.

Broad US symbol catalogs, multiple model providers, global assets, news, and
fully point-in-time fundamental coverage remain later slices.

## RetailTrader Phase Implementation Notes

The RetailTrader-only phase uses completed adjusted daily bars from an explicit
OpenBB/Yahoo route. Each normalized observation carries modeled 09:30 open and
16:00 close availability in `America/New_York` before it crosses into the
frozen domain contract. Daylight saving time is handled; exchange early-close
timestamps are not. Treating 16:00 as availability on an early-close day is
conservative rather than look-ahead.

Signals are calculated from a completed decision session. Orders and fills are
simulated at the next actual SPY-calendar session open and positions are marked
at that session's close. Replay and restarted single-step execution use the
same `simulation.runner.step` path. The append-only event stream is checked by
ledger reconstruction, and event/materialized-artifact mismatches fail before
any resume write.

Yahoo bars use `splits_and_dividends` adjustment for both the strategy and its
references. Dividends are not added separately. Adjusted OHLC fills are
normalized research approximations rather than executable quote claims. Actual
SPY and equal-weight outputs are no-cost, fractional fixed-basket references
funded at the first execution open. The offline demo keeps its distinct
**Synthetic mega-cap proxy** reference and never labels it SPY.

Because the universe is the present-day fixed large-cap list, the real-price
workflow is classified as `hindsight_current_universe`, even though bar access
is time-gated. It must not be described as a cutoff-safe universe replay or a
forward track record.

This phase does not implement AgentTrader Lab model calls, agent proposals,
research retrieval, or forward paper operation. Those remain companion-project
work behind the repository boundary described above. RetailTrader continues to
make every score, weight, simulated order, fill, cash, and position calculation.
