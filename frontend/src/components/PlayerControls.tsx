import { useCallback, useEffect, useRef, useState } from "react";
import { fmt } from "../lib/fmt";

// The control bar for free viewing: a normal video player, over the video, in the
// shape everyone already knows — scrub, skip, volume, subtitles, fullscreen.
//
// Over the video rather than under it because fullscreen is where a long episode
// is actually watched, and controls that live below the frame simply vanish there.
// It follows the usual convention of hiding a couple of seconds into playback and
// coming back on any mouse movement, so the picture is unobstructed while watching
// but never more than a twitch away.

type Props = {
  playing: boolean;
  elapsed: number;              // seconds into the material
  duration: number;
  buffered: number;             // seconds of contiguous buffer ahead of the start
  volume: number;               // 0-1
  muted: boolean;
  full: boolean;
  subtitles?: boolean;          // undefined hides the CC button entirely
  onToggle: () => void;
  onSeek: (seconds: number) => void;
  onNudge: (delta: number) => void;
  onVolume: (v: number) => void;
  onMute: () => void;
  onSubtitles?: () => void;
  onFullscreen: () => void;
};

export default function PlayerControls({
  playing, elapsed, duration, buffered, volume, muted, full, subtitles,
  onToggle, onSeek, onNudge, onVolume, onMute, onSubtitles, onFullscreen,
}: Props) {
  const barRef = useRef<HTMLDivElement>(null);
  const [scrubbing, setScrubbing] = useState(false);
  const [hoverAt, setHoverAt] = useState<number | null>(null);

  const at = useCallback((clientX: number) => {
    const box = barRef.current?.getBoundingClientRect();
    if (!box || !box.width) return 0;
    return Math.min(1, Math.max(0, (clientX - box.left) / box.width)) * duration;
  }, [duration]);

  // Scrubbing follows the pointer live, the way a real player does — releasing is
  // a confirmation, not the first time anything moves.
  const onDown = (e: React.PointerEvent) => {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    setScrubbing(true);
    onSeek(at(e.clientX));
  };
  const onMove = (e: React.PointerEvent) => {
    setHoverAt(at(e.clientX));
    if (scrubbing) onSeek(at(e.clientX));
  };
  const onUp = (e: React.PointerEvent) => {
    (e.target as Element).releasePointerCapture?.(e.pointerId);
    setScrubbing(false);
  };

  const pct = duration ? (elapsed / duration) * 100 : 0;
  const bufPct = duration ? Math.min(100, (buffered / duration) * 100) : 0;

  return (
    <div
      // Stop clicks reaching the video underneath, which toggles play.
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
      className="absolute inset-x-0 bottom-0 z-30 bg-gradient-to-t from-black/90 via-black/60 to-transparent px-4 pb-3 pt-12"
    >
      <div
        ref={barRef}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerLeave={() => { setHoverAt(null); setScrubbing(false); }}
        className="group/bar relative cursor-pointer py-2"
        style={{ touchAction: "none" }}
      >
        {hoverAt !== null && (
          <span
            style={{ left: `${duration ? (hoverAt / duration) * 100 : 0}%` }}
            className="pointer-events-none absolute bottom-full -translate-x-1/2 rounded bg-black/90 px-1.5 py-0.5 text-xs tabular-nums text-white"
          >
            {fmt(hoverAt)}
          </span>
        )}
        <div className="h-1 w-full rounded-full bg-white/25 transition-all group-hover/bar:h-1.5">
          <div className="absolute h-1 rounded-full bg-white/30 transition-all group-hover/bar:h-1.5"
            style={{ width: `${bufPct}%` }} />
          <div className="relative h-full rounded-full bg-indigo-500" style={{ width: `${pct}%` }}>
            {/* The handle: hidden until the bar is hovered or being dragged, so a
                still frame is not permanently marked with a dot. */}
            <span className={`absolute right-0 top-1/2 h-3 w-3 -translate-y-1/2 translate-x-1/2 rounded-full bg-indigo-400 transition-opacity
              ${scrubbing ? "opacity-100" : "opacity-0 group-hover/bar:opacity-100"}`} />
          </div>
        </div>
      </div>

      <div className="flex items-center gap-1 text-white">
        <Btn onClick={onToggle} title={playing ? "Pause (space)" : "Play (space)"}>
          {playing ? "❚❚" : "▶"}
        </Btn>
        <Btn onClick={() => onNudge(-10)} title="Back 10s (←)">↺</Btn>
        <Btn onClick={() => onNudge(10)} title="Forward 10s (→)">↻</Btn>

        <div className="group/vol flex items-center">
          <Btn onClick={onMute} title={muted ? "Unmute (m)" : "Mute (m)"}>
            {muted || volume === 0 ? "🔇" : volume < 0.5 ? "🔈" : "🔊"}
          </Btn>
          <input
            type="range" min={0} max={1} step={0.05}
            value={muted ? 0 : volume}
            onChange={(e) => onVolume(Number(e.target.value))}
            title="Volume (↑ ↓)"
            className="h-1 w-0 cursor-pointer accent-white opacity-0 transition-all group-hover/vol:mr-2 group-hover/vol:w-20 group-hover/vol:opacity-100"
          />
        </div>

        <span className="ml-1 select-none text-sm tabular-nums text-white/85">
          {fmt(elapsed)} <span className="text-white/40">/ {fmt(duration)}</span>
        </span>

        <span className="flex-1" />

        {subtitles !== undefined && (
          <Btn onClick={onSubtitles} title={subtitles ? "Subtitles off (c)" : "Subtitles on (c)"}
            className={subtitles ? "border-b-2 border-indigo-400" : "opacity-60"}>
            CC
          </Btn>
        )}
        <Btn onClick={onFullscreen} title={full ? "Exit fullscreen (f)" : "Fullscreen (f)"}>
          {full ? "⤡" : "⤢"}
        </Btn>
      </div>
    </div>
  );
}

function Btn({ onClick, title, children, className = "" }: {
  onClick?: () => void; title: string; children: React.ReactNode; className?: string;
}) {
  return (
    <button onClick={onClick} title={title} aria-label={title}
      className={`rounded px-2.5 py-1 text-sm leading-none hover:bg-white/20 ${className}`}>
      {children}
    </button>
  );
}
