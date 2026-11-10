import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ExceptionRow, Run } from "./api";
import { api } from "./api";
import Detail from "./Detail";
import QueueGrid, { decisionText, groupValue } from "./QueueGrid";
import type { GroupKey } from "./QueueGrid";
import RunsGrid from "./RunsGrid";
import { label } from "./labels";

type View = "queue" | "runs";

export default function App() {
  const [view, setView] = useState<View>("queue");
  const [rows, setRows] = useState<ExceptionRow[] | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [groupBy, setGroupBy] = useState<GroupKey>("none");
  const [text, setText] = useState("");
  const [certainty, setCertainty] = useState("all");
  const [decision, setDecision] = useState("all");

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
    api.runs().then(setRuns).catch((e) => setError(String(e)));
  }, []);

  const filtered = useMemo(() => {
    const needle = text.trim().toLowerCase();
    return (rows ?? []).filter(
      (r) =>
        (certainty === "all" || r.certainty === certainty) &&
        (decision === "all" || decisionText(r) === decision) &&
        (needle === "" ||
          `${r.order_ref} ${label(r.code)} ${r.seller_name ?? ""} ${r.detail}`
            .toLowerCase()
            .includes(needle)),
    );
  }, [rows, text, certainty, decision]);

  const groups = useMemo(() => {
    const map = new Map<string, ExceptionRow[]>();
    for (const r of filtered) {
      const key = groupValue(r, groupBy);
      map.set(key, [...(map.get(key) ?? []), r]);
    }
    return [...map.entries()];
  }, [filtered, groupBy]);

  return (
    <main className="page">
      <header className="top">
        <h1>ReckonAI</h1>
        <nav>
          <button className={view === "queue" ? "tab on" : "tab"} onClick={() => setView("queue")}>
            Exception queue
          </button>
          <button className={view === "runs" ? "tab on" : "tab"} onClick={() => setView("runs")}>
            Runs
          </button>
        </nav>
      </header>

      {error && <div className="panel error">{error}</div>}

      {view === "runs" ? (
        <RunsGrid
          runs={runs}
          onOpen={(id) => {
            setRunId(id);
            setSelected(null);
            setView("queue");
          }}
        />
      ) : (
        <>
          <div className="controls">
            <label>
              Search
              <input value={text} onChange={(e) => setText(e.target.value)} placeholder="order, type, seller" />
            </label>
            <label>
              Status
              <select value={certainty} onChange={(e) => setCertainty(e.target.value)}>
                <option value="all">All</option>
                <option value="confirmed">Confirmed</option>
                <option value="suspected">Suspected</option>
              </select>
            </label>
            <label>
              Decision
              <select value={decision} onChange={(e) => setDecision(e.target.value)}>
                <option value="all">All</option>
                <option value="Undecided">Undecided</option>
                <option value="Approved">Approved</option>
                <option value="Rejected">Rejected</option>
              </select>
            </label>
            <label>
              Group by
              <select value={groupBy} onChange={(e) => setGroupBy(e.target.value as GroupKey)}>
                <option value="none">None</option>
                <option value="code">Type</option>
                <option value="seller">Seller</option>
                <option value="certainty">Confirmed or suspected</option>
                <option value="decision">Decision</option>
              </select>
            </label>
            {runId && (
              <button
                onClick={() => {
                  setRunId(undefined);
                  setSelected(null);
                }}
              >
                Showing one run - show all
              </button>
            )}
          </div>

          {rows === null ? (
            <p className="muted">Loading...</p>
          ) : filtered.length === 0 ? (
            <p className="muted">No exceptions match.</p>
          ) : (
            groups.map(([title, groupRows]) => (
              <QueueGrid
                key={title}
                title={title}
                rows={groupRows}
                selectedId={selected}
                onSelect={setSelected}
              />
            ))
          )}

          {selected && <Detail key={selected} id={selected} onChanged={refresh} />}
        </>
      )}
    </main>
  );
}
