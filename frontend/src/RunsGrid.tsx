import type { ColDef } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import type { Run } from "./api";
import { formatDateTime } from "./labels";

const columns: ColDef<Run>[] = [
  {
    headerName: "Started",
    valueGetter: (p) => p.data?.started_at,
    valueFormatter: (p) => (p.value ? formatDateTime(p.value) : ""),
    sort: "desc",
    width: 190,
  },
  {
    headerName: "Window",
    valueGetter: (p) =>
      p.data
        ? `${formatDateTime(p.data.window_start)} to ${formatDateTime(p.data.window_end)}`
        : "",
    flex: 1,
    minWidth: 260,
  },
  { headerName: "Status", field: "status", width: 120 },
  { headerName: "Orders", field: "order_count", width: 110 },
  { headerName: "Findings", field: "finding_count", width: 110 },
  { headerName: "Exceptions", field: "exception_count", width: 120 },
];

interface Props {
  runs: Run[];
  onOpen: (runId: string) => void;
}

export default function RunsGrid({ runs, onOpen }: Props) {
  return (
    <div>
      <p className="muted">
        Each row is one reconciliation run. Select a run to see the exceptions
        it concluded.
      </p>
      <div className="ag-theme-quartz grid">
        <AgGridReact<Run>
          rowData={runs}
          columnDefs={columns}
          defaultColDef={{ sortable: true, resizable: true, filter: true }}
          domLayout="autoHeight"
          getRowId={(p) => p.data.id}
          onRowClicked={(e) => e.data && onOpen(e.data.id)}
        />
      </div>
    </div>
  );
}
