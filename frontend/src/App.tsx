import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Coverage, ExceptionRow, Run } from "./api";
import { api } from "./api";
import { decisionText } from "./columns";
import CoverageView from "./CoverageView";
import DetailDrawer from "./DetailDrawer";
import QueueView from "./QueueView";
import RunsView from "./RunsView";

type View = "queue" | "coverage" | "runs";

type RunNote = { tone: "ok" | "bad" | "busy"; text: string } | null;

function Kpi(props: { label: string; value: number; tone?: string }) {
  return (
    <div className={`kpi ${props.tone ?? ""}`}>
      <span className="kpi-value">{props.value}</span>
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
        setRunNote({
          tone: result.status === "already_done" ? "ok" : "bad",
          text: result.message,
        });
        return;
      }
      let job = result.job;
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
        waited += 2;
        job = await api.job(job.id);
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
            <Kpi label="Reconciled clean" value={coverage?.clean ?? 0} tone="good" />
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
