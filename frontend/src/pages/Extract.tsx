import { useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { api, type Chunk, type Extraction } from "../lib/api";
import Markdown from "../components/Markdown";

const TYPE_LABEL: Record<string, string> = {
  sentence: "Sentences",
  noun_verb: "Noun–verb",
  grammar: "Grammar",
};

export default function Extract() {
  const { sid } = useParams();
  const id = Number(sid);
  const nav = useNavigate();
  const [res, setRes] = useState<Extraction | null>(null);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [accepted, setAccepted] = useState<number | null>(null);

  const run = async () => {
    setBusy(true); setErr("");
    try {
      const r = await api.extract(id, { accept: false });
      setRes(r);
      setChunks(r.chunks);
    } catch (e) { setErr(String(e)); }
    finally { setBusy(false); }
  };

  const edit = (i: number, field: keyof Chunk, v: string) =>
    setChunks((cs) => cs.map((c, j) => (j === i ? { ...c, [field]: v } : c)));
  const remove = (i: number) => setChunks((cs) => cs.filter((_, j) => j !== i));

  const accept = async () => {
    setBusy(true);
    try {
      const r = await api.acceptChunks({ chunks, segment_id: id });
      setAccepted(r.accepted);
    } catch (e) { setErr(String(e)); }
    finally { setBusy(false); }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Link to="/" className="text-sm text-neutral-500 hover:underline">← Library</Link>
        <h1 className="font-semibold">AI extraction — segment {id}</h1>
        <Link to={`/watch/${id}`} className="ml-auto rounded-lg border px-3 py-1.5 text-sm dark:border-neutral-700">Watch →</Link>
      </div>

      {!res && (
        <button onClick={run} disabled={busy}
          className="rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
          {busy ? "Extracting… (this calls the model)" : "Run extraction"}
        </button>
      )}
      {err && <p className="text-sm text-red-600">{err}</p>}

      {res && (
        <>
          <section className="rounded-xl border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
            <h2 className="mb-2 font-semibold">Narrative primer (read this before watching)</h2>
            <div className="text-sm leading-relaxed text-neutral-700 dark:text-neutral-300">
              <Markdown text={res.primer_md} />
            </div>
          </section>

          {res.warnings.length > 0 && (
            <p className="rounded-lg bg-amber-50 p-2 text-sm text-amber-800 dark:bg-amber-950/40">
              {res.warnings.join("; ")}
            </p>
          )}

          <section className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold">{chunks.length} chunks — review &amp; edit before accepting</h2>
              {accepted === null ? (
                <button onClick={accept} disabled={busy || chunks.length === 0}
                  className="rounded-lg bg-emerald-600 px-4 py-2 font-semibold text-white hover:bg-emerald-700 disabled:opacity-50">
                  Accept {chunks.length} → deck (due tomorrow)
                </button>
              ) : (
                <button onClick={() => nav("/")} className="rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white">
                  Added {accepted} cards ✓ — Done
                </button>
              )}
            </div>

            {["sentence", "noun_verb", "grammar"].map((t) => (
              <div key={t}>
                <h3 className="mb-1 text-sm font-medium text-neutral-500">{TYPE_LABEL[t]}</h3>
                <div className="space-y-2">
                  {chunks.map((c, i) => c.type === t && (
                    <div key={i} className="flex flex-wrap items-center gap-2 rounded-lg border border-neutral-200 p-2 dark:border-neutral-800">
                      <input value={c.ko} onChange={(e) => edit(i, "ko", e.target.value)}
                        className="min-w-[12rem] flex-1 rounded border-0 bg-transparent text-lg focus:outline-none" />
                      <input value={c.en} onChange={(e) => edit(i, "en", e.target.value)}
                        className="min-w-[10rem] flex-1 rounded bg-neutral-50 px-2 py-1 text-sm dark:bg-neutral-800" placeholder="English" />
                      <input value={c.gloss} onChange={(e) => edit(i, "gloss", e.target.value)}
                        className="w-32 rounded bg-neutral-50 px-2 py-1 text-sm dark:bg-neutral-800" placeholder="gloss" />
                      <button onClick={() => remove(i)} className="rounded px-2 text-neutral-400 hover:text-red-600" title="remove">✕</button>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </section>
        </>
      )}
    </div>
  );
}
