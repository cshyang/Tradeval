export function ExperimentProgress({ events, status, classification = "HINDSIGHT SCENARIO" }: { events: string[]; status: string; classification?: string }) {
  return <section aria-live="polite" className="builder-progress"><div className="builder-kicker">{classification} · {status.toUpperCase()}</div>
    <ol>{events.length ? events.map((event, index) => <li key={`${index}-${event}`}>{event.replaceAll("_", " ")}</li>) : <li>Waiting for the first committed event</li>}</ol>
  </section>;
}
