import type { ColDef, ICellRendererParams } from "ag-grid-community";
import type { ExceptionRow } from "./api";
import { formatDate, label } from "./labels";
import {
  CERTAINTY_HINT,
  DAYS_OPEN_HINT,
  DECIDED_HINT,
  FACTOR_NAMES,
  FOUND_HINT,
  ORDER_DATE_HINT,
  PRIORITY_HINT,
  VERDICT_TEXT,
} from "./copy";
import { formatFraction, formatMoney } from "./money";

export function decisionText(row: ExceptionRow): string {
  if (!row.decision) return "Undecided";
  return row.decision.outcome === "approved" ? "Approved" : "Rejected";
}

export function verdictText(row: ExceptionRow): string {
  if (!row.investigation) return "Not investigated";
  return VERDICT_TEXT[row.investigation.verdict] ?? "Investigation failed";
}

const Pill = ({ text, tone }: { text: string; tone: string }) => (
  <span className={`pill ${tone}`}>{text}</span>
);

// A decimal-string collation: "90.00" before "100.00". Not arithmetic - the
// grid orders the strings, and nothing here adds, rounds or converts.
const byDecimalString = (a: string | null, b: string | null): number =>
  (a ?? "").localeCompare(b ?? "", "en", { numeric: true });

function factorsText(row: ExceptionRow): string {
  return Object.entries(row.priority.factors)
    .map(([k, v]) => `${FACTOR_NAMES[k] ?? k}: ${formatFraction(v)}`)
    .join("\n");
}

export const columns: ColDef<ExceptionRow>[] = [
  {
    colId: "priority",
    headerName: "Priority",
    headerTooltip: PRIORITY_HINT,
    valueGetter: (p) => p.data?.priority.score ?? null,
    comparator: byDecimalString,
    filter: "agNumberColumnFilter",
    sort: "desc",
    width: 150,
    cellRenderer: (p: ICellRendererParams<ExceptionRow>) => {
      if (!p.data) return null;
      const share = formatFraction(p.data.priority.score);
      return (
        <span className="meter" title={factorsText(p.data)}>
          <span className="meter-track">
            <span className="meter-fill" style={{ width: share }} />
          </span>
          <span className="meter-text">{share}</span>
        </span>
      );
    },
  },
  {
    colId: "status",
    headerName: "Certainty",
    headerTooltip:
      "Confirmed: the records agree on what happened. Suspected: the link between records was inferred, so check it.",
    valueGetter: (p) =>
      p.data ? (p.data.certainty === "confirmed" ? "Confirmed" : "Suspected") : "",
    enableRowGroup: true,
    filter: "agSetColumnFilter",
    width: 135,
    cellRenderer: (p: ICellRendererParams<ExceptionRow>) =>
      p.data ? (
        <span title={CERTAINTY_HINT[p.data.certainty]}>
          <Pill text={p.value} tone={p.data.certainty} />
        </span>
      ) : null,
  },
  {
    colId: "type",
    headerName: "Type",
    valueGetter: (p) => (p.data ? label(p.data.code) : ""),
    enableRowGroup: true,
    filter: "agSetColumnFilter",
    flex: 2,
    minWidth: 250,
  },
  {
    colId: "order",
    headerName: "Order",
    valueGetter: (p) => p.data?.order_ref ?? "",
    enableRowGroup: true,
    width: 115,
    cellClass: "mono",
  },
  {
    colId: "seller",
    headerName: "Seller",
    valueGetter: (p) =>
      p.data ? (p.data.seller_name ?? `${p.data.seller_count} sellers`) : "",
    enableRowGroup: true,
    filter: "agSetColumnFilter",
    flex: 1,
    minWidth: 140,
  },
  {
    colId: "impact",
    headerName: "Difference",
    headerTooltip: "The size of the gap, in the currency of the record.",
    // The amount string, ordered and filtered by the grid. Display uses the
    // record's own currency; no arithmetic happens in this file.
    valueGetter: (p) => p.data?.impact.amount ?? null,
    comparator: byDecimalString,
    filter: "agNumberColumnFilter",
    type: "rightAligned",
    width: 135,
    // An explicit cellClass replaces the type's own, so the alignment class
    // is restated here.
    cellClass: ["ag-right-aligned-cell", "money"],
    cellRenderer: (p: ICellRendererParams<ExceptionRow>) =>
      p.data ? formatMoney(p.data.impact) : null,
  },
  {
    colId: "currency",
    headerName: "Currency",
    valueGetter: (p) => p.data?.impact.currency ?? "",
    enableRowGroup: true,
    filter: "agSetColumnFilter",
    hide: true,
    width: 100,
  },
  {
    colId: "placed",
    headerName: "Order date",
    headerTooltip: ORDER_DATE_HINT,
    valueGetter: (p) => p.data?.order_placed_at ?? null,
    valueFormatter: (p) =>
      p.node?.group ? "" : p.value ? formatDate(p.value) : "unknown",
    filter: "agDateColumnFilter",
    width: 150,
  },
  {
    colId: "found",
    headerName: "First found",
    headerTooltip: FOUND_HINT,
    valueGetter: (p) => p.data?.first_found_at ?? null,
    valueFormatter: (p) =>
      p.node?.group ? "" : p.value ? formatDate(p.value) : "unknown",
    filter: "agDateColumnFilter",
    width: 155,
  },
  {
    colId: "daysOpen",
    headerName: "Days open",
    headerTooltip: DAYS_OPEN_HINT,
    // Whole days since first reported. Numeric so it sorts and filters as a
    // number; the formatter only changes how it reads.
    valueGetter: (p) =>
      p.data
        ? Math.max(
            0,
            Math.floor(
              (Date.now() - Date.parse(p.data.first_found_at)) / 86_400_000,
            ),
          )
        : null,
    valueFormatter: (p) =>
      p.node?.group || p.value == null
        ? ""
        : p.value === 0
          ? "Today"
          : p.value === 1
            ? "1 day"
            : `${p.value} days`,
    filter: "agNumberColumnFilter",
    type: "rightAligned",
    width: 145,
  },
  {
    colId: "investigation",
    headerName: "AI result",
    headerTooltip: "What the AI found when it looked into this exception.",
    valueGetter: (p) => (p.data ? verdictText(p.data) : ""),
    enableRowGroup: true,
    filter: "agSetColumnFilter",
    width: 150,
  },
  {
    colId: "decision",
    headerName: "Decision",
    valueGetter: (p) => (p.data ? decisionText(p.data) : ""),
    enableRowGroup: true,
    filter: "agSetColumnFilter",
    width: 135,
    cellRenderer: (p: ICellRendererParams<ExceptionRow>) =>
      p.data ? (
        <Pill text={p.value} tone={p.data.decision?.outcome ?? "undecided"} />
      ) : null,
  },
  {
    colId: "decidedOn",
    headerName: "Decided on",
    headerTooltip: DECIDED_HINT,
    valueGetter: (p) => p.data?.decision?.decided_at ?? null,
    valueFormatter: (p) =>
      p.node?.group ? "" : p.value ? formatDate(p.value) : "Not decided",
    filter: "agDateColumnFilter",
    width: 150,
  },
];

// What the AI assistant is told about each column. Categorical columns send
// their values so "FX issues for Bellweather" can be matched; no amount column
// ever does.
export const columnContext = {
  priority: { description: "Priority score of the exception, higher first" },
  status: {
    description: "Confirmed discrepancy or suspected issue",
    includeSetValues: true,
  },
  type: { description: "The kind of exception", includeSetValues: true },
  order: { description: "The marketplace order reference" },
  seller: {
    description: "The seller the exception affects",
    includeSetValues: true,
  },
  impact: { description: "Size of the difference, in its own currency" },
  currency: { description: "Currency of the impact", includeSetValues: true },
  placed: { description: "When the order was placed" },
  found: { description: "When a check first reported this problem" },
  daysOpen: { description: "Whole days since a check first reported this problem" },
  decidedOn: { description: "When the latest decision was recorded, if any" },
  investigation: {
    description: "Whether the AI investigation found a cause",
    includeSetValues: true,
  },
  decision: {
    description: "The human decision: Undecided, Approved or Rejected",
    includeSetValues: true,
  },
};
