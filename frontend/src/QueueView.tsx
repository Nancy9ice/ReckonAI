import type {
  GridApi,
  GridReadyEvent,
  GridState,
  RowClickedEvent,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { useCallback, useMemo, useRef, useState } from "react";
import type { ExceptionRow } from "./api";
import AssistantBar from "./AssistantBar";
import { columns, decisionText } from "./columns";

const SETTLED = new Set(["reported", "insufficient_evidence"]);
import SavedViews from "./SavedViews";
import { gridTheme } from "./theme";

type Certainty = "all" | "confirmed" | "suspected";
type Decided = "all" | "Undecided" | "decided";

const GROUPS: { id: string; label: string }[] = [
  { id: "", label: "No grouping" },
  { id: "type", label: "Issue" },
  { id: "seller", label: "Seller" },
  { id: "status", label: "Certainty" },
  { id: "decision", label: "Decision" },
  { id: "currency", label: "Currency" },
  { id: "__custom", label: "Custom grouping" },
];

interface Props {
  rows: ExceptionRow[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  runFilterActive: boolean;
  onClearRun: () => void;
  runId?: string;
  investigating: boolean;
  onInvestigate: (ids: string[], skipSettled: boolean) => void;
}

function Segmented<T extends string>(props: {
  label: string;
  value: T;
  options: { id: T; text: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="seg" role="group" aria-label={props.label}>
      {props.options.map((o) => (
        <button
          key={o.id}
          type="button"
          className={props.value === o.id ? "on" : ""}
          onClick={() => props.onChange(o.id)}
        >
          {o.text}
        </button>
      ))}
    </div>
  );
}

export default function QueueView({
  rows,
  selectedId,
  onSelect,
  runFilterActive,
  onClearRun,
  runId,
  investigating,
  onInvestigate,
}: Props) {
  const gridApi = useRef<GridApi<ExceptionRow> | null>(null);
  const initial = useRef<GridState | null>(null);
  const [quick, setQuick] = useState("");
  const [certainty, setCertainty] = useState<Certainty>("all");
  const [decided, setDecided] = useState<Decided>("all");
  const [group, setGroup] = useState("");
  const [checked, setChecked] = useState<string[]>([]);
  // Two-step, inline: every investigation is a paid model call, and a browser
  // dialog would block the page.
  const [confirm, setConfirm] = useState<{
    kind: "all" | "selected";
    ids: string[];
  } | null>(null);

  const visible = useMemo(
    () =>
      rows.filter(
        (r) =>
          (certainty === "all" || r.certainty === certainty) &&
          (decided === "all" ||
            (decided === "decided"
              ? decisionText(r) !== "Undecided"
              : decisionText(r) === "Undecided")),
      ),
    [rows, certainty, decided],
  );

  // The grid is the source of truth for grouping: the assistant, a saved view
  // and the sidebar all change it, so the select follows the grid.
  const onGroupChanged = useCallback(() => {
    const cols = gridApi.current?.getRowGroupColumns() ?? [];
    if (cols.length === 0) setGroup("");
    else if (cols.length === 1 && GROUPS.some((g) => g.id === cols[0].getColId()))
      setGroup(cols[0].getColId());
    else setGroup("__custom");
  }, []);

  const onSelection = useCallback(() => {
    setChecked((gridApi.current?.getSelectedRows() ?? []).map((r) => r.id));
  }, []);

  // "All" means every row in view that has no conclusion yet; a failed or
  // missing investigation counts as none.
  const unsettled = useMemo(
    () => visible.filter((r) => !SETTLED.has(r.investigation?.verdict ?? "")),
    [visible],
  );

  const onReady = useCallback((e: GridReadyEvent<ExceptionRow>) => {
    gridApi.current = e.api;
    initial.current = e.api.getState();
  }, []);

  const changeGroup = (id: string) => {
    if (id === "__custom") return;
    setGroup(id);
    gridApi.current?.applyColumnState({
      state: id ? [{ colId: id, rowGroup: true, hide: true }] : [],
      defaultState: { rowGroup: false, hide: null },
    });
    // Re-show the column that was grouped away when grouping is cleared.
    if (!id) gridApi.current?.setColumnsVisible(["type", "seller", "status"], true);
  };

  const get = useCallback(() => gridApi.current, []);
  const start = useCallback(() => initial.current, []);

  return (
    <section className="card queue">
      <div className="queue-head">
        <h2>Exceptions to review</h2>
        <div className="queue-actions">
          {checked.length > 0 && (
            <button
              type="button"
              disabled={investigating}
              onClick={() => setConfirm({ kind: "selected", ids: checked })}
            >
              Investigate selected ({checked.length})
            </button>
          )}
          <button
            type="button"
            className="primary"
            disabled={investigating || unsettled.length === 0}
            title={
              unsettled.length === 0
                ? "Everything in view already has a conclusion"
                : "Investigate every exception in view that has no conclusion yet"
            }
            onClick={() =>
              setConfirm({ kind: "all", ids: unsettled.map((r) => r.id) })
            }
          >
            {investigating ? "Investigating..." : `Investigate all (${unsettled.length})`}
          </button>
        </div>
      </div>

      {confirm && (
        <div className="confirm" role="alertdialog" aria-label="Confirm investigation">
          <span>
            Ask the AI to look into <strong>{confirm.ids.length}</strong>{" "}
            {confirm.ids.length === 1 ? "exception" : "exceptions"}? It works
            through them one at a time, and each one uses paid AI credit.
          </span>
          <span className="confirm-buttons">
            <button
              type="button"
              className="primary"
              onClick={() => {
                onInvestigate(confirm.ids, confirm.kind === "all");
                setConfirm(null);
              }}
            >
              Confirm
            </button>
            <button type="button" onClick={() => setConfirm(null)}>
              Cancel
            </button>
          </span>
        </div>
      )}

      <AssistantBar gridApi={get as () => GridApi | null} />

      <div className="toolbar">
        <input
          className="search"
          aria-label="Search the queue"
          placeholder="Search orders, sellers, issues"
          value={quick}
          onChange={(e) => setQuick(e.target.value)}
        />
        <Segmented<Certainty>
          label="Certainty"
          value={certainty}
          onChange={setCertainty}
          options={[
            { id: "all", text: "All" },
            { id: "confirmed", text: "Confirmed" },
            { id: "suspected", text: "Suspected" },
          ]}
        />
        <Segmented<Decided>
          label="Decision"
          value={decided}
          onChange={setDecided}
          options={[
            { id: "all", text: "Any decision" },
            { id: "Undecided", text: "Undecided" },
            { id: "decided", text: "Decided" },
          ]}
        />
        <label className="inline">
          Group by
          <select value={group} onChange={(e) => changeGroup(e.target.value)}>
            {GROUPS.map((g) => (
              <option key={g.id} value={g.id}>
                {g.label}
              </option>
            ))}
          </select>
        </label>
        <SavedViews gridApi={get as () => GridApi | null} initialState={start} />
        <div className="views export">
          <a
            className="btn"
            href={
              runId
                ? `/api/export/exceptions.csv?run_id=${runId}`
                : "/api/export/exceptions.csv"
            }
            download
          >
            Export queue CSV
          </a>
          <a className="btn" href="/api/export/decisions.csv" download>
            Decision log CSV
          </a>
        </div>
        {runFilterActive && (
          <button className="chip" type="button" onClick={onClearRun}>
            Showing one run - show all
          </button>
        )}
      </div>

      {rows.length === 0 ? (
        <div className="empty">
          <strong>No exceptions yet.</strong>
          <span>
            A reconciliation run has not concluded any. The scheduled run
            executes daily; a wider run can be queued by hand.
          </span>
        </div>
      ) : (
        <div className="grid-wrap">
          {/* The open row's highlight is a style keyed to its id, not a class the
              grid applies. The grid sets `row-id` on every row, and a class from
              `getRowClass` is only re-evaluated when the grid decides to: on the
              deployed site it did not, so every row opened stayed blue. A rule
              that names one row cannot go stale. */}
          {selectedId !== null && (
            <style>{`.ag-row[row-id="${CSS.escape(selectedId)}"] { background: var(--row-open-bg); box-shadow: inset 4px 0 0 var(--row-open-bar); }`}</style>
          )}
          <AgGridReact<ExceptionRow>
            theme={gridTheme}
            rowData={visible}
            columnDefs={columns}
            defaultColDef={{
              sortable: true,
              resizable: true,
              filter: true,
              floatingFilter: false,
            }}
            quickFilterText={quick}
            getRowId={(p) => p.data.id}
            onGridReady={onReady}
            onColumnRowGroupChanged={onGroupChanged}
            // Ticking a row selects it for a bulk action; clicking the row
            // itself opens the detail drawer.
            rowSelection={{
              mode: "multiRow",
              checkboxes: true,
              headerCheckbox: true,
              enableClickSelection: false,
              groupSelects: "descendants",
            }}
            onSelectionChanged={onSelection}
            onRowClicked={(e: RowClickedEvent<ExceptionRow>) =>
              e.data && onSelect(e.data.id)
            }
            groupDefaultExpanded={1}
            autoGroupColumnDef={{
              headerName: "Group",
              minWidth: 170,
              cellRendererParams: { suppressCount: false },
            }}
            rowGroupPanelShow="onlyWhenGrouping"
            suppressAggFuncInHeader
            sideBar={{
              toolPanels: ["columns", "filters"],
              defaultToolPanel: "",
              hiddenByDefault: false,
            }}
            statusBar={{
              statusPanels: [
                { statusPanel: "agTotalAndFilteredRowCountComponent", align: "left" },
                { statusPanel: "agSelectedRowCountComponent", align: "center" },
              ],
            }}
            domLayout="autoHeight"
            tooltipShowDelay={300}
          />
        </div>
      )}
    </section>
  );
}
