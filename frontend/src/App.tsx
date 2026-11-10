import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ExceptionRow, Run } from "./api";
import { api } from "./api";
import { decisionText } from "./columns";
import DetailDrawer from "./DetailDrawer";
import QueueView from "./QueueView";
import RunsView from "./RunsView";

type View = "queue" | "runs";

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
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    api
      .runs()
      .then(setRuns)
      .catch((e) => setError(String(e)));
  }, []);

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
          <button
            className={view === "queue" ? "tab on" : "tab"}
            onClick={() => setView("queue")}
          >
            Exception queue
          </button>
          <button
            className={view === "runs" ? "tab on" : "tab"}
            onClick={() => setView("runs")}
          >
            Runs
          </button>
        </nav>
      </header>

      <main className={selected && view === "queue" ? "page with-drawer" : "page"}>
        {error && <div className="banner bad">{error}</div>}

        {view === "queue" && (
          <section className="kpis" aria-label="Queue summary">
            <Kpi label="Open exceptions" value={stats.open} />
            <Kpi label="Confirmed" value={stats.confirmed} tone="bad" />
            <Kpi label="Suspected" value={stats.suspected} tone="warn" />
            <Kpi label="Awaiting a decision" value={stats.undecided} tone="accent" />
            <Kpi label="Investigated" value={stats.investigated} />
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
        ) : rows === null ? (
          <p className="muted">Loading...</p>
        ) : (
          <QueueView
            rows={rows}
            selectedId={selected}
            onSelect={setSelected}
            runFilterActive={runId !== undefined}
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
          onChanged={refresh}
          onClose={close}
        />
      )}
    </div>
  );
}
