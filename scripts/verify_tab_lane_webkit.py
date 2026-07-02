"""WebKit verification of the picking-tab-lane Safari clip-path fix.

Loads /jam?session=<sid>, switches the chord-now view to "tab", clicks Play,
waits a few seconds, then probes the live DOM:

  * computed `clip-path` on `.tab-notes` must resolve to `none` (the v=27 CSS
    rule is what unbreaks Safari; if computed-style still shows the
    `url(#...)` clip we know the override didn't take effect).
  * count how many `.tab-notes` children whose post-transform x lands inside
    the SVG viewBox window `[36, 628]`. Pre-fix in Safari this was 0; post-fix
    it should be a handful (lookahead + already-visible notes).
  * screenshot the picking-tab-lane region.

Exits non-zero on any of:
  - server not reachable
  - jam page never produced a `.tab-notes` element
  - `.tab-notes` has 0 children after Play
  - 0 children fall inside viewBox window
  - computed clip-path is still a `url(...)` clip
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


JAM_URL = "http://127.0.0.1:8000/jam"
SESSION_ID = "c3687f79"  # Linkin Park, has 242 user_midi notes (deep-link path)
OUT_DIR = Path("/tmp/tab_lane_verify")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> int:
    with sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()

        console_lines: list[str] = []
        page.on("console", lambda msg: console_lines.append(f"[{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: console_lines.append(f"[pageerror] {err}"))

        url = f"{JAM_URL}/{SESSION_ID}"  # deep-link path
        print(f"[verify] loading {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # Wait for the chord guidance UI to actually mount (the toggle
        # element exists in the HTML template but is initially hidden;
        # it becomes visible once the chord-guidance lane renders).
        try:
            page.wait_for_function(
                """() => {
                  const el = document.getElementById('chord-now-view-toggle');
                  if (!el) return false;
                  const cs = getComputedStyle(el);
                  return cs.visibility !== 'hidden' && cs.display !== 'none' && el.offsetParent !== null;
                }""",
                timeout=45000,
            )
            print("[verify] chord-now-view-toggle visible")
        except Exception as e:
            print(f"[verify] FAIL waiting for view toggle: {e}")
            print("\n".join(console_lines[-50:]))
            (OUT_DIR / "no_toggle.png").write_bytes(page.screenshot(full_page=True))
            return 2

        # Give the analysis-complete handler a moment to fully populate
        # state.r / leadMidiNotes before we trigger the lazy lane build.
        page.wait_for_timeout(1500)

        # Click the "Tab" button to mount the picking-tab-lane SVG.
        page.locator('#chord-now-view-toggle .cv-toggle-btn[data-view="tab"]').click()
        print("[verify] clicked Tab toggle")
        page.wait_for_timeout(2000)
        post_click = page.evaluate(
            """() => {
              const host = document.getElementById('chord-now-tab-lane');
              const svg = host && host.querySelector('.picking-tab-lane');
              const notes = host && host.querySelector('.tab-notes');
              const toggleRoot = document.getElementById('chord-now-view-toggle');
              const tabBtn = toggleRoot && toggleRoot.querySelector('.cv-toggle-btn[data-view="tab"]');
              return {
                hostHidden: host ? host.hidden : 'no-host',
                hostDisplay: host ? getComputedStyle(host).display : 'no-host',
                svgPresent: !!svg,
                notesPresent: !!notes,
                notesKids: notes ? notes.childNodes.length : -1,
                tabBtnActive: tabBtn ? tabBtn.classList.contains('is-active') : 'no-btn',
                toggleBound: toggleRoot ? toggleRoot.dataset.bound : 'no-root',
              };
            }"""
        )
        print(f"[verify] post-click state of chord-now-tab-lane: {post_click}")

        try:
            page.wait_for_selector(".picking-tab-lane", timeout=10000)
        except Exception as e:
            print(f"[verify] FAIL waiting for .picking-tab-lane SVG: {e}")
            print("\n".join(console_lines[-50:]))
            (OUT_DIR / "no_svg.png").write_bytes(page.screenshot(full_page=True))
            return 3

        # Pick the 'fret' glyph so we exercise the SVG <text> path (the
        # original bug was reported most visibly on text glyphs).
        try:
            page.locator('.cv-style-btn[data-glyph="fret"]').click(timeout=3000)
            print("[verify] selected fret glyph")
        except Exception:
            print("[verify] note: fret style button not present, continuing with default dot")

        # Verify CSS clip-path override took effect.
        clip_path = page.eval_on_selector(
            "#chord-now-tab-lane .tab-notes",
            "el => getComputedStyle(el).clipPath",
        )
        print(f"[verify] computed clipPath on .tab-notes = {clip_path!r}")

        # Click Play to start the audio clock (WebKit headless allows
        # AudioContext.resume() after a user gesture; .click() counts).
        try:
            page.locator("#t-play").click(timeout=5000)
            print("[verify] clicked Play")
        except Exception as e:
            print(f"[verify] FAIL clicking Play: {e}")
            (OUT_DIR / "play_fail.png").write_bytes(page.screenshot(full_page=True))
            return 4

        # Let the playhead advance a few seconds so the notesGroup transform
        # has a non-trivial tx (this is where the Safari clip-path quirk
        # manifested — at tx≈0 the clip wouldn't be visibly broken yet).
        time.sleep(4.0)

        # Pre-probe: dump state info.
        meta = page.evaluate(
            """() => {
              const allLanes = document.querySelectorAll('.picking-tab-lane');
              const out = [];
              for (const svg of allLanes) {
                const notes = svg.querySelector('.tab-notes');
                out.push({
                  host: svg.parentElement && svg.parentElement.id,
                  hidden: svg.parentElement && svg.parentElement.hidden,
                  display: getComputedStyle(svg).display,
                  notesKids: notes ? notes.childNodes.length : -1,
                  transform: notes ? notes.getAttribute('transform') : null,
                  clipPath: notes ? getComputedStyle(notes).clipPath : null,
                });
              }
              return out;
            }"""
        )
        print(f"[verify] all .picking-tab-lane instances: {meta}")

        # Probe live DOM. Target #chord-now-tab-lane — this is the toggle-view
        # lane we put in "fret" glyph mode (SVG <text>), the same code path the
        # original report flagged as invisible in Safari due to the clip-path
        # quirk on text elements.
        probe = page.evaluate(
            """() => {
              const svg = document.querySelector('#chord-now-tab-lane .picking-tab-lane');
              const notes = document.querySelector('#chord-now-tab-lane .tab-notes');
              if (!svg || !notes) return { ok: false, reason: 'no svg or .tab-notes' };
              const vb = svg.getAttribute('viewBox') || '';
              const vbParts = vb.split(/\\s+/).map(Number);
              const vbW = vbParts[2] || 0;
              const tr = notes.getAttribute('transform') || '';
              const m = tr.match(/translate\\(([-\\d.]+)/);
              const tx = m ? parseFloat(m[1]) : 0;
              const cs = getComputedStyle(notes);
              const kids = notes.childNodes;
              let total = kids.length;
              let inWindow = 0;
              const PAD_LEFT = 36;
              const samples = [];
              for (let i = 0; i < kids.length; i++) {
                const k = kids[i];
                const xRaw = k.getAttribute && (k.getAttribute('x') || k.getAttribute('cx'));
                if (xRaw == null) continue;
                const xv = parseFloat(xRaw) + tx;
                if (xv >= PAD_LEFT && xv <= vbW) {
                  inWindow++;
                  if (samples.length < 5) {
                    samples.push({
                      idx: i,
                      tag: k.tagName,
                      cls: k.getAttribute('class'),
                      xLocal: parseFloat(xRaw),
                      xViewport: +xv.toFixed(1),
                      text: (k.textContent || '').slice(0, 8),
                    });
                  }
                }
              }
              return {
                ok: true,
                viewBox: vb,
                tx,
                total,
                inWindow,
                clipPath: cs.clipPath,
                visibility: cs.visibility,
                display: cs.display,
                opacity: cs.opacity,
                samples,
              };
            }"""
        )
        print(f"[verify] probe = {probe}")

        # Screenshot the tab lane region.
        shot_path = OUT_DIR / "tab_lane_after_play.png"
        try:
            page.locator("#chord-now-tab-lane .picking-tab-lane").screenshot(path=str(shot_path))
            print(f"[verify] wrote screenshot {shot_path}")
        except Exception as e:
            print(f"[verify] screenshot failed: {e}")

        # Verdict.
        ok = (
            probe.get("ok")
            and probe.get("total", 0) > 0
            and probe.get("inWindow", 0) > 0
            and (probe.get("clipPath") in (None, "none", "") or "url(" not in (probe.get("clipPath") or ""))
        )

        if not ok:
            print("[verify] FAIL — fix did not produce visible glyphs in viewport window")
            print("\n[verify] last console lines:")
            print("\n".join(console_lines[-30:]))
            browser.close()
            return 5

        print(f"[verify] PASS — {probe['inWindow']} of {probe['total']} note glyphs land in viewBox window after 4s play")
        browser.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
