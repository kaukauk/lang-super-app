import { useEffect, useMemo, useRef, useState } from "react";
import type { Cue } from "../lib/api";
import { fmt } from "../lib/fmt";

let apiReady: Promise<void> | null = null;
function loadYT(): Promise<void> {
  if ((window as any).YT?.Player) return Promise.resolve();
  if (apiReady) return apiReady;
  apiReady = new Promise((resolve) => {
    (window as any).onYouTubeIframeAPIReady = () => resolve();
    const s = document.createElement("script");
    s.src = "https://www.youtube.com/iframe_api";
    document.head.appendChild(s);
  });
  return apiReady;
}

type Block = { start_s: number; end_s: number; text: string };

// Merge consecutive caption cues into sentence-ish blocks. Korean auto-captions
// often lack punctuation, so we break on sentence punctuation OR a noticeable
// pause OR length.
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
  youtubeId: string;
  startS: number;
  endS: number;
  mode: "blind" | "study";
  cues?: Cue[];
  onSaveCue?: (text: string) => void;
};

export default function Player({ youtubeId, startS, endS, mode, cues = [], onSaveCue }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const playerRef = useRef<any>(null);
  const [time, setTime] = useState(startS);
  const [playing, setPlaying] = useState(false);
  const [unit, setUnit] = useState<"line" | "sentence">("line");
  const [offset, setOffset] = useState(0); // +later, -earlier (seconds)

  useEffect(() => {
    let interval: any;
    let cancelled = false;
    loadYT().then(() => {
      if (cancelled || !hostRef.current) return;
      const inner = document.createElement("div");
      hostRef.current.innerHTML = "";
      hostRef.current.appendChild(inner);
      playerRef.current = new (window as any).YT.Player(inner, {
        videoId: youtubeId,
        playerVars: {
          start: Math.floor(startS),
          controls: mode === "blind" ? 0 : 1,
          disablekb: 1, rel: 0, modestbranding: 1, cc_load_policy: 0,
        },
        events: { onStateChange: (e: any) => setPlaying(e.data === 1) },
      });
      interval = setInterval(() => {
        const p = playerRef.current;
        if (!p?.getCurrentTime) return;
        const t = p.getCurrentTime();
        setTime(t);
        if (t >= endS) p.pauseVideo();
      }, 150);
    });
    return () => {
      cancelled = true;
      clearInterval(interval);
      try { playerRef.current?.destroy(); } catch { /* noop */ }
    };
  }, [youtubeId, startS, endS, mode]);

  const lineBlocks = useMemo<Block[]>(
    () => cues.map((c) => ({ start_s: c.start_s, end_s: c.start_s + c.dur_s, text: c.text.trim() })),
    [cues],
  );
  const sentences = useMemo(() => groupIntoSentences(cues), [cues]);
  const items = unit === "line" ? lineBlocks : sentences;
  // +offset = subtitles appear later; -offset = earlier.
  const t = time - offset;
  // Show the most recently STARTED caption and switch the instant the next one
  // begins. YouTube auto-caption durations overlap and run long, so matching by
  // [start, start+dur] kept an old line up and delayed the next one.
  const active = useMemo<Block | undefined>(() => {
    let idx = -1;
    for (let i = 0; i < items.length; i++) {
      if (items[i].start_s <= t) idx = i;
      else break;
    }
    if (idx < 0) return undefined;
    const cur = items[idx];
    const nextStart = items[idx + 1]?.start_s ?? Infinity;
    // visible until the next caption starts, but hide ~1s into a real silence
    const visibleUntil = Math.min(nextStart, cur.end_s + 1.0);
    return t < visibleUntil ? cur : undefined;
  }, [items, t]);

  const progress = Math.min(100, Math.max(0, ((time - startS) / (endS - startS)) * 100));

  return (
    <div>
      <div className="relative aspect-video w-full overflow-hidden rounded-xl bg-black">
        <div ref={hostRef} className="h-full w-full [&>iframe]:h-full [&>iframe]:w-full" />
        {mode === "blind" && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-black/30 to-transparent" />
        )}
        {mode === "study" && active && (
          <button
            onClick={() => { playerRef.current?.pauseVideo(); onSaveCue?.(active.text); }}
            className="group absolute bottom-6 left-1/2 max-w-[92%] -translate-x-1/2 rounded-lg bg-black/75 px-4 py-2 text-center text-xl font-medium leading-snug text-white hover:bg-indigo-700"
            title="Click to save this as a card"
          >
            {active.text}
            <span className="ml-2 hidden text-xs text-indigo-200 group-hover:inline">＋ save</span>
          </button>
        )}
      </div>

      {/* Study-mode subtitle controls */}
      {mode === "study" && (
        <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
          <div className="inline-flex rounded-lg border border-neutral-300 p-0.5 dark:border-neutral-700">
            {(["line", "sentence"] as const).map((u) => (
              <button key={u} onClick={() => setUnit(u)}
                className={`rounded-md px-2.5 py-1 capitalize ${unit === u ? "bg-indigo-600 text-white" : ""}`}>
                {u}
              </button>
            ))}
          </div>
          <div className="inline-flex items-center gap-1">
            <span className="text-neutral-500">Subtitle timing</span>
            <button onClick={() => setOffset((o) => +(o - 0.5).toFixed(1))} className="rounded border px-2 py-1 dark:border-neutral-700" title="show earlier">−0.5s</button>
            <span className="w-16 text-center tabular-nums">{offset > 0 ? "+" : ""}{offset.toFixed(1)}s</span>
            <button onClick={() => setOffset((o) => +(o + 0.5).toFixed(1))} className="rounded border px-2 py-1 dark:border-neutral-700" title="show later">+0.5s</button>
            {offset !== 0 && <button onClick={() => setOffset(0)} className="ml-1 text-neutral-500 hover:underline">reset</button>}
          </div>
          <span className="text-neutral-400">(− earlier · + later)</span>
        </div>
      )}

      {/* Our own transport (the only controls in blind mode) */}
      <div className="mt-3 flex items-center gap-3">
        <button
          onClick={() => (playing ? playerRef.current?.pauseVideo() : playerRef.current?.playVideo())}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700"
        >
          {playing ? "Pause" : "Play"}
        </button>
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800">
          <div className="h-full bg-indigo-500" style={{ width: `${progress}%` }} />
        </div>
        <span className="tabular-nums text-sm text-neutral-500">
          {fmt(time - startS)} / {fmt(endS - startS)}
        </span>
      </div>
    </div>
  );
}
