import type { ColDef, ICellRendererParams } from "ag-grid-community";
import type { ExceptionRow } from "./api";
import { formatDate, label } from "./labels";
import { formatFraction, formatMoney } from "./money";

export function decisionText(row: ExceptionRow): string {
  if (!row.decision) return "Undecided";
  return row.decision.outcome === "approved" ? "Approved" : "Rejected";
}

export function verdictText(row: ExceptionRow): string {
  if (!row.investigation) return "Not investigated";
  switch (row.investigation.verdict) {
    case "reported":
      return "Cause reported";
    case "insufficient_evidence":
      return "Insufficient evidence";
    default:
      return "Investigation failed";
  }
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
    .map(([k, v]) => `${k} ${formatFraction(v)}`)
    .join("  |  ");
}

export const columns: ColDef<ExceptionRow>[] = [
  {
    colId: "priority",
    headerName: "Priority",
    valueGetter: (p) => p.data?.priority.score ?? null,
    comparator: byDecimalString,
    filter: "agNumberColumnFilter",
    sort: "desc",
    width: 140,
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
    headerName: "Status",
    valueGetter: (p) =>
      p.data ? (p.data.certainty === "confirmed" ? "Confirmed" : "Suspected") : "",
    enableRowGroup: true,
    filter: "agSetColumnFilter",
    width: 118,
    cellRenderer: (p: ICellRendererParams<ExceptionRow>) =>
      p.data ? <Pill text={p.value} tone={p.data.certainty} /> : null,
  },
  {
    colId: "type",
    headerName: "Type",
    valueGetter: (p) => (p.data ? label(p.data.code) : ""),
    enableRowGroup: true,
    filter: "agSetColumnFilter",
    flex: 1.4,
    minWidth: 170,
  },
  {
    colId: "order",
    headerName: "Order",
    valueGetter: (p) => p.data?.order_ref ?? "",
    enableRowGroup: true,
    width: 110,
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
    minWidth: 120,
  },
  {
    colId: "impact",
    headerName: "Impact",
    // The amount string, ordered and filtered by the grid. Display uses the
    // record's own currency; no arithmetic happens in this file.
    valueGetter: (p) => p.data?.impact.amount ?? null,
    comparator: byDecimalString,
    filter: "agNumberColumnFilter",
    type: "rightAligned",
    width: 120,
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
    valueGetter: (p) => p.data?.order_placed_at ?? null,
    valueFormatter: (p) =>
      p.node?.group ? "" : p.value ? formatDate(p.value) : "unknown",
    width: 125,
  },
  {
    colId: "investigation",
    headerName: "Investigation",
    valueGetter: (p) => (p.data ? verdictText(p.data) : ""),
    enableRowGroup: true,
    filter: "agSetColumnFilter",
    width: 160,
  },
  {
    colId: "decision",
    headerName: "Decision",
    valueGetter: (p) => (p.data ? decisionText(p.data) : ""),
    enableRowGroup: true,
    filter: "agSetColumnFilter",
    width: 118,
    cellRenderer: (p: ICellRendererParams<ExceptionRow>) =>
      p.data ? (
        <Pill text={p.value} tone={p.data.decision?.outcome ?? "undecided"} />
      ) : null,
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
  impact: { description: "Size of the discrepancy, in its own currency" },
  currency: { description: "Currency of the impact", includeSetValues: true },
  placed: { description: "When the order was placed" },
  investigation: {
    description: "Whether the AI investigation found a cause",
    includeSetValues: true,
  },
  decision: {
    description: "The human decision: Undecided, Approved or Rejected",
    includeSetValues: true,
  },
};
