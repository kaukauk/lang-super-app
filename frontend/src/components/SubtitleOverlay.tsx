import { useCallback, useEffect, useRef, useState } from "react";

// The Korean subtitle that floats over the video. Shared by the local and YouTube
// players so drag, placement and sizing behave identically in both.
//
// Position is stored as a PERCENTAGE of the video box, not pixels: the same
// setting then means the same visual spot whether the box is a 640px window or a
// 4K screen. Windowed and fullscreen keep separate positions, because the right
// place in a small embedded box is not the right place on a whole screen — and
// both persist, so a placement chosen once holds for every later video.

type Pos = { x: number; y: number };   // percent of the container, 0-100
type Placements = { windowed: Pos; fullscreen: Pos };

const STORAGE_KEY = "langapp.subtitlePos.v1";
const DEFAULTS: Placements = {
  windowed: { x: 50, y: 86 },
  fullscreen: { x: 50, y: 90 },
};
// Movement under this is a click, not a drag. Without it every attempt to save a
// line would register as a tiny drag and move the subtitle instead.
const DRAG_SLOP_PX = 4;

function load(): Placements {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULTS;
    const p = JSON.parse(raw);
    return {
      windowed: { ...DEFAULTS.windowed, ...(p.windowed || {}) },
      fullscreen: { ...DEFAULTS.fullscreen, ...(p.fullscreen || {}) },
    };
  } catch {
    return DEFAULTS;      // corrupt or unavailable storage must not break playback
  }
}

function save(p: Placements) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(p)); } catch { /* private mode */ }
}

type Props = {
  text: string;
  full: boolean;
  onSave?: (text: string) => void;
  saveHint?: boolean;        // show the "+ save" affordance (study mode only)
};

export default function SubtitleOverlay({ text, full, onSave, saveHint = true }: Props) {
  const [places, setPlaces] = useState<Placements>(load);
  const [dragging, setDragging] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const origin = useRef<{ px: number; py: number; start: Pos; moved: boolean } | null>(null);

  const key = full ? "fullscreen" : "windowed";
  const pos = places[key];

  // Persist on release rather than on every pointer move — a drag fires dozens of
  // events and localStorage writes are synchronous.
  const commit = useCallback((next: Placements) => { setPlaces(next); save(next); }, []);

  const onPointerDown = (e: React.PointerEvent) => {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    origin.current = { px: e.clientX, py: e.clientY, start: pos, moved: false };
    setDragging(true);
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const o = origin.current;
    if (!o) return;
    const box = ref.current?.parentElement?.getBoundingClientRect();
    if (!box || !box.width || !box.height) return;
    const dx = e.clientX - o.px;
    const dy = e.clientY - o.py;
    if (!o.moved && Math.hypot(dx, dy) < DRAG_SLOP_PX) return;
    o.moved = true;
    // Clamp so the subtitle can never be dragged off the edge and stranded there.
    const x = Math.min(96, Math.max(4, o.start.x + (dx / box.width) * 100));
    const y = Math.min(97, Math.max(3, o.start.y + (dy / box.height) * 100));
    setPlaces((p) => ({ ...p, [key]: { x, y } }));
  };

  const onPointerUp = (e: React.PointerEvent) => {
    const o = origin.current;
    origin.current = null;
    setDragging(false);
    (e.target as Element).releasePointerCapture?.(e.pointerId);
    if (!o) return;
    if (o.moved) commit(places);          // a drag: remember where it was dropped
    else onSave?.(text);                  // a click: save the line as a card
  };

  // Keep the stored position honest if another player instance changed it.
  useEffect(() => { setPlaces(load()); }, [full]);

  // Font scales with the viewport in fullscreen so the line stays readable from
  // across a room, and is clamped so a very wide screen doesn't produce a banner.
  const fontSize = full ? "clamp(1.6rem, 2.6vw, 3rem)" : "clamp(1rem, 1.6vw, 1.5rem)";

  return (
    <div
      ref={ref}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      style={{
        left: `${pos.x}%`, top: `${pos.y}%`,
        transform: "translate(-50%, -50%)",
        fontSize,
        touchAction: "none",              // let us handle the drag, not the browser
      }}
      title={onSave ? "Click to save as a card · drag to move" : "Drag to move"}
      className={`group absolute z-20 max-w-[92%] select-none rounded-lg bg-black/75 px-4 py-2
        text-center font-medium leading-snug text-white
        ${dragging ? "cursor-grabbing ring-2 ring-indigo-400" : "cursor-grab hover:bg-indigo-700"}`}
    >
      {text}
      {saveHint && onSave && (
        <span className="ml-2 hidden align-middle text-xs text-indigo-200 group-hover:inline">
          ＋ save
        </span>
      )}
    </div>
  );
}
