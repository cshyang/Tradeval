"use client";

import { CSSProperties, useEffect, useState } from "react";
import type { Metrics, RunData, Selected } from "../lib/types";

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
  const [expIdx, setExpIdx] = useState(0);
  const [view, setView] = useState<"replay" | "compare">("replay");
  const [rebIdx, setRebIdx] = useState(0);
  const [symIdx, setSymIdx] = useState(0);
  const [theme, setTheme] = useState<"light" | "dark">("light");

  useEffect(() => {
    fetch("runs/data.json")
      .then((r) => r.json())
      .then((d: RunData) => {
        setData(d);
        setRebIdx(d.experiments[0].rebalances.length - 1);
      })
      .catch((e) => console.error("failed to load runs/data.json", e));
  }, []);

  const T = THEMES[theme];
  const vars = {
    "--bg": T.bg, "--ink": T.ink, "--mut": T.mut,
    "--fnt": T.fnt, "--hl": T.hl, "--pan": T.pan,
  } as CSSProperties;

  const tabStyle = (active: boolean): CSSProperties => ({
    fontFamily: SANS, fontSize: 13.5, fontWeight: 500,
    background: active ? T.ink : "transparent",
    color: active ? T.bg : T.mut,
    border: "none", borderRadius: 6, padding: "7px 18px", cursor: "pointer",
  });

  return (
    <div style={{ ...vars, fontFamily: SANS, background: "var(--bg)", color: "var(--ink)", minHeight: "100vh", display: "flex", flexDirection: "column", fontSize: 14, transition: "background 0.25s, color 0.25s" }}>
      <header style={{ display: "flex", alignItems: "center", flexWrap: "wrap", rowGap: 8, columnGap: 16, padding: "14px 28px 12px", borderBottom: "1px solid var(--ink)", flex: "none", minWidth: 0 }}>
        <div style={{ fontFamily: SERIF, fontSize: 23, fontWeight: 500, letterSpacing: "-0.01em", whiteSpace: "nowrap" }}>Philosophy Lab</div>
        <div style={{ fontSize: 10, letterSpacing: "0.12em", fontWeight: 500, color: "var(--mut)", whiteSpace: "nowrap" }}>SYNTHETIC DEMO DATA</div>
        <nav style={{ display: "flex", gap: 6, alignItems: "center", marginLeft: "auto" }}>
          <button onClick={() => setView("replay")} style={tabStyle(view === "replay")}>Replay</button>
          <button onClick={() => setView("compare")} style={tabStyle(view === "compare")}>Compare</button>
          <div title="Locked in v1" style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 13.5, color: "var(--mut)", opacity: 0.6, padding: "7px 14px", cursor: "not-allowed", userSelect: "none" }}>
            Scenarios <span style={{ fontSize: 10, letterSpacing: "0.06em", border: "1px solid var(--hl)", borderRadius: 5, padding: "1px 6px" }}>v1 · locked</span>
          </div>
        </nav>
        <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", minWidth: 0 }}>
          <div title="Available when a broker is connected" style={{ display: "flex", alignItems: "center", gap: 8, cursor: "not-allowed", userSelect: "none", opacity: 0.55 }}>
            <span style={{ fontSize: 12, color: "var(--mut)" }}>Live paper mode</span>
            <div style={{ width: 32, height: 18, borderRadius: 9, background: "var(--fnt)", border: "1px solid var(--hl)", position: "relative" }}>
              <div style={{ position: "absolute", top: 2, left: 2, width: 12, height: 12, borderRadius: "50%", background: "var(--mut)" }} />
            </div>
          </div>
          <button disabled title="Not available in this build" style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 500, color: "var(--mut)", opacity: 0.6, background: "transparent", border: "1px solid var(--hl)", borderRadius: 6, padding: "6px 14px", cursor: "not-allowed" }}>Connect broker</button>
          <button onClick={() => setTheme(theme === "light" ? "dark" : "light")} style={{ fontFamily: SANS, fontSize: 12.5, fontWeight: 500, color: "var(--ink)", background: "transparent", border: "1px solid var(--ink)", borderRadius: 6, padding: "6px 14px", cursor: "pointer" }}>{T.label}</button>
        </div>
      </header>

      {!data ? (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: SERIF, fontStyle: "italic", fontSize: 14, color: "var(--mut)" }}>loading run artifacts…</div>
      ) : (
        <Loaded
          data={data} T={T} view={view}
          expIdx={expIdx} rebIdx={rebIdx} symIdx={symIdx}
          setExp={(i) => { setExpIdx(i); setRebIdx(data.experiments[i].rebalances.length - 1); setSymIdx(0); }}
          setReb={(i) => { setRebIdx(i); setSymIdx(0); }}
          setSym={setSymIdx}
        />
      )}
    </div>
  );
}

type Theme = typeof THEMES.light;

function Loaded({ data, T, view, expIdx, rebIdx, symIdx, setExp, setReb, setSym }: {
  data: RunData; T: Theme; view: "replay" | "compare";
  expIdx: number; rebIdx: number; symIdx: number;
  setExp: (i: number) => void; setReb: (i: number) => void; setSym: (i: number) => void;
}) {
  const exps = data.experiments;
  const exp = exps[expIdx];
  const color = PALETTE[expIdx % PALETTE.length];

  return (
    <>
      <div style={{ display: "flex", alignItems: "stretch", padding: "0 28px", borderBottom: "1px solid var(--hl)", flex: "none" }}>
        {exps.map((e, i) => {
          const sel = i === expIdx;
          const c = PALETTE[i % PALETTE.length];
          const ret = e.evaluation.metrics.total_return ?? 0;
          return (
            <button key={e.id} onClick={() => setExp(i)} style={{ fontFamily: SANS, flex: 1, textAlign: "left", background: "transparent", border: "none", borderRight: "1px solid var(--hl)", padding: "15px 22px 13px 18px", cursor: "pointer", color: "var(--ink)", opacity: sel ? 1 : 0.5, display: "flex", flexDirection: "column", gap: 4 }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 9 }}>
                <div style={{ width: 22, height: 3, background: c, flex: "none", alignSelf: "center" }} />
                <span style={{ fontFamily: SERIF, fontSize: 17.5, fontWeight: sel ? 500 : 400 }}>{e.label}</span>
                <span style={{ fontSize: 11, color: "var(--mut)" }}>{e.version}</span>
                <span style={{ fontFamily: MONO, fontSize: 13, color: ret >= 0 ? T.gain : T.loss, marginLeft: "auto" }}>{pct(ret, true)}</span>
              </div>
              <div style={{ fontSize: 11.5, color: "var(--mut)" }}>{e.start} → {e.end}</div>
            </button>
          );
        })}
      </div>

      <main style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0, padding: "18px 28px 10px", gap: 16, overflow: "auto" }}>
        {view === "replay"
          ? <Replay data={data} T={T} exp={exp} color={color} rebIdx={rebIdx} symIdx={symIdx} setReb={setReb} setSym={setSym} />
          : <Compare data={data} T={T} />}
      </main>

      <footer style={{ flex: "none", display: "flex", gap: 24, alignItems: "center", padding: "9px 28px", borderTop: "1px solid var(--hl)", fontFamily: MONO, fontSize: 10.5, color: "var(--mut)" }}>
        <span>engine {exp.engine_version}</span>
        <span>run {exp.content_hash}</span>
        <span>universe {exp.universe}</span>
        <span style={{ marginLeft: "auto", fontFamily: SERIF, fontStyle: "italic", fontSize: 12 }}>artifacts are the API — no values computed client-side</span>
      </footer>
    </>
  );
}

function Replay({ data, T, exp, color, rebIdx, symIdx, setReb, setSym }: {
  data: RunData; T: Theme; exp: RunData["experiments"][0]; color: string;
  rebIdx: number; symIdx: number; setReb: (i: number) => void; setSym: (i: number) => void;
}) {
  const eq = exp.equity.map(parseFloat);
  const spy = data.spy.map(parseFloat);
  const ew = data.equal_weight.map(parseFloat);
  const all = SHOW_BENCHMARKS ? eq.concat(spy, ew) : eq;
  const lo = Math.min(...all) * 0.99, hi = Math.max(...all) * 1.01;
  const N = eq.length;
  const X = (i: number) => PAD_X + (i / (N - 1)) * (W - 2 * PAD_X);
  const Y = (v: number) => PAD_T + (1 - (v - lo) / (hi - lo)) * (H - PAD_T - PAD_B);
  const path = (arr: number[]) =>
    arr.map((v, i) => (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1)).join("");

  const rebs = exp.rebalances;
  const reb = rebs[rebIdx];
  const vs = eq[reb.week] / spy[reb.week] - 1;
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
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}><div style={{ width: 16, height: 0, borderTop: "2px dashed var(--mut)" }} />SPY</div>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}><div style={{ width: 16, height: 0, borderTop: "2px dotted var(--mut)" }} />Equal-weight</div>
              </>
            )}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "baseline", gap: 22, marginBottom: 6 }}>
          <div style={{ fontFamily: MONO, fontSize: 22, color: "var(--ink)" }}>{money(exp.equity[reb.week])}</div>
          <div style={{ fontFamily: MONO, fontSize: 12, color: "var(--mut)" }}>as of <span style={{ color: "var(--ink)" }}>{reb.as_of}</span></div>
          <div style={{ fontFamily: MONO, fontSize: 12, color: vs >= 0 ? T.gain : T.loss }}>{pct(vs, true)} vs SPY</div>
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
              <path d={path(spy)} fill="none" style={{ stroke: "var(--mut)" }} strokeWidth="1.2" strokeDasharray="6 5" />
              <path d={path(ew)} fill="none" style={{ stroke: "var(--mut)", opacity: 0.6 }} strokeWidth="1.1" strokeDasharray="2 4" />
            </>
          )}
          {CHART_FILL && <path d={path(eq) + `L${W - PAD_X} ${H}L${PAD_X} ${H}Z`} fill={color} opacity="0.08" stroke="none" />}
          <path d={path(eq)} fill="none" style={{ stroke: "var(--ink)" }} strokeWidth="1.8" />
          <line x1={X(reb.week).toFixed(1)} x2={X(reb.week).toFixed(1)} y1="0" y2={H} stroke={color} strokeWidth="1.4" opacity="0.6" />
          {rebs.map((rb, i) => (
            <circle key={i} onClick={() => setReb(i)} cx={X(rb.week).toFixed(1)} cy={Y(eq[rb.week]).toFixed(1)}
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
        <div style={{ display: "grid", gridTemplateColumns: "1.1fr 1.1fr 1fr", gap: 32, flex: 1, minHeight: 0, overflow: "auto" }}>
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
            <div style={{ fontSize: 11, color: "var(--mut)", marginTop: "auto", paddingTop: 10, fontStyle: "italic", fontFamily: SERIF }}>
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
  const cols = exps
    .map((e, i) => ({ label: e.label, color: PALETTE[i % PALETTE.length], dot: true }))
    .concat([
      { label: "SPY", color: T.mut, dot: false },
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
    mk(label, [...M(k), bm.spy[k] ?? null, bm.equal_weight[k] ?? null], f, dir);
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
    row("vs SPY", "spy_relative", fmtPctS, 1),
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
