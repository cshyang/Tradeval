export function ExperimentProgress({ events, status }: { events: string[]; status: string }) {
  return <section aria-live="polite" className="builder-progress"><div className="builder-kicker">HINDSIGHT SCENARIO · {status.toUpperCase()}</div>
    <ol>{events.length ? events.map((event, index) => <li key={`${index}-${event}`}>{event.replaceAll("_", " ")}</li>) : <li>Waiting for the first committed event</li>}</ol>
  </section>;
}
