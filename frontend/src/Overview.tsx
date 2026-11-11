import type { CSSProperties, ReactNode } from "react";
import type { Summary, SummaryBucket } from "./api";
import { typeTitle } from "./copy";
import { formatMoney } from "./money";

// A card carries volume (the count) and value (the sum at risk, one line per
// currency). The sums come from the server: this file formats, it never adds.
// There is no grand total, because amounts in different currencies are never
// combined (specs/mission.md P3).

type Tone = "violet" | "red" | "amber" | "green" | "slate";

const Icon = ({ children }: { children: ReactNode }) => (
  <svg
    viewBox="0 0 24 24"
    width="20"
    height="20"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    {children}
  </svg>
);

const ICONS: Record<string, ReactNode> = {
  list: (
    <Icon>
      <path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01" />
    </Icon>
  ),
  alert: (
    <Icon>
      <path d="M12 3 2.5 20h19L12 3z" />
      <path d="M12 10v4M12 17h.01" />
    </Icon>
  ),
  help: (
    <Icon>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 1-1 1.7M12 17h.01" />
    </Icon>
  ),
  inbox: (
    <Icon>
      <path d="M4 13 6 5h12l2 8v6H4v-6zM4 13h5l1 3h4l1-3h5" />
    </Icon>
  ),
  spark: (
    <Icon>
      <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3z" />
    </Icon>
  ),
  check: (
    <Icon>
      <circle cx="12" cy="12" r="9" />
      <path d="m8 12.5 2.8 2.8L16 9.5" />
    </Icon>
  ),
};

function Card(props: {
  label: string;
  count: number | null;
  unit: string;
  bucket?: SummaryBucket;
  tone: Tone;
  icon: string;
  note?: string;
}) {
  const value = props.bucket?.at_risk ?? [];
  return (
    <div className={`kcard ${props.tone}`}>
      <span className="kchip">{ICONS[props.icon]}</span>
      <div className="kbody">
        <span className="klabel">{props.label}</span>
        <span className="kcount">
          {props.count ?? "-"} <small>{props.unit}</small>
        </span>
        {props.bucket !== undefined && (
          <span className="kvalue">
            {value.length === 0 ? (
              <span className="muted">Nothing at risk</span>
            ) : (
              value.map((m) => (
                <span key={m.currency} className="money">
                  {formatMoney(m)}
                </span>
              ))
            )}
          </span>
        )}
        {props.note && <span className="knote">{props.note}</span>}
      </div>
    </div>
  );
}

export function KpiCards(props: {
  summary: Summary | null;
  investigated: number | null;
  clean: number | null;
}) {
  const s = props.summary;
  return (
    <section className="kcards" aria-label="Queue summary">
      <Card label="Open exceptions" count={s?.open.count ?? null} unit="issues"
        bucket={s?.open} tone="violet" icon="list" />
      <Card label="Confirmed" count={s?.confirmed.count ?? null} unit="issues"
        bucket={s?.confirmed} tone="red" icon="alert" />
      <Card label="Suspected" count={s?.suspected.count ?? null} unit="issues"
        bucket={s?.suspected} tone="amber" icon="help" />
      <Card label="Awaiting a decision" count={s?.undecided.count ?? null} unit="issues"
        bucket={s?.undecided} tone="violet" icon="inbox" />
      <Card label="Looked into by AI" count={props.investigated} unit="issues"
        tone="slate" icon="spark" note="of the open exceptions" />
      <Card label="Checked, no issues" count={props.clean} unit="orders"
        tone="green" icon="check" note="no finding on the newest check" />
    </section>
  );
}

// Bar length is each issue type's value at risk against the largest type in
// the same currency, as computed by the server; the browser only draws it.
export function TypePanel(props: {
  summary: Summary | null;
  active: string | null;
  onPick: (code: string | null) => void;
}) {
  const types = props.summary?.by_type ?? [];
  if (types.length === 0) return null;
  return (
    <section className="card typepanel" aria-label="Value at risk by issue">
      <div className="card-head">
        <h2>Where the value is</h2>
        <p className="muted">
          Bar length is value at risk, in each currency on its own. Click an
          issue to filter the list below.
        </p>
      </div>
      <ul>
        {types.map((t) => (
          <li key={t.code}>
            <button
              type="button"
              className={props.active === t.code ? "trow on" : "trow"}
              aria-pressed={props.active === t.code}
              onClick={() => props.onPick(props.active === t.code ? null : t.code)}
            >
              <span className="tname">{typeTitle(t.code)}</span>
              <span className="tcount">
                {t.count} {t.count === 1 ? "issue" : "issues"}
              </span>
              <span className="tbars">
                {t.at_risk.map((m) => (
                  <span key={m.currency} className="tbar">
                    <span className="track">
                      <span
                        className="fill"
                        style={{ "--s": t.shares[m.currency] ?? "0" } as CSSProperties}
                      />
                    </span>
                    <span className="money">{formatMoney(m)}</span>
                  </span>
                ))}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
