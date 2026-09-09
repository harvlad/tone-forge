"""Web audio invariants — the rules that keep mobile browsers audible.

Three faults shipped together and made the Auto Kit pad grid silent on
iPhone Safari while its UI kept animating. Each was a one-line habit that
reads fine in review, so each gets a static guard here rather than a code
comment nobody greps.

1. ONE AudioContext PER PAGE. Nine surfaces each did
   ``new (window.AudioContext || window.webkitAudioContext)()``. WebKit
   caps a page at four concurrent contexts; past the cap the constructor
   returns a context that never reaches ``running``, and because pad and
   waveform UIs animate off requestAnimationFrame rather than the audio
   clock, the page looks alive over silence. ``audio-context.js`` owns the
   single context; everyone else borrows it.

2. ``state === "suspended"`` IS NOT AN AUDIO-STOPPED CHECK. Safari has a
   fourth, non-standard state — ``interrupted`` — entered on a call, Siri,
   an AirPods route change, or a backgrounded tab. Equality guards skip
   it, so the context is never resumed. The correct test is
   ``state !== "running"`` (or a ``JamnAudio.unlock()`` call).

3. LOAD ORDER. ``audio-context.js`` defines ``window.JamnAudio``, which
   every audio surface now calls at mount. A page that loads a surface
   before (or without) it throws on the first pad press.

These are text rules over ``backend/static/*.js`` because the files are
classic scripts with no module graph to walk. They are deliberately
narrow: each one names the exact bug it prevents, and each has a
documented escape hatch (extend ``CONTEXT_OWNERS`` / add an inline
``allow-audio-state-check`` comment) so a genuine exception is a visible
decision rather than a silent edit.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC_ROOT = Path(__file__).resolve().parents[1] / "static"

# The only module allowed to construct an AudioContext.
CONTEXT_OWNERS = {"audio-context.js"}

# Scripts that play audio and therefore need window.JamnAudio in scope.
AUDIO_SURFACES = {
    "jam.js",
    "kit.js",
    "sequencer.js",
    "chopedit.js",
    "contribute.js",
    "arrangement.js",
    "lpview.js",
    "launchpad.js",
    "stage.js",
}

# Opt-out for a line that genuinely means "is it specifically suspended?"
ESCAPE_HATCH = "allow-audio-state-check"


def _sources() -> list[Path]:
    return sorted(
        p for p in STATIC_ROOT.glob("*.js") if not p.name.endswith(".test.mjs")
    )


def _blank(text: str, *, strings: bool) -> str:
    """Blank out comments (and optionally string bodies), preserving offsets.

    Every rule below is a text match, so prose has to go first: the words
    "AudioContext" and "suspended" appear all over this codebase's comments
    and log messages, and a guard that flags those is a guard someone
    deletes. Character count and newlines are preserved so line numbers in
    the failure message still point at the real source.

    Known limit: a regex literal containing ``//`` or ``/*`` can start a
    bogus comment run, which would blank real code and *under*-report. The
    bug-reintroduction cases in this module's own test run are what confirm
    the rules still fire.
    """
    out: list[str] = []
    state: str | None = None  # None | "line" | "block" | a quote character
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if state is None:
            if c == "/" and i + 1 < n and text[i + 1] == "/":
                state, i = "line", i + 2
                out.append("  ")
                continue
            if c == "/" and i + 1 < n and text[i + 1] == "*":
                state, i = "block", i + 2
                out.append("  ")
                continue
            if c in "\"'`":
                state = c
                out.append(c)
                i += 1
                continue
            out.append(c)
            i += 1
            continue
        if state == "line":
            if c == "\n":
                state = None
                out.append(c)
            else:
                out.append(" ")
            i += 1
            continue
        if state == "block":
            if c == "*" and i + 1 < n and text[i + 1] == "/":
                state, i = None, i + 2
                out.append("  ")
                continue
            out.append(c if c == "\n" else " ")
            i += 1
            continue
        # Inside a string literal; `state` holds the opening quote.
        if c == "\\" and i + 1 < n:
            out.append("  " if strings else text[i : i + 2])
            i += 2
            continue
        if c == state:
            state = None
            out.append(c)
            i += 1
            continue
        if c == "\n":  # unterminated literal — recover rather than eat the file
            state = None
            out.append(c)
            i += 1
            continue
        out.append(" " if strings else c)
        i += 1
    return "".join(out)


def _code_lines(path: Path, *, strings: bool = False) -> list[tuple[int, str]]:
    """(lineno, code) with comments — and optionally strings — blanked."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    stripped = _blank(raw, strings=strings).splitlines()
    return list(enumerate(stripped, start=1))


def _source_line(path: Path, lineno: int) -> str:
    """The real text of a line, for a readable failure message."""
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[lineno - 1].strip()


# ``var AC = window.AudioContext || window.webkitAudioContext; new AC()``
# is the shape that actually shipped, so match the *reference* to the
# constructor rather than the ``new`` — an alias has to come from
# somewhere, and this catches it wherever it is spelled.
_CONSTRUCTOR_REF = re.compile(r"\b(?:window\.)?(?:webkit)?AudioContext\b")


def test_only_audio_context_js_constructs_an_audiocontext() -> None:
    """Regression: nine private contexts blew WebKit's four-context cap.

    Borrow the page context via ``window.JamnAudio.context()`` instead.
    """
    offenders: list[str] = []
    for path in _sources():
        if path.name in CONTEXT_OWNERS:
            continue
        for lineno, line in _code_lines(path, strings=True):
            if _CONSTRUCTOR_REF.search(line):
                offenders.append(f"{path.name}:{lineno}: {_source_line(path, lineno)}")
    assert not offenders, (
        "AudioContext may only be constructed in "
        f"{sorted(CONTEXT_OWNERS)} — every other surface must borrow it via "
        "window.JamnAudio.context(). iOS Safari caps a page at four "
        "concurrent contexts and silently hands back a dead one past the "
        "cap.\n  " + "\n  ".join(offenders)
    )


# Equality against "suspended" — the guard that misses Safari's
# "interrupted". Matches ==, ===, and the property or a local alias on the
# left, in either quote style.
_SUSPENDED_EQ = re.compile(r"""===?\s*['"]suspended['"]|['"]suspended['"]\s*===?""")


def test_no_equality_guards_against_the_suspended_state() -> None:
    """Regression: Safari parks an interrupted context in "interrupted".

    ``ctx.state === "suspended"`` reads as "is audio stopped?" but answers
    a narrower question, so the context was never resumed after a call,
    Siri, a route change, or a backgrounded tab. Test ``!== "running"`` or
    call ``window.JamnAudio.unlock()``. A line that names both states is
    fine — that is the correct shape, not the bug.
    """
    offenders: list[str] = []
    for path in _sources():
        for lineno, line in _code_lines(path):
            if not _SUSPENDED_EQ.search(line):
                continue
            # Handling both states on the same line is the correct shape —
            # jam.js's playback watchdog already did this, and it is why the
            # song transport recovered from a call when the pads did not.
            if "interrupted" in line:
                continue
            if ESCAPE_HATCH in _source_line(path, lineno):
                continue
            offenders.append(f"{path.name}:{lineno}: {_source_line(path, lineno)}")
    assert not offenders, (
        'Guard on `state !== "running"` (or call window.JamnAudio.unlock()) '
        'rather than comparing against "suspended" — Safari on iOS uses a '
        'fourth state, "interrupted", which an equality guard skips, leaving '
        "the page animating in silence. If a line really does mean "
        f'"specifically suspended", mark it `// {ESCAPE_HATCH}`.\n  '
        + "\n  ".join(offenders)
    )


# Direct close() on anything named like an audio context.
_CTX_CLOSE = re.compile(r"\b(?:ctx|actx|audioContext|audioCtx|ac)\.close\s*\(")


def test_nobody_closes_a_borrowed_audio_context() -> None:
    """Regression: one surface unmounting must not silence the others.

    The context is shared now, so ``ctx.close()`` in a teardown path kills
    audio for every other surface on the page. ``JamnAudio.release(ctx)``
    closes a foreign context and declines to close the shared one.
    """
    offenders: list[str] = []
    for path in _sources():
        if path.name in CONTEXT_OWNERS:
            continue
        for lineno, line in _code_lines(path, strings=True):
            if _CTX_CLOSE.search(line):
                offenders.append(f"{path.name}:{lineno}: {_source_line(path, lineno)}")
    assert not offenders, (
        "Use window.JamnAudio.release(ctx) instead of ctx.close() — the "
        "context is shared page-wide, so closing it in one surface's "
        "teardown silences every other surface.\n  " + "\n  ".join(offenders)
    )


def _script_order(html: Path) -> list[str]:
    """Script basenames in document order (cache-busting query stripped)."""
    return [
        m.rsplit("/", 1)[-1].split("?", 1)[0]
        for m in re.findall(r"""<script[^>]*\bsrc=["']([^"']+)["']""", html.read_text())
    ]


HTML_PAGES = sorted(STATIC_ROOT.glob("*.html"))


@pytest.mark.parametrize("page", HTML_PAGES, ids=lambda p: p.name)
def test_audio_context_js_loads_before_every_audio_surface(page: Path) -> None:
    """Regression: JamnAudio must exist before the first pad press.

    Every audio surface calls ``window.JamnAudio`` at mount, so the owner
    script has to be parsed first. A page that loads none of them needs no
    audio-context.js at all.
    """
    scripts = _script_order(page)
    surfaces = [(i, name) for i, name in enumerate(scripts) if name in AUDIO_SURFACES]
    if not surfaces:
        pytest.skip(f"{page.name} loads no audio surface")

    assert "audio-context.js" in scripts, (
        f"{page.name} loads audio surfaces {[n for _, n in surfaces]} but never "
        "loads audio-context.js, which defines window.JamnAudio"
    )
    owner_at = scripts.index("audio-context.js")
    first_surface_at, first_surface = surfaces[0]
    assert owner_at < first_surface_at, (
        f"{page.name} loads {first_surface} before audio-context.js — "
        "window.JamnAudio would be undefined at mount"
    )
