import { useMemo, useRef, useState } from "react";
import type { Cue } from "../lib/api";
import { fmt } from "../lib/fmt";

type Block = { start_s: number; end_s: number; text: string };

// Merge caption cues into sentence-ish blocks (Korean auto-captions lack punctuation).
function groupIntoSentences(cues: Cue[]): Block[] {
  const out: Block[] = [];
  let cur: { start_s: number; end_s: number; texts: string[] } | null = null;
  const PUNCT = /[.?!。…！？]$/;
  for (let i = 0; i < cues.length; i++) {
    const c = cues[i];
    const text = c.text.trim();
    if (!cur) cur = { start_s: c.start_s, end_s: c.start_s + c.dur_s, texts: [text] };
    else { cur.end_s = c.start_s + c.dur_s; cur.texts.push(text); }
    const next = cues[i + 1];
    const gap = next ? next.start_s - (c.start_s + c.dur_s) : Infinity;
    const joined = cur.texts.join(" ");
    if (PUNCT.test(text) || gap > 1.2 || joined.length > 60 || !next) {
      out.push({ start_s: cur.start_s, end_s: cur.end_s, text: joined });
      cur = null;
    }
  }
  return out;
}

type Props = {
  src: string;          // the local snippet URL
  baseS: number;        // segment start in full-video time (cues are absolute)
  mode: "blind" | "study";
  cues?: Cue[];
  onSaveCue?: (text: string) => void;
};

// Plays the locally-cut ~5-min snippet (frame-accurate, no scrubbing in blind mode)
// with a Korean-subtitle overlay synced from the segment's cues.
export default function LocalPlayer({ src, baseS, mode, cues = [], onSaveCue }: Props) {
  const vidRef = useRef<HTMLVideoElement>(null);
  const [time, setTime] = useState(0);       // snippet-relative seconds
  const [dur, setDur] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [unit, setUnit] = useState<"line" | "sentence">("line");
  const [offset, setOffset] = useState(0);

  // cues are absolute; shift into snippet time
  const lineBlocks = useMemo<Block[]>(
    () => cues.map((c) => ({ start_s: c.start_s - baseS, end_s: c.start_s + c.dur_s - baseS, text: c.text.trim() })),
    [cues, baseS],
  );
  const sentences = useMemo(
    () => groupIntoSentences(cues).map((b) => ({ ...b, start_s: b.start_s - baseS, end_s: b.end_s - baseS })),
    [cues, baseS],
  );
  const items = unit === "line" ? lineBlocks : sentences;
  const t = time - offset;
  const active = useMemo<Block | undefined>(() => {
    let idx = -1;
    for (let i = 0; i < items.length; i++) { if (items[i].start_s <= t) idx = i; else break; }
    if (idx < 0) return undefined;
    const cur = items[idx];
    const nextStart = items[idx + 1]?.start_s ?? Infinity;
    return t < Math.min(nextStart, cur.end_s + 1.0) ? cur : undefined;
  }, [items, t]);

  const progress = dur ? Math.min(100, (time / dur) * 100) : 0;
  const toggle = () => { const v = vidRef.current; if (!v) return; v.paused ? v.play() : v.pause(); };

  return (
    <div>
      <div className="relative aspect-video w-full overflow-hidden rounded-xl bg-black">
        <video
          ref={vidRef}
          src={src}
          className="h-full w-full"
          controls={mode === "study"}
          controlsList="nodownload noplaybackrate"
          onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
          onLoadedMetadata={(e) => setDur(e.currentTarget.duration)}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
        />
        {mode === "study" && active && (
          <button
            onClick={() => { vidRef.current?.pause(); onSaveCue?.(active.text); }}
            className="group absolute bottom-12 left-1/2 max-w-[92%] -translate-x-1/2 rounded-lg bg-black/75 px-4 py-2 text-center text-xl font-medium leading-snug text-white hover:bg-indigo-700"
            title="Click to save this as a card"
          >
            {active.text}
            <span className="ml-2 hidden text-xs text-indigo-200 group-hover:inline">＋ save</span>
          </button>
        )}
      </div>

      {mode === "study" && (
        <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
          <div className="inline-flex rounded-lg border border-neutral-300 p-0.5 dark:border-neutral-700">
            {(["line", "sentence"] as const).map((u) => (
              <button key={u} onClick={() => setUnit(u)}
                className={`rounded-md px-2.5 py-1 capitalize ${unit === u ? "bg-indigo-600 text-white" : ""}`}>{u}</button>
            ))}
          </div>
          <div className="inline-flex items-center gap-1">
            <span className="text-neutral-500">Subtitle timing</span>
            <button onClick={() => setOffset((o) => +(o - 0.5).toFixed(1))} className="rounded border px-2 py-1 dark:border-neutral-700">−0.5s</button>
            <span className="w-16 text-center tabular-nums">{offset > 0 ? "+" : ""}{offset.toFixed(1)}s</span>
            <button onClick={() => setOffset((o) => +(o + 0.5).toFixed(1))} className="rounded border px-2 py-1 dark:border-neutral-700">+0.5s</button>
            {offset !== 0 && <button onClick={() => setOffset(0)} className="ml-1 text-neutral-500 hover:underline">reset</button>}
          </div>
        </div>
      )}

      <div className="mt-3 flex items-center gap-3">
        <button onClick={toggle} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700">
          {playing ? "Pause" : "Play"}
        </button>
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800">
          <div className="h-full bg-indigo-500" style={{ width: `${progress}%` }} />
        </div>
        <span className="tabular-nums text-sm text-neutral-500">{fmt(time)} / {fmt(dur)}</span>
      </div>
    </div>
  );
}
