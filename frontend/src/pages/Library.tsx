import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Segment, type Stats, type Video } from "../lib/api";
import { fmt } from "../lib/fmt";

export default function Library() {
  const [videos, setVideos] = useState<Video[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [sel, setSel] = useState<Video | null>(null);
  const [segs, setSegs] = useState<Segment[]>([]);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const reload = () => {
    api.videos().then(setVideos).catch((e) => setErr(String(e)));
    api.stats().then(setStats).catch(() => {});
  };
  useEffect(reload, []);

  const open = async (v: Video) => {
    setSel(v);
    setSegs(await api.segments(v.id));
  };

  const ingest = async () => {
    if (!url.trim()) return;
    setBusy(true); setErr("");
    try {
      await api.ingest({ url: url.trim() });
      setUrl("");
      reload();
    } catch (e) { setErr(String(e)); }
    finally { setBusy(false); }
  };

  const doExport = async () => {
    const txt = await api.exportTxt();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([txt], { type: "text/plain" }));
    a.download = "korean-deck.txt";
    a.click();
  };

  const doImport = async (f: File) => {
    const txt = await f.text();
    const r = await api.importTxt(txt);
    alert(`Imported ${r.created} cards (${r.skipped} skipped)`);
    reload();
  };

  return (
    <div className="space-y-6">
      {/* deck stats + Anki interop */}
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
        <div className="flex gap-4 text-sm">
          <Stat label="Due today" value={stats?.due} accent />
          <Stat label="New" value={stats?.new} />
          <Stat label="Total cards" value={stats?.total} />
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Link to="/warmup" className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-indigo-700">
            Start warm-up →
          </Link>
          <button onClick={doExport} className="rounded-lg border px-3 py-1.5 text-sm dark:border-neutral-700">Export .txt</button>
          <label className="cursor-pointer rounded-lg border px-3 py-1.5 text-sm dark:border-neutral-700">
            Import .txt
            <input type="file" accept=".txt" className="hidden" onChange={(e) => e.target.files?.[0] && doImport(e.target.files[0])} />
          </label>
        </div>
      </div>

      {/* add a video */}
      <div className="rounded-xl border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
        <h2 className="mb-2 font-semibold">Add a video</h2>
        <div className="flex gap-2">
          <input
            value={url} onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ingest()}
            placeholder="YouTube URL or video id"
            className="flex-1 rounded-lg border border-neutral-300 px-3 py-2 dark:border-neutral-700 dark:bg-neutral-950"
          />
          <button onClick={ingest} disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
            {busy ? "Fetching…" : "Ingest"}
          </button>
        </div>
        {err && <p className="mt-2 text-sm text-red-600">{err}</p>}
      </div>

      {/* video list */}
      <div className="grid gap-2">
        {videos.map((v) => (
          <button key={v.id} onClick={() => open(v)}
            className={`rounded-lg border p-3 text-left transition hover:border-indigo-400 dark:border-neutral-800 ${sel?.id === v.id ? "border-indigo-500" : "border-neutral-200"}`}>
            <div className="font-medium">{v.title || v.youtube_id}</div>
            <div className="text-sm text-neutral-500">{v.youtube_id} · {fmt(v.duration_s)}</div>
          </button>
        ))}
        {videos.length === 0 && <p className="text-sm text-neutral-500">No videos yet — add one above.</p>}
      </div>

      {/* day plan for selected video */}
      {sel && (
        <div className="rounded-xl border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
          <h2 className="mb-3 font-semibold">{sel.title || sel.youtube_id} — day plan</h2>
          <div className="space-y-2">
            {segs.map((s) => (
              <div key={s.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-neutral-200 p-2 dark:border-neutral-800">
                <span className="w-20 font-medium">
                  {s.is_full_review ? "Final" : `Day ${s.day}`}
                </span>
                <span className="text-sm text-neutral-500">{fmt(s.start_s)}–{fmt(s.end_s)} ({fmt(s.end_s - s.start_s)})</span>
                <div className="ml-auto flex gap-2">
                  {!s.is_full_review && (
                    <Link to={`/primer/${s.id}`} className="rounded-md border border-indigo-300 px-2.5 py-1 text-sm font-medium text-indigo-700 hover:bg-indigo-50 dark:border-indigo-800 dark:text-indigo-300">1 · Prime</Link>
                  )}
                  <Link to={`/watch/${s.id}`} className="rounded-md border px-2.5 py-1 text-sm dark:border-neutral-700">2 · Watch</Link>
                  {!s.is_full_review && (
                    <Link to={`/spar/${s.id}`} className="rounded-md border px-2.5 py-1 text-sm dark:border-neutral-700">3 · Spar</Link>
                  )}
                  {!s.is_full_review && (
                    <Link to={`/extract/${s.id}`} className="rounded-md border px-2.5 py-1 text-sm text-neutral-500 dark:border-neutral-700">Chunks</Link>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value?: number; accent?: boolean }) {
  return (
    <div>
      <div className={`text-2xl font-bold ${accent ? "text-indigo-600" : ""}`}>{value ?? "—"}</div>
      <div className="text-xs text-neutral-500">{label}</div>
    </div>
  );
}
