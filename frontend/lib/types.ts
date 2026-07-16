/** Shapes of runs/data.json, the engine-emitted view model. */

export type Factor = { name: string; value: number | null; contribution: number };
export type Selected = {
  symbol: string;
  weight: number;
  score: number;
  factors: Factor[];
};
export type Rejected = { symbol: string; reason: string; score: number | null };
export type Rebalance = {
  week: number;
  as_of: string;
  selected: Selected[];
  rejected: Rejected[];
};
export type Metrics = Record<string, number | null>;
export type Experiment = {
  id: string;
  label: string;
  version: string;
  start: string;
  end: string;
  engine_version: string;
  content_hash: string;
  universe: string;
  equity: string[];
  rebalances: Rebalance[];
  evaluation: { metrics: Metrics; fidelity: Metrics };
};
export type RunData = {
  dates: string[];
  spy: string[];
  equal_weight: string[];
  experiments: Experiment[];
  benchmarks: { spy: Metrics; equal_weight: Metrics };
};
