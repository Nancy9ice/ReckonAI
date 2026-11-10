import { useCallback, useEffect, useState } from "react";
import type { ExceptionDetail } from "./api";
import { api } from "./api";
import { formatDateTime, label } from "./labels";
import { formatFraction, formatMoney } from "./money";

interface Props {
  id: string;
  onChanged: () => void;
}

const APPROVER_KEY = "reckonai.approver";

function readApprover(): string {
  try {
    return localStorage.getItem(APPROVER_KEY) ?? "";
  } catch {
    return "";
  }
}

export default function Detail({ id, onChanged }: Props) {
  const [detail, setDetail] = useState<ExceptionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [approver, setApprover] = useState(readApprover);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setDetail(await api.exception(id));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [id]);

  useEffect(() => {
    setDetail(null);
    void load();
  }, [load]);

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

  const wasInFlight = useInFlightEdge(inFlight);
  useEffect(() => {
    if (wasInFlight) onChanged();
  }, [wasInFlight, onChanged]);

  if (error) return <div className="panel error">{error}</div>;
  if (!detail) return <div className="panel muted">Loading...</div>;

  const inv = detail.investigation_detail;
  const top = inv?.causes.find((c) => c.rank === 1);

  const investigate = async () => {
    setBusy(true);
    try {
      await api.investigate(id);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const decide = async (outcome: "approved" | "rejected") => {
    if (!approver.trim()) {
      setError("Enter your name before deciding.");
      return;
    }
    setBusy(true);
    try {
      try {
        localStorage.setItem(APPROVER_KEY, approver.trim());
      } catch {
        /* a blocked store only loses the remembered name */
      }
      setDetail(await api.decide(id, { outcome, approver, note: note || undefined }));
      setNote("");
      setError(null);
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel detail">
      <header>
        <h2>
          {label(detail.code)} <span className="muted">on {detail.order_ref}</span>
        </h2>
        <span className={`badge ${detail.certainty}`}>
          {detail.certainty === "confirmed" ? "Confirmed discrepancy" : "Suspected issue"}
        </span>
      </header>

      <p>{detail.detail}</p>

      <dl className="facts">
        <dt>Impact</dt>
        <dd>{formatMoney(detail.impact)}</dd>
        <dt>Expected</dt>
        <dd>{detail.expected ? formatMoney(detail.expected) : "not a comparison"}</dd>
        <dt>Reported</dt>
        <dd>{detail.reported ? formatMoney(detail.reported) : "not a comparison"}</dd>
        <dt>Seller</dt>
        <dd>{detail.seller_name ?? `${detail.seller_count} sellers on the order`}</dd>
        <dt>Priority</dt>
        <dd>
          {formatFraction(detail.priority.score)}{" "}
          <span className="muted">
            ({Object.entries(detail.priority.factors)
              .map(([k, v]) => `${k} ${formatFraction(v)}`)
              .join(", ")})
          </span>
        </dd>
        <dt>Evidence basis</dt>
        <dd>{detail.evidence_basis.replace(/_/g, " ")}</dd>
      </dl>

      <h3>Evidence records</h3>
      {detail.evidence_refs.length === 0 ? (
        <p className="muted">The finding names no records.</p>
      ) : (
        <ul className="refs">
          {detail.evidence_refs.map((r) => (
            <li key={r}><code>{r}</code></li>
          ))}
        </ul>
      )}

      <h3>Investigation</h3>
      {!inv && !inFlight && (
        <p className="muted">
          Not investigated yet.
          {detail.investigation_job?.status === "failed" && (
            <span className="error">
              {" "}Last attempt failed: {detail.investigation_job.last_error}
            </span>
          )}
        </p>
      )}
      {inFlight && <p className="muted">Investigation {jobStatus}; this page refreshes itself.</p>}
      <button disabled={busy || inFlight} onClick={() => void investigate()}>
        {inv ? "Investigate again" : "Investigate"}
      </button>

      {inv && (
        <div className="investigation">
          <p className="muted">
            {inv.provider} / {inv.model_requested}, {formatDateTime(inv.started_at)}
            {inv.downgraded && " (downgraded)"}
          </p>
          {inv.verdict === "failed" && (
            <p className="error">The investigation failed and produced no report.</p>
          )}
          {inv.verdict === "insufficient_evidence" && (
            <p>
              <strong>Insufficient evidence.</strong> The records available do not support a
              cause, and the agent declined to guess.
            </p>
          )}
          {inv.withheld.length > 0 && (
            <p className="warn">
              Unavailable to the agent: {inv.withheld.join(", ")}. This is not the same as
              those records being empty.
            </p>
          )}
          {top && (
            <div className="recommendation">
              <strong>Recommended resolution (top-ranked cause)</strong>
              <p>{top.description}</p>
            </div>
          )}
          <ol className="causes">
            {inv.causes.map((c) => (
              <li key={c.rank}>
                <div>
                  {c.description}{" "}
                  <span className="muted">confidence {formatFraction(c.confidence)}</span>
                </div>
                <div className="cites">
                  {c.cited_refs.map((r) => (
                    <code key={r} className={c.unresolved_refs.includes(r) ? "unresolved" : ""}>
                      {r}
                      {c.unresolved_refs.includes(r) ? " (does not resolve)" : ""}
                    </code>
                  ))}
                </div>
              </li>
            ))}
          </ol>
          {inv.summarized_reasoning && (
            <details>
              <summary>Summarised reasoning</summary>
              <p>{inv.summarized_reasoning}</p>
            </details>
          )}
        </div>
      )}

      <h3>Decision</h3>
      <p className="muted">
        Recording a decision moves no money. It is a note of intent; nothing is refunded,
        adjusted or paid out by this product.
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
        <button className="approve" disabled={busy} onClick={() => void decide("approved")}>
          Approve
        </button>
        <button className="reject" disabled={busy} onClick={() => void decide("rejected")}>
          Reject
        </button>
      </div>
      {detail.decisions.length > 0 && (
        <ul className="history">
          {detail.decisions.map((d, i) => (
            <li key={d.id}>
              <span className={`badge ${d.outcome}`}>{d.outcome}</span> by {d.approver},{" "}
              {formatDateTime(d.decided_at)}
              {d.note ? ` - ${d.note}` : ""}
              {i === 0 ? " (current)" : ""}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// True for exactly one render after an in-flight job settles, so the parent
// refreshes the queue once rather than on every poll.
function useInFlightEdge(inFlight: boolean): boolean {
  const [prev, setPrev] = useState(inFlight);
  const [edge, setEdge] = useState(false);
  useEffect(() => {
    setEdge(prev && !inFlight);
    setPrev(inFlight);
  }, [inFlight, prev]);
  return edge;
}
