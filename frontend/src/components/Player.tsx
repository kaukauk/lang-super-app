import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Cue, VocabToken } from "../lib/api";
import { fmt } from "../lib/fmt";
import { useAnnotations } from "../lib/useAnnotations";
import PlayerControls from "./PlayerControls";
import SubtitleOverlay from "./SubtitleOverlay";

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
  onAddWord?: (token: VocabToken, line: string) => Promise<unknown>;
  // Free viewing: our own chrome over the embed instead of a study day's fixed
  // window. Same switch, same controls and same keys as the local player, so a
  // YouTube video in the library behaves like a downloaded one.
  seekable?: boolean;
};

export default function Player({ youtubeId, startS, endS, mode, cues = [], onAddWord,
                                 seekable = false }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const playerRef = useRef<any>(null);
  const [full, setFull] = useState(false);
  const [time, setTime] = useState(startS);
  const [playing, setPlaying] = useState(false);
  const [unit, setUnit] = useState<"line" | "sentence">("line");
  const [offset, setOffset] = useState(0); // +later, -earlier (seconds)
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [buffered, setBuffered] = useState(0);
  const [subsOn, setSubsOn] = useState(true);
  const [chrome, setChrome] = useState(true);
  const hideTimer = useRef<any>(null);

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
          // Never YouTube's own controls: in a study day they would allow skipping
          // ahead, and in free viewing they would sit under our subtitle overlay
          // and cover the words. Ours are drawn over the embed instead.
          controls: 0,
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
        // The embed reports one fraction of the whole video rather than ranges,
        // which is enough to shade the bar ahead of the playhead.
        if (p.getVideoLoadedFraction && p.getDuration) {
          setBuffered(p.getVideoLoadedFraction() * (p.getDuration() || 0) - startS);
        }
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
  // Annotated up front for the whole day, so a word is already live the moment its
  // line appears. Study mode only — blind mode shows no subtitles to hover.
  const texts = useMemo(() => items.map((b) => b.text), [items]);
  const showSubs = mode === "study" && (!seekable || subsOn);
  const annotations = useAnnotations(texts, showSubs);
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

  const dur = Math.max(0, endS - startS);
  const elapsed = Math.max(0, time - startS);
  const progress = dur ? Math.min(100, Math.max(0, (elapsed / dur) * 100)) : 0;

  const toggle = () =>
    playing ? playerRef.current?.pauseVideo() : playerRef.current?.playVideo();
  const seekSeconds = (seconds: number) => {
    const p = playerRef.current;
    if (!p?.seekTo || !seekable) return;
    p.seekTo(startS + Math.min(dur, Math.max(0, seconds)), true);
  };
  const nudge = (delta: number) => seekSeconds(elapsed + delta);
  const applyVolume = (vol: number, mute: boolean) => {
    const p = playerRef.current;
    setVolume(vol); setMuted(mute);
    if (!p?.setVolume) return;
    p.setVolume(Math.round(vol * 100));
    mute ? p.mute?.() : p.unMute?.();
  };

  // Chrome hides a couple of seconds into playback and returns on any movement.
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

  const toggleFullscreen = () => {
    if (document.fullscreenElement) { document.exitFullscreen(); return; }
    boxRef.current?.requestFullscreen?.().catch(() => {});
  };

  useEffect(() => {
    const onChange = () => setFull(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  // f toggles, as in every other video player. Ignored while typing so it cannot
  // fire from a text field elsewhere on the page.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && /^(INPUT|TEXTAREA)$/.test(el.tagName)) return;
      const k = e.key.toLowerCase();
      if (k === "f") { e.preventDefault(); toggleFullscreen(); }
      if (!seekable) return;
      if (e.key === " " || k === "k") { e.preventDefault(); toggle(); }
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
  }, [seekable, dur, elapsed, volume, muted, mode, playing, wake]);

  return (
    <div>
      <div ref={boxRef}
        onPointerMove={seekable ? wake : undefined}
        className={`group relative w-full overflow-hidden bg-black
          ${full ? "h-screen" : "aspect-video rounded-xl"}
          ${seekable && !chrome ? "cursor-none" : ""}`}>
        <div ref={hostRef} className="h-full w-full [&>iframe]:h-full [&>iframe]:w-full" />

        {/* The iframe swallows pointer events, so click-to-pause needs a surface of
            our own over it. Below the subtitle overlay and the controls, which both
            need their own clicks. */}
        {seekable && (
          <div className="absolute inset-0 z-10"
            onClick={toggle} onDoubleClick={toggleFullscreen} />
        )}

        {/* Fullscreen the wrapper, not the iframe: the subtitle overlay and the
            controls are ours, and fullscreening the iframe would hand the screen to
            YouTube's own player and leave them behind. */}
        {!seekable && (
          <button
            onClick={toggleFullscreen}
            title={full ? "Exit fullscreen (f)" : "Fullscreen (f)"}
            className="absolute right-3 top-3 z-20 rounded-lg bg-black/60 px-3 py-1.5 text-sm text-white opacity-0 transition hover:bg-black/80 focus:opacity-100 group-hover:opacity-100"
          >
            {full ? "⤢ Exit" : "⤢ Fullscreen"}
          </button>
        )}
        {mode === "blind" && !seekable && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-black/30 to-transparent" />
        )}
        {showSubs && active && (
          <SubtitleOverlay
            text={active.text} full={full}
            tokens={annotations[active.text]}
            onAdd={onAddWord}
          />
        )}

        {seekable && (
          <div className={`transition-opacity duration-200 ${chrome ? "opacity-100" : "pointer-events-none opacity-0"}`}>
            <PlayerControls
              playing={playing} elapsed={elapsed} duration={dur} buffered={buffered}
              volume={volume} muted={muted} full={full}
              subtitles={mode === "study" ? subsOn : undefined}
              onToggle={toggle}
              onSeek={seekSeconds}
              onNudge={nudge}
              onVolume={(v) => applyVolume(v, false)}
              onMute={() => applyVolume(volume, !muted)}
              onSubtitles={() => setSubsOn((o) => !o)}
              onFullscreen={toggleFullscreen}
            />
          </div>
        )}
      </div>

      {/* Study-mode subtitle controls */}
      {showSubs && (
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

      {/* Free viewing carries its whole transport over the video; a study day keeps
          the play/pause and read-only progress below it. */}
      {!seekable && (
        <div className="mt-3 flex items-center gap-3">
          <button onClick={toggle}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700">
            {playing ? "Pause" : "Play"}
          </button>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800">
            <div className="h-full bg-indigo-500" style={{ width: `${progress}%` }} />
          </div>
          <span className="tabular-nums text-sm text-neutral-500">
            {fmt(elapsed)} / {fmt(dur)}
          </span>
        </div>
      )}
    </div>
  );
}
