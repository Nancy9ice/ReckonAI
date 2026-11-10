import { useEffect, useState } from "react";
import type { Evidence } from "./api";
import { api } from "./api";
import { fieldLabel, fieldValue, sourceName } from "./copy";
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
              This reference is real, but it changes every time the data is
              reloaded, so it will not find this record again later. The payment
              or refund it belongs to has a lasting reference.
            </p>
          )}

          <dl className="facts evidence">
            {record.fields.map((f) => (
              <div className="fact-row" key={f.label}>
                <dt>{fieldLabel(f.label)}</dt>
                <dd className={f.money ? "money" : ""}>
                  {f.money ? (
                    formatMoney(f.money)
                  ) : f.value !== null ? (
                    fieldValue(f.value)
                  ) : (
                    <span
                      className="muted"
                      title="The record does not state this. That is different from zero."
                    >
                      not stated
                    </span>
                  )}
                </dd>
              </div>
            ))}
          </dl>

          {record.raw && (
            <>
              <h3>The original record</h3>
              <p className="muted">
                Kept exactly as {sourceName(record.raw.source)} sent it on{" "}
                {formatDateTime(record.raw.retrieved_at)}, so you can check this
                page against the source.
              </p>
              <details className="tech">
                <summary>Technical details</summary>
                <p className="muted mono sha">
                  {record.raw.record_type}
                  {record.raw.endpoint ? ` from ${record.raw.endpoint}` : ""}
                  <br />
                  fingerprint {record.raw.sha256}
                </p>
              </details>
              {raw === null ? (
                <button onClick={() => void showRaw()}>
                  Show the original data
                </button>
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
