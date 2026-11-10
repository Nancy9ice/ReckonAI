import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Coverage, ExceptionRow, Run } from "./api";
import { api } from "./api";
import { decisionText } from "./columns";
import CoverageView from "./CoverageView";
import DetailDrawer from "./DetailDrawer";
import QueueView from "./QueueView";
import RunsView from "./RunsView";

type View = "queue" | "coverage" | "runs";

type RunNote = { tone: "ok" | "bad" | "busy" | "info"; text: string } | null;

// `null` renders as a dash: a figure that has not loaded is not a measured zero.
function Kpi(props: { label: string; value: number | null; tone?: string }) {
  return (
    <div className={`kpi ${props.tone ?? ""}`}>
      <span className="kpi-value">{props.value ?? "-"}</span>
      <span className="kpi-label">{props.label}</span>
    </div>
  );
}

export default function App() {
  const [view, setView] = useState<View>("queue");
  const [rows, setRows] = useState<ExceptionRow[] | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runNote, setRunNote] = useState<RunNote>(null);
  const [running, setRunning] = useState(false);
  const [investigating, setInvestigating] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Only the newest request may set the rows: switching run and back fires two
  // fetches, and the slower, older one must not overwrite the newer answer.
  const latest = useRef(0);
  const refresh = useCallback(async () => {
    const ticket = ++latest.current;
    try {
      const fetched = await api.exceptions(runId);
      if (ticket === latest.current) {
        setRows(fetched);
        setError(null);
      }
    } catch (e) {
      if (ticket === latest.current) setError(String(e));
    }
  }, [runId]);

  const refreshAll = useCallback(async () => {
    await refresh();
    api.runs().then(setRuns).catch((e) => setError(String(e)));
    api.coverage().then(setCoverage).catch((e) => setError(String(e)));
  }, [refresh]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    api.runs().then(setRuns).catch((e) => setError(String(e)));
    api.coverage().then(setCoverage).catch((e) => setError(String(e)));
  }, []);

  // Ask for a run, then follow its job until it settles. The window is
  // date-grained by design, so a second request the same day is answered rather
  // than queued; that answer is shown as given.
  const runNow = async () => {
    setRunning(true);
    setRunNote({ tone: "busy", text: "Requesting a reconciliation run..." });
    try {
      const result = await api.requestRun();
      if (!result.job) {
        // "Already done today" is information, not success: new data may be
        // waiting for tomorrow, and a green banner would read as "up to date".
        setRunNote({
          tone: result.status === "already_done" ? "info" : "bad",
          text: result.message,
        });
        return;
      }
      let job = result.job;
      let misses = 0;
      setRunNote({ tone: "busy", text: "Run queued. Waiting for the worker..." });
      for (let waited = 0; job.status === "queued" || job.status === "claimed"; ) {
        if (waited >= 120) {
          setRunNote({
            tone: "bad",
            text: "The run is still waiting. Is the worker running?",
          });
          return;
        }
        await new Promise((r) => setTimeout(r, 2000));
        if (!mounted.current) return;
        waited += 2;
        // One failed poll is not a failed run: the worker may well be
        // finishing it. Give up only after three in a row.
        try {
          job = await api.job(job.id);
          misses = 0;
        } catch (e) {
          if (++misses >= 3) throw e;
          continue;
        }
        if (job.status === "claimed")
          setRunNote({ tone: "busy", text: "Reconciling..." });
      }
      if (job.status === "done") {
        await refreshAll();
        setRunNote({ tone: "ok", text: "Run complete. The queue is up to date." });
      } else {
        setRunNote({
          tone: "bad",
          text: `The run failed: ${job.last_error ?? "no reason recorded"}`,
        });
      }
    } catch (e) {
      setRunNote({ tone: "bad", text: String(e) });
    } finally {
      setRunning(false);
    }
  };

  // Queue investigations, then follow their jobs. The worker runs them one at a
  // time, so this polls until every job settles, refreshing the queue as each
  // finishes so rows change from "Not investigated" while it works.
  const investigate = async (ids: string[], skipSettled: boolean) => {
    setInvestigating(true);
    setRunNote({ tone: "busy", text: "Queueing investigations..." });
    try {
      const result = await api.investigateMany(ids, skipSettled);
      const parts = [`${result.queued} queued`];
      if (result.in_flight) parts.push(`${result.in_flight} already running`);
      if (result.skipped) parts.push(`${result.skipped} already concluded`);
      if (result.not_found) parts.push(`${result.not_found} not found`);

      let pending = result.jobs.filter(
        (j) => j.status === "queued" || j.status === "claimed",
      );
      const total = pending.length;
      let failed = 0;
      for (let waited = 0; pending.length > 0; ) {
        if (!mounted.current) return;
        if (waited >= 900) {
          setRunNote({
            tone: "bad",
            text: `Still waiting on ${pending.length} investigations. Is the worker running?`,
          });
          return;
        }
        setRunNote({
          tone: "busy",
          text: `${parts.join(", ")}. Investigating: ${total - pending.length} of ${total} done...`,
        });
        await new Promise((r) => setTimeout(r, 3000));
        waited += 3;
        const next = [];
        let settledNow = false;
        for (const job of pending) {
          try {
            const fresh = await api.job(job.id);
            if (fresh.status === "done") settledNow = true;
            else if (fresh.status === "failed") {
              failed += 1;
              settledNow = true;
            } else next.push(fresh);
          } catch {
            next.push(job); // a missed poll is not a failed job
          }
        }
        pending = next;
        if (settledNow) await refresh();
      }
      await refreshAll();
      setRunNote({
        tone: failed > 0 ? "bad" : "ok",
        text:
          `${parts.join(", ")}. ` +
          (total === 0
            ? "Nothing new to investigate."
            : failed > 0
              ? `Finished, ${failed} could not run - open one to see why.`
              : "All finished."),
      });
    } catch (e) {
      setRunNote({ tone: "bad", text: String(e) });
    } finally {
      setInvestigating(false);
    }
  };

  const stats = useMemo(() => {
    const all = rows ?? [];
    return {
      open: all.length,
      confirmed: all.filter((r) => r.certainty === "confirmed").length,
      suspected: all.filter((r) => r.certainty === "suspected").length,
      undecided: all.filter((r) => decisionText(r) === "Undecided").length,
      investigated: all.filter((r) => r.investigation !== null).length,
    };
  }, [rows]);

  const close = useCallback(() => setSelected(null), []);

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="mark" aria-hidden="true" />
          <div>
            <strong>ReckonAI</strong>
            <span className="tagline">Reconciliation for marketplaces on PayPal</span>
          </div>
        </div>
        <nav>
          {(
            [
              ["queue", "Exception queue"],
              ["coverage", "Coverage"],
              ["runs", "Runs"],
            ] as [View, string][]
          ).map(([id, text]) => (
            <button
              key={id}
              className={view === id ? "tab on" : "tab"}
              onClick={() => setView(id)}
            >
              {text}
            </button>
          ))}
          <button
            className="run-now"
            disabled={running}
            onClick={() => void runNow()}
          >
            {running ? "Running..." : "Run reconciliation"}
          </button>
        </nav>
      </header>

      <main className={selected && view === "queue" ? "page with-drawer" : "page"}>
        {error && <div className="banner bad">{error}</div>}
        {runNote && (
          <div className={`banner ${runNote.tone}`} role="status">
            {runNote.text}
            {!running && (
              <button className="dismiss" onClick={() => setRunNote(null)}>
                Dismiss
              </button>
            )}
          </div>
        )}

        {view === "queue" && (
          <section className="kpis" aria-label="Queue summary">
            <Kpi label="Open exceptions" value={stats.open} />
            <Kpi label="Confirmed" value={stats.confirmed} tone="bad" />
            <Kpi label="Suspected" value={stats.suspected} tone="warn" />
            <Kpi label="Awaiting a decision" value={stats.undecided} tone="accent" />
            <Kpi label="Investigated" value={stats.investigated} />
            <Kpi label="Reconciled clean" value={coverage ? coverage.clean : null} tone="good" />
          </section>
        )}

        {view === "runs" ? (
          <RunsView
            runs={runs}
            onOpen={(id) => {
              setRunId(id);
              setSelected(null);
              setView("queue");
            }}
          />
        ) : view === "coverage" ? (
          <CoverageView coverage={coverage} />
        ) : rows === null ? (
          <p className="muted">Loading...</p>
        ) : (
          <QueueView
            rows={rows}
            selectedId={selected}
            onSelect={setSelected}
            runFilterActive={runId !== undefined}
            runId={runId}
            investigating={investigating}
            onInvestigate={(ids, skip) => void investigate(ids, skip)}
            onClearRun={() => {
              setRunId(undefined);
              setSelected(null);
            }}
          />
        )}
      </main>

      {selected && view === "queue" && (
        <DetailDrawer
          key={selected}
          id={selected}
          onChanged={refreshAll}
          onClose={close}
        />
      )}
    </div>
  );
}
