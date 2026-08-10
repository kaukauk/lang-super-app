import { useEffect, useRef, useState } from "react";
import { api, type VocabToken } from "./api";

// Word-level annotation for a whole day of subtitles, fetched once and kept keyed
// by line text.
//
// Batched deliberately: on the server everything expensive is per-request rather
// than per-line — one read of the Anki deck, one model call covering every unknown
// word — so asking for the day up front costs about what asking for a single line
// would, and every line after the first is already there when it appears. Asking
// per-line as the video played would put a network round trip in front of a
// subtitle that is on screen for two seconds.
export function useAnnotations(texts: string[], enabled = true) {
  const [byText, setByText] = useState<Record<string, VocabToken[]>>({});
  const asked = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!enabled) return;
    const missing = [...new Set(texts.filter((t) => t.trim() && !asked.current.has(t)))];
    if (!missing.length) return;
    // Marked before the request, not after: this effect re-runs when the results
    // land, and without it the same lines would be requested again.
    missing.forEach((t) => asked.current.add(t));
    let live = true;
    api.annotate(missing)
      .then((r) => {
        if (!live) return;
        setByText((m) => {
          const next = { ...m };
          for (const line of r.lines) next[line.text] = line.tokens;
          return next;
        });
      })
      // A failure here must not interrupt playback — the subtitle just stays plain.
      .catch(() => missing.forEach((t) => asked.current.delete(t)));
    return () => { live = false; };
  }, [texts, enabled]);

  return byText;
}
