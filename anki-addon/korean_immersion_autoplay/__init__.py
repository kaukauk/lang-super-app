"""Let the AV card's <video> autoplay with sound when you flip to the answer.

The card back embeds the clip with an HTML <video autoplay>. Anki's webview is
Chromium, which refuses unmuted autoplay unless the document has a user
activation — and the Show Answer button lives in the reviewer's *bottom* webview,
so the click never activates the document the video is in. The clip therefore
sits there paused.

Measured against Anki's own QtWebEngine (25.09.5):

    unmuted autoplay, no gesture      play() rejected, NotAllowedError
    autoplay muted, then unmute       plays, then Chromium PAUSES it on unmute
    PlaybackRequiresUserGesture=False plays, unmuted, start to finish

So the muted-then-unmute trick that looks like the obvious workaround does not
work: Blink re-gates unmuting and pauses the element. Clearing the attribute is
what actually works, and it is exactly the same switch as Chromium's
--autoplay-policy=no-user-gesture-required, without depending on how Anki was
launched.

Scope: this turns off gesture-gated playback for Anki's webviews generally, not
just this note type. That is the whole mechanism — Chromium has no per-element
opt-out. In a flashcard app whose media you authored yourself, that is the point.

Install: copy this folder into your Anki addons21 directory and restart Anki.
  Tools > Add-ons > View Files, then drop `korean_immersion_autoplay` alongside.
"""

from __future__ import annotations

from aqt import gui_hooks, mw
from aqt.qt import QWebEngineProfile, QWebEngineSettings

_ATTR = QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture


def _allow(settings) -> None:
    if settings is not None:
        settings.setAttribute(_ATTR, False)


def _apply_to_profile() -> None:
    """The default profile covers webviews created later."""
    try:
        _allow(QWebEngineProfile.defaultProfile().settings())
    except Exception:
        pass


def _apply_to_reviewer() -> None:
    """Anki may give the reviewer its own page/profile, so set it there too. Runs on
    every card; setAttribute is idempotent and cheap.

    Wrapped broadly on purpose: this add-on is a nicety, and an API change in a
    future Anki must never be able to break reviewing itself."""
    try:
        page = mw.reviewer.web.page()
        _allow(page.settings())
        _allow(page.profile().settings())
    except Exception:
        pass


_apply_to_profile()
gui_hooks.main_window_did_init.append(_apply_to_profile)
gui_hooks.reviewer_did_show_answer.append(lambda _card: _apply_to_reviewer())
