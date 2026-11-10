import type { ColDef } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import type { Run } from "./api";
import { HINTS } from "./copy";
import { formatDateTime } from "./labels";
import { gridTheme } from "./theme";

const columns: ColDef<Run>[] = [
  {
    headerName: "Run at",
    valueGetter: (p) => p.data?.started_at,
    valueFormatter: (p) => (p.value ? formatDateTime(p.value) : ""),
    sort: "desc",
    width: 190,
  },
  {
    headerName: "Orders placed between",
    headerTooltip: HINTS.runWindow,
    valueGetter: (p) =>
      p.data
        ? `${formatDateTime(p.data.window_start)}  to  ${formatDateTime(p.data.window_end)}`
        : "",
    flex: 1,
    minWidth: 280,
  },
  {
    headerName: "Status",
    field: "status",
    width: 130,
    cellRenderer: (p: { value: string }) => (
      <span className={`pill ${p.value === "completed" ? "approved" : "suspected"}`}>
        {p.value}
      </span>
    ),
  },
  { headerName: "Orders", field: "order_count", width: 110, type: "rightAligned" },
  {
    headerName: "Findings",
    headerTooltip: HINTS.runFindings,
    field: "finding_count",
    width: 120,
    type: "rightAligned",
  },
  {
    headerName: "Need attention",
    headerTooltip: HINTS.runExceptions,
    field: "exception_count",
    width: 130,
    type: "rightAligned",
  },
];

interface Props {
  runs: Run[];
  onOpen: (runId: string) => void;
}

export default function RunsView({ runs, onOpen }: Props) {
  return (
    <section className="card">
      <div className="card-head">
        <h2>Past checks</h2>
        <p className="muted">
          Each row is one check of your orders against PayPal. Select one to see
          what it found.
        </p>
      </div>
      <div className="grid-wrap">
        <AgGridReact<Run>
          theme={gridTheme}
          rowData={runs}
          columnDefs={columns}
          defaultColDef={{ sortable: true, resizable: true, filter: true }}
          getRowId={(p) => p.data.id}
          onRowClicked={(e) => e.data && onOpen(e.data.id)}
          rowSelection={{ mode: "singleRow", checkboxes: false, enableClickSelection: true }}
          statusBar={{
            statusPanels: [
              { statusPanel: "agTotalAndFilteredRowCountComponent", align: "left" },
            ],
          }}
          domLayout="autoHeight"
        />
      </div>
    </section>
  );
}
