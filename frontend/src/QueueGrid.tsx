import type {
  ColDef,
  ICellRendererParams,
  RowClickedEvent,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { useMemo } from "react";
import type { ExceptionRow } from "./api";
import { formatDate, label } from "./labels";
import { formatFraction, formatMoney } from "./money";

export type GroupKey = "none" | "code" | "seller" | "certainty" | "decision";

export function groupValue(row: ExceptionRow, key: GroupKey): string {
  switch (key) {
    case "code":
      return label(row.code);
    case "seller":
      return row.seller_name ?? `All sellers on ${row.order_ref}`;
    case "certainty":
      return row.certainty === "confirmed" ? "Confirmed" : "Suspected";
    case "decision":
      return decisionText(row);
    default:
      return "All exceptions";
  }
}

export function decisionText(row: ExceptionRow): string {
  if (!row.decision) return "Undecided";
  return row.decision.outcome === "approved" ? "Approved" : "Rejected";
}

function verdictText(row: ExceptionRow): string {
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

const badge = (text: string, tone: string) => (
  <span className={`badge ${tone}`}>{text}</span>
);

interface Props {
  title: string;
  rows: ExceptionRow[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export default function QueueGrid({ title, rows, selectedId, onSelect }: Props) {
  const columns = useMemo<ColDef<ExceptionRow>[]>(
    () => [
      {
        headerName: "Priority",
        valueGetter: (p) => p.data?.priority.score,
        valueFormatter: (p) => formatFraction(p.value),
        sort: "desc",
        // Fixed four-place decimal strings, so a numeric collation orders them.
        comparator: (a: string, b: string) =>
          a.localeCompare(b, "en", { numeric: true }),
        width: 110,
        tooltipValueGetter: (p) =>
          p.data
            ? Object.entries(p.data.priority.factors)
                .map(([k, v]) => `${k}: ${formatFraction(v)}`)
                .join(" | ")
            : "",
      },
      {
        headerName: "Status",
        field: "certainty",
        width: 120,
        cellRenderer: (p: ICellRendererParams<ExceptionRow>) =>
          p.data
            ? badge(
                p.data.certainty === "confirmed" ? "Confirmed" : "Suspected",
                p.data.certainty,
              )
            : null,
      },
      {
        headerName: "Type",
        valueGetter: (p) => (p.data ? label(p.data.code) : ""),
        flex: 1.4,
        minWidth: 190,
      },
      { headerName: "Order", field: "order_ref", width: 120 },
      {
        headerName: "Seller",
        valueGetter: (p) => p.data?.seller_name ?? `${p.data?.seller_count} sellers`,
        flex: 1,
        minWidth: 130,
      },
      {
        headerName: "Impact",
        // Display only. The value is a formatted string, which sorts and
        // filters lexicographically ("$9.00" after "$10.00"), and the client
        // does no arithmetic on amounts - so this column offers neither. The
        // priority column carries the ordering by impact, within a currency.
        valueGetter: (p) => (p.data ? formatMoney(p.data.impact) : ""),
        sortable: false,
        filter: false,
        width: 130,
        type: "rightAligned",
      },
      {
        headerName: "Order date",
        valueGetter: (p) => p.data?.order_placed_at,
        valueFormatter: (p) => (p.value ? formatDate(p.value) : "unknown"),
        width: 130,
      },
      {
        headerName: "Investigation",
        valueGetter: (p) => (p.data ? verdictText(p.data) : ""),
        width: 170,
      },
      {
        headerName: "Decision",
        valueGetter: (p) => (p.data ? decisionText(p.data) : ""),
        width: 120,
        cellRenderer: (p: ICellRendererParams<ExceptionRow>) =>
          p.data
            ? badge(
                decisionText(p.data),
                p.data.decision?.outcome ?? "undecided",
              )
            : null,
      },
    ],
    [],
  );

  return (
    <section className="group">
      <h3>
        {title} <span className="count">{rows.length}</span>
      </h3>
      <div className="ag-theme-quartz grid">
        <AgGridReact<ExceptionRow>
          rowData={rows}
          columnDefs={columns}
          defaultColDef={{ sortable: true, resizable: true, filter: true }}
          domLayout="autoHeight"
          getRowId={(p) => p.data.id}
          rowSelection={{ mode: "singleRow", checkboxes: false, enableClickSelection: true }}
          onRowClicked={(e: RowClickedEvent<ExceptionRow>) =>
            e.data && onSelect(e.data.id)
          }
          getRowClass={(p) => (p.data?.id === selectedId ? "row-selected" : "")}
          tooltipShowDelay={300}
        />
      </div>
    </section>
  );
}
