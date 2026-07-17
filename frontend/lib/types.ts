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
  execution_as_of: string;
  selected: Selected[];
  rejected: Rejected[];
};
export type Metrics = Record<string, number | null>;
export type DataProvenance = {
  kind: "synthetic" | "real_market";
  validity: "synthetic_demo" | "hindsight_current_universe";
  label: string;
  transport: string;
  provider: string;
  provider_versions?: [string, string][];
  adjustment: string;
  retrieved_at?: string;
  query_hash?: string;
  normalized_hash?: string;
  benchmark_kind: "no_cost_reference";
  reference_method_version: "execution_open_fixed_basket_v1";
  execution_model_version: "prior_close_next_open_v1";
  warnings?: string[];
};
export type Experiment = {
  id: string;
  label: string;
  philosophy: string;
  version: string;
  start: string;
  end: string;
  cadence: string;
  engine_version: string;
  content_hash: string;
  universe: string;
  /** The run's real philosophy.yaml, rendered verbatim in the spec modal. */
  spec_yaml: string;
  tagline: string;
  data_provenance: DataProvenance;
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
