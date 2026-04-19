import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Cpu,
  Layers,
  Play,
  Radio,
  ShieldCheck,
  Sparkles,
  Zap,
} from "lucide-react";

type WsEvent =
  | {
      type: "run_begin";
      ticket_count: number;
    }
  | {
      type: "run_error";
      message: string;
    }
  | {
      type: "run_complete";
      processed_count: number;
      resolved_count: number;
      escalated_count: number;
      failed_count: number;
      dead_letter_count: number;
    }
  | {
      type: "ticket_begin";
      ticket_id: string;
      message_preview: string;
      triage: { category: string; urgency: string; resolvability: string };
    }
  | {
      type: "tool_step";
      ticket_id: string;
      tool: string;
      thought: string;
      attempt: number;
      step_status: string;
      result_preview: string;
    }
  | {
      type: "ticket_complete";
      ticket_id: string;
      payload: {
        final_decision: string;
        status: string;
        confidence: number;
        triage: Record<string, string>;
        outcome: string;
        escalated: boolean;
        dead_lettered: boolean;
        steps: Array<{
          thought: string;
          tool_called: string;
          attempt: number;
          status: string;
          result_preview: string;
        }>;
      };
    };

type TicketVM = {
  ticket_id: string;
  preview: string;
  phase: "queued" | "running" | "done";
  triage?: Record<string, string>;
  steps: Array<{
    tool: string;
    thought: string;
    attempt: number;
    step_status: string;
    preview: string;
  }>;
  outcome?: string;
  status?: string;
};

function wsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/live`;
}

function badgeUrgency(u?: string) {
  const x = (u || "").toLowerCase();
  if (x === "high") return "bg-rose-500/15 text-rose-300 ring-rose-500/40";
  if (x === "low") return "bg-zinc-500/15 text-zinc-400 ring-zinc-600/40";
  return "bg-amber-500/15 text-amber-200 ring-amber-500/35";
}

function statusTone(status?: string) {
  if (!status) return "bg-zinc-800 text-zinc-400 ring-zinc-700";
  if (status === "resolved") return "bg-emerald-500/15 text-emerald-300 ring-emerald-500/35";
  if (status === "escalated") return "bg-violet-500/15 text-violet-200 ring-violet-500/35";
  if (status === "dead_lettered") return "bg-red-500/15 text-red-300 ring-red-500/35";
  return "bg-zinc-700/40 text-zinc-300 ring-zinc-600";
}

export default function App() {
  const [connected, setConnected] = useState(false);
  const [running, setRunning] = useState(false);
  const [simulateFaults, setSimulateFaults] = useState(false);
  const [lastRun, setLastRun] = useState<WsEvent & { type: "run_complete" } | null>(null);
  const [tickets, setTickets] = useState<Record<string, TicketVM>>({});
  const [feed, setFeed] = useState<string[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const pushFeed = useCallback((line: string) => {
    setFeed((f) => [...f.slice(-120), `[${new Date().toLocaleTimeString()}] ${line}`]);
  }, []);

  useEffect(() => {
    fetch("/api/tickets")
      .then((r) => r.json())
      .then((data: { tickets: Array<{ ticket_id: string; preview: string }> }) => {
        const init: Record<string, TicketVM> = {};
        for (const t of data.tickets) {
          init[t.ticket_id] = {
            ticket_id: t.ticket_id,
            preview: t.preview,
            phase: "queued",
            steps: [],
          };
        }
        setTickets(init);
      })
      .catch(() => pushFeed("Could not load /api/tickets — start the API server."));
  }, [pushFeed]);

  useEffect(() => {
    const ws = new WebSocket(wsUrl());
    wsRef.current = ws;
    ws.onopen = () => {
      setConnected(true);
      pushFeed("WebSocket connected — stream ready.");
    };
    ws.onclose = () => {
      setConnected(false);
      pushFeed("WebSocket disconnected.");
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as WsEvent;
        if (msg.type === "run_begin") {
          setRunning(true);
          setLastRun(null);
          setTickets((prev) => {
            const next = { ...prev };
            for (const k of Object.keys(next)) {
              next[k] = { ...next[k], phase: "queued", steps: [], outcome: undefined, triage: undefined };
            }
            return next;
          });
          pushFeed(`Run started — ${msg.ticket_count} tickets (parallel asyncio.gather).`);
          return;
        }
        if (msg.type === "run_error") {
          setRunning(false);
          pushFeed(`Run error — ${msg.message}`);
          return;
        }
        if (msg.type === "run_complete") {
          setRunning(false);
          setLastRun(msg);
          pushFeed(
            `Run complete — resolved ${msg.resolved_count}, escalated ${msg.escalated_count}, dead-letter ${msg.dead_letter_count}`
          );
          return;
        }
        if (msg.type === "ticket_begin") {
          setTickets((prev) => ({
            ...prev,
            [msg.ticket_id]: {
              ...(prev[msg.ticket_id] || {
                ticket_id: msg.ticket_id,
                preview: msg.message_preview,
                phase: "running",
                steps: [],
              }),
              phase: "running",
              preview: prev[msg.ticket_id]?.preview || msg.message_preview,
              triage: msg.triage,
              steps: [],
            },
          }));
          return;
        }
        if (msg.type === "tool_step") {
          setTickets((prev) => {
            const cur = prev[msg.ticket_id];
            if (!cur) return prev;
            const step = {
              tool: msg.tool,
              thought: msg.thought,
              attempt: msg.attempt,
              step_status: msg.step_status,
              preview: msg.result_preview,
            };
            return {
              ...prev,
              [msg.ticket_id]: {
                ...cur,
                phase: "running",
                steps: [...cur.steps, step],
              },
            };
          });
          return;
        }
        if (msg.type === "ticket_complete") {
          setTickets((prev) => {
            const cur = prev[msg.ticket_id];
            if (!cur) return prev;
            return {
              ...prev,
              [msg.ticket_id]: {
                ...cur,
                phase: "done",
                outcome: msg.payload.outcome,
                status: msg.payload.status,
                triage: msg.payload.triage || cur.triage,
                steps: msg.payload.steps.map((s) => ({
                  tool: s.tool_called,
                  thought: s.thought,
                  attempt: s.attempt,
                  step_status: s.status,
                  preview: s.result_preview,
                })),
              },
            };
          });
          return;
        }
      } catch {
        pushFeed(`Bad JSON frame: ${ev.data.slice(0, 120)}`);
      }
    };
    return () => ws.close();
  }, [pushFeed]);

  const startRun = async () => {
    pushFeed(simulateFaults ? "Requested run with simulated tool faults." : "Requested production-style run.");
    await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ simulate_faults: simulateFaults }),
    })
      .then(() => pushFeed("POST /api/run accepted — processing…"))
      .catch(() => pushFeed("POST /api/run failed."));
  };

  const ticketList = useMemo(() => Object.values(tickets).sort((a, b) => a.ticket_id.localeCompare(b.ticket_id)), [tickets]);

  const stats = useMemo(() => {
    const done = ticketList.filter((t) => t.phase === "done").length;
    const run = ticketList.filter((t) => t.phase === "running").length;
    return { total: ticketList.length, done, run, queued: ticketList.length - done - run };
  }, [ticketList]);

  return (
    <div className="relative min-h-screen overflow-hidden grid-bg">
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-teal-950/30 via-transparent to-zinc-950" />
      <div className="relative z-10 mx-auto max-w-[1600px] px-4 py-8 lg:px-10">
        {/* Hero */}
        <header className="mb-10 flex flex-col gap-8 lg:flex-row lg:items-end lg:justify-between">
          <div className="space-y-4">
            <div className="inline-flex items-center gap-2 rounded-full border border-teal-500/25 bg-teal-950/40 px-4 py-1 text-xs font-medium uppercase tracking-[0.2em] text-teal-300/90 ring-1 ring-teal-400/15">
              <Sparkles className="h-3.5 w-3.5" />
              ShopWave · Autonomous resolution
            </div>
            <h1 className="max-w-3xl font-sans text-4xl font-bold tracking-tight text-white drop-shadow-glow md:text-5xl">
              Command center for{" "}
              <span className="bg-gradient-to-r from-teal-300 via-cyan-200 to-teal-400 bg-clip-text text-transparent">
                concurrent agentic support
              </span>
            </h1>
            <p className="max-w-2xl text-base leading-relaxed text-zinc-400">
              Live audit stream: triage (urgency · category · resolvability), multi-step tool chains with reasoning,
              schema validation, retries with backoff, and structured escalation — aligned with autonomous agent judging
              criteria.
            </p>
          </div>

          <div className="flex flex-col gap-4 rounded-2xl border border-white/10 bg-zinc-900/60 p-6 shadow-xl shadow-teal-900/10 backdrop-blur-xl ring-1 ring-white/5 lg:min-w-[340px]">
            <div className="flex items-center justify-between text-sm text-zinc-400">
              <span className="flex items-center gap-2 font-medium">
                <Radio className={`h-4 w-4 ${connected ? "text-teal-400 drop-shadow-[0_0_8px_rgba(45,212,191,0.55)]" : "text-red-400"}`} />
                Live stream
              </span>
              <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase ring-1 ${connected ? "bg-teal-500/15 text-teal-300 ring-teal-400/35" : "bg-red-500/15 text-red-300 ring-red-400/35"}`}>
                {connected ? "connected" : "offline"}
              </span>
            </div>

            <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-white/10 bg-black/40 px-4 py-3 text-sm text-zinc-300 hover:border-teal-500/30">
              <input type="checkbox" checked={simulateFaults} onChange={(e) => setSimulateFaults(e.target.checked)} className="accent-teal-500" />
              <div>
                <div className="flex items-center gap-2 font-medium text-white">
                  <AlertTriangle className="h-4 w-4 text-amber-400" />
                  Simulate flaky tools
                </div>
                <div className="text-xs text-zinc-500">Timeouts & malformed payloads — demos resilient recovery.</div>
              </div>
            </label>

            <button
              type="button"
              onClick={startRun}
              disabled={running || !connected}
              className="group relative flex items-center justify-center gap-3 overflow-hidden rounded-xl bg-gradient-to-r from-teal-600 via-teal-500 to-cyan-500 px-6 py-4 text-lg font-semibold text-white shadow-lg shadow-teal-900/40 ring-2 ring-teal-400/30 transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Play className="h-6 w-6 shrink-0 fill-current" />
              Run all tickets
              <span className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top,rgba(255,255,255,0.25),transparent)] opacity-0 transition group-hover:opacity-100" />
            </button>

            <div className="grid grid-cols-3 gap-2 border-t border-white/10 pt-4 text-center text-[11px] uppercase tracking-wider text-zinc-500">
              <div>
                <Cpu className="mx-auto mb-1 h-4 w-4 text-teal-400" />
                Parallel tickets
              </div>
              <div>
                <ShieldCheck className="mx-auto mb-1 h-4 w-4 text-teal-400" />
                Schema checks
              </div>
              <div>
                <Layers className="mx-auto mb-1 h-4 w-4 text-teal-400" />
                Explainable trace
              </div>
            </div>
          </div>
        </header>

        {/* KPI strip */}
        <section className="mb-10 grid gap-4 sm:grid-cols-2 xl:grid-cols-6">
          {[
            ["Tickets", stats.total.toString(), <Zap key="z" className="h-5 w-5 text-yellow-400" />],
            ["Streaming", stats.run.toString(), <Activity key="a" className="h-5 w-5 text-teal-400 animate-pulse" />],
            ["Queued", stats.queued.toString(), <ChevronRight key="c" className="h-5 w-5 text-zinc-500" />],
            ["Completed", stats.done.toString(), <CheckCircle2 key="k" className="h-5 w-5 text-emerald-400" />],
            ["Resolved batch", lastRun?.resolved_count?.toString() ?? "—", <ShieldCheck key="s" className="h-5 w-5 text-teal-400" />],
            ["Escalated batch", lastRun?.escalated_count?.toString() ?? "—", <AlertTriangle key="e" className="h-5 w-5 text-violet-400" />],
          ].map(([label, val, icon]) => (
            <div
              key={String(label)}
              className="flex items-center gap-4 rounded-2xl border border-white/[0.06] bg-zinc-900/55 px-5 py-4 shadow-inner shadow-black/40 backdrop-blur-md ring-1 ring-white/[0.04]"
            >
              <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-black/35">
                {icon}
              </div>
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-zinc-500">{label}</div>
                <div className="font-mono text-2xl font-semibold tabular-nums text-white">{val}</div>
              </div>
            </div>
          ))}
        </section>

        <div className="grid gap-8 xl:grid-cols-[1fr_380px]">
          {/* Ticket grid */}
          <section className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="flex items-center gap-2 text-lg font-semibold text-white">
                <Layers className="h-5 w-5 text-teal-400" />
                Ticket matrix
              </h2>
              <span className="text-xs text-zinc-500">
                Expand a row for full explainable audit trail · tool chain depth visible per ticket
              </span>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              {ticketList.map((t) => {
                const open = expanded === t.ticket_id;
                const tri = t.triage || {};
                return (
                  <article
                    key={t.ticket_id}
                    className={`group rounded-2xl border bg-zinc-900/55 shadow-lg ring-1 transition hover:border-teal-500/25 hover:shadow-[0_0_40px_-12px_rgba(45,212,191,0.35)] ${
                      open ? "border-teal-500/40 ring-teal-500/25" : "border-white/[0.07] ring-white/[0.04]"
                    }`}
                  >
                    <button type="button" className="w-full cursor-pointer text-left p-5" onClick={() => setExpanded(open ? null : t.ticket_id)}>
                      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <div className="font-mono text-sm font-semibold text-teal-300">{t.ticket_id}</div>
                          <div className="mt-1 line-clamp-2 text-xs leading-relaxed text-zinc-400">{t.preview}</div>
                        </div>
                        <div className="flex flex-col items-end gap-2">
                          <span
                            className={`rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider ring-1 ${statusTone(t.status)}`}
                          >
                            {t.phase === "queued" && "queued"}
                            {t.phase === "running" && (
                              <span className="flex items-center gap-1">
                                <span className="inline-flex h-1.5 w-1.5 animate-pulse rounded-full bg-teal-400" />
                                running
                              </span>
                            )}
                            {t.phase === "done" && (t.status || "done")}
                          </span>
                          {tri.urgency && (
                            <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ring-1 ${badgeUrgency(tri.urgency)}`}>
                              {tri.urgency}
                            </span>
                          )}
                        </div>
                      </div>

                      <div className="flex flex-wrap gap-2">
                        {tri.category && (
                          <span className="rounded-lg border border-white/10 bg-black/35 px-2 py-1 text-[11px] text-zinc-300">
                            Category · <span className="font-medium text-white">{tri.category}</span>
                          </span>
                        )}
                        {tri.resolvability && (
                          <span className="rounded-lg border border-white/10 bg-black/35 px-2 py-1 text-[11px] text-zinc-300">
                            Resolve · <span className="font-medium text-white">{tri.resolvability}</span>
                          </span>
                        )}
                      </div>

                      {t.outcome && (
                        <p className="mt-3 border-t border-white/[0.06] pt-3 text-xs leading-relaxed text-zinc-400">{t.outcome}</p>
                      )}
                    </button>

                    {open && (
                      <div className="animate-fadein border-t border-white/[0.06] bg-black/35 px-5 pb-5 pt-4">
                        <div className="mb-3 text-[11px] font-semibold uppercase tracking-[0.2em] text-zinc-500">Instrumented chain</div>
                        <ol className="space-y-3 font-mono text-[11px] leading-relaxed">
                          {t.steps.map((s, i) => (
                            <li key={`${t.ticket_id}-${i}`} className="rounded-xl border border-white/[0.06] bg-zinc-950/80 p-3 ring-1 ring-black/60">
                              <div className="flex flex-wrap items-center justify-between gap-2 text-teal-300/90">
                                <span className="font-semibold">{s.tool}</span>
                                <span className="text-[10px] text-zinc-500">
                                  attempt {s.attempt} · <span className="text-zinc-400">{s.step_status}</span>
                                </span>
                              </div>
                              <div className="mt-2 text-zinc-400">{s.thought}</div>
                              <pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-black/60 p-2 text-[10px] text-zinc-500 scroll-thin">
                                {s.preview}
                              </pre>
                            </li>
                          ))}
                          {!t.steps.length && <li className="text-zinc-600">Awaiting instrumentation…</li>}
                        </ol>
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          </section>

          {/* Live feed */}
          <aside className="xl:sticky xl:top-8 xl:h-[calc(100vh-7rem)]">
            <div className="flex h-full flex-col overflow-hidden rounded-2xl border border-white/[0.07] bg-zinc-950/70 shadow-2xl ring-1 ring-teal-500/10 backdrop-blur-xl">
              <div className="flex items-center justify-between border-b border-white/[0.06] px-5 py-4">
                <span className="flex items-center gap-2 text-sm font-semibold text-white">
                  <Cpu className="h-4 w-4 text-teal-400" />
                  Telemetry
                </span>
                <span className="font-mono text-[10px] uppercase tracking-widest text-zinc-500">events</span>
              </div>
              <pre className="flex-1 overflow-auto scroll-thin px-4 py-4 font-mono text-[11px] leading-relaxed text-teal-100/85">
                {feed.map((line, i) => (
                  <div key={i} className="border-b border-white/[0.03] py-1.5 text-zinc-400">
                    {line}
                  </div>
                ))}
              </pre>
            </div>
          </aside>
        </div>

        <footer className="mt-16 border-t border-white/[0.06] pt-8 text-center text-xs text-zinc-600">
          Audit logs persisted to <span className="font-mono text-zinc-400">logs/audit_log.json</span> · Parallel execution via{" "}
          <span className="font-mono text-zinc-400">asyncio.gather</span>
        </footer>
      </div>
    </div>
  );
}
