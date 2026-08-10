import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import LocalPlayer from "../components/LocalPlayer";
import Player from "../components/Player";
import { api, type Cue, type SegmentDetail, type VocabToken } from "../lib/api";

export default function Watch() {
  const { sid } = useParams();
  const id = Number(sid);
  const [seg, setSeg] = useState<SegmentDetail | null>(null);
  const [cues, setCues] = useState<Cue[]>([]);
  const [mode, setMode] = useState<"blind" | "study">("blind");
  const [saved, setSaved] = useState(0);

  useEffect(() => {
    api.segment(id).then(setSeg);
    api.cues(id).then(setCues);
  }, [id]);

  if (!seg) return <p className="text-neutral-500">Loading…</p>;
  // A joined episode is already on disk, so play it directly and seek to the
  // day's range — nothing to build first. Otherwise prefer a cut snippet, and
  // fall back to the YouTube embed when neither exists.
  const isLocal = seg.source === "local";
  const hasClip = seg.has_clip ?? false;

  // One word, with the line it came from as its context. Nothing is added until a
  // word is actually clicked — hovering only reads.
  const addWord = async (tok: VocabToken, line: string) => {
    const r = await api.addVocab({
      word: tok.word, context_ko: line, gloss_en: tok.gloss_en,
      pos: tok.pos, display: tok.display, start: tok.start,
    });
    setSaved((n) => n + r.pushed);
    return r;
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Link to="/" className="text-sm text-neutral-500 hover:underline">← Library</Link>
        <h1 className="font-semibold">
          {seg.is_full_review ? "Final full rewatch" : `Day ${seg.day_number}`} · {seg.title}
        </h1>
        <Link to={`/spar/${seg.id}`} className="ml-auto rounded-lg border px-3 py-1.5 text-sm dark:border-neutral-700">
          Sparring →
        </Link>
      </div>

      {/* mode switch enforces blind-first */}
      <div className="inline-flex rounded-lg border border-neutral-300 p-1 text-sm dark:border-neutral-700">
        <button onClick={() => setMode("blind")}
          className={`rounded-md px-3 py-1 ${mode === "blind" ? "bg-indigo-600 text-white" : ""}`}>
          1 · Blind watch
        </button>
        <button onClick={() => setMode("study")}
          className={`rounded-md px-3 py-1 ${mode === "study" ? "bg-indigo-600 text-white" : ""}`}>
          2 · Study (subtitles)
        </button>
      </div>

      <p className="text-sm text-neutral-500">
        {mode === "blind"
          ? "No subtitles, no scrubbing — rely on the audio and the primer you read."
          : "Korean subtitles are synced to the audio. Green words are already in your deck, orange ones you know in another form. Hover a word to read it, click it to make a card."}
      </p>

      {isLocal ? (
        <LocalPlayer key={mode} src={api.streamUrl(seg.video_id)} baseS={0} mode={mode}
          cues={cues} onAddWord={addWord} seekTo={seg.start_s} stopAt={seg.end_s} />
      ) : hasClip ? (
        <LocalPlayer key={mode} src={api.clipUrl(id)} baseS={seg.start_s} mode={mode} cues={cues} onAddWord={addWord} />
      ) : (
        <>
          <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
            Showing the YouTube stream. <Link to={`/primer/${id}`} className="font-medium underline">Build this day</Link> to get the
            exact local clip (frame-accurate) plus the audio/video Anki cards.
          </div>
          <Player key={mode} youtubeId={seg.youtube_id} startS={seg.start_s} endS={seg.end_s} mode={mode} cues={cues} onAddWord={addWord} />
        </>
      )}

      {saved > 0 && <p className="text-sm text-emerald-600">Added {saved} word{saved === 1 ? "" : "s"} to Anki.</p>}
    </div>
  );
}
