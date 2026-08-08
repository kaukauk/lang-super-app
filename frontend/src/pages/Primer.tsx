import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type PrimerItem } from "../lib/api";

// Phase 1B "Hit List": shadow each target sentence with its clean TTS audio,
// then flip for the in-context hint. This surface never enters Anki.
export default function Primer() {
  const { sid } = useParams();
  const id = Number(sid);
  const [items, setItems] = useState<PrimerItem[] | null>(null);
  const [i, setI] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const [building, setBuilding] = useState(false);
  const [err, setErr] = useState("");
  const audioRef = useRef<HTMLAudioElement>(null);

  const load = () => api.primer(id).then(setItems).catch((e) => setErr(String(e)));
  useEffect(() => { load(); }, [id]);

  // autoplay the clean TTS for the current card (shadow it)
  useEffect(() => {
    setRevealed(false);
    audioRef.current?.load();
    audioRef.current?.play().catch(() => {});
  }, [i, items]);

  const build = async () => {
    setBuilding(true); setErr("");
    try { await api.buildAv(id, { push: true }); await load(); setI(0); }
    catch (e) { setErr(String(e)); }
    finally { setBuilding(false); }
  };

  if (items === null) return <p className="text-neutral-500">Loading…</p>;

  if (items.length === 0) {
    return (
      <div className="space-y-4">
        <BackBar id={id} />
        <div className="rounded-xl border border-neutral-200 bg-white p-6 text-center dark:border-neutral-800 dark:bg-neutral-900">
          <p className="mb-3 text-neutral-600 dark:text-neutral-300">
            This day hasn't been built yet. Building downloads the clip, transcribes it
            with Whisper, picks 5 target sentences, generates the primer audio, and
            pushes the audio/video cards into Anki.
          </p>
          <button onClick={build} disabled={building}
            className="rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
            {building ? "Building… (this can take a few minutes)" : "Build this day"}
          </button>
          {err && <p className="mt-3 text-sm text-red-600">{err}</p>}
        </div>
      </div>
    );
  }

  const it = items[i];
  return (
    <div className="space-y-4">
      <BackBar id={id} />
      <div className="flex items-center justify-between text-sm text-neutral-500">
        <span>Target {i + 1} of {items.length}</span>
        <Link to={`/watch/${id}`} className="rounded-lg border px-3 py-1.5 dark:border-neutral-700">Watch the clip →</Link>
      </div>

      <div className="rounded-2xl border border-neutral-200 bg-white p-10 text-center dark:border-neutral-800 dark:bg-neutral-900">
        {/* Lead-up in Korean, read not recalled: it sets the scene for the target
            without becoming part of what you have to produce. Deliberately quieter
            and smaller so the eye lands on the target first. */}
        {it.context_ko && (
          <p className="mx-auto mb-4 max-w-2xl text-base leading-relaxed text-neutral-400 dark:text-neutral-500">
            {it.context_ko}
          </p>
        )}
        <div className="text-3xl font-semibold leading-snug">{it.target_ko}</div>
        {/* audio is the target clause only — the context above is not spoken */}
        <audio ref={audioRef} src={api.primerAudioUrl(id, it.target_idx)} />
        <div className="mt-6 flex items-center justify-center gap-3">
          <button onClick={() => { audioRef.current?.load(); audioRef.current?.play().catch(() => {}); }}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700">
            ▶ Play & shadow
          </button>
          <button onClick={() => setRevealed((r) => !r)}
            className="rounded-lg border px-4 py-2 text-sm dark:border-neutral-700">
            {revealed ? "Hide hint" : "Reveal hint"}
          </button>
        </div>
        {revealed && (
          <p className="mt-6 text-neutral-500">{it.hint_en || "(no hint)"}</p>
        )}
      </div>

      <div className="flex justify-between">
        <button onClick={() => setI((n) => Math.max(0, n - 1))} disabled={i === 0}
          className="rounded-lg border px-4 py-2 text-sm disabled:opacity-40 dark:border-neutral-700">← Prev</button>
        {i < items.length - 1 ? (
          <button onClick={() => setI((n) => n + 1)}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700">Next →</button>
        ) : (
          <Link to={`/watch/${id}`} className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700">
            Done — watch the clip →
          </Link>
        )}
      </div>
    </div>
  );
}

function BackBar({ id }: { id: number }) {
  return (
    <div className="flex items-center gap-3">
      <Link to="/" className="text-sm text-neutral-500 hover:underline">← Library</Link>
      <h1 className="font-semibold">Primer · day {id} <span className="font-normal text-neutral-500">— shadow the targets</span></h1>
    </div>
  );
}
