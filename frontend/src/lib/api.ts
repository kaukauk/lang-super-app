// Typed client for the FastAPI backend. All calls go through /api (proxied in
// dev by Vite, same-origin in production where FastAPI serves the built app).

export type Video = {
  added_at?: string | null; id: number; youtube_id: string; title: string; duration_s: number };
export type VideoDetail = {
  id: number; youtube_id: string; title: string; duration_s: number;
  source: string; playable: boolean;
  series: string | null; season: number | null; episode: number | null;
  part_label: string | null;
};
export type Segment = {
  id: number; day: number; start_s: number; end_s: number; is_full_review: boolean;
  done?: boolean;
};
export type SegmentDetail = {
  id: number; video_id: number; youtube_id: string; title: string;
  day_number: number; start_s: number; end_s: number; is_full_review: boolean;
  has_clip?: boolean; source?: string; part_label?: string | null;
};
export type Cue = { start_s: number; dur_s: number; text: string };
export type LibraryEpisode = {
  video_id: number; youtube_id: string; title: string; source: string;
  season: number | null; episode: number | null; part_label: string | null;
  duration_s: number; n_days: number; n_done: number; complete: boolean;
  next_segment_id: number | null; next_day: number | null;
};
export type LibrarySeries = {
  title: string;
  seasons: { season: number; episodes: LibraryEpisode[] }[];
  n_episodes: number; n_complete: number; days_total: number; days_done: number;
  continue: LibraryEpisode | null;
};
export type LibraryOverview = { series: LibrarySeries[]; videos: LibraryEpisode[] };
// One highlightable word inside a subtitle line. `start`/`end` index into the
// line's characters, so the renderer can colour the word in place without
// re-tokenising anything in the browser.
export type VocabToken = {
  start: number; end: number; surface: string; word: string; lemma: string;
  pos: string; display: string; gloss_en: string;
  status: "known" | "derived" | "new";
};
export type AnnotatedLine = { text: string; tokens: VocabToken[] };
export type Chunk = { type: string; ko: string; en: string; gloss: string };
export type Extraction = {
  primer_md: string; counts: Record<string, number>; warnings: string[]; chunks: Chunk[];
  accepted_card_ids?: number[];
};
export type Card = {
  id: number; kind: string; front_ko: string; back_en: string; gloss: string; state: string;
};
export type Stats = { total: number | null; chunks: number | null; source: string };
export type Correction = {
  original: string; corrected: string; note_en: string;
  kind: "spelling" | "grammar" | "naturalness";
};
export type SparReply = {
  reply_ko: string; micro_feedback_en: string; corrections: Correction[];
  suggested_user_reply_ko: string;
  suggested_user_reply_en: string; targets_used: string[]; unexpected: string[];
  warnings: string[]; glosses: Record<string, string>;
};
export type ChunkCard = {
  note_id: number; added_at: string | null; prompt: string; chunk: string; context: string; ending: string;
  anchor: string; target: string; gloss: string; audio: string; tags: string[];
};
export type Lookup = { word: string; gloss_en: string; ending: string; lemma: string };
export type SparOpening = {
  anchor_ko: string; role_en: string; prompt_en: string; scene_en: string;
  glosses: Record<string, string>;
};
export type AvTarget = {
  target_idx: number; target_ko: string; context_ko: string; explanation_en: string;
  start_s: number; end_s: number; context_start_s: number;
  audio_file: string; clip_file: string; primer_audio: string; anki_note_id: number | null;
};
export type AvBuild = {
  segment_id: number; youtube_id: string; snippet_path: string; pushed: number;
  warnings: string[]; targets: AvTarget[];
};
export type PrimerItem = {
  target_idx: number; target_ko: string; hint_en: string; audio_path: string;
  context_ko: string; seen: boolean | null; has_clip: boolean;
};

function qs(params?: Record<string, string | number | undefined>): string {
  if (!params) return "";
  const p = Object.entries(params).filter(([, v]) => v !== undefined && v !== null);
  return p.length ? "?" + p.map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&") : "";
}

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const r = await fetch("/api" + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  const ct = r.headers.get("content-type") || "";
  return (ct.includes("json") ? r.json() : r.text()) as Promise<T>;
}

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  health: () => req<{ ok: boolean }>("/health"),
  videos: () => req<Video[]>("/videos"),
  ingest: (body: { url?: string; path?: string; replace?: boolean }) =>
    req<{ video_id: number; n_study_segments: number; title?: string }>("/videos/ingest", post(body)),
  video: (vid: number) => req<VideoDetail>(`/videos/${vid}`),
  videoCues: (vid: number) => req<Cue[]>(`/videos/${vid}/cues`),
  segments: (vid: number) => req<Segment[]>(`/videos/${vid}/segments`),
  segment: (sid: number) => req<SegmentDetail>(`/segments/${sid}`),
  cues: (sid: number) => req<Cue[]>(`/segments/${sid}/cues`),
  extract: (sid: number, body: { accept?: boolean; today?: number }) =>
    req<Extraction>(`/segments/${sid}/extract`, post(body)),
  acceptChunks: (body: { chunks: Chunk[]; segment_id?: number }) =>
    req<{ accepted: number }>("/cards/accept", post(body)),
  stats: (params?: { today?: number }) => req<Stats>(`/stats${qs(params)}`),
  cards: () => req<Card[]>("/cards"),
  addCard: (body: { ko: string; en?: string; gloss?: string }) =>
    req<{ id: number }>("/cards", post(body)),
  exportTxt: () => fetch("/api/export.txt").then((r) => r.text()),
  importTxt: (content: string, deck = "Korean") =>
    req<{ created: number; skipped: number }>("/import", post({ content, deck })),
  sparStart: (segment_id?: number) =>
    req<{ session_id: number }>("/spar/start", post({ segment_id })),
  sparOpen: (sid: number, segment_id?: number) =>
    req<SparOpening>(`/spar/${sid}/open`, post({ segment_id })),
  sparTurn: (sid: number, body: { text: string; scene?: string; wind_down?: boolean }) =>
    req<SparReply>(`/spar/${sid}/turn`, post(body)),
  chunksBrowse: () => req<{ deck: string; cards: ChunkCard[] }>("/chunks/browse"),
  chunkAudioUrl: (f: string) => `/api/chunks/audio/${encodeURIComponent(f)}`,
  sparLookup: (sid: number, word: string, context_ko: string,
               known_glosses: Record<string, string> = {}) =>
    req<Lookup>(`/spar/${sid}/lookup`, post({ word, context_ko, known_glosses })),
  sparHarvest: (sid: number, accept = true) =>
    req<{ chunks: Chunk[]; corrections: Correction[]; lookups: Lookup[];
          accepted_card_ids?: number[]; anki?: { pushed: number; error?: string } }>(
      `/spar/${sid}/harvest`, post({ accept, push_anki: true })),

  // --- AV pipeline (audio/video cards + in-app primer) ---
  buildAv: (sid: number, body: { push?: boolean; make_media?: boolean; replace?: boolean; url?: string; section?: string } = {}) =>
    req<AvBuild>(`/segments/${sid}/build-av-cards`, post(body)),
  primer: (sid: number) => req<PrimerItem[]>(`/segments/${sid}/primer`),
  primerAudioUrl: (sid: number, idx: number) => `/api/segments/${sid}/primer-audio/${idx}`,
  primerClipUrl: (sid: number, idx: number) => `/api/segments/${sid}/primer-clip/${idx}`,
  markSeen: (sid: number, idx: number, seen: boolean | null) =>
    req<{ target_idx: number; seen: boolean | null }>(
      `/segments/${sid}/primer/${idx}/seen`, post({ seen })),
  clipUrl: (sid: number) => `/api/segments/${sid}/clip`,
  streamUrl: (videoId: number) => `/api/videos/${videoId}/stream`,
  library: () => req<LibraryOverview>("/library"),
  ingestSeason: (season: number, replace = false) =>
    req<{ season: number; added: number; skipped: string[]; failed: string[] }>(
      "/library/ingest-season", { method: "POST", body: JSON.stringify({ season, replace }) }),
  markDone: (sid: number, done = true) =>
    req<{ segment_id: number; done: boolean }>(`/segments/${sid}/done?done=${done}`,
      { method: "POST" }),
  ankiCheck: () => req<{ ok: boolean; url: string; version: number; decks: string[] }>("/anki/check"),
  annotate: (texts: string[]) =>
    req<{ lines: AnnotatedLine[] }>("/annotate", post({ texts })),
  addVocab: (body: { word: string; context_ko: string; gloss_en?: string;
                     pos?: string; display?: string; start?: number }) =>
    req<{ pushed: number; skipped: number; deck: string;
          shape: "cloze" | "isolated"; blocked_by: string[] }>("/vocab/add", post(body)),
};
