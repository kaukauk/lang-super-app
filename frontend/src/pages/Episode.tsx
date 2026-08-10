import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import LocalPlayer from "../components/LocalPlayer";
import { api, type Cue, type VideoDetail, type VocabToken } from "../lib/api";

// Just watch the episode. No day, no blind-then-study order, no marking anything
// done — the whole file, seekable, subtitles on or off.
//
// Deliberately separate from the study day rather than a mode inside it: the day's
// constraints (fixed five minutes, no scrubbing, blind pass first) are the method,
// and quietly relaxing them there would make it possible to skip the method by
// accident. Here nothing is being tracked, so nothing needs protecting.
export default function Episode() {
  const { vid } = useParams();
  const id = Number(vid);
  const [video, setVideo] = useState<VideoDetail | null>(null);
  const [cues, setCues] = useState<Cue[]>([]);
  const [err, setErr] = useState("");
  const [added, setAdded] = useState(0);

  useEffect(() => {
    setVideo(null);
    api.video(id).then(setVideo).catch((e) => setErr(String(e)));
    api.videoCues(id).then(setCues).catch(() => setCues([]));
  }, [id]);

  const addWord = async (tok: VocabToken, line: string) => {
    const r = await api.addVocab({
      word: tok.word, context_ko: line, gloss_en: tok.gloss_en,
      pos: tok.pos, display: tok.display, start: tok.start,
    });
    setAdded((n) => n + r.pushed);
    return r;
  };

  if (err) return <p className="text-sm text-red-600">{err}</p>;
  if (!video) return <p className="text-neutral-500">Loading…</p>;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/" className="text-sm text-neutral-500 hover:underline">← Library</Link>
        <h1 className="font-semibold">{video.title}</h1>
        <span className="text-sm text-neutral-500">{Math.round(video.duration_s / 60)} min</span>
      </div>

      {!video.playable ? (
        <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          This one isn't a local file, so there is nothing to play here. Open it from
          the library's day plan to watch it on YouTube.
        </div>
      ) : (
        <>
          {cues.length === 0 && (
            <p className="text-sm text-amber-600 dark:text-amber-400">
              No subtitles stored for this episode.
            </p>
          )}

          {/* Subtitles are a control inside the player (CC), not a checkbox on the
              page: toggling them mid-episode is a viewing decision, and it must
              not restart playback. */}
          <LocalPlayer
            src={api.streamUrl(video.id)} baseS={0} seekable
            mode={cues.length ? "study" : "blind"}
            cues={cues}
            onAddWord={addWord}
          />

          <p className="text-sm text-neutral-500">
            Space or K plays and pauses · ← → jump 10s · ↑ ↓ volume · M mute ·
            C subtitles · F fullscreen · double-click the video for fullscreen
          </p>
          {cues.length > 0 && (
            <p className="text-sm text-neutral-500">
              Green words are already in your deck, orange ones you know in another
              form. Hover a word to read it, click it to make a card.
            </p>
          )}
          {added > 0 && (
            <p className="text-sm text-emerald-600">
              Added {added} word{added === 1 ? "" : "s"} to Anki.
            </p>
          )}
        </>
      )}
    </div>
  );
}
