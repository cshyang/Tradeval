export function PhilosophyDraft({ description }: { description: string }) {
  return <section className="builder-draft">
    <div className="builder-kicker">AI-INTERPRETED · REVIEW BEFORE RUNNING</div>
    <h3>Quality, patience, and an explicit margin of safety</h3>
    <p>{description}</p>
    <dl><div><dt>Principles</dt><dd>Durable returns on capital; balance-sheet resilience; valuation discipline.</dd></div>
      <div><dt>Assumption</dt><dd>Public filings and adjusted prices approximate the requested style.</dd></div>
      <div><dt>Unsupported</dt><dd>Private management access, authentic investor intent, and future knowledge.</dd></div></dl>
  </section>;
}
