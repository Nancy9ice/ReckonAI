import type { ColDef } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import type { Coverage } from "./api";
import { formatDate } from "./labels";
import { formatMoney } from "./money";
import { gridTheme } from "./theme";

type Clean = Coverage["clean_orders"][number];
type Parked = Coverage["parked_items"][number];

const cleanColumns: ColDef<Clean>[] = [
  { headerName: "Order", field: "order_ref", width: 130, cellClass: "mono" },
  {
    headerName: "Sellers",
    valueGetter: (p) => p.data?.sellers.join(", "),
    flex: 1,
    minWidth: 180,
  },
  {
    headerName: "Order total",
    valueGetter: (p) => p.data?.total.amount,
    cellRenderer: (p: { data?: Clean }) =>
      p.data ? formatMoney(p.data.total) : null,
    type: "rightAligned",
    cellClass: ["ag-right-aligned-cell", "money"],
    width: 150,
  },
  {
    headerName: "Order date",
    valueGetter: (p) => p.data?.placed_at,
    valueFormatter: (p) => (p.value ? formatDate(p.value) : ""),
    width: 140,
  },
];

const parkedColumns: ColDef<Parked>[] = [
  { headerName: "Order", field: "order_ref", width: 130, cellClass: "mono" },
  { headerName: "Why it is parked", field: "detail", flex: 1, minWidth: 300, wrapText: true, autoHeight: true },
];

function Stat(props: { label: string; value: number; tone?: string; hint: string }) {
  return (
    <div className={`kpi ${props.tone ?? ""}`} title={props.hint}>
      <span className="kpi-value">{props.value}</span>
      <span className="kpi-label">{props.label}</span>
    </div>
  );
}

export default function CoverageView({ coverage }: { coverage: Coverage | null }) {
  if (!coverage) return <p className="muted">Loading...</p>;

  return (
    <>
      <section className="kpis five" aria-label="Coverage summary">
        <Stat label="Orders in the data" value={coverage.orders_total} hint="Every order held for this marketplace." />
        <Stat label="With an exception" value={coverage.with_exceptions} tone="bad" hint="In the exception queue." />
        <Stat label="Parked, awaiting evidence" value={coverage.parked} tone="warn" hint="Not exceptions: the other side of the record has not arrived." />
        <Stat label="Reconciled clean" value={coverage.clean} tone="good" hint="A run looked at it after it arrived and found nothing." />
        <Stat label="Not yet examined" value={coverage.not_examined} tone="muted" hint="No completed run has looked at these since they arrived. Unknown, not clean." />
      </section>

      <p className="muted basis">{coverage.basis}</p>

      <section className="card spaced">
        <div className="card-head">
          <h2>Parked, awaiting evidence</h2>
          <p className="muted">
            These are not discrepancies. The record that would settle them has not
            arrived yet, so they are held back from the queue and re-evaluated on
            the next run.
          </p>
        </div>
        {coverage.parked_items.length === 0 ? (
          <p className="muted">Nothing is parked.</p>
        ) : (
          <div className="grid-wrap">
            <AgGridReact<Parked>
              theme={gridTheme}
              rowData={coverage.parked_items}
              columnDefs={parkedColumns}
              getRowId={(p) => p.data.id}
              domLayout="autoHeight"
            />
          </div>
        )}
      </section>

      {coverage.not_examined > 0 && (
        <section className="card spaced">
          <div className="card-head">
            <h2>Not yet examined</h2>
            <p className="muted">
              No completed run has looked at these since they arrived, so they
              are not claimed clean. Run a reconciliation to examine them.
            </p>
          </div>
          <div className="refs">
            {coverage.not_examined_orders.map((r) => (
              <code key={r}>{r}</code>
            ))}
          </div>
        </section>
      )}

      <section className="card spaced">
        <div className="card-head">
          <h2>Reconciled clean</h2>
          <p className="muted">
            Orders where the marketplace, the ledger and PayPal agree.
          </p>
        </div>
        <div className="grid-wrap">
          <AgGridReact<Clean>
            theme={gridTheme}
            rowData={coverage.clean_orders}
            columnDefs={cleanColumns}
            defaultColDef={{ sortable: true, resizable: true, filter: true }}
            getRowId={(p) => p.data.order_ref}
            statusBar={{
              statusPanels: [
                { statusPanel: "agTotalAndFilteredRowCountComponent", align: "left" },
              ],
            }}
            domLayout="autoHeight"
          />
        </div>
      </section>
    </>
  );
}
