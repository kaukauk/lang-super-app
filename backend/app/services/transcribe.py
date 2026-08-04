"""Speech-to-text with accurate per-sentence timing (faster-whisper).

YouTube captions decide the coarse ~5-min snippet boundaries; this module gives
the precise sentence timings the AV cards need. The real model is loaded lazily
(singleton). `group_sentences` — the part worth testing — is a pure function that
folds Whisper's word stream into sentences, so the whole pipeline runs offline via
`FakeTranscriber`."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from .. import config


@dataclass
class Word:
    text: str
    start_s: float
    end_s: float


@dataclass
class Sentence:
    text: str
    start_s: float
    end_s: float
    words: list[Word] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text, "start_s": self.start_s, "end_s": self.end_s,
            "words": [{"text": w.text, "start_s": w.start_s, "end_s": w.end_s}
                      for w in self.words],
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Sentence":
        return Sentence(
            text=d["text"], start_s=d["start_s"], end_s=d["end_s"],
            words=[Word(w["text"], w["start_s"], w["end_s"]) for w in d.get("words", [])],
        )


# A sentence ends on terminal punctuation, or on a Korean sentence-final ending
# (…다 / …요 / …까 / …죠 / …네 / …군) followed by a real pause — Whisper often
# omits punctuation on Korean speech.
_TERMINAL_PUNCT = re.compile(r"[.!?…。！？]\s*$")
_KO_ENDER = re.compile(r"(다|요|까|죠|네|군|군요|구나|세요|거든요)$")


def _looks_final(text: str, gap_to_next: float, *, min_gap: float) -> bool:
    t = text.strip()
    if not t:
        return False
    if _TERMINAL_PUNCT.search(t):
        return True
    # strip a trailing quote/paren before checking the Korean ender
    stripped = re.sub(r'["”\'\)\]]+$', "", t)
    return bool(_KO_ENDER.search(stripped)) and gap_to_next >= min_gap


def _force_split_at(cur: list[Word], *, min_prefix_s: float) -> int:
    """Index in `cur` to end a sentence at when the run has outgrown the hard cap.

    Cut at the widest silence, nudged toward a Korean sentence-final ending — the
    least-bad boundary when the speech itself offers no real one. Candidates must
    leave at least `min_prefix_s` behind, so a forced cut can't shave off one word
    and leave the remainder still over the cap.

    With no candidate — a long silence blew past the cap in one step — fall back to
    cutting just before the word that did it, rather than holding an over-long run
    together on the strength of a gap that argues the opposite."""
    best_i, best_score = max(0, len(cur) - 2), -1.0
    for i in range(len(cur) - 1):
        if cur[i].end_s - cur[0].start_s < min_prefix_s:
            continue
        score = cur[i + 1].start_s - cur[i].end_s
        if _KO_ENDER.search(re.sub(r'["”\'\)\]]+$', "", cur[i].text.strip())):
            score += 0.5
        if score > best_score:
            best_i, best_score = i, score
    return best_i


def group_sentences(
    words: list[Word], *, min_gap_s: float = 0.6, max_len_s: Optional[float] = None,
    hard_max_len_s: Optional[float] = None,
) -> list[Sentence]:
    """Fold a flat word stream into sentences. Breaks on terminal punctuation, on a
    Korean sentence-final ending followed by a >= min_gap_s pause, or when a run past
    max_len_s reaches any pause.

    All three of those need a pause, and continuous dialogue may simply never offer
    one — so `hard_max_len_s` force-splits regardless. Without that backstop a run
    grows unbounded: real cartoon audio produced a single 52-second, 176-character
    "sentence", which then became a card target and a minute-long clip."""
    max_len_s = config.WHISPER_SENT_SOFT_MAX_S if max_len_s is None else max_len_s
    hard = config.WHISPER_SENT_HARD_MAX_S if hard_max_len_s is None else hard_max_len_s
    hard = max(hard, max_len_s)

    sentences: list[Sentence] = []
    cur: list[Word] = []

    def emit(run: list[Word]) -> None:
        text = re.sub(r"\s+", " ", "".join(x.text for x in run).strip())
        if text:
            sentences.append(Sentence(text=text, start_s=run[0].start_s,
                                      end_s=run[-1].end_s, words=list(run)))

    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt.start_s - w.end_s) if nxt else float("inf")
        run_len = cur[-1].end_s - cur[0].start_s
        end_here = (
            nxt is None
            or _looks_final(w.text, gap, min_gap=min_gap_s)
            or (run_len >= max_len_s and gap >= min_gap_s)
        )
        if end_here:
            emit(cur)
            cur = []
        elif run_len >= hard:
            k = _force_split_at(cur, min_prefix_s=hard * 0.4)
            emit(cur[:k + 1])
            cur = cur[k + 1:]
    return sentences


# --- provider interface --------------------------------------------------

@runtime_checkable
class Transcriber(Protocol):
    def transcribe(self, audio_path: str | Path) -> list[Sentence]:
        ...


class WhisperTranscriber:
    """faster-whisper backend. The model is heavy, so it is created once per
    process and reused (lazy singleton)."""

    _model = None

    def __init__(self, model: Optional[str] = None, *, device: Optional[str] = None,
                 compute_type: Optional[str] = None, language: Optional[str] = None):
        self.model_name = model or config.WHISPER_MODEL
        self.device = device or config.WHISPER_DEVICE
        self.compute_type = compute_type or config.WHISPER_COMPUTE
        self.language = language or config.WHISPER_LANG

    def _build(self, device: str, compute_type: str):
        from faster_whisper import WhisperModel  # lazy: heavy import
        return WhisperModel(self.model_name, device=device, compute_type=compute_type)

    def _get_model(self):
        if WhisperTranscriber._model is None:
            try:
                WhisperTranscriber._model = self._build(self.device, self.compute_type)
            except Exception:
                WhisperTranscriber._model = self._build("cpu", "int8")
        return WhisperTranscriber._model

    def _run(self, model, audio_path: str | Path):
        return model.transcribe(str(audio_path), language=self.language,
                                word_timestamps=True, vad_filter=config.WHISPER_VAD)

    def transcribe(self, audio_path: str | Path) -> list[Sentence]:
        model = self._get_model()
        try:
            segments, _info = self._run(model, audio_path)
            segments = list(segments)            # force lazy generation (CUDA loads here)
        except (RuntimeError, OSError) as exc:
            # CUDA/cuDNN/cuBLAS libs load lazily at inference; fall back to CPU.
            if not any(s in str(exc).lower() for s in ("cublas", "cudnn", "cuda", "gpu", "library")):
                raise
            WhisperTranscriber._model = self._build("cpu", "int8")
            segments, _info = self._run(WhisperTranscriber._model, audio_path)
            segments = list(segments)
        words: list[Word] = []
        for seg in segments:
            for w in (seg.words or []):
                words.append(Word(text=w.word, start_s=float(w.start), end_s=float(w.end)))
        if not words:
            # no word timestamps (silence/edge case) — fall back to segment spans
            for seg in segments:
                words.append(Word(text=seg.text, start_s=float(seg.start),
                                  end_s=float(seg.end)))
        return group_sentences(words)


class FakeTranscriber:
    """Offline transcriber for tests / --fake. Returns canned sentences, or reads a
    sibling `<audio>.fake.json` fixture if present."""

    def __init__(self, sentences: Optional[list[Sentence]] = None):
        self._sentences = sentences

    def transcribe(self, audio_path: str | Path) -> list[Sentence]:
        if self._sentences is not None:
            return list(self._sentences)
        fixture = Path(str(audio_path) + ".fake.json")
        if fixture.exists():
            return [Sentence.from_dict(d) for d in json.loads(fixture.read_text("utf-8"))]
        return _demo_sentences()


def _demo_sentences() -> list[Sentence]:
    raw = [
        ("오늘 어디 가요?", 0.0, 1.6),
        ("저는 시장에 가요.", 1.8, 3.4),
        ("자전거를 타고 싶어요.", 3.6, 5.2),
        ("날씨가 정말 좋아요.", 5.4, 7.0),
        ("우리 같이 가요.", 7.2, 8.6),
        ("네, 좋아요.", 8.8, 9.8),
    ]
    return [Sentence(text=t, start_s=a, end_s=b,
                     words=[Word(t, a, b)]) for (t, a, b) in raw]


# --- caching -------------------------------------------------------------

def cache_path(youtube_id: str, segment_id: int) -> Path:
    from . import media
    return media.segment_dir(youtube_id, segment_id) / "whisper.json"


def load_cached(youtube_id: str, segment_id: int) -> Optional[list[Sentence]]:
    """The cache is really Whisper's word stream; grouping is a pure post-process, so
    re-derive it on load. A fix to `group_sentences` then reaches segments already
    transcribed instead of needing an expensive re-run to take effect."""
    p = cache_path(youtube_id, segment_id)
    if not p.exists():
        return None
    sents = [Sentence.from_dict(d) for d in json.loads(p.read_text("utf-8"))]
    if sents and all(s.words for s in sents):
        return group_sentences([w for s in sents for w in s.words])
    return sents


def save_cache(youtube_id: str, segment_id: int, sentences: list[Sentence]) -> Path:
    p = cache_path(youtube_id, segment_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([s.as_dict() for s in sentences], ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p
