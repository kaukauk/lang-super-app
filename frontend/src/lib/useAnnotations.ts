import { useEffect, useRef, useState } from "react";
import { api, type VocabToken } from "./api";

// Word-level annotation for the subtitles of whatever is playing, fetched in
// order and kept keyed by line text.
//
// Batched, because on the server everything expensive is per-request rather than
// per-line — one read of the Anki deck, one model call covering every unknown word
// in the batch — so asking for many lines costs little more than asking for one,
// and asking per line as the video played would put a network round trip in front
// of a subtitle that is on screen for two seconds.
//
// But not all at once. A 51-minute video is nearly a thousand lines, and asking
// for the whole thing meant one request that took minutes and produced nothing
// until every last line was done — so the words stayed dead through the part you
// were actually watching. Chunks go out in playback order instead: the opening
// lines are live in a second or two and the rest fills in behind you.
const CHUNK = 60;

export function useAnnotations(texts: string[], enabled = true) {
  const [byText, setByText] = useState<Record<string, VocabToken[]>>({});
  const asked = useRef<Set<string>>(new Set());
  // Re-read Anki once per player, on the first chunk. Reviews done since the last
  // video should be reflected the moment this one starts — a word studied this
  // morning ought to be green this evening — and the server otherwise trusts a
  // cached index for five minutes.
  const firstFetch = useRef(true);
  const running = useRef(false);

  useEffect(() => {
    if (!enabled || running.current) return;
    const missing = [...new Set(texts.filter((t) => t.trim() && !asked.current.has(t)))];
    if (!missing.length) return;

    let live = true;
    running.current = true;
    (async () => {
      for (let i = 0; i < missing.length; i += CHUNK) {
        if (!live) break;
        const chunk = missing.slice(i, i + CHUNK);
        // Marked before the request, not after: this effect re-runs as each chunk
        // lands, and without it the same lines would be requested again.
        chunk.forEach((t) => asked.current.add(t));
        const refresh = firstFetch.current;
        firstFetch.current = false;
        try {
          const r = await api.annotate(chunk, refresh);
          if (!live) break;
          setByText((m) => {
            const next = { ...m };
            for (const line of r.lines) next[line.text] = line.tokens;
            return next;
          });
        } catch {
          // A failure must not interrupt playback — those lines stay plain, and
          // are retried the next time the effect sees them missing.
          chunk.forEach((t) => asked.current.delete(t));
        }
      }
      running.current = false;
    })();

    return () => { live = false; running.current = false; };
  }, [texts, enabled]);

  return byText;
}
