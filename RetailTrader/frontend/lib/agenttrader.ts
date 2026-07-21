export const AGENTTRADER_URL = process.env.NEXT_PUBLIC_AGENTTRADER_API_URL ?? "http://localhost:4317";

export interface JobReference { job_id: string; experiment_id: string }
export interface JobStatus { jobId: string; experimentId: string; status: string; stage: string; error: string | null }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${AGENTTRADER_URL}${path}`, init);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error ?? `AgentTrader request failed (${response.status})`);
  return body as T;
}

export function interpretPhilosophy(description: string, key: string) {
  return request<JobReference>("/experiments/philosophy", {
    method: "POST", headers: { "content-type": "application/json", "Idempotency-Key": key },
    body: JSON.stringify({ description }),
  });
}

export function startExperiment(body: Record<string, unknown>, key: string) {
  return request<JobReference>("/experiments", {
    method: "POST", headers: { "content-type": "application/json", "Idempotency-Key": key },
    body: JSON.stringify(body),
  });
}

export function getExperiment(id: string) { return request<JobStatus>(`/experiments/${id}`); }
export function cancelExperiment(id: string) { return request<JobStatus>(`/experiments/${id}/cancel`, { method: "POST" }); }
export function eventUrl(id: string) { return `${AGENTTRADER_URL}/experiments/${id}/events`; }
