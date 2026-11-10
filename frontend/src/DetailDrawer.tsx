import { useCallback, useEffect, useRef, useState } from "react";
import type { ExceptionDetail } from "./api";
import { api } from "./api";
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

    return (
      <>
        <header className="drawer-head">
          <div>
            <span className={`pill ${detail.certainty}`}>
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
          <span className="hero-label">Impact</span>
          <span className="hero-value money">{formatMoney(detail.impact)}</span>
          <span className="hero-sub">
            Priority {formatFraction(detail.priority.score)}
          </span>
        </div>

        <p className="lead">{detail.detail}</p>

        <dl className="facts">
          <dt>Expected</dt>
          <dd className="money">
            {detail.expected ? formatMoney(detail.expected) : "not a comparison"}
          </dd>
          <dt>Reported</dt>
          <dd className="money">
            {detail.reported ? formatMoney(detail.reported) : "not a comparison"}
          </dd>
          <dt>Seller</dt>
          <dd>{detail.seller_name ?? `${detail.seller_count} sellers on the order`}</dd>
          <dt>Evidence basis</dt>
          <dd>{detail.evidence_basis.replace(/_/g, " ")}</dd>
          <dt>Priority factors</dt>
          <dd>
            {Object.entries(detail.priority.factors)
              .map(([k, v]) => `${k} ${formatFraction(v)}`)
              .join(", ")}
          </dd>
        </dl>

        <h3>Evidence records</h3>
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

        <h3>AI investigation</h3>
        {!inv && !inFlight && (
          <p className="muted">
            Not investigated yet.
            {detail.investigation_job?.status === "failed" && (
              <span className="note bad">
                {" "}
                Last attempt failed: {detail.investigation_job.last_error}
              </span>
            )}
          </p>
        )}
        {inFlight && (
          <p className="muted">Investigation {jobStatus}. This panel refreshes itself.</p>
        )}
        <button
          className="primary"
          disabled={busy || inFlight}
          onClick={() => void investigate()}
        >
          {inv ? "Investigate again" : "Investigate"}
        </button>

        {inv && (
          <div className="investigation">
            <p className="muted">
              {inv.provider} / {inv.model_requested},{" "}
              {formatDateTime(inv.started_at)}
              {inv.downgraded && " (downgraded)"}
            </p>
            {inv.verdict === "failed" && (
              <p className="note bad">
                The investigation failed and produced no report.
                {inv.summarized_reasoning ? ` ${inv.summarized_reasoning}` : ""}
              </p>
            )}
            {inv.verdict === "insufficient_evidence" && (
              <p className="callout">
                <strong>Insufficient evidence.</strong> The records available do
                not support a cause, and the agent declined to guess.
              </p>
            )}
            {inv.withheld.length > 0 && (
              <p className="note warn">
                Unavailable to the agent: {inv.withheld.join(", ")}. This is not
                the same as those records being empty.
              </p>
            )}
            {top && (
              <div className="callout accent">
                <strong>Recommended resolution</strong>
                <span className="muted"> (top-ranked cause)</span>
                <p>{top.description}</p>
              </div>
            )}
            <ol className="causes">
              {inv.causes.map((c) => (
                <li key={c.rank}>
                  <div>
                    {c.description}{" "}
                    <span className="muted">
                      confidence {formatFraction(c.confidence)}
                    </span>
                  </div>
                  <div className="refs">
                    {c.cited_refs.map((r) =>
                      c.unresolved_refs.includes(r) ? (
                        <code key={r} className="unresolved">
                          {r} (does not resolve)
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
                </li>
              ))}
            </ol>
            {inv.summarized_reasoning && inv.verdict !== "failed" && (
              <details>
                <summary>Summarised reasoning</summary>
                <p>{inv.summarized_reasoning}</p>
              </details>
            )}
          </div>
        )}

        <h3>Decision</h3>
        <p className="muted">
          Recording a decision moves no money. It is a note of intent; nothing is
          refunded, adjusted or paid out by this product.
        </p>
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
                <span className={`pill ${d.outcome}`}>{d.outcome}</span> by{" "}
                {d.approver}, {formatDateTime(d.decided_at)}
                {d.note ? ` - ${d.note}` : ""}
                {i === 0 ? " (current)" : ""}
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
