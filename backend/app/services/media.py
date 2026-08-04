"""Media infrastructure: download a video once (yt-dlp) and cut clips / extract
audio from it (ffmpeg). Command construction is factored into pure functions
(`build_*_cmd`) so they unit-test without spawning a process; only `_run` and the
public `ensure_video` / `cut_clip` / `extract_audio` actually shell out.

Everything lives under `config.MEDIA_CACHE_DIR/<youtube_id>/` (git-ignored)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import config


class MediaError(RuntimeError):
    """A yt-dlp/ffmpeg/ffprobe invocation failed."""


@dataclass(frozen=True)
class VideoPaths:
    youtube_id: str
    dir: Path
    video: Path          # source.mp4 (video+audio)
    duration_s: float


def cache_dir(youtube_id: str) -> Path:
    return Path(config.MEDIA_CACHE_DIR) / youtube_id


def segment_dir(youtube_id: str, segment_id: int) -> Path:
    return cache_dir(youtube_id) / f"seg{segment_id}"


# --- pure command builders (tested) -------------------------------------

def _ytdlp_argv() -> list[str]:
    """yt-dlp is installed into the venv as a package; its console script may not be
    on the subprocess PATH, so default to `python -m yt_dlp`. An explicit
    LANGAPP_YTDLP_BIN override is honoured as-is."""
    if config.YTDLP_BIN == "yt-dlp":
        return [sys.executable, "-m", "yt_dlp"]
    return [config.YTDLP_BIN]


def _js_runtime_args() -> list[str]:
    """`--js-runtimes NAME:PATH` for the first JS runtime we can find, so yt-dlp can
    solve YouTube's `n` challenge. "auto" probes deno (yt-dlp's own default) then
    node; an explicit setting is passed through as-is. Empty list if none found —
    yt-dlp still runs, just with its deprecation warning."""
    setting = config.YTDLP_JS_RUNTIME
    if not setting:
        return []
    if setting != "auto":
        return ["--js-runtimes", setting]
    for name in ("deno", "node", "bun"):
        # run.sh puts node in ~/.local/node/bin, which isn't on the subprocess PATH.
        path = shutil.which(name) or shutil.which(name, path=str(Path.home() / ".local/node/bin"))
        if path:
            return ["--js-runtimes", f"{name}:{path}"]
    return []


def build_ytdlp_cmd(
    url_or_id: str, out_template: str, *, section: Optional[str] = None,
) -> list[str]:
    """Download a single mp4 (best video+audio, merged) to `out_template`.

    `section` (e.g. "*0:00-30:00") limits the download to a time range so a long
    video doesn't pull in full — passed straight to yt-dlp --download-sections."""
    cmd = [
        *_ytdlp_argv(),
        "-f", config.YTDLP_FORMAT,
        "--merge-output-format", "mp4",
        "-o", out_template,
        "--no-playlist",
        "--no-progress",
        *_js_runtime_args(),
    ]
    if config.YTDLP_REMOTE_COMPONENTS:
        # Fetches (and caches) the challenge-solver script the runtime executes.
        cmd += ["--remote-components", config.YTDLP_REMOTE_COMPONENTS]
    if section:
        cmd += ["--download-sections", section, "--force-keyframes-at-cuts"]
    cmd += [url_or_id]
    return cmd


def build_ffprobe_cmd(src: str) -> list[str]:
    return [
        config.FFPROBE_BIN, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", src,
    ]


def build_ffmpeg_audio_cmd(
    src: str, start_s: float, end_s: float, out: str,
    *, fmt: str = "mp3", sample_rate: Optional[int] = None, mono: bool = False,
) -> list[str]:
    """Extract [start_s, end_s] of `src` as an audio file. Re-encodes (frame
    accurate). For Whisper input use fmt='wav', sample_rate=16000, mono=True."""
    cmd = [
        config.FFMPEG_BIN, "-y",
        "-ss", f"{start_s:.3f}", "-to", f"{end_s:.3f}",
        "-i", src, "-vn",
    ]
    if sample_rate:
        cmd += ["-ar", str(sample_rate)]
    if mono:
        cmd += ["-ac", "1"]
    if fmt == "mp3":
        cmd += ["-codec:a", "libmp3lame", "-q:a", "4"]
    cmd += [out]
    return cmd


def build_ffmpeg_clip_cmd(
    src: str, start_s: float, end_s: float, out: str, *, reencode: bool = True,
) -> list[str]:
    """Cut [start_s, end_s] of `src` as a video clip. reencode=True is frame
    accurate (use for short card clips); reencode=False stream-copies (fast, fine
    for the long ~5-min watch snippet).

    The codec follows the output extension: `.webm` gets VP9/Opus, which is what
    Anki's webview can actually decode (see config.AV_CLIP_EXT); anything else
    gets H.264/AAC."""
    cmd = [config.FFMPEG_BIN, "-y", "-ss", f"{start_s:.3f}", "-to", f"{end_s:.3f}", "-i", src]
    if not reencode:
        cmd += ["-c", "copy"]
    elif out.endswith(".webm"):
        cmd += [
            "-c:v", "libvpx-vp9", "-crf", str(config.AV_CLIP_CRF), "-b:v", "0",
            "-deadline", "good", "-cpu-used", str(config.AV_CLIP_CPU_USED),
            "-row-mt", "1", "-c:a", "libopus", "-b:a", "96k",
        ]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart"]
    cmd += [out]
    return cmd


# --- side-effecting helpers ---------------------------------------------

def _run(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise MediaError(f"executable not found: {cmd[0]!r}") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise MediaError(f"{cmd[0]} failed ({proc.returncode}): {tail}")
    return proc.stdout


def probe_duration(src: str | Path) -> float:
    out = _run(build_ffprobe_cmd(str(src)))
    try:
        return float(json.loads(out)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise MediaError(f"could not read duration from ffprobe output: {out!r}") from exc


def ensure_video(
    youtube_id: str, *, url: Optional[str] = None, section: Optional[str] = None,
) -> VideoPaths:
    """Download the video once into the cache (idempotent). Returns paths + the
    true ffprobe duration (more accurate than the last-caption-end estimate).

    `section` (e.g. "*0:00-30:00") limits a long video to a time window."""
    d = cache_dir(youtube_id)
    d.mkdir(parents=True, exist_ok=True)
    video = d / "source.mp4"
    if not video.exists():
        _run(build_ytdlp_cmd(url or youtube_id, str(d / "source.%(ext)s"), section=section))
        if not video.exists():
            # yt-dlp may have produced a non-mp4 container; grab whatever it made.
            produced = next((p for p in d.glob("source.*") if p.suffix != ".part"), None)
            if produced is None:
                raise MediaError(f"yt-dlp produced no output for {youtube_id!r}")
            video = produced
    return VideoPaths(youtube_id=youtube_id, dir=d, video=video,
                      duration_s=probe_duration(video))


def extract_audio(
    src: str | Path, start_s: float, end_s: float, out: str | Path,
    *, fmt: str = "mp3", sample_rate: Optional[int] = None, mono: bool = False,
) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(build_ffmpeg_audio_cmd(str(src), start_s, end_s, str(out),
                                fmt=fmt, sample_rate=sample_rate, mono=mono))
    return out


def cut_clip(
    src: str | Path, start_s: float, end_s: float, out: str | Path,
    *, reencode: bool = True,
) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(build_ffmpeg_clip_cmd(str(src), start_s, end_s, str(out), reencode=reencode))
    return out
