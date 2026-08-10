import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useNavigate } from "react-router-dom";
import {
  api, type LibraryEpisode, type LibraryOverview, type LibrarySeries,
  type Segment, type Stats,
} from "../lib/api";
import { fmt } from "../lib/fmt";

// The library. Series get a shelf with one obvious action — Continue — because
// the thing you do 95% of the time is "carry on where I left off", and that
// should never cost more than a click. Everything else (picking a specific
// episode, adding a URL, deck import/export) stays available but out of the way.
//
// The whole screen comes from one /api/library call: per-episode requests would
// be dozens of round trips for a view meant to feel instant.

export default function Library() {
  const [data, setData] = useState<LibraryOverview | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(() => {
    api.library().then(setData).catch((e) => setErr(String(e)));
    api.stats().then(setStats).catch(() => {});
  }, []);
  useEffect(load, [load]);

  const importSeason = async (season: number) => {
    setBusy(`s${season}`); setErr("");
    try { await api.ingestSeason(season); load(); }
    catch (e) { setErr(String(e)); }
    finally { setBusy(""); }
  };

  if (!data) return <p className="text-neutral-500">Loading…</p>;
  const empty = data.series.length === 0 && data.videos.length === 0;

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">Immersion library</h1>
        <span className="text-sm text-neutral-500">
          {stats?.total ?? "—"} cards in Anki · {stats?.chunks ?? "—"} harvested chunks
        </span>
        <Link to="/chunks" className="ml-auto rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-indigo-700">
          Browse chunks →
        </Link>
      </div>

      {err && (
        <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">{err}</p>
      )}

      {empty && (
        <div className="rounded-xl border border-dashed border-neutral-300 p-8 text-center dark:border-neutral-700">
          <p className="font-medium">Nothing here yet.</p>
          <p className="mt-1 text-sm text-neutral-500">
            Import a downloaded season below, or add a single video by URL.
          </p>
        </div>
      )}

      {data.series.map((s) => <Shelf key={s.title} series={s} onChange={load} />)}

      {data.videos.length > 0 && (
        <section className="space-y-3">
          <h2 className="font-semibold">Other videos</h2>
          <Grid episodes={data.videos} onChange={load} />
        </section>
      )}

      <Admin busy={busy} onImportSeason={importSeason} onChange={load} setErr={setErr} />
    </div>
  );
}

function Shelf({ series, onChange }: { series: LibrarySeries; onChange: () => void }) {
  const nav = useNavigate();
  const [season, setSeason] = useState(series.seasons[0]?.season ?? 1);
  const episodes = useMemo(
    () => series.seasons.find((s) => s.season === season)?.episodes ?? [],
    [series, season]);

  const pct = series.days_total ? (series.days_done / series.days_total) * 100 : 0;
  const cont = series.continue;

  return (
    <section className="space-y-4">
      <div className="rounded-2xl bg-gradient-to-br from-indigo-600 to-violet-700 p-6 text-white">
        <h2 className="text-lg font-semibold">{series.title}</h2>
        <p className="mt-1 text-sm text-indigo-100">
          {series.n_complete} of {series.n_episodes} episodes finished ·{" "}
          {series.days_done} of {series.days_total} days
        </p>
        <div className="mt-3 h-2 w-full max-w-md overflow-hidden rounded-full bg-white/25">
          <div className="h-full rounded-full bg-white transition-all" style={{ width: `${pct}%` }} />
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          {cont?.next_segment_id ? (
            <>
              <button onClick={() => nav(`/primer/${cont.next_segment_id}`)}
                className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-indigo-700 hover:bg-indigo-50">
                ▶ Continue · {label(cont)} day {cont.next_day}
              </button>
              <span className="text-sm text-indigo-100">
                {cont.n_done} of {cont.n_days} days done in that episode
              </span>
            </>
          ) : (
            <span className="rounded-lg bg-white/20 px-4 py-2 text-sm font-medium">
              Series complete — nothing left to study
            </span>
          )}
        </div>
      </div>

      {series.seasons.length > 1 && (
        <div className="inline-flex rounded-lg border border-neutral-300 p-0.5 text-sm dark:border-neutral-700">
          {series.seasons.map((s) => (
            <button key={s.season} onClick={() => setSeason(s.season)}
              className={`rounded-md px-3 py-1 ${s.season === season ? "bg-indigo-600 text-white" : ""}`}>
              Season {s.season}
            </button>
          ))}
        </div>
      )}

      <Grid episodes={episodes} onChange={onChange} />
    </section>
  );
}

function Grid({ episodes, onChange }: { episodes: LibraryEpisode[]; onChange: () => void }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {episodes.map((ep) => (
          <EpisodeCard key={ep.video_id} ep={ep}
            open={open === ep.video_id}
            onToggle={() => setOpen(open === ep.video_id ? null : ep.video_id)} />
        ))}
      </div>
      {open !== null && (
        <DayPlan video={episodes.find((e) => e.video_id === open)!} onChange={onChange} />
      )}
    </div>
  );
}

function label(ep: LibraryEpisode) {
  if (ep.season == null || ep.episode == null) return ep.title;
  // "8a (parts 1-3)" -> the "a"; a whole episode has no suffix.
  const m = ep.part_label?.match(/^\d+([a-z])/);
  return `S${ep.season}E${ep.episode}${m ? m[1] : ""}`;
}

function EpisodeCard({ ep, open, onToggle }: {
  ep: LibraryEpisode; open: boolean; onToggle: () => void;
}) {
  const pct = ep.n_days ? (ep.n_done / ep.n_days) * 100 : 0;
  // A div wrapping a button and a link, not one big button: "study this episode"
  // and "just watch it" are different intentions and a link nested inside a button
  // is not a thing the browser will honour.
  return (
    <div
      className={`flex h-full flex-col rounded-xl border p-4 text-left transition
        ${open ? "border-indigo-500 ring-1 ring-indigo-500" : ep.complete
          ? "border-emerald-300 bg-emerald-50/50 dark:border-emerald-900 dark:bg-emerald-950/20"
          : "border-neutral-200 bg-white hover:border-indigo-400 dark:border-neutral-800 dark:bg-neutral-900"}`}>
      <button onClick={onToggle} className="flex-1 text-left">
        <div className="flex items-baseline gap-2">
          <span className="font-semibold">{label(ep)}</span>
          {ep.complete && <span className="text-xs font-medium text-emerald-600 dark:text-emerald-400">✓ done</span>}
          <span className="ml-auto text-xs text-neutral-400">{Math.round(ep.duration_s / 60)} min</span>
        </div>
        {/* a split episode is missing footage — say which parts, so the jump isn't a surprise */}
        {ep.part_label && (
          <span className="mt-1 block text-xs text-amber-600 dark:text-amber-400">{ep.part_label}</span>
        )}
        <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800">
          <div className={`h-full rounded-full ${ep.complete ? "bg-emerald-500" : "bg-indigo-500"}`}
            style={{ width: `${pct}%` }} />
        </div>
        <p className="mt-2 text-xs text-neutral-500">
          {ep.n_done} / {ep.n_days} days{!ep.complete && ep.next_day ? ` · next: day ${ep.next_day}` : ""}
        </p>
      </button>
      <div className="mt-3 flex items-center gap-2 border-t border-neutral-200 pt-3 dark:border-neutral-800">
        {/* Only local episodes: watching straight through means playing the file. */}
        {ep.source === "local" && (
          <Link to={`/episode/${ep.video_id}`}
            className="rounded-lg bg-neutral-900 px-3 py-1.5 text-sm font-semibold text-white hover:bg-neutral-700 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200">
            ▶ Watch
          </Link>
        )}
        <button onClick={onToggle}
          className="ml-auto rounded-lg border px-3 py-1.5 text-sm text-neutral-600 hover:border-indigo-400 dark:border-neutral-700 dark:text-neutral-300">
          {open ? "Hide days" : "Study days"}
        </button>
      </div>
    </div>
  );
}

function DayPlan({ video, onChange }: { video: LibraryEpisode; onChange: () => void }) {
  const [segs, setSegs] = useState<Segment[] | null>(null);
  useEffect(() => { setSegs(null); api.segments(video.video_id).then(setSegs); },
    [video.video_id]);

  const toggle = async (sid: number, done: boolean) => {
    await api.markDone(sid, done);
    onChange();
    setSegs(await api.segments(video.video_id));
  };

  if (!segs) return <p className="text-sm text-neutral-500">Loading day plan…</p>;
  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
      <h3 className="mb-3 font-semibold">{video.title} — day plan</h3>
      <div className="space-y-2">
        {segs.map((s) => (
          <div key={s.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-neutral-200 p-2 dark:border-neutral-800">
            <input type="checkbox" checked={!!s.done} onChange={(e) => toggle(s.id, e.target.checked)}
              className="h-4 w-4 accent-emerald-600" title="Mark this day finished" />
            <span className="w-20 font-medium">{s.is_full_review ? "Final" : `Day ${s.day}`}</span>
            <span className="text-sm text-neutral-500">
              {fmt(s.start_s)}–{fmt(s.end_s)} ({fmt(s.end_s - s.start_s)})
            </span>
            <div className="ml-auto flex gap-2">
              {!s.is_full_review && (
                <Link to={`/primer/${s.id}`} className="rounded-md border border-indigo-300 px-2.5 py-1 text-sm font-medium text-indigo-700 hover:bg-indigo-50 dark:border-indigo-800 dark:text-indigo-300">1 · Prime</Link>
              )}
              <Link to={`/watch/${s.id}`} className="rounded-md border px-2.5 py-1 text-sm dark:border-neutral-700">2 · Watch</Link>
              {!s.is_full_review && (
                <Link to={`/check/${s.id}`} className="rounded-md border px-2.5 py-1 text-sm dark:border-neutral-700">3 · Check</Link>
              )}
              {!s.is_full_review && (
                <Link to={`/spar/${s.id}`} className="rounded-md border px-2.5 py-1 text-sm dark:border-neutral-700">4 · Spar</Link>
              )}
              {!s.is_full_review && (
                <Link to={`/extract/${s.id}`} className="rounded-md border px-2.5 py-1 text-sm text-neutral-500 dark:border-neutral-700">Chunks</Link>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// Adding videos and moving the deck around: needed, but not what this screen is
// for, so it lives at the bottom behind a disclosure.
function Admin({ busy, onImportSeason, onChange, setErr }: {
  busy: string; onImportSeason: (s: number) => void;
  onChange: () => void; setErr: (s: string) => void;
}) {
  const [url, setUrl] = useState("");
  const [adding, setAdding] = useState(false);

  const ingest = async () => {
    if (!url.trim()) return;
    setAdding(true); setErr("");
    try { await api.ingest({ url: url.trim() }); setUrl(""); onChange(); }
    catch (e) { setErr(String(e)); }
    finally { setAdding(false); }
  };

  const doExport = async () => {
    const txt = await api.exportTxt();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([txt], { type: "text/plain" }));
    a.download = "korean-deck.txt";
    a.click();
  };

  const doImport = async (f: File) => {
    const r = await api.importTxt(await f.text());
    alert(`Imported ${r.created} cards (${r.skipped} skipped)`);
    onChange();
  };

  return (
    <details className="rounded-xl border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
      <summary className="cursor-pointer font-semibold">Add videos &amp; deck tools</summary>

      <div className="mt-4 space-y-4">
        <div>
          <p className="mb-2 text-sm text-neutral-500">
            Import a season you have already downloaded and joined.
          </p>
          <div className="flex gap-2">
            {[1, 2].map((s) => (
              <button key={s} onClick={() => onImportSeason(s)} disabled={!!busy}
                className="rounded-lg border px-3 py-1.5 text-sm disabled:opacity-50 dark:border-neutral-700">
                {busy === `s${s}` ? "Importing…" : `Import season ${s}`}
              </button>
            ))}
          </div>
        </div>

        <div>
          <p className="mb-2 text-sm text-neutral-500">Or add a single YouTube video.</p>
          <div className="flex gap-2">
            <input value={url} onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && ingest()}
              placeholder="YouTube URL or video id"
              className="flex-1 rounded-lg border border-neutral-300 px-3 py-2 dark:border-neutral-700 dark:bg-neutral-950" />
            <button onClick={ingest} disabled={adding}
              className="rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
              {adding ? "Fetching…" : "Ingest"}
            </button>
          </div>
        </div>

        <div className="flex gap-2">
          <button onClick={doExport} className="rounded-lg border px-3 py-1.5 text-sm dark:border-neutral-700">Export .txt</button>
          <label className="cursor-pointer rounded-lg border px-3 py-1.5 text-sm dark:border-neutral-700">
            Import .txt
            <input type="file" accept=".txt" className="hidden"
              onChange={(e) => e.target.files?.[0] && doImport(e.target.files[0])} />
          </label>
        </div>
      </div>
    </details>
  );
}
