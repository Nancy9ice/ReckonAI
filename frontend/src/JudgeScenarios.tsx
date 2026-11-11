import { useEffect, useRef, useState } from "react";
import type { JudgeRow, JudgeState } from "./api";
import { api } from "./api";
import { JUDGE, TYPE_COPY } from "./copy";

const STORAGE_KEY = "reckonai.judgeName";
const VALID = /^[A-Za-z0-9]{1,12}$/;
const POLL_MS = 5000;
const FETCH_AFTER_S = 60;
const GIVE_UP_S = 150;

function defaultName(): string {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved && VALID.test(saved)) return saved;
  } catch {
    /* storage can be blocked; a fresh name still works */
  }
  const alphabet = "abcdefghjkmnpqrstuvwxyz23456789";
  let out = "";
  for (let i = 0; i < 6; i++)
    out += alphabet[Math.floor(Math.random() * alphabet.length)];
  return out;
}

function remember(name: string) {
  try {
    window.localStorage.setItem(STORAGE_KEY, name);
  } catch {
    /* not essential */
  }
}

// The API reports an outcome as an engine code. Say it the way the queue does.
function expected(row: JudgeRow): string {
  const code = row.expected.split(" ")[0];
  if (row.expected.startsWith("no exception")) return JUDGE.nothingFlagged;
  return TYPE_COPY[code]?.title ?? row.expected;
}

function arrival(row: JudgeRow): string {
  if (!row.placed) return JUDGE.notPlaced;
  if (!row.paypal_side_ingested) return JUDGE.notYet;
  return row.path === "fallback" ? JUDGE.viaFetch : JUDGE.viaWebhook;
}

type Phase = "idle" | "working" | "done" | "failed";

export default function JudgeScenarios({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState(defaultName);
  const [phase, setPhase] = useState<Phase>("idle");
  const [note, setNote] = useState<string | null>(null);
  const [rows, setRows] = useState<JudgeRow[]>([]);
  const [late, setLate] = useState<string | null>(null);
  const [lateBusy, setLateBusy] = useState(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const valid = VALID.test(name);
  const working = phase === "working";

  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

  // Place whatever is missing, then keep asking until the set is complete. The
  // server never waits: each call does the next step that is possible and
  // answers at once, and asking again is safe (the same name never duplicates).
  const create = async () => {
    if (!valid) return;
    remember(name);
    setPhase("working");
    setLate(null);
    setNote(JUDGE.working);
    const started = Date.now();
    let fetched = false;
    let misses = 0;
    let state: JudgeState | null = null;
    const ask = async (): Promise<JudgeState | null> => {
      try {
        const next = await api.judgeCreate(name);
        misses = 0;
        return next;
      } catch (e) {
        // One failed poll is not a failed run; three in a row is.
        if (++misses >= 3) throw e;
        return state;
      }
    };
    try {
      state = await api.judgeCreate(name);
      for (;;) {
        if (!alive.current) return;
        if (state) setRows(state.scenarios);
        if (state?.error) {
          setPhase("failed");
          setNote(JUDGE.failed(`PayPal reported a problem: ${state.error}.`));
          return;
        }
        if (state?.complete) {
          setPhase("done");
          setNote(JUDGE.done(name.toUpperCase()));
          return;
        }
        const waited = (Date.now() - started) / 1000;
        if (waited >= GIVE_UP_S) {
          setPhase("failed");
          setNote(JUDGE.stuck);
          return;
        }
        if (waited >= FETCH_AFTER_S && !fetched) {
          fetched = true;
          setNote(JUDGE.fetching);
          const after = await api.judgeIngest(name);
          if (after.error) {
            setRows(after.scenarios);
            setPhase("failed");
            setNote(JUDGE.failed(`${after.error}.`));
            return;
          }
          state = await ask();
          continue;
        }
        const placed = state?.scenarios.filter((r) => r.paypal_side_ingested).length ?? 0;
        setNote(placed === 0 && waited < 15 ? JUDGE.working : JUDGE.waiting(placed));
        await sleep(POLL_MS);
        state = await ask();
      }
    } catch (e) {
      if (!alive.current) return;
      setPhase("failed");
      setNote(JUDGE.failed(String(e)));
    }
  };

  const deliverLate = async () => {
    setLateBusy(true);
    try {
      const out = await api.judgeLateRefund(name);
      setLate(out.late_refund === "delivered" ? JUDGE.lateDone : JUDGE.lateAlready);
    } catch (e) {
      setLate(String(e));
    } finally {
      if (alive.current) setLateBusy(false);
    }
  };

  return (
    <section className="card spaced judge" aria-label={JUDGE.title}>
      <div className="card-head">
        <h2>{JUDGE.title}</h2>
        <p className="muted">{JUDGE.intro}</p>
      </div>

      <div className="judge-form">
        <label>
          {JUDGE.nameLabel}
          <input
            value={name}
            maxLength={12}
            disabled={working}
            onChange={(e) => setName(e.target.value.trim())}
            aria-invalid={!valid}
          />
        </label>
        <button className="run-now" disabled={working || !valid} onClick={() => void create()}>
          {working ? "Working..." : JUDGE.create}
        </button>
        <button onClick={onClose} disabled={working}>
          {JUDGE.close}
        </button>
      </div>
      {!valid && <p className="muted">{JUDGE.nameInvalid}</p>}

      {note && (
        <div
          className={`banner ${phase === "done" ? "ok" : phase === "failed" ? "bad" : "busy"}`}
          role="status"
        >
          {note}
        </div>
      )}

      {rows.length > 0 && (
        <table className="judge-table">
          <thead>
            <tr>
              <th>Order</th>
              <th>What it is</th>
              <th>{JUDGE.expect}</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.ref}>
                <td className="mono">{r.ref}</td>
                <td>{r.case}</td>
                <td>{expected(r)}</td>
                <td>{arrival(r)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {phase === "done" && (
        <div className="judge-late">
          <strong>{JUDGE.lateTitle}</strong>
          <p className="muted">{JUDGE.lateHint}</p>
          <button disabled={lateBusy} onClick={() => void deliverLate()}>
            {lateBusy ? "Working..." : JUDGE.late}
          </button>
          {late && <p role="status">{late}</p>}
        </div>
      )}
    </section>
  );
}
