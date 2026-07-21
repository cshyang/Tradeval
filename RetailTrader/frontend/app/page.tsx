"use client";

import { CSSProperties, useEffect, useState } from "react";
import type { Experiment, RunData, Selected } from "../lib/types";
import { ExperimentBuilder } from "../components/ExperimentBuilder";

const SERIF = "var(--font-serif), serif";
const SANS = "var(--font-sans), sans-serif";
const MONO = "var(--font-mono), monospace";

// Design props, baked at their defaults (the .dc prop editor has no runtime here).
const PALETTE = ["#bfa27b", "#86ac9f", "#a49bc4"];
const SHOW_BENCHMARKS = true;
const CHART_FILL = true;

const THEMES = {
  light: {
    bg: "#faf9f5", ink: "#26282a", mut: "#8b8d87", fnt: "#eceae3",
    hl: "#e6e4dd", pan: "#f1efe8", gain: "#6f9e7d", loss: "#b98a80", label: "☾ Dark",
  },
  dark: {
    bg: "#17181a", ink: "#e8e6df", mut: "#90928c", fnt: "#26272b",
    hl: "#2c2d31", pan: "#1f2023", gain: "#8fbc9c", loss: "#c99a91", label: "☀ Light",
  },
};
type Theme = typeof THEMES.light;

const W = 1000, H = 256, PAD_T = 10, PAD_B = 8;
// The design had no x-padding; with real data the last rebalance lands exactly on
// x=W, so its marker and cursor line render half-clipped. Inset both ends.
const PAD_X = 8;

const pct = (v: number | null, signed = false) => {
  if (v === null || Number.isNaN(v)) return "—";
  return (signed && v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%";
};
const money = (s: string) =>
  Number(parseFloat(s).toFixed(0)).toLocaleString("en-US").replace(/,/g, " ");

export default function Page() {
  const [data, setData] = useState<RunData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [expIdx, setExpIdx] = useState(0);
  const [view, setView] = useState<"replay" | "compare">("replay");
  const [rebIdx, setRebIdx] = useState(0);
  const [symIdx, setSymIdx] = useState(0);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [specOpen, setSpecOpen] = useState(false);
  const [specIdx, setSpecIdx] = useState(0);
  const [newOpen, setNewOpen] = useState(false);

  useEffect(() => {
    fetch("runs/data.json")
      .then((response) => {
        if (!response.ok) throw new Error(`artifact request failed (${response.status})`);
        return response.json();
      })
      .then((d: RunData) => {
        if (!d.experiments.length || d.experiments.some((experiment) => !experiment.rebalances.length)) {
          throw new Error("artifact export contains no complete experiments");
        }
        setData(d);
        setRebIdx(d.experiments[0].rebalances.length - 1);
      })
      .catch((error: unknown) => {
        const message = error instanceof Error ? error.message : "unknown artifact error";
        setLoadError(message);
      });
  }, []);

  // Esc closes whichever modal is open.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setSpecOpen(false);
      setNewOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const T = THEMES[theme];
  const activeProvenance = data?.experiments[expIdx]?.data_provenance;
  const vars = {
    "--bg": T.bg, "--ink": T.ink, "--mut": T.mut,
    "--fnt": T.fnt, "--hl": T.hl, "--pan": T.pan,
  } as CSSProperties;

  const navStyle = (active: boolean): CSSProperties => ({
    fontFamily: SANS, fontSize: 13.5, fontWeight: 500, textAlign: "left",
    background: active ? T.ink : "transparent",
    color: active ? T.bg : T.mut,
    border: "none", borderRadius: 6, padding: "8px 12px", cursor: "pointer",
  });

  const selectExp = (i: number) => {
    setExpIdx(i);
    if (data) setRebIdx(data.experiments[i].rebalances.length - 1);
    setSymIdx(0);
  };

  return (
    <div className="app-shell" style={{ ...vars, fontFamily: SANS, background: "var(--bg)", color: "var(--ink)", height: "100vh", display: "flex", fontSize: 14, transition: "background 0.25s, color 0.25s", overflow: "hidden" }}>
      <aside className="sidebar" style={{ width: 236, flex: "none", display: "flex", flexDirection: "column", borderRight: "1px solid var(--hl)", padding: "18px 0 14px", minHeight: 0 }}>
        <div style={{ padding: "0 20px 16px", borderBottom: "1px solid var(--hl)" }}>
          <div style={{ fontFamily: SERIF, fontSize: 21, fontWeight: 500, letterSpacing: "-0.01em" }}>Philosophy Lab</div>
          <div
            title={activeProvenance?.warnings?.join("\n")}
            style={{ fontSize: 9.5, letterSpacing: "0.12em", fontWeight: 500, color: "var(--mut)", marginTop: 5 }}
          >
            {activeProvenance?.label ?? "LOADING RUN PROVENANCE"}
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 2, padding: "14px 12px", borderBottom: "1px solid var(--hl)" }}>
          <button onClick={() => setView("replay")} style={navStyle(view === "replay")}>Replay</button>
          <button onClick={() => setView("compare")} style={navStyle(view === "compare")}>Compare</button>
          <div title="Locked in v1" style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13.5, color: "var(--mut)", opacity: 0.6, padding: "8px 12px", cursor: "not-allowed", userSelect: "none" }}>
            Scenarios <span style={{ fontSize: 10, letterSpacing: "0.06em", border: "1px solid var(--hl)", borderRadius: 5, padding: "1px 6px" }}>v1 · locked</span>
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 2, padding: "14px 12px", flex: 1, minHeight: 0, overflow: "auto" }}>
          <div style={{ fontSize: 9.5, letterSpacing: "0.12em", fontWeight: 600, color: "var(--mut)", padding: "0 12px 8px" }}>PHILOSOPHIES</div>
          {data?.experiments.map((e, i) => {
            const sel = i === expIdx;
            const c = PALETTE[i % PALETTE.length];
            const ret = e.evaluation.metrics.total_return ?? 0;
            return (
              <div key={e.id} role="button" tabIndex={0} aria-pressed={sel} onClick={() => selectExp(i)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectExp(i); } }} style={{ fontFamily: SANS, textAlign: "left", background: sel ? T.pan : "transparent", borderLeft: `3px solid ${sel ? c : "transparent"}`, borderRadius: "0 6px 6px 0", padding: "9px 12px 8px", cursor: "pointer", color: "var(--ink)", opacity: sel ? 1 : 0.55, display: "flex", flexDirection: "column", gap: 3, userSelect: "none" }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                  <span style={{ fontFamily: SERIF, fontSize: 16, fontWeight: sel ? 500 : 400 }}>{e.label}</span>
                  <span style={{ fontSize: 10.5, color: "var(--mut)" }}>{e.version}</span>
                  <span style={{ fontFamily: MONO, fontSize: 12, color: ret >= 0 ? T.gain : T.loss, marginLeft: "auto" }}>{pct(ret, true)}</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <div style={{ width: 16, height: 3, background: c, flex: "none" }} />
                  <span style={{ fontSize: 10.5, color: "var(--mut)" }}>{e.start.slice(0, 4)} – {e.end.slice(0, 4)}</span>
                  <button onClick={(ev) => { ev.stopPropagation(); setSpecIdx(i); setSpecOpen(true); }} title="View YAML spec" style={{ fontFamily: MONO, fontSize: 10, color: "var(--mut)", background: "transparent", border: "1px solid var(--hl)", borderRadius: 4, padding: "1px 7px", cursor: "pointer", marginLeft: "auto" }}>spec ›</button>
                </div>
              </div>
            );
          })}
          <button onClick={() => setNewOpen(true)} style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 500, textAlign: "left", background: "transparent", color: "var(--mut)", border: "1px dashed var(--hl)", borderRadius: 6, padding: "8px 12px", cursor: "pointer", marginTop: 8 }}>+ New philosophy</button>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: "14px 20px 0", borderTop: "1px solid var(--hl)" }}>
          <div title="Forward paper mode is not included in this historical replay build" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", cursor: "not-allowed", userSelect: "none", opacity: 0.55 }}>
            <span style={{ fontSize: 12, color: "var(--mut)" }}>Forward paper mode</span>
            <div style={{ width: 32, height: 18, borderRadius: 9, background: "var(--fnt)", border: "1px solid var(--hl)", position: "relative" }}>
              <div style={{ position: "absolute", top: 2, left: 2, width: 12, height: 12, borderRadius: "50%", background: "var(--mut)" }} />
            </div>
          </div>
          <button disabled title="Not available in this build" style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 500, color: "var(--mut)", opacity: 0.6, background: "transparent", border: "1px solid var(--hl)", borderRadius: 6, padding: "7px 12px", cursor: "not-allowed" }}>Connect broker</button>
          <button onClick={() => setTheme(theme === "light" ? "dark" : "light")} style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 500, color: "var(--ink)", background: "transparent", border: "1px solid var(--ink)", borderRadius: 6, padding: "7px 12px", cursor: "pointer" }}>{T.label}</button>
        </div>
      </aside>

      <div className="content-shell" style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", minHeight: 0 }}>
        {loadError ? (
          <div role="alert" style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", padding: 32, fontFamily: SERIF, fontSize: 15, color: "var(--mut)", textAlign: "center" }}>
            Could not load run artifacts: {loadError}. Run the demo export command and reload.
          </div>
        ) : !data ? (
          <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: SERIF, fontStyle: "italic", fontSize: 14, color: "var(--mut)" }}>loading run artifacts…</div>
        ) : (
          <>
            <main style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0, padding: "20px 28px 10px", gap: 16, overflow: "auto" }}>
              {view === "replay" ? (
                <Replay data={data} T={T} exp={data.experiments[expIdx]} color={PALETTE[expIdx % PALETTE.length]}
                  rebIdx={rebIdx} symIdx={symIdx}
                  setReb={(i) => { setRebIdx(i); setSymIdx(0); }} setSym={setSymIdx} />
              ) : (
                <Compare data={data} T={T} />
              )}
            </main>
            <footer className="artifact-footer" style={{ flex: "none", display: "flex", gap: 24, alignItems: "center", padding: "9px 28px", borderTop: "1px solid var(--hl)", fontFamily: MONO, fontSize: 10.5, color: "var(--mut)" }}>
              <span>engine {data.experiments[expIdx].engine_version}</span>
              <span>run {data.experiments[expIdx].content_hash}</span>
              <span>universe {data.experiments[expIdx].universe}</span>
              <span title={data.experiments[expIdx].data_provenance.warnings?.join("\n")}>
                data {data.experiments[expIdx].data_provenance.transport}/{data.experiments[expIdx].data_provenance.provider}
                {" · "}{data.experiments[expIdx].data_provenance.adjustment}
                {" · "}{data.experiments[expIdx].data_provenance.validity}
                {" · "}{data.experiments[expIdx].data_provenance.benchmark_kind}
                {" / "}{data.experiments[expIdx].data_provenance.reference_method_version}
              </span>
              <span style={{ marginLeft: "auto", fontFamily: SERIF, fontStyle: "italic", fontSize: 12 }}>artifacts are the API — no values computed client-side</span>
            </footer>
          </>
        )}
      </div>

      {data && specOpen && (
        <SpecModal exp={data.experiments[specIdx]} T={T} color={PALETTE[specIdx % PALETTE.length]}
          onClose={() => setSpecOpen(false)}
          onFork={() => { setSpecOpen(false); setNewOpen(true); }} />
      )}
      {newOpen && <ExperimentBuilder onClose={() => setNewOpen(false)} />}
    </div>
  );
}

function Modal({ children, width, onClose }: { children: React.ReactNode; width: number; onClose: () => void }) {
  return (
    <div onClick={onClose} role="presentation" style={{ position: "fixed", inset: 0, background: "rgba(20,20,18,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50 }}>
      <div onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true"
        style={{ width, maxWidth: "calc(100vw - 32px)", maxHeight: "86vh", overflow: "auto", background: "var(--bg)", color: "var(--ink)", border: "1px solid var(--ink)", borderRadius: 8, padding: "24px 28px", boxSizing: "border-box", display: "flex", flexDirection: "column", gap: 16, boxShadow: "0 24px 60px rgba(0,0,0,0.35)" }}>
        {children}
      </div>
    </div>
  );
}

function SpecModal({ exp, T, color, onClose, onFork }: {
  exp: Experiment; T: Theme; color: string; onClose: () => void; onFork: () => void;
}) {
  const ret = exp.evaluation.metrics.total_return ?? 0;
  const meta: [string, string][] = [
    ["experiment", exp.id],
    ["period", `${exp.start} → ${exp.end}`],
    ["universe", exp.universe],
    ["cadence", exp.cadence],
    ["engine", exp.engine_version],
    ["content hash", exp.content_hash],
  ];
  return (
    <Modal width={540} onClose={onClose}>
      <div>
        <div style={{ width: 34, height: 4, background: color, marginBottom: 10 }} />
        <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
          <div style={{ fontFamily: SERIF, fontSize: 22, fontWeight: 500 }}>{exp.label}</div>
          <span style={{ fontSize: 11, color: "var(--mut)", border: "1px solid var(--hl)", borderRadius: 5, padding: "1px 7px" }}>{exp.version}</span>
          <span style={{ fontFamily: MONO, fontSize: 13, color: ret >= 0 ? T.gain : T.loss, marginLeft: "auto" }}>{pct(ret, true)}</span>
        </div>
        <div style={{ fontSize: 12.5, color: "var(--mut)", marginTop: 4, fontStyle: "italic", fontFamily: SERIF }}>{exp.tagline}</div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 24px", borderTop: "1px solid var(--hl)", paddingTop: 14 }}>
        {meta.map(([k, v]) => (
          <div key={k} style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
            <span style={{ fontSize: 11, color: "var(--mut)" }}>{k}</span>
            <span style={{ fontFamily: MONO, fontSize: 11.5, textAlign: "right" }}>{v}</span>
          </div>
        ))}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ fontSize: 10, letterSpacing: "0.12em", fontWeight: 600, color: "var(--mut)" }}>SPEC · YAML</div>
        <div style={{ fontFamily: MONO, fontSize: 11.5, lineHeight: 1.65, whiteSpace: "pre", overflow: "auto", background: "var(--pan)", border: "1px solid var(--hl)", borderRadius: 6, padding: "14px 16px" }}>{exp.spec_yaml.trimEnd()}</div>
      </div>
      <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", borderTop: "1px solid var(--hl)", paddingTop: 14 }}>
        <button onClick={onFork} style={{ fontFamily: SANS, fontSize: 13, fontWeight: 500, background: "transparent", color: "var(--ink)", border: "1px solid var(--hl)", borderRadius: 6, padding: "8px 16px", cursor: "pointer" }}>Fork as new philosophy</button>
        <button onClick={onClose} style={{ fontFamily: SANS, fontSize: 13, fontWeight: 500, background: "var(--ink)", color: "var(--bg)", border: "1px solid var(--ink)", borderRadius: 6, padding: "8px 16px", cursor: "pointer" }}>Close</button>
      </div>
    </Modal>
  );
}

function Replay({ data, T, exp, color, rebIdx, symIdx, setReb, setSym }: {
  data: RunData; T: Theme; exp: Experiment; color: string;
  rebIdx: number; symIdx: number; setReb: (i: number) => void; setSym: (i: number) => void;
}) {
  const eq = exp.equity.map(parseFloat);
  const proxy = data.synthetic_mega_cap_proxy.map(parseFloat);
  const ew = data.equal_weight.map(parseFloat);
  const all = SHOW_BENCHMARKS ? eq.concat(proxy, ew) : eq;
  const lo = Math.min(...all) * 0.99, hi = Math.max(...all) * 1.01;
  const N = eq.length;
  const X = (i: number) => PAD_X + (i / (N - 1)) * (W - 2 * PAD_X);
  const Y = (v: number) => PAD_T + (1 - (v - lo) / (hi - lo)) * (H - PAD_T - PAD_B);
  const path = (arr: number[]) =>
    arr.map((v, i) => (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1)).join("");

  const rebs = exp.rebalances;
  const reb = rebs[rebIdx];
  const referenceLabel = exp.data_provenance.kind === "real_market" ? "SPY" : "Synthetic mega-cap proxy";
  const vs = reb.relative_to_synthetic_mega_cap_proxy;
  // ponytail: the weekly demo cadence puts a marker on every point (130), where
  // the design assumed a sparser timeline — shrink the dot so the curve reads.
  const dotR = rebs.length > 40 ? 2.5 : 4;

  const selected = reb.selected;
  const maxW = selected.length ? Math.max(...selected.map((s) => s.weight)) : 1;
  const cur: Selected | undefined = selected[Math.min(symIdx, selected.length - 1)];
  const maxC = cur ? Math.max(...cur.factors.map((f) => Math.abs(f.contribution)), 0.001) : 1;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18, minHeight: 0, flex: 1 }}>
      <section style={{ flex: "none" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 16, marginBottom: 4 }}>
          <div style={{ fontFamily: SERIF, fontSize: 19, fontWeight: 500 }}>Equity replay</div>
          <div style={{ fontFamily: MONO, fontSize: 11, color: "var(--mut)" }}>base 100 000 · weekly closes</div>
          <div style={{ display: "flex", alignItems: "center", gap: 16, marginLeft: "auto", fontSize: 12, color: "var(--mut)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--ink)" }}>
              <div style={{ width: 16, height: 2, background: color }} />{exp.label}
            </div>
            {SHOW_BENCHMARKS && (
              <>
                 <div style={{ display: "flex", alignItems: "center", gap: 6 }}><div style={{ width: 16, height: 0, borderTop: "2px dashed var(--mut)" }} />{referenceLabel}</div>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}><div style={{ width: 16, height: 0, borderTop: "2px dotted var(--mut)" }} />Equal-weight</div>
              </>
            )}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "baseline", gap: 22, marginBottom: 6 }}>
          <div style={{ fontFamily: MONO, fontSize: 22, color: "var(--ink)" }}>{money(exp.equity[reb.week])}</div>
          <div style={{ fontFamily: MONO, fontSize: 12, color: "var(--mut)" }}>as of <span style={{ color: "var(--ink)" }}>{reb.execution_as_of}</span></div>
           <div style={{ fontFamily: MONO, fontSize: 12, color: vs >= 0 ? T.gain : T.loss }}>{pct(vs, true)} vs {referenceLabel}</div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginLeft: "auto" }}>
            <button onClick={() => setReb(Math.max(0, rebIdx - 1))} style={{ fontFamily: SANS, fontSize: 14, background: "transparent", color: "var(--ink)", border: "1px solid var(--hl)", borderRadius: 6, width: 30, height: 26, cursor: "pointer" }}>‹</button>
            <div style={{ fontSize: 12, color: "var(--mut)", whiteSpace: "nowrap" }}>rebalance <span style={{ color: "var(--ink)", fontWeight: 500 }}>{rebIdx + 1}</span> / {rebs.length}</div>
            <button onClick={() => setReb(Math.min(rebs.length - 1, rebIdx + 1))} style={{ fontFamily: SANS, fontSize: 14, background: "transparent", color: "var(--ink)", border: "1px solid var(--hl)", borderRadius: 6, width: 30, height: 26, cursor: "pointer" }}>›</button>
          </div>
        </div>
        <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ display: "block" }}>
          {[0.25, 0.5, 0.75, 1].map((f, i) => {
            const y = (PAD_T + f * (H - PAD_T - PAD_B)).toFixed(1);
            return <line key={i} x1="0" x2={W} y1={y} y2={y} style={{ stroke: "var(--fnt)" }} strokeWidth="1" />;
          })}
          {SHOW_BENCHMARKS && (
            <>
               <path d={path(proxy)} fill="none" style={{ stroke: "var(--mut)" }} strokeWidth="1.2" strokeDasharray="6 5" />
              <path d={path(ew)} fill="none" style={{ stroke: "var(--mut)", opacity: 0.6 }} strokeWidth="1.1" strokeDasharray="2 4" />
            </>
          )}
          {CHART_FILL && <path d={path(eq) + `L${W - PAD_X} ${H}L${PAD_X} ${H}Z`} fill={color} opacity="0.08" stroke="none" />}
          <path d={path(eq)} fill="none" style={{ stroke: "var(--ink)" }} strokeWidth="1.8" />
          <line x1={X(reb.week).toFixed(1)} x2={X(reb.week).toFixed(1)} y1="0" y2={H} stroke={color} strokeWidth="1.4" opacity="0.6" />
          {rebs.map((rb, i) => (
            <circle key={i} role="button" tabIndex={0} aria-label={`Rebalance ${i + 1} executed ${rb.execution_as_of}`} onClick={() => setReb(i)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") setReb(i); }} cx={X(rb.week).toFixed(1)} cy={Y(eq[rb.week]).toFixed(1)}
              r={i === rebIdx ? 6 : dotR} fill={i === rebIdx ? color : T.bg}
              style={{ stroke: "var(--ink)" }} strokeWidth="1.3" cursor="pointer" />
          ))}
        </svg>
        <div style={{ display: "flex", justifyContent: "space-between", fontFamily: MONO, fontSize: 10.5, color: "var(--mut)", paddingTop: 6, borderTop: "1px solid var(--hl)", marginTop: 2 }}>
          <span>{data.dates[0]}</span><span>{data.dates[Math.floor(N / 2)]}</span><span>{data.dates[N - 1]}</span>
        </div>
      </section>

      <section style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", borderTop: "1px solid var(--ink)", paddingTop: 14 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 16, marginBottom: 12, flex: "none" }}>
          <div style={{ fontFamily: SERIF, fontSize: 19, fontWeight: 500 }}>Rebalance decisions</div>
          <div style={{ fontFamily: MONO, fontSize: 12, color: "var(--ink)" }}>{reb.as_of}</div>
          <div style={{ fontSize: 12, color: "var(--mut)" }}>{selected.length} selected · {reb.rejected.length} rejected</div>
        </div>
        <div className="decision-grid" style={{ display: "grid", gridTemplateColumns: "1.1fr 1.1fr 1fr", gap: 32, flex: 1, minHeight: 0, overflow: "auto" }}>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <div style={{ fontSize: 10, letterSpacing: "0.12em", fontWeight: 600, color: "var(--mut)", marginBottom: 8 }}>SELECTED · WEIGHT</div>
            {selected.length === 0 && (
              <div style={{ fontFamily: SERIF, fontStyle: "italic", fontSize: 13, color: "var(--mut)", padding: "10px 0" }}>
                No symbol passed this philosophy&apos;s filters — the engine held cash.
              </div>
            )}
            {selected.map((s, i) => (
              <button key={s.symbol} onClick={() => setSym(i)} style={{ fontFamily: SANS, display: "grid", gridTemplateColumns: "58px 1fr 52px 46px", alignItems: "center", gap: 12, background: i === symIdx ? T.pan : "transparent", border: "none", borderBottom: "1px solid var(--fnt)", borderRadius: 0, padding: "8px 6px", cursor: "pointer", textAlign: "left", color: "var(--ink)" }}>
                <span style={{ fontSize: 13.5, fontWeight: i === symIdx ? 600 : 400 }}>{s.symbol}</span>
                <div style={{ height: 3, background: "var(--fnt)" }}>
                  <div style={{ height: "100%", width: ((s.weight / maxW) * 100).toFixed(0) + "%", background: i === symIdx ? color : T.hl }} />
                </div>
                <span style={{ fontFamily: MONO, fontSize: 12, textAlign: "right" }}>{pct(s.weight)}</span>
                <span style={{ fontFamily: MONO, fontSize: 11, color: "var(--mut)", textAlign: "right" }}>{s.score}</span>
              </button>
            ))}
          </div>

          <div style={{ display: "flex", flexDirection: "column", borderLeft: "1px solid var(--hl)", paddingLeft: 32 }}>
            <div style={{ fontSize: 10, letterSpacing: "0.12em", fontWeight: 600, color: "var(--mut)", marginBottom: 8 }}>
              FACTOR ATTRIBUTION · <span style={{ color: "var(--ink)" }}>{cur?.symbol ?? "—"}</span>
            </div>
            {cur?.factors.map((f) => (
              <div key={f.name} style={{ display: "grid", gridTemplateColumns: "130px 1fr 52px", alignItems: "center", gap: 12, padding: "9px 0", borderBottom: "1px solid var(--fnt)" }}>
                <div>
                  <div style={{ fontSize: 13 }}>{f.name.replace(/_/g, " ")}</div>
                  <div style={{ fontFamily: MONO, fontSize: 10, color: "var(--mut)" }}>value {f.value ?? "—"}</div>
                </div>
                <div style={{ height: 3, background: "var(--fnt)" }}>
                  <div style={{ height: "100%", width: ((Math.abs(f.contribution) / maxC) * 100).toFixed(0) + "%", background: f.contribution >= 0 ? "var(--ink)" : T.loss }} />
                </div>
                <span style={{ fontFamily: MONO, fontSize: 12, textAlign: "right" }}>{(f.contribution >= 0 ? "+" : "") + f.contribution}</span>
              </div>
            ))}
            <div style={{ fontSize: 12, color: "var(--mut)", marginTop: "auto", paddingTop: 10, fontStyle: "italic", fontFamily: SERIF }}>
              Score is the sum of factor contributions — engine-emitted, rendered verbatim.
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", borderLeft: "1px solid var(--hl)", paddingLeft: 32 }}>
            <div style={{ fontSize: 10, letterSpacing: "0.12em", fontWeight: 600, color: "var(--mut)", marginBottom: 8 }}>REJECTED · REASON</div>
            {reb.rejected.map((r) => (
              <div key={r.symbol} style={{ display: "grid", gridTemplateColumns: "58px 1fr 40px", alignItems: "baseline", gap: 12, padding: "8px 0", borderBottom: "1px solid var(--fnt)" }}>
                <span style={{ fontSize: 13.5, fontWeight: 500, color: "var(--mut)" }}>{r.symbol}</span>
                <span style={{ fontSize: 12, color: "var(--mut)" }}>{r.reason}</span>
                <span style={{ fontFamily: MONO, fontSize: 11, color: "var(--mut)", textAlign: "right" }}>{r.score ?? "—"}</span>
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}

type Row =
  | { isSection: true; label: string }
  | { isSection: false; label: string; cells: { text: string; color: string; weight: string }[] };

function Compare({ data, T }: { data: RunData; T: Theme }) {
  const exps = data.experiments;
  const bm = data.benchmarks;
  const referenceLabel = data.data_provenance.kind === "real_market" ? "SPY" : "Synthetic mega-cap proxy";
  const cols = exps
    .map((e, i) => ({ label: e.label, color: PALETTE[i % PALETTE.length], dot: true }))
    .concat([
      { label: referenceLabel, color: T.mut, dot: false },
      { label: "Equal-wt", color: T.mut, dot: false },
    ]);

  const fmtPctS = (v: number) => pct(v, true);
  const fmtPct0 = (v: number) => pct(v, false);
  const fmtNum = (v: number) => String(v);
  const fmtX = (v: number) => v.toFixed(2) + "×";

  const mk = (label: string, vals: (number | null)[], f: (v: number) => string, dir: number): Row => {
    const valid = vals.filter((v): v is number => v !== null && !Number.isNaN(v));
    const best = dir ? (dir > 0 ? Math.max(...valid) : Math.min(...valid)) : NaN;
    const worst = dir ? (dir > 0 ? Math.min(...valid) : Math.max(...valid)) : NaN;
    return {
      isSection: false, label,
      cells: vals.map((v) => ({
        text: v === null || Number.isNaN(v) ? "—" : f(v),
        color: dir && v === best && valid.length > 1 ? T.gain : dir && v === worst && valid.length > 1 ? T.loss : T.ink,
        weight: dir && v === best && valid.length > 1 ? "500" : "400",
      })),
    };
  };
  const M = (k: string) => exps.map((e) => e.evaluation.metrics[k] ?? null);
  const F = (k: string) => exps.map((e) => e.evaluation.fidelity[k] ?? null);
  const row = (label: string, k: string, f: (v: number) => string, dir: number) =>
    mk(label, [...M(k), bm.synthetic_mega_cap_proxy[k] ?? null, bm.equal_weight[k] ?? null], f, dir);
  const frow = (label: string, k: string, f: (v: number) => string, dir: number) =>
    mk(label, [...F(k), null, null], f, dir);

  const rows: Row[] = [
    row("Total return", "total_return", fmtPctS, 1),
    row("CAGR", "cagr", fmtPctS, 1),
    row("Volatility (ann.)", "volatility", fmtPct0, -1),
    row("Sharpe", "sharpe", (v) => v.toFixed(2), 1),
    row("Max drawdown", "max_drawdown", fmtPctS, 1),
    row("Turnover (ann.)", "turnover", fmtX, 0),
    row("Trade count", "trade_count", fmtNum, 0),
    row("Avg holding (days)", "avg_holding_days", fmtNum, 0),
    row("Cash exposure", "cash_exposure", fmtPct0, 0),
    row("Max concentration", "max_concentration", fmtPct0, 0),
    row(`vs ${referenceLabel}`, "synthetic_mega_cap_proxy_relative", fmtPctS, 1),
    { isSection: true, label: "PHILOSOPHY FIDELITY" },
    frow("Factor coverage", "factor_coverage", fmtPct0, 1),
    frow("Constraint interventions", "constraint_interventions", fmtNum, -1),
    frow("Ranking churn", "ranking_churn", fmtPct0, -1),
    frow("Selection stability", "selection_stability", fmtPct0, 1),
    frow("Rule violations", "rule_violations", fmtNum, 0),
  ];

  const grid = `210px repeat(${cols.length}, 1fr)`;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, flex: 1, minHeight: 0 }}>
      <section style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 16, marginBottom: 14, flex: "none" }}>
          <div style={{ fontFamily: SERIF, fontSize: 19, fontWeight: 500 }}>Comparison — all experiments</div>
          <div style={{ fontFamily: MONO, fontSize: 11, color: "var(--mut)" }}>{data.dates[0]} → {data.dates[data.dates.length - 1]}</div>
          <div style={{ marginLeft: "auto", fontFamily: SERIF, fontStyle: "italic", fontSize: 13, color: "var(--mut)", border: "1px solid var(--hl)", borderRadius: 6, padding: "5px 12px" }}>
            Research prototype. Historical replay is descriptive. Not financial advice.
          </div>
        </div>
        <div style={{ overflow: "auto", minHeight: 0 }}>
          <div style={{ display: "grid", gridTemplateColumns: grid, borderBottom: "1px solid var(--ink)" }}>
            <div style={{ padding: "8px 10px", fontSize: 10, letterSpacing: "0.12em", fontWeight: 600, color: "var(--mut)" }}>METRIC</div>
            {cols.map((c) => (
              <div key={c.label} style={{ padding: "8px 10px", fontFamily: SERIF, fontSize: 15, fontWeight: 500, color: "var(--ink)", display: "flex", alignItems: "center", gap: 8, justifyContent: "flex-end", textAlign: "right" }}>
                {c.dot && <div style={{ width: 16, height: 3, background: c.color, flex: "none" }} />}
                {c.label}
              </div>
            ))}
          </div>
          {rows.map((r, i) =>
            r.isSection ? (
              <div key={i} style={{ fontSize: 10, letterSpacing: "0.14em", fontWeight: 600, color: "var(--mut)", padding: "18px 10px 7px", borderBottom: "1px solid var(--ink)" }}>{r.label}</div>
            ) : (
              <div key={i} style={{ display: "grid", gridTemplateColumns: grid, borderBottom: "1px solid var(--fnt)" }}>
                <div style={{ padding: "7px 10px", fontSize: 12.5, color: "var(--mut)" }}>{r.label}</div>
                {r.cells.map((cell, j) => (
                  <div key={j} style={{ padding: "7px 10px", fontFamily: MONO, fontSize: 12, textAlign: "right", color: cell.color, fontWeight: cell.weight }}>{cell.text}</div>
                ))}
              </div>
            )
          )}
        </div>
      </section>
    </div>
  );
}
