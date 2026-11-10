import type { ColDef } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import type { Coverage } from "./api";
import { HINTS } from "./copy";
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
  { headerName: "Why it is on hold", field: "detail", flex: 1, minWidth: 300, wrapText: true, autoHeight: true },
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
        <Stat label="Orders in total" value={coverage.orders_total} hint="Every order held for this marketplace." />
        <Stat label="Need attention" value={coverage.with_exceptions} tone="bad" hint="These are in the exception queue." />
        <Stat label="On hold" value={coverage.parked} tone="warn" hint={HINTS.parked} />
        <Stat label="Checked, no issues" value={coverage.clean} tone="good" hint={HINTS.clean} />
        <Stat label="Not checked yet" value={coverage.not_examined} tone="muted" hint={HINTS.notExamined} />
      </section>

      <p className="muted basis">{coverage.basis}</p>

      <section className="card spaced">
        <div className="card-head">
          <h2>On hold: waiting for the other record</h2>
          <p className="muted">
            These are not mistakes. {HINTS.parked}
          </p>
        </div>
        {coverage.parked_items.length === 0 ? (
          <p className="muted">Nothing is on hold.</p>
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
            <h2>Not checked yet</h2>
            <p className="muted">
              {HINTS.notExamined} Run a reconciliation to check them.
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
          <h2>Checked, no issues</h2>
          <p className="muted">
            Orders where the marketplace, the seller ledger and PayPal agree.
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
