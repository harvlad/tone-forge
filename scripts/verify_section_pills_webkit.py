"""WebKit verification of the Fix B/C section-pill indicators.

Loads /jam/c3687f79 (Linkin Park "One Step Closer" — the reference
case for the boundary-detector under-segmentation bug), waits for the
section bar to render, and probes each section pill for:

  * The section label (pill textContent starts with the section type)
  * Whether it has the `section-pill-suspicious` class
  * Whether it has a `.section-duration-warn` child span
  * The computed border-style (should be `dashed` on suspicious pills)

Reference truth for c3687f79 AFTER Fix C boundary re-detection
+ H2 relabel (bundle-read path runs Fix B guard → Fix C resegment
→ H2 relabel → Fix B guard again, so the pill layout reflects both
the split state and the re-derived labels):

  0: intro     12.5s → no flag
  1: verse     15.0s → no flag
  2: verse     14.0s → no flag       (was part of 35s "prechorus" block)
  3: chorus    21.1s → no flag       (H2 promoted to ANCHOR)
  4: chorus    10.0s → no flag
  5: chorus    19.0s → no flag       (was part of 70s "chorus" block)
  6: chorus    14.0s → no flag       (   ''   )
  7: chorus    21.5s → no flag       (   ''   )
  8: verse     15.7s → no flag       (H2 identified as DEVELOPMENT/bridge)
  9: chorus    17.6s → no flag
 10: chorus    12.5s → no flag
 11: outro      3.7s → fragment      ← suspicious

The 70s CHORUS block that was flagged pre-Fix-C is now cleanly split
into 4 sub-sections + 1 bridge (indices 5-8), giving a musically
defensible verse2/chorus2/bridge/chorus3 shape. The 35s "prechorus"
block was actually verse2 + chorus1 tail, re-classified correctly
via H2 chord-trigram matching after the split. Only the outro
fragment retains a suspicious flag.

Exits 0 on pass. Non-zero on:
  - server unreachable
  - no section pills rendered
  - suspicious count mismatch
  - dashed border missing on flagged pills
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


JAM_URL = "http://127.0.0.1:8000/jam"
SESSION_ID = "c3687f79"
OUT_DIR = Path("/tmp/section_pills_verify")
OUT_DIR.mkdir(parents=True, exist_ok=True)

EXPECTED_SUSPICIOUS_INDICES = {11}
EXPECTED_TOTAL_PILLS = 12


def main() -> int:
    with sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()

        console_lines: list[str] = []
        page.on("console", lambda msg: console_lines.append(f"[{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: console_lines.append(f"[pageerror] {err}"))

        url = f"{JAM_URL}/{SESSION_ID}"
        print(f"[verify] loading {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # Wait for the section bar to have at least 3 pills.
        try:
            page.wait_for_function(
                """() => {
                  const pills = document.querySelectorAll('#section-bar .section-pill');
                  return pills.length >= 3;
                }""",
                timeout=45000,
            )
            print("[verify] section bar populated")
        except Exception as e:
            print(f"[verify] FAIL waiting for section pills: {e}")
            print("\n".join(console_lines[-30:]))
            (OUT_DIR / "no_pills.png").write_bytes(page.screenshot(full_page=True))
            return 2

        probe = page.evaluate(
            """() => {
              const pills = document.querySelectorAll('#section-bar .section-pill');
              const out = [];
              for (let i = 0; i < pills.length; i++) {
                const p = pills[i];
                const cs = getComputedStyle(p);
                const warn = p.querySelector('.section-duration-warn');
                out.push({
                  idx: i,
                  text: (p.textContent || '').trim().slice(0, 40),
                  suspicious: p.classList.contains('section-pill-suspicious'),
                  borderStyle: cs.borderTopStyle,
                  hasWarnGlyph: !!warn,
                  warnTitle: warn ? warn.title : '',
                });
              }
              return out;
            }"""
        )

        print("[verify] pill probe:")
        for row in probe:
            print(f"  {row}")

        (OUT_DIR / "section_bar.png").write_bytes(
            page.locator("#section-bar").screenshot()
        )
        print(f"[verify] wrote screenshot {OUT_DIR / 'section_bar.png'}")

        if len(probe) != EXPECTED_TOTAL_PILLS:
            print(
                f"[verify] FAIL — pill count {len(probe)} != "
                f"expected {EXPECTED_TOTAL_PILLS} (Fix C should split the "
                f"70s chorus + 35s prechorus blocks into 12 total)"
            )
            browser.close()
            return 6

        suspicious_idxs = {r["idx"] for r in probe if r["suspicious"]}
        if suspicious_idxs != EXPECTED_SUSPICIOUS_INDICES:
            print(
                f"[verify] FAIL — suspicious set {suspicious_idxs} != "
                f"expected {EXPECTED_SUSPICIOUS_INDICES}"
            )
            browser.close()
            return 3

        for r in probe:
            if r["suspicious"]:
                if r["borderStyle"] != "dashed":
                    print(
                        f"[verify] FAIL — pill {r['idx']} suspicious but "
                        f"border-style={r['borderStyle']!r}, expected 'dashed'"
                    )
                    browser.close()
                    return 4
                if not r["hasWarnGlyph"]:
                    print(f"[verify] FAIL — pill {r['idx']} missing warn glyph")
                    browser.close()
                    return 5

        print(
            f"[verify] PASS — {len(suspicious_idxs)} suspicious pills all have "
            f"dashed border + warn glyph"
        )
        browser.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
