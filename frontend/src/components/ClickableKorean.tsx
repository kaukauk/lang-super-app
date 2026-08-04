import { useState } from "react";
import { api, type Lookup } from "../lib/api";

// Click any word in the AI's line to see what it means, shown above the word so the
// sentence stays readable underneath. The gloss is recorded server-side and becomes
// a harvest candidate: an unprompted lookup mid-conversation is the cleanest
// evidence of a comprehension gap there is.
//
// Punctuation is split off the clickable token but kept in the rendered text, so
// "싶어요?" is clickable as 싶어요 and still reads with its question mark.
const TRAILING = /^(.*?)([.,!?…]*)$/;

type Props = {
  sessionId: number;
  text: string;
  glosses?: Record<string, string>;   // the turn's own glosses — most clicks are free
};

export default function ClickableKorean({ sessionId, text, glosses = {} }: Props) {
  const [seen, setSeen] = useState<Record<string, Lookup>>({});
  const [pending, setPending] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const click = async (word: string) => {
    if (seen[word]) { setOpen(open === word ? null : word); return; }
    setPending(word);
    try {
      const r = await api.sparLookup(sessionId, word, text, glosses);
      setSeen((s) => ({ ...s, [word]: r }));
      setOpen(word);
    } catch (e) {
      setSeen((s) => ({ ...s, [word]: { word, gloss_en: String(e), ending: "", lemma: "" } }));
      setOpen(word);
    } finally {
      setPending(null);
    }
  };

  return (
    <div className="text-lg leading-loose">
      {text.split(/(\s+)/).map((tok, i) => {
        if (!tok.trim()) return <span key={i}>{tok}</span>;
        const [, word, punct] = tok.match(TRAILING) ?? [, tok, ""];
        if (!word) return <span key={i}>{tok}</span>;
        const hit = seen[word];
        const showing = open === word && hit;
        return (
          <span key={i} className="relative inline-block">
            {showing && (
              <span className="absolute bottom-full left-1/2 z-10 mb-0.5 -translate-x-1/2 whitespace-nowrap rounded-md bg-neutral-800 px-2 py-0.5 text-xs font-medium text-white shadow dark:bg-neutral-700">
                {hit.gloss_en}
                {hit.ending && <span className="ml-1.5 text-neutral-400">{hit.ending}</span>}
              </span>
            )}
            <button
              onClick={() => click(word)}
              title="What does this mean?"
              className={`rounded px-0.5 transition-colors hover:bg-amber-100 dark:hover:bg-amber-900/40 ${
                hit ? "bg-amber-50 underline decoration-amber-400 decoration-dotted underline-offset-4 dark:bg-amber-950/30" : ""
              } ${pending === word ? "opacity-50" : ""}`}
            >
              {word}
            </button>
            {punct}
          </span>
        );
      })}
    </div>
  );
}
