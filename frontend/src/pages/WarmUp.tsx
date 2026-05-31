import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Card } from "../lib/api";

const GRADES = [
  { key: "again", label: "Again", hint: "1", cls: "bg-red-600 hover:bg-red-700" },
  { key: "hard", label: "Hard", hint: "2", cls: "bg-amber-600 hover:bg-amber-700" },
  { key: "good", label: "Good", hint: "3", cls: "bg-emerald-600 hover:bg-emerald-700" },
  { key: "easy", label: "Easy", hint: "4", cls: "bg-sky-600 hover:bg-sky-700" },
];

export default function WarmUp() {
  const [queue, setQueue] = useState<Card[]>([]);
  const [i, setI] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const [done, setDone] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.due().then((cards) => { setQueue(cards); setLoading(false); });
  }, []);

  const card = queue[i];

  const grade = useCallback(async (g: string) => {
    if (!card || !revealed) return;
    await api.grade(card.id, g);
    setDone((d) => d + 1);
    setRevealed(false);
    setI((x) => x + 1);
  }, [card, revealed]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!card) return;
      if ((e.key === " " || e.key === "Enter") && !revealed) { e.preventDefault(); setRevealed(true); return; }
      if (revealed) {
        const g = GRADES.find((x) => x.hint === e.key);
        if (g) { e.preventDefault(); grade(g.key); }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [card, revealed, grade]);

  if (loading) return <p className="text-neutral-500">Loading reviews…</p>;

  if (!card) {
    return (
      <div className="rounded-xl border border-neutral-200 bg-white p-10 text-center dark:border-neutral-800 dark:bg-neutral-900">
        <div className="text-5xl">🎉</div>
        <h2 className="mt-3 text-xl font-semibold">Warm-up complete</h2>
        <p className="mt-1 text-neutral-500">You reviewed {done} card{done === 1 ? "" : "s"}.</p>
        <Link to="/" className="mt-4 inline-block rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-700">
          Back to library
        </Link>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-3 flex items-center justify-between text-sm text-neutral-500">
        <span>{done + 1} / {queue.length}</span>
        <span className="rounded-full bg-neutral-200 px-2 py-0.5 dark:bg-neutral-800">{card.kind}</span>
      </div>

      <div className="flex min-h-[40vh] flex-col items-center justify-center rounded-xl border border-neutral-200 bg-white p-8 text-center dark:border-neutral-800 dark:bg-neutral-900">
        <div className="text-3xl font-semibold leading-relaxed">{card.front_ko}</div>
        {revealed && (
          <div className="mt-6 border-t border-neutral-200 pt-6 dark:border-neutral-800">
            <div className="text-xl">{card.back_en}</div>
            {card.gloss && <div className="mt-2 text-neutral-500">{card.gloss}</div>}
          </div>
        )}
      </div>

      <div className="mt-4">
        {!revealed ? (
          <button onClick={() => setRevealed(true)}
            className="w-full rounded-lg bg-neutral-800 py-3 font-semibold text-white hover:bg-neutral-700 dark:bg-neutral-200 dark:text-neutral-900">
            Show answer <span className="opacity-60">(space)</span>
          </button>
        ) : (
          <div className="grid grid-cols-4 gap-2">
            {GRADES.map((g) => (
              <button key={g.key} onClick={() => grade(g.key)}
                className={`rounded-lg py-3 font-semibold text-white ${g.cls}`}>
                {g.label}<span className="ml-1 opacity-70">{g.hint}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
