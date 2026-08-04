"""Client for AnkiConnect (https://foosoft.net/projects/anki-connect/).

Every request is a `{action, version, params}` envelope POSTed as JSON; every
response is `{result, error}`. The transport is injectable so the tests (and the
--fake CLI paths) can exercise the payload construction with no Anki running.

The note type is deliberately audio-front / video-back:
  front  target sentence + its clean audio (autoplays)
  back   the source clip (autoplays, carries its own audio) + an English gloss
so the back never replays the front's audio over the video.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Union

from .. import config

VERSION = 6

# A transport takes the request envelope and returns the {result, error} dict.
Transport = Callable[[dict[str, Any]], dict[str, Any]]

FIELDS = ["TargetKo", "TargetAudio", "ContextKo", "Clip", "Explanation"]
TEMPLATE_NAME = "Immersion Recall"


class AnkiError(RuntimeError):
    """AnkiConnect returned a non-null `error`, or the transport failed."""


# --- payload construction -------------------------------------------------

@dataclass
class NoteSpec:
    """One AV card. Media are referenced by bare filename; the files themselves
    are uploaded separately via `storeMediaFile`."""

    target_ko: str
    target_audio_file: str = ""
    context_ko: str = ""
    clip_file: str = ""
    explanation_en: str = ""
    tags: list[str] = field(default_factory=list)

    def fields(self) -> dict[str, str]:
        return {
            "TargetKo": self.target_ko,
            # [sound:...] is what makes Anki autoplay it on the front.
            "TargetAudio": f"[sound:{self.target_audio_file}]" if self.target_audio_file else "",
            "ContextKo": self.context_ko,
            # Bare name — the template wraps it in <video> so the clip is embedded
            # in the card rather than handed to Anki's external mpv player. This
            # only renders for codecs the webview can decode, which is why clips
            # are written as VP9/Opus WebM (see config.AV_CLIP_EXT).
            "Clip": self.clip_file,
            "Explanation": self.explanation_en,
        }


_CSS = """.card {
  font-family: "Noto Sans KR", system-ui, sans-serif;
  font-size: 22px;
  text-align: center;
  color: #1a1a1a;
  background: #fbfbfb;
}
.target { font-size: 30px; font-weight: 600; line-height: 1.5; }
.context { font-size: 18px; color: #666; margin-top: 12px; }
.explanation { font-size: 17px; color: #444; margin-top: 16px; line-height: 1.5; }
video { max-width: 100%; margin: 8px auto; border-radius: 8px; }
hr { border: none; border-top: 1px solid #ddd; margin: 18px 0; }
"""

_FRONT = """<div class="target">{{TargetKo}}</div>
{{TargetAudio}}
"""

# No {{FrontSide}} and no {{TargetAudio}}: the clip carries the original audio,
# and replaying the TTS front over it would talk across the video.
_BACK = """<div class="target">{{TargetKo}}</div>
<video src="{{Clip}}" autoplay controls playsinline></video>
{{#ContextKo}}<div class="context">{{ContextKo}}</div>{{/ContextKo}}
<hr>
<div class="explanation">{{Explanation}}</div>
"""


def build_model_payload(model: str) -> dict[str, Any]:
    """`createModel` params for the AV note type."""
    return {
        "modelName": model,
        "inOrderFields": list(FIELDS),
        "css": _CSS,
        "isCloze": False,
        "cardTemplates": [{"Name": TEMPLATE_NAME, "Front": _FRONT, "Back": _BACK}],
    }


def build_note_payload(note: NoteSpec, deck: Optional[str] = None,
                       model: Optional[str] = None) -> dict[str, Any]:
    """The `note` object for `addNote`."""
    return {
        "deckName": deck or config.ANKI_DECK,
        "modelName": model or config.ANKI_MODEL,
        "fields": note.fields(),
        "tags": list(note.tags),
        # Scope dupes to the deck: the same sentence appearing in another deck
        # is not our problem, but twice in this one is.
        "options": {"allowDuplicate": False, "duplicateScope": "deck"},
    }


# --- transports -----------------------------------------------------------

class HttpTransport:
    """POSTs to a running AnkiConnect. Anki must be open with the add-on installed."""

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        self.url = url
        self.timeout = timeout

    def __call__(self, req: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(req).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except AnkiError:
            raise
        except Exception as exc:
            raise AnkiError(f"cannot reach AnkiConnect at {self.url}: {exc}") from exc


class FakeTransport:
    """In-memory AnkiConnect. Records every request and answers from `results`,
    falling back to sensible empties so tests only pin what they care about."""

    def __init__(self, results: Optional[dict[str, Any]] = None) -> None:
        self.results = dict(results or {})
        self.requests: list[dict[str, Any]] = []
        self._next_note_id = 1500000000000     # shaped like a real Anki note id

    def __call__(self, req: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(req)
        action = req.get("action", "")
        if action in self.results:
            return {"result": self.results[action], "error": None}
        if action == "addNote":
            self._next_note_id += 1
            return {"result": self._next_note_id, "error": None}
        if action in ("deckNames", "modelNames", "findNotes"):
            return {"result": [], "error": None}
        if action == "version":
            return {"result": VERSION, "error": None}
        return {"result": None, "error": None}


# --- client ---------------------------------------------------------------

class AnkiConnectClient:
    def __init__(self, url: Optional[str] = None,
                 transport: Optional[Transport] = None) -> None:
        self.url = url or config.ANKICONNECT_URL
        self.transport: Transport = transport or HttpTransport(self.url)

    # -- plumbing --
    def invoke(self, action: str, **params: Any) -> Any:
        resp = self.transport({"action": action, "version": VERSION, "params": params})
        error = (resp or {}).get("error")
        if error:
            raise AnkiError(f"{action}: {error}")
        return (resp or {}).get("result")

    def version(self) -> int:
        return self.invoke("version")

    # -- deck / model setup --
    def ensure_deck(self, deck: Optional[str] = None) -> None:
        """createDeck is idempotent in AnkiConnect — it returns the existing id."""
        self.invoke("createDeck", deck=deck or config.ANKI_DECK)

    def _existing_template_name(self, model: str) -> str:
        """The name `updateModelTemplates` has to be keyed by to actually hit this
        note type's template.

        AnkiConnect matches templates by name and silently ignores names the note
        type doesn't have — so an update keyed by a name we picked is a no-op on a
        collection whose template is called something else, and the stale template
        survives with no error. Anki has no rename-through-the-API, so target
        whatever is really there instead."""
        existing = list(self.invoke("modelTemplates", modelName=model) or {})
        if TEMPLATE_NAME in existing or not existing:
            return TEMPLATE_NAME
        if len(existing) == 1:
            return existing[0]
        raise AnkiError(
            f"note type {model!r} has templates {existing}, none named {TEMPLATE_NAME!r} — "
            "rename one, or point LANGAPP_ANKI_MODEL at a note type this app owns")

    def ensure_model(self, model: Optional[str] = None) -> None:
        """Create the note type, or push template/styling edits onto the existing
        one so a template change here reaches decks built by an earlier run."""
        model = model or config.ANKI_MODEL
        payload = build_model_payload(model)
        if model not in (self.invoke("modelNames") or []):
            self.invoke("createModel", **payload)
            return
        tmpl = payload["cardTemplates"][0]
        self.invoke("updateModelTemplates", model={
            "name": model,
            "templates": {
                self._existing_template_name(model): {
                    "Front": tmpl["Front"], "Back": tmpl["Back"],
                },
            },
        })
        self.invoke("updateModelStyling", model={"name": model, "css": payload["css"]})

    # -- media --
    def store_media_file(self, filename: str, path: Union[str, Path]) -> Any:
        """Copy a local file into Anki's collection.media by absolute path."""
        return self.invoke("storeMediaFile", filename=filename,
                           path=str(Path(path).resolve()))

    # -- notes --
    def add_note(self, note: NoteSpec, deck: Optional[str] = None,
                 model: Optional[str] = None) -> Optional[int]:
        """Returns the new note id, or None if Anki rejected it as a duplicate —
        re-running a build should be a no-op, not a crash."""
        try:
            return self.invoke("addNote", note=build_note_payload(note, deck, model))
        except AnkiError as exc:
            if "duplicate" in str(exc).lower():
                return None
            raise

    def delete_notes_by_tag(self, tag: str) -> int:
        """Used by --replace to clear a segment's previous cards. Returns the count."""
        ids = self.invoke("findNotes", query=f"tag:{tag}") or []
        if ids:
            self.invoke("deleteNotes", notes=ids)
        return len(ids)
