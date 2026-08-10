import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Cue, VocabToken } from "../lib/api";
import { fmt } from "../lib/fmt";
import { useAnnotations } from "../lib/useAnnotations";
import PlayerControls from "./PlayerControls";
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
  // Free viewing rather than a study day: the full player — scrub, volume, a CC
  // toggle, auto-hiding chrome. Off by default, because during a study day
  // skipping ahead defeats the point of the fixed five minutes: the study pass is
  // the same footage again, watched through, not scrubbed.
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
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [buffered, setBuffered] = useState(0);
  // Subtitles are a player control in free viewing, the way CC is in any player.
  // A study day has no switch: the mode picker above the video is the switch.
  const [subsOn, setSubsOn] = useState(true);
  const [chrome, setChrome] = useState(true);   // controls visible
  const hideTimer = useRef<any>(null);

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
  const showSubs = mode === "study" && (!seekable || subsOn);
  // Annotated up front for the whole day, so a word is already live the moment its
  // line appears. Only when subtitles are actually showing — there is nothing to
  // hover otherwise, and it is a model call.
  const texts = useMemo(() => items.map((b) => b.text), [items]);
  const annotations = useAnnotations(texts, showSubs);
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
  const seekTo_ = (seconds: number) => {
    const v = vidRef.current;
    if (!v || !seekable || !dur) return;
    v.currentTime = (seekTo ?? 0) + Math.min(dur, Math.max(0, seconds));
  };
  const nudge = (delta: number) => {
    const v = vidRef.current;
    if (!v || !seekable) return;
    const lo = seekTo ?? 0;
    const hi = stopAt ?? (v.duration || Infinity);
    v.currentTime = Math.min(hi, Math.max(lo, v.currentTime + delta));
  };

  const applyVolume = (vol: number, mute: boolean) => {
    const v = vidRef.current;
    setVolume(vol); setMuted(mute);
    if (v) { v.volume = vol; v.muted = mute; }
  };

  // Chrome hides a couple of seconds into playback and returns on any movement —
  // the convention every player follows, so the picture is clear while watching
  // but the controls are never more than a twitch away.
  const wake = useCallback(() => {
    setChrome(true);
    clearTimeout(hideTimer.current);
    hideTimer.current = setTimeout(() => setChrome(false), 2500);
  }, []);
  useEffect(() => {
    if (!playing) { clearTimeout(hideTimer.current); setChrome(true); return; }
    wake();
    return () => clearTimeout(hideTimer.current);
  }, [playing, wake]);

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
      const k = e.key.toLowerCase();
      if (k === "f") { e.preventDefault(); toggleFullscreen(); }
      if (e.key === " " || k === "k") { e.preventDefault(); toggle(); }
      if (!seekable) return;
      if (e.key === "ArrowLeft") { e.preventDefault(); nudge(-10); }
      if (e.key === "ArrowRight") { e.preventDefault(); nudge(10); }
      if (e.key === "ArrowUp") { e.preventDefault(); applyVolume(Math.min(1, volume + 0.1), false); }
      if (e.key === "ArrowDown") { e.preventDefault(); applyVolume(Math.max(0, volume - 0.1), muted); }
      if (k === "m") { e.preventDefault(); applyVolume(volume, !muted); }
      if (k === "c" && mode === "study") { e.preventDefault(); setSubsOn((o) => !o); }
      wake();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [seekable, dur, volume, muted, mode, wake]);

  return (
    <div>
      {/* aspect-video sizes the box normally; in fullscreen the browser sizes it,
          so drop the ratio and let the video letterbox itself inside the screen */}
      <div ref={boxRef}
        onPointerMove={seekable ? wake : undefined}
        onClick={seekable ? toggle : undefined}
        onDoubleClick={seekable ? toggleFullscreen : undefined}
        className={`group relative w-full overflow-hidden bg-black
          ${full ? "h-screen" : "aspect-video rounded-xl"}
          ${seekable && !chrome ? "cursor-none" : ""}`}>
        <video
          ref={vidRef}
          src={src}
          className="h-full w-full object-contain"
          // Never the browser's own controls. In a study day they would bring a
          // seek bar, and skipping ahead defeats the point of a fixed five
          // minutes. In free viewing they would sit under our subtitle overlay
          // and cover the words. Both cases get our own chrome instead.
          controls={false}
          controlsList="nodownload noplaybackrate"
          onTimeUpdate={(e) => {
            const v = e.currentTarget;
            // Stop at the day's end rather than rolling on into the next day's
            // material, which would spoil footage the next primer sets up.
            if (stopAt !== undefined && v.currentTime >= stopAt) v.pause();
            setTime(v.currentTime);
          }}
          onProgress={(e) => {
            const v = e.currentTarget;
            const lo = seekTo ?? 0;
            let end = 0;
            for (let i = 0; i < v.buffered.length; i++) {
              if (v.buffered.start(i) <= v.currentTime) end = v.buffered.end(i);
            }
            setBuffered(Math.max(0, end - lo));
          }}
          onLoadedMetadata={(e) => {
            const v = e.currentTarget;
            if (seekTo !== undefined) v.currentTime = seekTo;
            setDur(stopAt !== undefined ? stopAt - (seekTo ?? 0) : v.duration);
            v.volume = volume; v.muted = muted;
          }}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
        />
        {showSubs && active && (
          <SubtitleOverlay
            text={active.text} full={full}
            tokens={annotations[active.text]}
            onAdd={onAddWord}
          />
        )}

        {seekable ? (
          <div className={`transition-opacity duration-200 ${chrome ? "opacity-100" : "pointer-events-none opacity-0"}`}>
            <PlayerControls
              playing={playing} elapsed={elapsed} duration={dur} buffered={buffered}
              volume={volume} muted={muted} full={full}
              subtitles={mode === "study" ? subsOn : undefined}
              onToggle={toggle}
              onSeek={seekTo_}
              onNudge={nudge}
              onVolume={(v) => applyVolume(v, false)}
              onMute={() => applyVolume(volume, !muted)}
              onSubtitles={() => setSubsOn((o) => !o)}
              onFullscreen={toggleFullscreen}
            />
          </div>
        ) : (
          <>
            {/* A study day keeps the minimal set: no seek bar anywhere, because
                the fixed five minutes are the method. */}
            <button
              onClick={toggleFullscreen}
              title={full ? "Exit fullscreen (f)" : "Fullscreen (f)"}
              className="absolute right-3 top-3 rounded-lg bg-black/60 px-3 py-1.5 text-sm text-white opacity-0 transition hover:bg-black/80 focus:opacity-100 group-hover:opacity-100"
            >
              {full ? "⤢ Exit" : "⤢ Fullscreen"}
            </button>
            {full && (
              <div className="absolute inset-x-0 bottom-0 z-10 flex items-center gap-3 bg-gradient-to-t from-black/80 to-transparent px-6 pb-4 pt-10 opacity-0 transition focus-within:opacity-100 group-hover:opacity-100">
                <button onClick={toggle}
                  className="rounded-lg bg-white/15 px-4 py-2 text-sm font-semibold text-white hover:bg-white/25">
                  {playing ? "Pause" : "Play"}
                </button>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-white/25">
                  <div className="h-full rounded-full bg-white" style={{ width: `${progress}%` }} />
                </div>
                <span className="tabular-nums text-sm text-white/80">
                  {fmt(elapsed)} / {fmt(dur)}
                </span>
              </div>
            )}
          </>
        )}
      </div>

      {mode === "study" && showSubs && (
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

      {/* Free viewing carries its whole transport over the video; a study day
          keeps the play/pause and read-only progress below it. */}
      {!seekable && (
        <div className="mt-3 flex items-center gap-3">
          <button onClick={toggle} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700">
            {playing ? "Pause" : "Play"}
          </button>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800">
            <div className="h-full rounded-full bg-indigo-500" style={{ width: `${progress}%` }} />
          </div>
          <span className="tabular-nums text-sm text-neutral-500">{fmt(elapsed)} / {fmt(dur)}</span>
        </div>
      )}
    </div>
  );
}
