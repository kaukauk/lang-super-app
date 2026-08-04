import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type SparOpening, type SparReply } from "../lib/api";
import ClickableKorean from "../components/ClickableKorean";

type Msg =
  | { role: "user"; text: string }
  | { role: "assistant"; text: string; meta: SparReply };

const BUDGET_S = 15 * 60;   // the ~15-minute roleplay block

export default function Spar() {
  const { sid } = useParams();
  const segmentId = Number(sid);
  const [session, setSession] = useState<number | null>(null);
  const [opening, setOpening] = useState<SparOpening | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [pushed, setPushed] = useState<number | null>(null);
  const [harvested, setHarvested] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const endRef = useRef<HTMLDivElement>(null);

  // States 1+2: start the session and run the Anchor + Prompt opener.
  useEffect(() => {
    let alive = true;
    api.sparStart(segmentId).then(async (s) => {
      if (!alive) return;
      setSession(s.session_id);
      try {
        const op = await api.sparOpen(s.session_id, segmentId);
        if (alive) setOpening(op);
      } catch { /* opener is best-effort */ }
    });
    return () => { alive = false; };
  }, [segmentId]);

  // 15-minute timer (drives the wind-down nudge)
  useEffect(() => {
    if (!opening) return;
    const t = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(t);
  }, [opening]);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);

  const windDown = elapsed >= BUDGET_S;

  const send = async () => {
    if (!input.trim() || !session || busy) return;
    const text = input.trim();
    setInput("");
    setMsgs((m) => [...m, { role: "user", text }]);
    setBusy(true);
    try {
      const reply = await api.sparTurn(session, { text, wind_down: windDown });
      setMsgs((m) => [...m, { role: "assistant", text: reply.reply_ko, meta: reply }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: "assistant", text: `(error: ${e})`, meta: {} as SparReply }]);
    } finally {
      setBusy(false);
    }
  };

  const harvest = async () => {
    if (!session) return;
    const r = await api.sparHarvest(session, true);
    // words you looked up become cards too, so count them in what you report back
    setHarvested(r.chunks.length + (r.lookups?.length ?? 0));
    setPushed(r.anki?.error ? -1 : (r.anki?.pushed ?? 0));
  };

  return (
    <div className="flex h-[calc(100vh-7rem)] flex-col">
      <div className="mb-3 flex items-center gap-3">
        <Link to="/" className="text-sm text-neutral-500 hover:underline">← Library</Link>
        <h1 className="font-semibold">Sparring</h1>
        {opening?.role_en && (
          <span className="rounded-full bg-indigo-100 px-2.5 py-0.5 text-xs font-medium text-indigo-700 dark:bg-indigo-950/50 dark:text-indigo-300">
            You play: {opening.role_en}
          </span>
        )}
        <span className={`tabular-nums text-xs ${windDown ? "font-semibold text-amber-600" : "text-neutral-400"}`}>
          {Math.floor(elapsed / 60)}:{(elapsed % 60).toString().padStart(2, "0")} / 15:00{windDown ? " · wrap up" : ""}
        </span>
        <button onClick={harvest} className="ml-auto rounded-lg border px-3 py-1.5 text-sm dark:border-neutral-700">
          End &amp; harvest chunks
        </button>
      </div>

      {harvested !== null && (
        <p className="mb-2 rounded-lg bg-emerald-50 p-2 text-sm text-emerald-700 dark:bg-emerald-950/40">
          Harvested {harvested} new chunk{harvested === 1 ? "" : "s"} for tomorrow's warm-up.
          {pushed !== null && pushed >= 0 && ` ${pushed} pushed to Korean::Chunks.`}
          {pushed === -1 && " Anki wasn't reachable — the chunks are saved, push again later."}
        </p>
      )}

      <div className="flex-1 space-y-4 overflow-y-auto rounded-xl border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
        {/* States 1+2: the scene anchor + your role + the floor */}
        {opening && (
          <div className="space-y-2 rounded-xl border border-indigo-200 bg-indigo-50/60 p-3 dark:border-indigo-900 dark:bg-indigo-950/30">
            {opening.anchor_ko && (
              <div>
                <div className="text-xs uppercase tracking-wide text-indigo-500">The scene, picking up…</div>
                <div className="text-lg">{opening.anchor_ko}</div>
              </div>
            )}
            {opening.prompt_en && (
              <p className="text-sm text-indigo-800 dark:text-indigo-200">🎭 {opening.prompt_en}</p>
            )}
          </div>
        )}
        {!opening && msgs.length === 0 && (
          <p className="text-sm text-neutral-500">Setting the scene…</p>
        )}
        {msgs.map((m, idx) =>
          m.role === "user" ? (
            <div key={idx} className="flex justify-end">
              <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2 text-white">{m.text}</div>
            </div>
          ) : (
            <div key={idx} className="max-w-[85%] space-y-2">
              <div className="rounded-2xl rounded-bl-sm bg-neutral-100 px-4 py-2 dark:bg-neutral-800">
                {/* click any word you don't know — it glosses inline and joins the harvest */}
                {session ? (
                  <ClickableKorean sessionId={session} text={m.text} glosses={m.meta.glosses} />
                ) : (
                  <div className="text-lg">{m.text}</div>
                )}
              </div>
              {m.meta.corrections?.length > 0 && (
                <div className="space-y-1 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 dark:border-rose-900 dark:bg-rose-950/30">
                  {m.meta.corrections.map((c, ci) => (
                    <div key={ci} className="text-sm">
                      <span className="text-rose-400 line-through decoration-rose-300">{c.original}</span>
                      <span className="mx-1.5 text-rose-400">→</span>
                      <span className="font-medium text-rose-800 dark:text-rose-200">{c.corrected}</span>
                      {c.note_en && <span className="ml-2 text-xs text-rose-600 dark:text-rose-400">{c.note_en}</span>}
                      {/* naturalness isn't an error — say so, or it reads as one */}
                      {c.kind === "naturalness" && (
                        <span className="ml-1.5 rounded-full bg-rose-100 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-rose-500 dark:bg-rose-900/50">
                          sounds off
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {m.meta.micro_feedback_en && (
                <div className="rounded-lg bg-amber-50 px-3 py-1.5 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
                  💡 {m.meta.micro_feedback_en}
                </div>
              )}
              <div className="flex flex-wrap items-center gap-2 text-xs">
                {m.meta.targets_used?.map((t) => (
                  <span key={t} className="rounded-full bg-emerald-100 px-2 py-0.5 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300">✓ {t}</span>
                ))}
                {m.meta.unexpected?.length > 0 && (
                  <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-neutral-500 dark:bg-neutral-800">new: {m.meta.unexpected.join(", ")}</span>
                )}
              </div>
              {m.meta.suggested_user_reply_ko && (
                <button
                  onClick={() => setInput(m.meta.suggested_user_reply_ko)}
                  className="rounded-lg border border-dashed border-neutral-300 px-3 py-1.5 text-left text-sm hover:border-indigo-400 dark:border-neutral-700"
                  title="Use this reply"
                >
                  <span className="text-neutral-400">suggested →</span> {m.meta.suggested_user_reply_ko}
                  <span className="ml-1 text-neutral-400">({m.meta.suggested_user_reply_en})</span>
                </button>
              )}
            </div>
          ),
        )}
        <div ref={endRef} />
      </div>

      <div className="mt-3 flex gap-2">
        <input
          value={input} onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="한국어로 입력하세요…"
          className="flex-1 rounded-lg border border-neutral-300 px-3 py-2 dark:border-neutral-700 dark:bg-neutral-950"
        />
        <button onClick={send} disabled={busy} className="rounded-lg bg-indigo-600 px-5 py-2 font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
          {busy ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}
