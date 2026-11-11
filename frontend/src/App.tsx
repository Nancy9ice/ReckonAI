import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Coverage, ExceptionRow, Run, Summary } from "./api";
import { api } from "./api";
import { HINTS, JUDGE } from "./copy";
import CoverageView from "./CoverageView";
import DetailDrawer from "./DetailDrawer";
import JudgeScenarios from "./JudgeScenarios";
import { KpiCards, TypePanel } from "./Overview";
import QueueView from "./QueueView";
import RunsView from "./RunsView";

type View = "queue" | "coverage" | "runs";

type RunNote = { tone: "ok" | "bad" | "busy" | "info"; text: string } | null;

const NAV: { id: View; text: string; title: string; sub: string; icon: string }[] = [
  {
    id: "queue",
    text: "Exceptions",
    title: "Financial Reconciliation",
    sub: "Marketplace records checked against PayPal, by volume and by value.",
    icon: "M4 6h16M4 12h16M4 18h10",
  },
  {
    id: "coverage",
    text: "Coverage",
    title: "Coverage",
    sub: "What the checks have and have not looked at.",
    icon: "M12 3a9 9 0 1 0 9 9M12 3v9l6 4",
  },
  {
    id: "runs",
    text: "Past checks",
    title: "Past checks",
    sub: "Every reconciliation run, newest first.",
    icon: "M12 7v5l3 2M21 12a9 9 0 1 1-3-6.7M21 4v5h-5",
  },
];

export default function App() {
  const [view, setView] = useState<View>("queue");
  const [rows, setRows] = useState<ExceptionRow[] | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [typeFilter, setTypeFilter] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runNote, setRunNote] = useState<RunNote>(null);
  const [running, setRunning] = useState(false);
  const [investigating, setInvestigating] = useState(false);
  // The test-transactions panel exists only where the operator has switched the
  // feature on; a failed probe hides it rather than showing a broken button.
  const [judgeOn, setJudgeOn] = useState(false);
  const [judgeOpen, setJudgeOpen] = useState(false);
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
      // Separate requests: the summary failing must not freeze the queue.
      const summaryRequest = api.summary(runId).then(
        (sums) => ({ sums }),
        (e: unknown) => ({ failure: e }),
      );
      const fetched = await api.exceptions(runId);
      const outcome = await summaryRequest;
      if (ticket === latest.current) {
        setRows(fetched);
        if ("sums" in outcome) setSummary(outcome.sums);
        else {
          setSummary(null);
          setError(`The summary could not be loaded: ${String(outcome.failure)}`);
          return;
        }
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

  // An issue filter that no longer matches anything in the summary is dropped,
  // so a different run or a cleared fault cannot leave an empty grid behind it.
  useEffect(() => {
    if (
      typeFilter !== null &&
      summary !== null &&
      !summary.by_type.some((t) => t.code === typeFilter)
    )
      setTypeFilter(null);
  }, [summary, typeFilter]);

  useEffect(() => {
    api.runs().then(setRuns).catch((e) => setError(String(e)));
    api.coverage().then(setCoverage).catch((e) => setError(String(e)));
    api.judgeEnabled().then(setJudgeOn).catch(() => setJudgeOn(false));
  }, []);

  // Ask for a run, then follow its job until it settles. The window is
  // date-grained by design, so a second request the same day is answered rather
  // than queued; that answer is shown as given.
  const runNow = async () => {
    setRunning(true);
    setRunNote({ tone: "busy", text: "Starting a check of your orders..." });
    try {
      const result = await api.requestRun();
      if (!result.job) {
        // A refused request is information, not success, and not a failure: a
        // green banner would read as "up to date" and a red one as broken.
        setRunNote({
          tone: ["already_done", "nothing_new", "cooling_down"].includes(
            result.status,
          )
            ? "info"
            : "bad",
          text: result.message,
        });
        return;
      }
      let job = result.job;
      let misses = 0;
      setRunNote({ tone: "busy", text: "Check queued. Waiting for the background worker..." });
      for (let waited = 0; job.status === "queued" || job.status === "claimed"; ) {
        if (waited >= 120) {
          setRunNote({
            tone: "bad",
            text: "The check is still waiting to start. Make sure the background worker is running.",
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
          setRunNote({ tone: "busy", text: "Checking your orders..." });
      }
      if (job.status === "done") {
        await refreshAll();
        setRunNote({ tone: "ok", text: "Check complete. The queue is up to date." });
      } else {
        setRunNote({
          tone: "bad",
          text: `The check failed: ${job.last_error ?? "no reason recorded"}`,
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
    setRunNote({ tone: "busy", text: "Asking the AI to investigate..." });
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
            text: `Still waiting on ${pending.length} investigations. Make sure the background worker is running.`,
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
              ? `Finished, but ${failed} could not run. Open one to see why.`
              : "All finished."),
      });
    } catch (e) {
      setRunNote({ tone: "bad", text: String(e) });
    } finally {
      setInvestigating(false);
    }
  };

  const investigated = useMemo(
    () => (rows === null ? null : rows.filter((r) => r.investigation !== null).length),
    [rows],
  );

  const close = useCallback(() => setSelected(null), []);

  const current = NAV.find((n) => n.id === view) ?? NAV[0];

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="mark" aria-hidden="true">R</span>
          <strong>ReckonAI</strong>
        </div>
        <nav aria-label="Sections">
          {NAV.map((n) => (
            <button
              key={n.id}
              className={view === n.id ? "navitem on" : "navitem"}
              aria-current={view === n.id ? "page" : undefined}
              onClick={() => setView(n.id)}
            >
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none"
                stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
                strokeLinejoin="round" aria-hidden="true">
                <path d={n.icon} />
              </svg>
              {n.text}
            </button>
          ))}
        </nav>
        <p className="sidefoot">Reconciliation for marketplaces on PayPal</p>
      </aside>

      <div className="content">
        <header className="pagehead">
          <div>
            <h1>{current.title}</h1>
            <p className="muted">{current.sub}</p>
          </div>
          <div className="head-actions">
          {judgeOn && (
            <button
              className="secondary"
              title={JUDGE.buttonHint}
              onClick={() => setJudgeOpen((open) => !open)}
            >
              {JUDGE.button}
            </button>
          )}
          <button
            className="run-now"
            title={HINTS.runNow}
            disabled={running}
            onClick={() => void runNow()}
          >
            <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true">
              <path d="M7 4.5v15l12-7.5z" />
            </svg>
            {running ? "Checking..." : "Run reconciliation"}
          </button>
          </div>
        </header>

        <main className={selected && view === "queue" ? "page with-drawer" : "page"}>
          {judgeOn && judgeOpen && (
            <JudgeScenarios onClose={() => setJudgeOpen(false)} />
          )}
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
            <>
              <KpiCards
                summary={summary}
                investigated={investigated}
                clean={coverage ? coverage.clean : null}
              />
              <TypePanel summary={summary} active={typeFilter} onPick={setTypeFilter} />
            </>
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
              onDeselect={close}
              runFilterActive={runId !== undefined}
              runId={runId}
              typeFilter={typeFilter}
              onClearType={() => setTypeFilter(null)}
              investigating={investigating}
              onInvestigate={(ids, skip) => void investigate(ids, skip)}
              onClearRun={() => {
                setRunId(undefined);
                setSelected(null);
              }}
            />
          )}
        </main>
      </div>

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
