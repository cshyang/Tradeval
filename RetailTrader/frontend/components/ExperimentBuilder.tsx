"use client";
import { startTransition, useEffect, useState } from "react";
import { eventUrl, getExperiment, interpretPhilosophy, startExperiment } from "../lib/agenttrader";
import { AgentDecisionPanel } from "./AgentDecisionPanel";
import { ExperimentProgress } from "./ExperimentProgress";
import { InterventionPanel } from "./InterventionPanel";
import { PhilosophyDraft } from "./PhilosophyDraft";

const symbols = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "BRK.B", "JPM", "V", "MA", "COST", "HD"];

export function ExperimentBuilder({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState<"describe"|"review"|"configure"|"run">("describe");
  const [description, setDescription] = useState("Buffett-inspired long-term quality value");
  const [job, setJob] = useState<string>(); const [events, setEvents] = useState<string[]>([]);
  const [status, setStatus] = useState("idle"); const [error, setError] = useState<string>();
  const [position, setPosition] = useState(12); const [turnover, setTurnover] = useState(20);

  useEffect(() => {
    if (!job) return;
    const source = new EventSource(eventUrl(job));
    source.onmessage = (event) => setEvents((current) => [...current, JSON.parse(event.data).stage]);
    source.addEventListener("stage", (event) => setEvents((current) => [...current, JSON.parse((event as MessageEvent).data).stage]));
    source.onerror = () => source.close();
    const timer = window.setInterval(() => void getExperiment(job).then((value) => setStatus(value.status)), 800);
    return () => { source.close(); window.clearInterval(timer); };
  }, [job]);

  const interpret = async () => { try { setError(undefined); await interpretPhilosophy(description, crypto.randomUUID()); startTransition(() => setStep("review")); } catch (e) { setError(e instanceof Error ? e.message : "Interpretation failed"); } };
  const run = async () => { try {
    const id = `exp-${Date.now()}`; const protocolHash = `sha256:${"a".repeat(64)}`;
    const result = await startExperiment({ experiment_id: id, mandate: { schema_version: 1, experiment_id: id, capital: { currency: "USD", initial_cash: "100000.00" }, market: "US", universe: { symbols, screener: "price_quality_v1", max_candidates: 12, minimum_history_sessions: 250, minimum_average_dollar_volume: "10000000", minimum_evidence_coverage: .7, pinned_symbols: [], excluded_symbols: [] }, cadence: "monthly", horizon: { kind: "hindsight", start: "2025-01-01", end: "2025-12-31" }, limits: { minimum_cash_weight: .1, maximum_position_weight: position / 100, maximum_turnover: turnover / 100, maximum_drawdown: .25 } }, protocol: { schema_version: 1, provider: "anthropic", model_id: "configured-server-model", system_prompt_hash: protocolHash, recipe: "proposal-v1", tools: ["get_candidate_data", "submit_proposals"], sampling: { temperature: 0, max_tokens: 4000 }, timeout_ms: 300000, retry_count: 1 } }, crypto.randomUUID());
    setJob(result.job_id); setStatus("queued"); setStep("run");
  } catch (e) { setError(e instanceof Error ? e.message : "Experiment failed"); } };

  return <div className="builder-backdrop" onMouseDown={onClose}><div className="builder" role="dialog" aria-modal="true" aria-label="Build agent experiment" onMouseDown={(e) => e.stopPropagation()}>
    <header><div><div className="builder-kicker">AGENTTRADER LAB · NEW EXPERIMENT</div><h2>{step === "describe" ? "Describe the discipline" : step === "run" ? "Experiment in motion" : "Freeze the mandate"}</h2></div><button onClick={onClose} aria-label="Close">×</button></header>
    {step === "describe" ? <><label htmlFor="philosophy">Investment philosophy</label><textarea id="philosophy" value={description} onChange={(e) => setDescription(e.target.value)} /><p className="builder-help">Name a style, not just an institution. Interpretation is labeled and reviewable.</p></> : null}
    {step === "review" ? <PhilosophyDraft description={description} /> : null}
    {step === "configure" ? <><div className="builder-fields"><label>Capital<input value="$100,000" readOnly /></label><label>Cadence<input value="Monthly" readOnly /></label><label>Max position<input type="number" value={position} onChange={(e) => setPosition(Number(e.target.value))} /></label><label>Max turnover<input type="number" value={turnover} onChange={(e) => setTurnover(Number(e.target.value))} /></label></div><div className="builder-candidates"><div className="builder-kicker">CANDIDATE PREVIEW · APPROXIMATELY 12</div>{symbols.map((symbol) => <span key={symbol}>{symbol}</span>)}</div></> : null}
    {step === "run" ? <><ExperimentProgress events={events} status={status} /><div className="builder-notes"><AgentDecisionPanel /><InterventionPanel /></div></> : null}
    {error ? <p role="alert" className="builder-error">{error}</p> : null}
    <footer>{step !== "describe" && step !== "run" ? <button className="secondary" onClick={() => setStep(step === "review" ? "describe" : "review")}>Back</button> : <span />}
      {step === "describe" ? <button onClick={() => void interpret()}>Interpret philosophy</button> : step === "review" ? <button onClick={() => setStep("configure")}>Configure experiment</button> : step === "configure" ? <button onClick={() => void run()}>Start hindsight scenario</button> : <button onClick={onClose}>View equity replay</button>}</footer>
  </div></div>;
}
