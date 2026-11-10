import type { GridApi, GridState } from "ag-grid-community";
import { useState } from "react";

const KEY = "reckonai.views";

type Views = Record<string, GridState>;

function read(): Views {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "{}") as Views;
  } catch {
    return {};
  }
}

function write(views: Views): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(views));
  } catch {
    /* a blocked store only loses saved views; the grid is unaffected */
  }
}

interface Props {
  gridApi: () => GridApi | null;
  initialState: () => GridState | null;
}

// A saved view is AG Grid's own state object (filters, sorting, grouping,
// columns) kept in this browser. It holds no record data.
export default function SavedViews({ gridApi, initialState }: Props) {
  const [views, setViews] = useState<Views>(read);
  const [selected, setSelected] = useState("");
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");

  const save = () => {
    const grid = gridApi();
    const trimmed = name.trim();
    if (!grid || !trimmed) return;
    const next = { ...views, [trimmed]: grid.getState() };
    write(next);
    setViews(next);
    setSelected(trimmed);
    setNaming(false);
    setName("");
  };

  const apply = (view: string) => {
    setSelected(view);
    const grid = gridApi();
    if (grid && views[view]) grid.setState(views[view]);
  };

  const remove = () => {
    const rest = { ...views };
    delete rest[selected];
    write(rest);
    setViews(rest);
    setSelected("");
  };

  const reset = () => {
    const grid = gridApi();
    const start = initialState();
    if (grid && start) grid.setState(start);
    setSelected("");
  };

  return (
    <div className="views">
      <select
        aria-label="Saved views"
        value={selected}
        onChange={(e) => apply(e.target.value)}
      >
        <option value="">Saved views</option>
        {Object.keys(views).map((n) => (
          <option key={n} value={n}>
            {n}
          </option>
        ))}
      </select>
      {naming ? (
        <form
          className="inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <input
            autoFocus
            aria-label="View name"
            placeholder="View name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <button type="submit" disabled={!name.trim()}>
            Save
          </button>
          <button type="button" onClick={() => setNaming(false)}>
            Cancel
          </button>
        </form>
      ) : (
        <button type="button" onClick={() => setNaming(true)}>
          Save view
        </button>
      )}
      {selected && !naming && (
        <button type="button" onClick={remove}>
          Delete
        </button>
      )}
      <button type="button" onClick={reset}>
        Reset
      </button>
    </div>
  );
}
