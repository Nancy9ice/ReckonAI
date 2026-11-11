import { useCallback, useEffect, useRef, useState } from "react";
import type { ExceptionDetail } from "./api";
import { api } from "./api";
import {
  CERTAINTY_HINT,
  DECISION_EXPLAINED,
  FACTOR_NAMES,
  NO_CAUSE_EXPLAINED,
  PRIORITY_HINT,
  basisText,
  typeCopy,
} from "./copy";
import EvidenceView from "./EvidenceView";
import { formatDateTime, label } from "./labels";
import { formatFraction, formatMoney } from "./money";

interface Props {
  id: string;
  onChanged: () => void;
  onClose: () => void;
}

const APPROVER_KEY = "reckonai.approver";

function readApprover(): string {
  try {
    return localStorage.getItem(APPROVER_KEY) ?? "";
  } catch {
    return "";
  }
}

export default function DetailDrawer({ id, onChanged, onClose }: Props) {
  const [detail, setDetail] = useState<ExceptionDetail | null>(null);
  // A failed load replaces the panel; a failed action sits beside the form, so
  // the form stays usable and the loaded detail is not lost.
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [approver, setApprover] = useState(readApprover);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [openRef, setOpenRef] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setDetail(await api.exception(id));
      setLoadError(null);
    } catch (e) {
      setLoadError(String(e));
    }
  }, [id]);

  useEffect(() => {
    setDetail(null);
    void load();
  }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Poll while an investigation job is in flight; stop when it settles.
  const jobStatus = detail?.investigation_job?.status;
  const inFlight = jobStatus === "queued" || jobStatus === "claimed";
  useEffect(() => {
    if (!inFlight) return;
    const timer = setInterval(() => {
      void load();
    }, 2000);
    return () => clearInterval(timer);
  }, [inFlight, load]);

  // Refresh the queue exactly once when a job settles, not on every poll.
  const wasInFlight = useRef(false);
  useEffect(() => {
    if (wasInFlight.current && !inFlight) onChanged();
    wasInFlight.current = inFlight;
  }, [inFlight, onChanged]);

  const investigate = async () => {
    setBusy(true);
    try {
      await api.investigate(id);
      setActionError(null);
      await load();
    } catch (e) {
      setActionError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const decide = async (outcome: "approved" | "rejected") => {
    if (!approver.trim()) {
      setActionError("Enter your name before deciding.");
      return;
    }
    setBusy(true);
    try {
      try {
        localStorage.setItem(APPROVER_KEY, approver.trim());
      } catch {
        /* a blocked store only loses the remembered name */
      }
      setDetail(
        await api.decide(id, { outcome, approver, note: note || undefined }),
      );
      setNote("");
      setActionError(null);
      onChanged();
    } catch (e) {
      setActionError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const body = () => {
    if (!detail) {
      return loadError ? (
        <p className="note bad">{loadError}</p>
      ) : (
        <p className="muted">Loading...</p>
      );
    }
    const inv = detail.investigation_detail;
    const top = inv?.causes.find((c) => c.rank === 1);
    // The top cause is shown once, in the "Probable cause" box with its
    // citations; the list below it is only the alternatives.
    const others = inv?.causes.filter((c) => c !== top) ?? [];
    const refs = (c: { cited_refs: string[]; unresolved_refs: string[] }) => (
      <div className="refs">
        {c.cited_refs.map((r) =>
          c.unresolved_refs.includes(r) ? (
            <code key={r} className="unresolved">
              <span title="The AI cited an id that is not in the records, so do not rely on it.">
                {r} (not found in the records)
              </span>
            </code>
          ) : (
            <button
              key={r}
              className="ref"
              title="Open this record"
              onClick={() => setOpenRef(r)}
            >
              {r}
            </button>
          ),
        )}
      </div>
    );
    const copy = typeCopy(detail.code);

    return (
      <>
        <header className="drawer-head">
          <div>
            <span
              className={`pill ${detail.certainty}`}
              title={CERTAINTY_HINT[detail.certainty]}
            >
              {detail.certainty === "confirmed"
                ? "Confirmed discrepancy"
                : "Suspected issue"}
            </span>
            <h2>{label(detail.code)}</h2>
            <p className="muted mono">{detail.order_ref}</p>
          </div>
          <button className="icon" aria-label="Close" onClick={onClose}>
            Close
          </button>
        </header>

        <div className="hero">
          <span className="hero-label">Difference</span>
          <span className="hero-value money">{formatMoney(detail.impact)}</span>
          <span className="hero-sub" title={PRIORITY_HINT}>
            Priority {formatFraction(detail.priority.score)} (how urgent)
          </span>
        </div>

        <div className="explain">
          <p className="explain-title">What this means</p>
          <p>{copy.meaning}</p>
          <p className="explain-title">What to do</p>
          <p>{copy.action}</p>
        </div>

        <h3>What the system found</h3>
        <p className="lead">{detail.detail}</p>

        <dl className="facts">
          {detail.expected && (
            <>
              <dt>{copy.expectedLabel ?? "Expected"}</dt>
              <dd className="money">{formatMoney(detail.expected)}</dd>
            </>
          )}
          {detail.reported && (
            <>
              <dt>{copy.reportedLabel ?? "Recorded"}</dt>
              <dd className="money">{formatMoney(detail.reported)}</dd>
            </>
          )}
          <dt>Seller</dt>
          <dd>{detail.seller_name ?? `${detail.seller_count} sellers on the order`}</dd>
          <dt>How well it is backed</dt>
          <dd>{basisText(detail.evidence_basis)}</dd>
          <dt title={PRIORITY_HINT}>What drives the priority</dt>
          <dd>
            {Object.entries(detail.priority.factors)
              .map(([k, v]) =>
                v === null
                  ? `${FACTOR_NAMES[k] ?? k}: not investigated yet`
                  : `${FACTOR_NAMES[k] ?? k} ${formatFraction(v)}`,
              )
              .join(", ")}
          </dd>
        </dl>

        <h3>Records behind this finding</h3>
        {detail.evidence_refs.length === 0 ? (
          <p className="muted">The finding names no records.</p>
        ) : (
          <div className="refs">
            {detail.evidence_refs.map((r) => (
              <button
                key={r}
                className="ref"
                title="Open this record"
                onClick={() => setOpenRef(r)}
              >
                {r}
              </button>
            ))}
          </div>
        )}

        <p className="muted hint">Select a record to open it.</p>

        <section className="aicard">
        <h3>
          <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true">
            <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3z" />
          </svg>
          AI investigation
        </h3>
        {!inv && !inFlight && (
          <p className="muted">
            The AI has not looked at this yet.
            {detail.investigation_job?.status === "failed" && (
              <span className="note bad">
                {" "}
                The last attempt failed: {detail.investigation_job.last_error}
              </span>
            )}
          </p>
        )}
        {inFlight && (
          <p className="muted">
            {jobStatus === "queued"
              ? "Waiting for the worker to start."
              : "The AI is working on it."}{" "}
            This panel updates by itself.
          </p>
        )}
        <button
          className="primary"
          disabled={busy || inFlight}
          onClick={() => void investigate()}
        >
          {inv ? "Ask the AI again" : "Ask the AI to investigate"}
        </button>

        {inv && (
          <div className="investigation">
            <p className="muted">
              Looked into on {formatDateTime(inv.started_at)} (model:{" "}
              {inv.model_requested})
              {inv.downgraded && ", on a fallback model"}
            </p>
            {inv.verdict === "failed" && (
              <p className="note bad">
                The investigation could not finish, so there is no answer.
                {inv.summarized_reasoning ? ` ${inv.summarized_reasoning}` : ""}
              </p>
            )}
            {inv.verdict === "insufficient_evidence" && (
              <p className="callout">
                <strong>No clear cause.</strong> {NO_CAUSE_EXPLAINED}
              </p>
            )}
            {inv.withheld.length > 0 && (
              <p className="note warn">
                The AI was not allowed to see: {inv.withheld.join(", ")}. Its
                answer does not cover them, and that is not the same as there
                being nothing there.
              </p>
            )}
            {top && (
              <div className="callout accent">
                <div className="callout-head">
                  <strong>Probable cause</strong>
                  <span className="conf" title="The AI's own stated confidence, not a measured accuracy.">
                    {formatFraction(top.confidence)} confidence
                  </span>
                </div>
                <p>{top.description}</p>
                {refs(top)}
              </div>
            )}
            {others.length > 0 && (
              <>
                {top && <p className="muted">Other possible causes</p>}
                <ol className="causes">
                  {others.map((c) => (
                    <li key={c.rank}>
                      <div>
                        {c.description}{" "}
                        <span className="muted">
                          ({formatFraction(c.confidence)} sure)
                        </span>
                      </div>
                      {refs(c)}
                    </li>
                  ))}
                </ol>
              </>
            )}
            {inv.summarized_reasoning && inv.verdict !== "failed" && (
              <details>
                <summary>How the AI reasoned</summary>
                <p>{inv.summarized_reasoning}</p>
              </details>
            )}
          </div>
        )}
        </section>

        <h3>Your decision</h3>
        <p className="muted">{DECISION_EXPLAINED}</p>
        <div className="decide">
          <input
            aria-label="Your name"
            placeholder="Your name"
            value={approver}
            onChange={(e) => setApprover(e.target.value)}
          />
          <input
            aria-label="Note"
            placeholder="Note (optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <div className="decide-buttons">
            <button
              className="approve"
              disabled={busy}
              onClick={() => void decide("approved")}
            >
              Approve
            </button>
            <button
              className="reject"
              disabled={busy}
              onClick={() => void decide("rejected")}
            >
              Reject
            </button>
          </div>
        </div>
        {(actionError || loadError) && (
          <p className="note bad">{actionError ?? loadError}</p>
        )}
        {detail.decisions.length > 0 && (
          <ul className="history">
            {detail.decisions.map((d, i) => (
              <li key={d.id}>
                <span className={`pill ${d.outcome}`}>
                  {d.outcome === "approved" ? "Approved" : "Rejected"}
                </span>{" "}
                by {d.approver}, {formatDateTime(d.decided_at)}
                {d.note ? ` - ${d.note}` : ""}
                {i === 0 ? " (latest)" : ""}
              </li>
            ))}
          </ul>
        )}
      </>
    );
  };

  return (
    <aside className="drawer" aria-label="Exception detail">
      {openRef ? (
        <EvidenceView refId={openRef} onBack={() => setOpenRef(null)} />
      ) : (
        body()
      )}
    </aside>
  );
}
