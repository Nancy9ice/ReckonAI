import type { GridApi } from "ag-grid-community";
import { useState } from "react";
import { api } from "./api";
import { columnContext } from "./columns";

interface Props {
  gridApi: () => GridApi | null;
}

const EXAMPLES = [
  "confirmed FX issues",
  "group by seller",
  "undecided, highest impact first",
];

// AG Grid's AI Toolkit: the grid describes itself (schema + state), the server
// asks the model for a new state, and the grid applies it. The model changes
// how the queue is displayed and nothing else - it is sent no record and no
// amount, and the aggregation and pivot features are left out of the schema so
// it cannot be asked to total anything.
export default function AssistantBar({ gridApi }: Props) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "ok" | "error"; text: string } | null>(
    null,
  );

  const ask = async (sentence: string) => {
    const grid = gridApi();
    if (!grid || !sentence.trim()) return;
    setBusy(true);
    setNote(null);
    try {
      const schema = grid.getStructuredSchema({
        exclude: ["aggregation", "pivot", "columnSizing"],
        columns: columnContext,
      });
      const result = await api.gridAssistant({
        request: sentence,
        state: grid.getState() as Record<string, unknown>,
        schema: schema as Record<string, unknown>,
      });
      if (Object.keys(result.gridState).length > 0) {
        grid.setState(
          result.gridState,
          result.propertiesToIgnore as Parameters<GridApi["setState"]>[1],
        );
      }
      setNote({ tone: "ok", text: result.explanation });
    } catch (e) {
      setNote({ tone: "error", text: String(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="assistant">
      <div className="assistant-inner">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void ask(text);
        }}
      >
        <span className="spark" aria-hidden="true" />
        <input
          aria-label="Ask the queue"
          placeholder="Ask the queue, for example: confirmed FX issues for one seller"
          value={text}
          maxLength={500}
          onChange={(e) => setText(e.target.value)}
        />
        <button className="primary" disabled={busy || !text.trim()} type="submit">
          {busy ? "Thinking" : "Apply"}
        </button>
      </form>
      <div className="assistant-foot">
        {note ? (
          <span className={note.tone === "ok" ? "note ok" : "note bad"}>
            {note.text}
          </span>
        ) : (
          <span className="hints">
            Try
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                type="button"
                className="hint"
                onClick={() => {
                  setText(ex);
                  void ask(ex);
                }}
              >
                {ex}
              </button>
            ))}
          </span>
        )}
      </div>
      </div>
    </div>
  );
}
