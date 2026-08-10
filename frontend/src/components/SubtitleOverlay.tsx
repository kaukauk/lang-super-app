import { useCallback, useEffect, useRef, useState } from "react";
import type { VocabToken } from "../lib/api";

// The Korean subtitle that floats over the video. Shared by the local and YouTube
// players so drag, placement and sizing behave identically in both.
//
// Position is stored as a PERCENTAGE of the video box, not pixels: the same
// setting then means the same visual spot whether the box is a 640px window or a
// 4K screen. Windowed and fullscreen keep separate positions, because the right
// place in a small embedded box is not the right place on a whole screen — and
// both persist, so a placement chosen once holds for every later video.
//
// Words are individually live. Hovering one shows what it means; clicking one —
// and only clicking one — sends it to Anki. Colour says what the deck already
// holds: green for a card with this exact form, orange for a word that is in the
// deck but not in the form on screen. Particles and endings are never marked,
// because they are grammar rather than vocabulary.

type Pos = { x: number; y: number };   // percent of the container, 0-100
type Placements = { windowed: Pos; fullscreen: Pos };

const STORAGE_KEY = "langapp.subtitlePos.v1";
const DEFAULTS: Placements = {
  windowed: { x: 50, y: 86 },
  fullscreen: { x: 50, y: 90 },
};
// Movement under this is a click, not a drag. Without it every attempt to add a
// word would register as a tiny drag and move the subtitle instead.
const DRAG_SLOP_PX = 4;

const TONE: Record<VocabToken["status"], string> = {
  // Colour, not background: the subtitle already sits on a dark plate, and blocks
  // of highlight over moving video are hard to read past.
  known: "text-emerald-300 decoration-emerald-400/60",
  derived: "text-amber-300 decoration-amber-400/60",
  new: "text-white decoration-white/30",
};
const TONE_NOTE: Record<VocabToken["status"], string> = {
  known: "in your deck",
  derived: "known, but not this form",
  new: "new word",
};

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
  tokens?: VocabToken[];
  onAdd?: (token: VocabToken, line: string) => Promise<{ shape?: string } | void>;
};

export default function SubtitleOverlay({ text, full, tokens = [], onAdd }: Props) {
  const [places, setPlaces] = useState<Placements>(load);
  const [dragging, setDragging] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const [pending, setPending] = useState<number | null>(null);
  // Keyed by word rather than by index: the same word turning green everywhere it
  // appears is the honest result of having just made a card for it. The value is
  // what to say about the card, since a word whose line was too hard for a cloze
  // gets a plain word card instead and that is worth knowing.
  const [added, setAdded] = useState<Record<string, string>>({});
  const [failed, setFailed] = useState<Record<string, string>>({});
  const ref = useRef<HTMLDivElement>(null);
  const origin = useRef<{ px: number; py: number; start: Pos; moved: boolean } | null>(null);
  const draggedRef = useRef(false);

  const key = full ? "fullscreen" : "windowed";
  const pos = places[key];

  // Persist on release rather than on every pointer move — a drag fires dozens of
  // events and localStorage writes are synchronous.
  const commit = useCallback((next: Placements) => { setPlaces(next); save(next); }, []);

  const onPointerDown = (e: React.PointerEvent) => {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    origin.current = { px: e.clientX, py: e.clientY, start: pos, moved: false };
    draggedRef.current = false;
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
    draggedRef.current = true;
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
    if (o?.moved) commit(places);          // a drag: remember where it was dropped
  };

  const click = async (tok: VocabToken, i: number) => {
    // The word is inside the draggable plate, so a drag that happened to end on a
    // word must not also make a card.
    if (draggedRef.current || !onAdd || added[tok.word] || pending !== null) return;
    setPending(i);
    try {
      const made = await onAdd(tok, text);
      setAdded((a) => ({
        ...a,
        [tok.word]: made && made.shape === "isolated"
          ? "added as a word card ✓" : "added to Anki ✓",
      }));
      setFailed((f) => { const { [tok.word]: _drop, ...rest } = f; return rest; });
    } catch (err) {
      setFailed((f) => ({ ...f, [tok.word]: String(err) }));
    } finally {
      setPending(null);
    }
  };

  // Keep the stored position honest if another player instance changed it.
  useEffect(() => { setPlaces(load()); }, [full]);
  // A new line means nothing is hovered — the pointer never left the old word.
  useEffect(() => { setHover(null); }, [text]);

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
      title="Drag to move"
      className={`group absolute z-20 max-w-[92%] select-none rounded-lg bg-black/75 px-4 py-2
        text-center font-medium leading-snug text-white
        ${dragging ? "cursor-grabbing ring-2 ring-indigo-400" : "cursor-grab"}`}
    >
      {renderPieces(text, tokens).map((piece, i) =>
        typeof piece === "string" ? (
          <span key={i}>{piece}</span>
        ) : (
          <Word
            key={i}
            token={piece}
            active={hover === i}
            pending={pending === i}
            added={added[piece.word]}
            error={failed[piece.word]}
            clickable={!!onAdd}
            onEnter={() => setHover(i)}
            onLeave={() => setHover((h) => (h === i ? null : h))}
            onClick={() => click(piece, i)}
          />
        ),
      )}
    </div>
  );
}

// The line broken into plain runs and annotated words, in order. Spans come from
// the server already non-overlapping and sorted, so this is a single pass.
function renderPieces(text: string, tokens: VocabToken[]): (string | VocabToken)[] {
  const out: (string | VocabToken)[] = [];
  let at = 0;
  for (const tok of tokens) {
    if (tok.start < at || tok.end > text.length) continue;
    if (tok.start > at) out.push(text.slice(at, tok.start));
    out.push(tok);
    at = tok.end;
  }
  if (at < text.length) out.push(text.slice(at));
  return out;
}

type WordProps = {
  token: VocabToken; active: boolean; pending: boolean; added?: string;
  error?: string; clickable: boolean;
  onEnter: () => void; onLeave: () => void; onClick: () => void;
};

function Word({ token, active, pending, added, error, clickable,
                onEnter, onLeave, onClick }: WordProps) {
  const status = added ? "known" : token.status;
  return (
    <span className="relative inline-block"
      onPointerEnter={onEnter} onPointerLeave={onLeave} onClick={onClick}>
      {active && (
        // Sized in em so it tracks the subtitle: readable in fullscreen without
        // becoming a second subtitle competing with the first.
        <span
          style={{ fontSize: "0.5em" }}
          className="pointer-events-none absolute bottom-full left-1/2 z-30 mb-1 -translate-x-1/2
            whitespace-nowrap rounded-md bg-neutral-900/95 px-2.5 py-1.5 text-center
            font-normal leading-tight text-white shadow-lg ring-1 ring-white/10"
        >
          <span className="font-semibold">{token.display}</span>
          {token.gloss_en && <span className="text-neutral-300"> — {token.gloss_en}</span>}
          <span className="block text-[0.85em] text-neutral-400">
            {error ? error.slice(0, 80)
              : added ? added
              : pending ? "adding…"
              : `${TONE_NOTE[token.status]}${clickable ? " · click to add" : ""}`}
          </span>
        </span>
      )}
      <span
        className={`rounded underline decoration-dotted underline-offset-4 transition-colors
          ${TONE[status]} ${active ? "bg-white/15" : ""} ${pending ? "opacity-60" : ""}
          ${clickable && !added ? "cursor-pointer" : ""}`}
      >
        {token.surface}
      </span>
    </span>
  );
}
