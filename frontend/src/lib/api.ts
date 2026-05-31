// Typed client for the FastAPI backend. All calls go through /api (proxied in
// dev by Vite, same-origin in production where FastAPI serves the built app).

export type Video = { id: number; youtube_id: string; title: string; duration_s: number };
export type Segment = {
  id: number; day: number; start_s: number; end_s: number; is_full_review: boolean;
};
export type SegmentDetail = {
  id: number; video_id: number; youtube_id: string; title: string;
  day_number: number; start_s: number; end_s: number; is_full_review: boolean;
};
export type Cue = { start_s: number; dur_s: number; text: string };
export type Chunk = { type: string; ko: string; en: string; gloss: string };
export type Extraction = {
  primer_md: string; counts: Record<string, number>; warnings: string[]; chunks: Chunk[];
  accepted_card_ids?: number[];
};
export type Card = {
  id: number; kind: string; front_ko: string; back_en: string; gloss: string; state: string;
};
export type Stats = { total: number; new: number; due: number };
export type SparReply = {
  reply_ko: string; micro_feedback_en: string; suggested_user_reply_ko: string;
  suggested_user_reply_en: string; targets_used: string[]; unexpected: string[];
  warnings: string[]; glosses: Record<string, string>;
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
  segments: (vid: number) => req<Segment[]>(`/videos/${vid}/segments`),
  segment: (sid: number) => req<SegmentDetail>(`/segments/${sid}`),
  cues: (sid: number) => req<Cue[]>(`/segments/${sid}/cues`),
  extract: (sid: number, body: { accept?: boolean; today?: number }) =>
    req<Extraction>(`/segments/${sid}/extract`, post(body)),
  acceptChunks: (body: { chunks: Chunk[]; segment_id?: number }) =>
    req<{ accepted: number }>("/cards/accept", post(body)),
  due: (params?: { today?: number; limit?: number }) =>
    req<Card[]>(`/reviews/due${qs(params)}`),
  grade: (cardId: number, grade: string) =>
    req<Card>(`/reviews/${cardId}/grade`, post({ grade })),
  stats: (params?: { today?: number }) => req<Stats>(`/stats${qs(params)}`),
  cards: () => req<Card[]>("/cards"),
  addCard: (body: { ko: string; en?: string; gloss?: string }) =>
    req<{ id: number }>("/cards", post(body)),
  exportTxt: () => fetch("/api/export.txt").then((r) => r.text()),
  importTxt: (content: string, deck = "Korean") =>
    req<{ created: number; skipped: number }>("/import", post({ content, deck })),
  sparStart: (segment_id?: number) =>
    req<{ session_id: number }>("/spar/start", post({ segment_id })),
  sparTurn: (sid: number, body: { text: string; scene?: string }) =>
    req<SparReply>(`/spar/${sid}/turn`, post(body)),
  sparHarvest: (sid: number, accept = true) =>
    req<{ chunks: Chunk[]; accepted_card_ids?: number[] }>(`/spar/${sid}/harvest`, post({ accept })),
};
