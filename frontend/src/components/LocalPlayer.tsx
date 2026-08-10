import { useEffect, useMemo, useRef, useState } from "react";
import type { Cue, VocabToken } from "../lib/api";
import { fmt } from "../lib/fmt";
import { useAnnotations } from "../lib/useAnnotations";
import SubtitleOverlay from "./SubtitleOverlay";

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
  src: string;          // the local snippet URL, or a whole joined episode
  baseS: number;        // what absolute time video-time 0 corresponds to
  mode: "blind" | "study";
  cues?: Cue[];
  onAddWord?: (token: VocabToken, line: string) => Promise<unknown>;
  // Whole-episode playback: seek here on load and stop there, instead of cutting
  // a per-day snippet first. A joined episode is one file on disk, so a day can be
  // watched the moment it is imported rather than after a build step.
  seekTo?: number;
  stopAt?: number;
  // Free viewing rather than a study day: a scrub bar and 10-second jumps. Off by
  // default, because during a study day skipping ahead defeats the point of the
  // fixed five minutes — the study pass is the same footage again, watched
  // through, not scrubbed.
  seekable?: boolean;
};

// Plays the locally-cut ~5-min snippet (frame-accurate, no scrubbing in blind mode)
// with a Korean-subtitle overlay synced from the segment's cues.
export default function LocalPlayer({ src, baseS, mode, cues = [], onAddWord,
                                      seekTo, stopAt, seekable = false }: Props) {
  const vidRef = useRef<HTMLVideoElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const [full, setFull] = useState(false);
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
  // Annotated up front for the whole day, so a word is already live the moment its
  // line appears. Study mode only — blind mode shows no subtitles to hover.
  const texts = useMemo(() => items.map((b) => b.text), [items]);
  const annotations = useAnnotations(texts, mode === "study");
  const t = time - offset;
  const active = useMemo<Block | undefined>(() => {
    let idx = -1;
    for (let i = 0; i < items.length; i++) { if (items[i].start_s <= t) idx = i; else break; }
    if (idx < 0) return undefined;
    const cur = items[idx];
    const nextStart = items[idx + 1]?.start_s ?? Infinity;
    return t < Math.min(nextStart, cur.end_s + 1.0) ? cur : undefined;
  }, [items, t]);

  // Progress is through the DAY, not the file: on a whole episode the playhead
  // starts at the day's offset, so a bar measured from zero would open at 60%.
  const progress = dur ? Math.min(100, Math.max(0, ((time - (seekTo ?? 0)) / dur) * 100)) : 0;
  const toggle = () => { const v = vidRef.current; if (!v) return; v.paused ? v.play() : v.pause(); };
  const elapsed = Math.max(0, time - (seekTo ?? 0));

  // Both expressed in the file's own timeline, since that is what <video> wants,
  // while everything shown to the viewer is relative to the start of the day.
  const seekFraction = (f: number) => {
    const v = vidRef.current;
    if (!v || !seekable || !dur) return;
    v.currentTime = (seekTo ?? 0) + Math.min(1, Math.max(0, f)) * dur;
  };
  const nudge = (delta: number) => {
    const v = vidRef.current;
    if (!v || !seekable) return;
    const lo = seekTo ?? 0;
    const hi = stopAt ?? (v.duration || Infinity);
    v.currentTime = Math.min(hi, Math.max(lo, v.currentTime + delta));
  };

  // Fullscreen the WRAPPER, not the <video>. Fullscreening the element itself
  // leaves the subtitle overlay behind, which is the whole point of study mode.
  const toggleFullscreen = () => {
    if (document.fullscreenElement) { document.exitFullscreen(); return; }
    boxRef.current?.requestFullscreen?.().catch(() => {});
  };

  useEffect(() => {
    const onChange = () => setFull(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  // f toggles, matching every video player the user already has muscle memory for.
  // Ignored while typing so it cannot fire from a text field elsewhere on the page.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && /^(INPUT|TEXTAREA)$/.test(el.tagName)) return;
      if (e.key === "f" || e.key === "F") { e.preventDefault(); toggleFullscreen(); }
      if (e.key === " ") { e.preventDefault(); toggle(); }
      if (seekable && e.key === "ArrowLeft") { e.preventDefault(); nudge(-10); }
      if (seekable && e.key === "ArrowRight") { e.preventDefault(); nudge(10); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [seekable, dur]);

  return (
    <div>
      {/* aspect-video sizes the box normally; in fullscreen the browser sizes it,
          so drop the ratio and let the video letterbox itself inside the screen */}
      <div ref={boxRef}
        className={`group relative w-full overflow-hidden bg-black ${full ? "h-screen" : "aspect-video rounded-xl"}`}>
        <video
          ref={vidRef}
          src={src}
          className="h-full w-full object-contain"
          // Never the browser's own controls, in either mode. They bring a seek
          // bar, and skipping ahead defeats the point of a fixed 5-minute day:
          // the study pass is meant to be the same footage again, watched
          // through, not scrubbed. Both modes now share the play/pause and
          // read-only progress bar below.
          controls={false}
          controlsList="nodownload noplaybackrate"
          onTimeUpdate={(e) => {
            const v = e.currentTarget;
            // Stop at the day's end rather than rolling on into the next day's
            // material, which would spoil footage the next primer sets up.
            if (stopAt !== undefined && v.currentTime >= stopAt) v.pause();
            setTime(v.currentTime);
          }}
          onLoadedMetadata={(e) => {
            const v = e.currentTarget;
            if (seekTo !== undefined) v.currentTime = seekTo;
            setDur(stopAt !== undefined ? stopAt - (seekTo ?? 0) : v.duration);
          }}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
        />
        {mode === "study" && active && (
          <SubtitleOverlay
            text={active.text} full={full}
            tokens={annotations[active.text]}
            onAdd={onAddWord}
          />
        )}

        {/* Sits over the video so it is reachable in fullscreen too, where the
            controls below the box are off-screen. */}
        <button
          onClick={toggleFullscreen}
          title={full ? "Exit fullscreen (f)" : "Fullscreen (f)"}
          className="absolute right-3 top-3 rounded-lg bg-black/60 px-3 py-1.5 text-sm text-white opacity-0 transition hover:bg-black/80 focus:opacity-100 group-hover:opacity-100"
        >
          {full ? "⤢ Exit" : "⤢ Fullscreen"}
        </button>

        {/* In fullscreen every control below the box is off-screen, so the whole
            transport moves over the video — otherwise there would be no way to
            pause, let alone seek, without leaving fullscreen first. */}
        {full && (
          <div className="absolute inset-x-0 bottom-0 z-10 flex items-center gap-3 bg-gradient-to-t from-black/80 to-transparent px-6 pb-4 pt-10 opacity-0 transition focus-within:opacity-100 group-hover:opacity-100">
            <button onClick={toggle}
              className="rounded-lg bg-white/15 px-4 py-2 text-sm font-semibold text-white hover:bg-white/25">
              {playing ? "Pause" : "Play"}
            </button>
            <Bar progress={progress} seekable={seekable} onSeek={seekFraction} dark />
            <span className="tabular-nums text-sm text-white/80">
              {fmt(elapsed)} / {fmt(dur)}
            </span>
          </div>
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
        {seekable && (
          <>
            <button onClick={() => nudge(-10)} title="Back 10s (←)"
              className="rounded-lg border px-2.5 py-2 text-sm dark:border-neutral-700">↺ 10s</button>
            <button onClick={() => nudge(10)} title="Forward 10s (→)"
              className="rounded-lg border px-2.5 py-2 text-sm dark:border-neutral-700">10s ↻</button>
          </>
        )}
        <Bar progress={progress} seekable={seekable} onSeek={seekFraction} />
        <span className="tabular-nums text-sm text-neutral-500">{fmt(elapsed)} / {fmt(dur)}</span>
      </div>
    </div>
  );
}

// The progress bar, which is also the scrub bar when seeking is allowed. Clicking
// is enough: a drag handle would be one more thing to hit precisely on a bar that
// is mostly used to jump back a scene.
function Bar({ progress, seekable, onSeek, dark = false }: {
  progress: number; seekable: boolean; onSeek: (f: number) => void; dark?: boolean;
}) {
  return (
    <div
      onClick={(e) => {
        if (!seekable) return;
        const box = e.currentTarget.getBoundingClientRect();
        onSeek((e.clientX - box.left) / box.width);
      }}
      title={seekable ? "Click to jump" : undefined}
      // Taller when it can be clicked: a 2px strip is a readout, and asking
      // someone to hit one with a pointer is the difference between the two.
      style={{ height: seekable ? 10 : 8 }}
      className={`flex-1 overflow-hidden rounded-full ${seekable ? "cursor-pointer" : ""}
        ${dark ? "bg-white/25" : "bg-neutral-200 dark:bg-neutral-800"}`}
    >
      <div className={`h-full rounded-full ${dark ? "bg-white" : "bg-indigo-500"}`}
        style={{ width: `${progress}%` }} />
    </div>
  );
}
