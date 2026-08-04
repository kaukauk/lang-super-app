import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type ChunkCard } from "../lib/api";

// Browse the harvested chunks. Read-only on purpose: Anki owns reviewing, so this
// never schedules or grades anything — it exists to look through what you've
// collected. The list is read live from Anki, so an edit or delete there shows up
// here on the next load.

type Mode = "list" | "cards";
type Side = "both" | "front" | "back";
type First = "korean" | "english";

const SIDES: { key: Side; label: string }[] = [
  { key: "both", label: "Both sides" },
  { key: "front", label: "Front only" },
  { key: "back", label: "Back only" },
];

export default function Chunks() {
  const [cards, setCards] = useState<ChunkCard[] | null>(null);
  const [err, setErr] = useState("");
  const [mode, setMode] = useState<Mode>("list");
  const [side, setSide] = useState<Side>("both");
  const [first, setFirst] = useState<First>("korean");
  const [i, setI] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);

  const load = useCallback(() => {
    setErr("");
    api.chunksBrowse()
      .then((r) => setCards(r.cards))
      .catch((e) => setErr(String(e)));
  }, []);
  useEffect(load, [load]);

  const n = cards?.length ?? 0;
  const card = cards?.[i];

  const go = useCallback((delta: number) => {
    setI((prev) => Math.min(Math.max(prev + delta, 0), Math.max(n - 1, 0)));
    setFlipped(false);
  }, [n]);

  // arrow keys or a/d to move, space to flip
  useEffect(() => {
    if (mode !== "cards") return;
    const onKey = (e: KeyboardEvent) => {
      const k = e.key.toLowerCase();
      if (k === "arrowleft" || k === "a") { e.preventDefault(); go(-1); }
      else if (k === "arrowright" || k === "d") { e.preventDefault(); go(1); }
      else if (k === " " || k === "spacebar") { e.preventDefault(); setFlipped((f) => !f); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode, go]);

  const play = () => { audioRef.current?.load(); audioRef.current?.play().catch(() => {}); };

  // In flashcard mode "first" chooses which side leads. Korean-first is the
  // production direction; English-first only has anything to show on cards that
  // carry a gloss, which is the ones born from a word you looked up.
  const withGloss = useMemo(() => (cards ?? []).filter((c) => c.gloss), [cards]);

  if (err) {
    return (
      <div className="space-y-3">
        <Header />
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          <p className="font-medium">Can’t reach Anki.</p>
          <p className="mt-1">These are read live from your collection, so Anki needs to be open. Start it and try again.</p>
          <button onClick={load} className="mt-2 rounded-md border border-amber-400 px-2.5 py-1 font-medium">Retry</button>
        </div>
      </div>
    );
  }
  if (cards === null) return <p className="text-neutral-500">Loading…</p>;
  if (n === 0) {
    return (
      <div className="space-y-3">
        <Header />
        <p className="text-neutral-500">No harvested chunks yet — finish a sparring session to collect some.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Header count={n} onReload={load} />

      <div className="flex flex-wrap items-center gap-3 text-sm">
        <Toggle value={mode} onChange={(m) => { setMode(m); setFlipped(false); }}
          options={[{ key: "list" as Mode, label: "List" }, { key: "cards" as Mode, label: "Flashcards" }]} />
        {mode === "list" ? (
          <Toggle<Side> value={side} onChange={setSide} options={SIDES} />
        ) : (
          <>
            <Toggle value={first} onChange={(f) => { setFirst(f); setFlipped(false); }}
              options={[{ key: "korean" as First, label: "Korean first" },
                        { key: "english" as First, label: "English first" }]} />
            <span className="text-neutral-400">← → or A/D to move · space to flip</span>
          </>
        )}
      </div>

      {mode === "cards" && first === "english" && withGloss.length < n && (
        <p className="rounded-lg bg-neutral-100 px-3 py-1.5 text-xs text-neutral-500 dark:bg-neutral-800">
          Only {withGloss.length} of {n} carry English — the rest are Korean-only by design,
          and show their Korean side either way.
        </p>
      )}

      {mode === "list" ? (
        <div className="divide-y divide-neutral-200 overflow-hidden rounded-xl border border-neutral-200 dark:divide-neutral-800 dark:border-neutral-800">
          {cards.map((c) => (
            <div key={c.note_id} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 bg-white px-4 py-3 dark:bg-neutral-900">
              {side !== "back" && (
                <div className="min-w-[14rem] flex-1">
                  {c.context && <div className="text-xs text-neutral-400">{c.context}</div>}
                  {c.ending && <span className="mr-2 rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-500 dark:bg-neutral-800">{c.ending}</span>}
                  <span className="text-lg">{c.prompt}</span>
                </div>
              )}
              {side !== "front" && (
                <div className="min-w-[14rem] flex-1">
                  <span className="text-lg font-medium">{c.chunk}</span>
                  {c.anchor && <div className="text-sm text-neutral-500">{c.anchor} → {c.target}</div>}
                  {c.gloss && <div className="text-sm italic text-neutral-500">{c.gloss}</div>}
                </div>
              )}
              {c.tags.includes("lookup") && (
                <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] uppercase tracking-wide text-amber-700 dark:bg-amber-950/50 dark:text-amber-300">looked up</span>
              )}
            </div>
          ))}
        </div>
      ) : (
        card && (
          <div className="space-y-3">
            <div
              onClick={() => setFlipped((f) => !f)}
              className="flex min-h-[16rem] cursor-pointer select-none flex-col items-center justify-center rounded-xl border border-neutral-200 bg-white p-8 text-center dark:border-neutral-800 dark:bg-neutral-900"
            >
              {/* leading side: korean-first shows the blanked prompt, english-first the gloss */}
              {!flipped ? (
                first === "korean" ? (
                  <>
                    {card.context && <div className="mb-3 text-sm text-neutral-400">{card.context}</div>}
                    {card.ending && <div className="mb-3 rounded-full bg-neutral-100 px-3 py-1 text-sm text-neutral-500 dark:bg-neutral-800">{card.ending}</div>}
                    <div className="text-3xl font-semibold leading-relaxed">{card.prompt}</div>
                  </>
                ) : (
                  <div className="text-2xl italic text-neutral-600 dark:text-neutral-300">
                    {card.gloss || <span className="text-base not-italic text-neutral-400">(Korean-only card — flip for the Korean)</span>}
                  </div>
                )
              ) : (
                <>
                  <div className="text-3xl font-semibold leading-relaxed">{card.chunk}</div>
                  {card.anchor && <div className="mt-3 text-lg text-neutral-500">{card.anchor} <span className="text-neutral-400">→</span> {card.target}</div>}
                  {card.gloss && first === "korean" && <div className="mt-3 italic text-neutral-500">{card.gloss}</div>}
                </>
              )}
              <div className="mt-6 text-xs text-neutral-400">{flipped ? "space to flip back" : "space to flip"}</div>
            </div>

            <div className="flex items-center gap-3">
              <button onClick={() => go(-1)} disabled={i === 0}
                className="rounded-lg border px-3 py-1.5 text-sm disabled:opacity-40 dark:border-neutral-700">← Prev</button>
              <span className="tabular-nums text-sm text-neutral-500">{i + 1} / {n}</span>
              <button onClick={() => go(1)} disabled={i >= n - 1}
                className="rounded-lg border px-3 py-1.5 text-sm disabled:opacity-40 dark:border-neutral-700">Next →</button>
              {card.audio && (
                <>
                  <button onClick={play} className="ml-auto rounded-lg border px-3 py-1.5 text-sm dark:border-neutral-700">▶ Audio</button>
                  <audio ref={audioRef} src={api.chunkAudioUrl(card.audio)} />
                </>
              )}
            </div>
          </div>
        )
      )}
    </div>
  );
}

function Header({ count, onReload }: { count?: number; onReload?: () => void }) {
  return (
    <div className="flex items-center gap-3">
      <h1 className="font-semibold">Harvested chunks</h1>
      {count !== undefined && <span className="text-sm text-neutral-500">{count} in Korean::Chunks</span>}
      {onReload && (
        <button onClick={onReload} className="ml-auto text-sm text-neutral-500 hover:underline">Reload from Anki</button>
      )}
    </div>
  );
}

function Toggle<T extends string>({ value, onChange, options }: {
  value: T; onChange: (v: T) => void; options: { key: T; label: string }[];
}) {
  return (
    <div className="inline-flex rounded-lg border border-neutral-300 p-0.5 dark:border-neutral-700">
      {options.map((o) => (
        <button key={o.key} onClick={() => onChange(o.key)}
          className={`rounded-md px-2.5 py-1 ${value === o.key ? "bg-indigo-600 text-white" : ""}`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
