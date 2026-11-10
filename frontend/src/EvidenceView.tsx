import { useEffect, useState } from "react";
import type { Evidence } from "./api";
import { api } from "./api";
import { formatDateTime } from "./labels";
import { formatMoney } from "./money";

interface Props {
  refId: string;
  onBack: () => void;
}

const KIND_LABELS: Record<string, string> = {
  order: "Order",
  order_item: "Order line",
  payment: "Payment",
  capture: "Capture",
  refund: "Refund",
  seller_ledger_entry: "Seller ledger entry",
  disbursement: "Disbursement",
  platform_fee: "Platform fee",
};

// Opens one cited reference as the record the product holds. An absent value is
// shown as "not stated", never as zero: a capture that states no PayPal fee is a
// different claim from one that states a fee of nothing.
export default function EvidenceView({ refId, onBack }: Props) {
  const [record, setRecord] = useState<Evidence | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [raw, setRaw] = useState<Record<string, unknown> | null>(null);
  const [rawError, setRawError] = useState<string | null>(null);

  useEffect(() => {
    setRecord(null);
    setRaw(null);
    setError(null);
    api
      .evidence(refId)
      .then(setRecord)
      .catch((e) => setError(String(e)));
  }, [refId]);

  const showRaw = async () => {
    try {
      setRaw(await api.evidenceRaw(refId));
      setRawError(null);
    } catch (e) {
      setRawError(String(e));
    }
  };

  return (
    <>
      <header className="drawer-head">
        <div>
          <button className="back" onClick={onBack}>
            Back to the exception
          </button>
          <h2>{record ? (KIND_LABELS[record.kind] ?? record.kind) : "Record"}</h2>
          <p className="muted mono">{refId}</p>
        </div>
      </header>

      {error && <p className="note bad">{error}</p>}
      {!record && !error && <p className="muted">Loading...</p>}

      {record && (
        <>
          {!record.stable && (
            <p className="callout">
              This id names a real record but is rebuilt every time the data is
              reseeded, so it will not find the record again. A stable reference
              is the capture or refund it belongs to.
            </p>
          )}

          <dl className="facts evidence">
            {record.fields.map((f) => (
              <div className="fact-row" key={f.label}>
                <dt>{f.label}</dt>
                <dd className={f.money ? "money" : ""}>
                  {f.money ? (
                    formatMoney(f.money)
                  ) : f.value !== null ? (
                    f.value
                  ) : (
                    <span className="muted">not stated</span>
                  )}
                </dd>
              </div>
            ))}
          </dl>

          {record.raw && (
            <>
              <h3>Provider record</h3>
              <p className="muted">
                Retained exactly as received: {record.raw.source},{" "}
                {record.raw.record_type}, {formatDateTime(record.raw.retrieved_at)}
                {record.raw.endpoint ? `, ${record.raw.endpoint}` : ""}.
              </p>
              <p className="muted mono sha">sha256 {record.raw.sha256}</p>
              {raw === null ? (
                <button onClick={() => void showRaw()}>Show the raw response</button>
              ) : (
                <pre className="raw">{JSON.stringify(raw, null, 2)}</pre>
              )}
              {rawError && <p className="note bad">{rawError}</p>}
            </>
          )}
        </>
      )}
    </>
  );
}
