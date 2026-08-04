import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type PrimerItem } from "../lib/api";

// The between-passes check. After the blind watch, each target line comes up with
// its clean TTS: did you catch it? "Caught it" moves on. "Missed it" plays the
// scene clip — the same file the Anki card embeds, so recognising it here is the
// same act as recognising it in review. Then the second pass is informed rather
// than a blind repeat.
export default function Check() {
  const { sid } = useParams();
  const id = Number(sid);
  const [items, setItems] = useState<PrimerItem[] | null>(null);
  const [i, setI] = useState(0);
  const [err, setErr] = useState("");
  const audioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => { api.primer(id).then(setItems).catch((e) => setErr(String(e))); }, [id]);

  // shadow the line first — the check is "did I catch this", so it must be heard
  useEffect(() => {
    audioRef.current?.load();
    audioRef.current?.play().catch(() => {});
  }, [i, items]);

  if (err) return <p className="text-sm text-red-600">{err}</p>;
  if (items === null) return <p className="text-neutral-500">Loading…</p>;
  if (items.length === 0) {
    return (
      <div className="space-y-3">
        <BackBar id={id} />
        <p className="text-neutral-500">
          Nothing to check yet — <Link to={`/primer/${id}`} className="underline">build this day</Link> first.
        </p>
      </div>
    );
  }

  const it = items[i];
  const done = items.filter((x) => x.seen !== null).length;
  const missed = items.filter((x) => x.seen === false).length;

  const mark = async (seen: boolean) => {
    setItems((prev) => prev && prev.map((x) => (x.target_idx === it.target_idx ? { ...x, seen } : x)));
    try { await api.markSeen(id, it.target_idx, seen); } catch (e) { setErr(String(e)); }
  };
  const next = () => setI((n) => Math.min(n + 1, items.length - 1));

  return (
    <div className="space-y-4">
      <BackBar id={id} />

      <div className="flex items-center gap-3 text-sm text-neutral-500">
        <span>Line {i + 1} of {items.length}</span>
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800">
          <div className="h-full bg-indigo-500 transition-all" style={{ width: `${(done / items.length) * 100}%` }} />
        </div>
        {missed > 0 && <span className="text-amber-600">{missed} missed</span>}
      </div>

      <div className="rounded-xl border border-neutral-200 bg-white p-6 dark:border-neutral-800 dark:bg-neutral-900">
        {it.context_ko && (
          <div className="mb-2 text-sm text-neutral-400">{it.context_ko}</div>
        )}
        <div className="text-2xl font-semibold leading-relaxed">{it.target_ko}</div>
        <audio ref={audioRef} src={api.primerAudioUrl(id, it.target_idx)} />
        <button
          onClick={() => { audioRef.current?.load(); audioRef.current?.play().catch(() => {}); }}
          className="mt-3 rounded-lg border px-3 py-1 text-sm dark:border-neutral-700"
        >
          ▶ Replay audio
        </button>

        {it.seen === null ? (
          <div className="mt-5 flex gap-2">
            <button onClick={() => mark(true)}
              className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700">
              Caught it
            </button>
            <button onClick={() => mark(false)}
              className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-700">
              Missed it
            </button>
          </div>
        ) : (
          <div className="mt-5 space-y-3">
            {/* Only a miss earns the clip. Showing it on a hit would turn the check
                into a rewatch and remove the reason to answer honestly. */}
            {it.seen === false && (
              it.has_clip ? (
                <>
                  <video
                    key={it.target_idx}
                    src={api.primerClipUrl(id, it.target_idx)}
                    controls autoPlay playsInline
                    className="w-full rounded-lg bg-black"
                  />
                  <p className="text-sm text-neutral-500">{it.hint_en}</p>
                </>
              ) : (
                <p className="text-sm text-neutral-500">No clip was built for this line.</p>
              )
            )}
            <div className="flex items-center gap-2">
              <button onClick={() => mark(it.seen === false ? true : false)}
                className="text-sm text-neutral-500 hover:underline">
                undo — mark as {it.seen === false ? "caught" : "missed"}
              </button>
              <div className="ml-auto">
                {i < items.length - 1 ? (
                  <button onClick={next}
                    className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700">
                    Next line →
                  </button>
                ) : (
                  <Link to={`/watch/${id}`}
                    className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700">
                    Start second pass →
                  </Link>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-1.5">
        {items.map((x, n) => (
          <button key={x.target_idx} onClick={() => setI(n)}
            title={x.target_ko}
            className={`h-2.5 w-8 rounded-full ${
              n === i ? "ring-2 ring-indigo-400 " : ""
            }${x.seen === null ? "bg-neutral-300 dark:bg-neutral-700"
              : x.seen ? "bg-emerald-500" : "bg-amber-500"}`} />
        ))}
      </div>
    </div>
  );
}

function BackBar({ id }: { id: number }) {
  return (
    <div className="flex items-center gap-3">
      <Link to="/" className="text-sm text-neutral-500 hover:underline">← Library</Link>
      <h1 className="font-semibold">Primer check — did you catch these?</h1>
      <Link to={`/watch/${id}`} className="ml-auto text-sm text-neutral-500 hover:underline">
        Skip to watch →
      </Link>
    </div>
  );
}
