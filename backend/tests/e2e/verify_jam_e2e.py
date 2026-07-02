"""End-to-end drive of the four Rehearsal v2 follow-ups.

Uses session id 5fff8bd2 (Paramore "That's What You Get", 129 BPM,
14 sections) via the /jam/<id> deep link, so this exercises the real
analysis pipeline output — no fixtures.

Verifies:
  1. Skill map auto-opens on bandroom entry when >=3 rehearsal:v1:*
     keys exist in localStorage and the sessionStorage gate is unset.
  2. Debug tag pills render on real rehearsal rows under ?debug=1.
  3. Per-section BPM chip renders with a real number in the
     difficulty-hints strip.
  4. Best-rep WAV download of a seeded blob emits a browser download
     whose suggested filename matches _bestRepFilename(0).
"""
import base64
import re
import struct
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
SESSION_ID = "5fff8bd2"


def make_wav_bytes():
    n = 8
    sr = 22050
    data = struct.pack("<" + "h" * n, 0, 4000, 8000, 4000, 0, -4000, -8000, -4000)
    bps = 2
    br = sr * bps
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, sr, br, bps, 16)
    dc = struct.pack("<4sI", b"data", len(data)) + data
    return b"RIFF" + struct.pack("<I", 4 + len(fmt) + len(dc)) + b"WAVE" + fmt + dc


def check(name, cond, detail=""):
    status = "OK  " if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  — {detail}" if detail else ""))
    return bool(cond)


def part_1_skill_map_auto_open(pw):
    """Seed 4 rehearsal:v1:* keys then deep-link so showView('bandroom')
    fires; overlay should appear."""
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context()
    ctx.grant_permissions(["microphone"])
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    # Prime the origin so localStorage is writable, then wipe + seed.
    page.goto(f"{BASE}/jam", wait_until="domcontentloaded")
    page.wait_for_function("window.__jam && window.__jam._rehearsal", timeout=15000)
    page.evaluate("""() => {
      const wipe = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith('rehearsal:v1:')) wipe.push(k);
      }
      for (const k of wipe) localStorage.removeItem(k);
      sessionStorage.removeItem('skillmap:autoopen:v1');
      for (const id of ['aaaa', 'bbbb', 'cccc', 'dddd']) {
        localStorage.setItem('rehearsal:v1:' + id,
          JSON.stringify({version: 4, mastery: {}, metadata: {analysisId: id, title: 'Seed ' + id}}));
      }
    }""")

    # Deep-link fires showView('bandroom') which triggers _maybeAutoOpenSkillMap.
    page.goto(f"{BASE}/jam/{SESSION_ID}", wait_until="domcontentloaded")
    page.wait_for_function("window.__jam && window.__jam._rehearsal", timeout=15000)

    try:
        page.wait_for_selector("#view-skill-map.is-visible", timeout=5000)
        opened, detail = True, ""
    except Exception as e:
        opened, detail = False, str(e)[:80]

    gate = page.evaluate("() => sessionStorage.getItem('skillmap:autoopen:v1')")

    browser.close()

    r = []
    r.append(check("skill map auto-opens with 4 seeded songs (deep-link → bandroom)", opened, detail))
    r.append(check("session gate flag armed after auto-open", gate == "1", f"got {gate!r}"))
    r.append(check("(1) no console errors", not errors, "; ".join(errors[:3])))
    return r


def part_234_deep_link_session(pw):
    """Fresh context (no seeded rehearsals, no gate), deep-link session,
    enter rehearsal, verify debug pills + BPM chip + WAV download."""
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context()
    ctx.grant_permissions(["microphone"])
    page = ctx.new_page()

    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    # Clear residual seed from part 1 sharing the origin.
    page.goto(f"{BASE}/jam", wait_until="domcontentloaded")
    page.evaluate("""() => {
      const wipe = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith('rehearsal:v1:')) wipe.push(k);
      }
      for (const k of wipe) localStorage.removeItem(k);
      sessionStorage.clear();
    }""")

    # Deep-link with ?debug=1 so tag pills render.
    page.goto(f"{BASE}/jam/{SESSION_ID}?debug=1", wait_until="domcontentloaded")
    page.wait_for_function("window.__jam && window.__jam._rehearsal", timeout=15000)

    # onAnalysisComplete populates state.sections / state.analysisId.
    page.wait_for_function(
        "window.__jam && window.__jam.state "
        "&& Array.isArray(window.__jam.state.sections) "
        "&& window.__jam.state.sections.length > 0 "
        "&& window.__jam.state.analysisId",
        timeout=20000,
    )

    info = page.evaluate("""() => {
      const s = window.__jam.state;
      return {
        n: s.sections.length,
        tempo: s.tempo_bpm,
        beats: (s.beatTimes || []).length,
        analysisId: s.analysisId,
      };
    }""")

    r = []
    r.append(check(
        f"deep-link loads session {SESSION_ID}",
        info["n"] >= 4 and info["analysisId"],
        f"sections={info['n']} tempo={info['tempo']} beats={info['beats']} id={info['analysisId']!r}",
    ))

    # No skill-map bait in this context, so bandroom lands directly.
    page.wait_for_selector("#bandroom-start-rehearsal", timeout=5000)
    page.click("#bandroom-start-rehearsal")
    page.wait_for_selector("#rehearsal-section-list [data-section-idx]", timeout=10000)

    # ── Part 2: debug tag pills on real rows ─────────────────
    pill_info = page.evaluate("""() => {
      const rows = document.querySelectorAll('#rehearsal-section-list [data-section-idx]');
      let rowsWithPills = 0, pillCount = 0;
      const tagIds = new Set();
      for (const row of rows) {
        const pills = row.querySelectorAll('.rehearsal-row-debug-tag');
        if (pills.length) rowsWithPills += 1;
        pillCount += pills.length;
        pills.forEach(p => tagIds.add(p.dataset.tagId));
      }
      return {rows: rows.length, rowsWithPills, pillCount, tagIds: Array.from(tagIds).sort()};
    }""")
    r.append(check(
        "debug tag pills render on real rows",
        pill_info["pillCount"] > 0,
        f"rows={pill_info['rows']} withPills={pill_info['rowsWithPills']} "
        f"total={pill_info['pillCount']} ids={pill_info['tagIds']}",
    ))
    r.append(check(
        "tag ids come from the registered set",
        all(t in {"barre", "colour", "jumps", "quick"} for t in pill_info["tagIds"]) and pill_info["tagIds"],
        f"got {pill_info['tagIds']}",
    ))

    # ── Part 3: BPM chip ───────────────────────────────────
    page.wait_for_selector('#rehearsal-section-hints [data-tag-id="bpm"]', timeout=5000)
    bpm_text = (page.text_content('#rehearsal-section-hints [data-tag-id="bpm"]') or "").strip()
    m = re.match(r"^(\d+)\s+BPM$", bpm_text)
    bpm_value = int(m.group(1)) if m else None
    r.append(check("BPM chip renders with 'NNN BPM' shape", m is not None, f"text={bpm_text!r}"))
    r.append(check(
        "BPM chip value is plausible (60..240)",
        bpm_value is not None and 60 <= bpm_value <= 240,
        f"got {bpm_value!r}",
    ))

    # ── Part 4: WAV download ───────────────────────────────
    # Pick an anchor sectionIdx (the ▶ Best rep + ⬇ WAV buttons only
    # render on anchor rows, not on variants).
    anchor_idx = page.evaluate("""() => {
      const g = window.__jam._rehearsal._rehearsalGroupsFromSections();
      const rows = (g.parts || []).concat(g.moments || []);
      if (rows.length && typeof rows[0].anchorIdx === 'number') return rows[0].anchorIdx;
      return 0;
    }""")
    wav_b64 = base64.b64encode(make_wav_bytes()).decode("ascii")

    save_ok = page.evaluate(
        """async ({b64, idx}) => {
          const bin = atob(b64);
          const u8 = new Uint8Array(bin.length);
          for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
          const blob = new Blob([u8], { type: 'audio/wav' });
          const id = window.__jam.state.analysisId;
          const ok = await window.__jam._rehearsal._saveBestRep(id, idx, blob);
          return { ok, size: blob.size, id, idx };
        }""",
        {"b64": wav_b64, "idx": anchor_idx},
    )
    r.append(check(
        "_saveBestRep persisted synthetic WAV blob",
        save_ok and save_ok.get("ok"),
        f"anchorIdx={anchor_idx} size={save_ok.get('size')} id={save_ok.get('id')!r}",
    ))

    # Click the anchor's row to force _selectRehearsalSection →
    # _buildRehearsalSectionList so the list consults the (now-populated)
    # _bestRepCache and appends both ▶ Best rep and ⬇ WAV buttons as
    # siblings of the row inside #rehearsal-section-list.
    page.click(f'#rehearsal-section-list [data-section-idx="{anchor_idx}"]')
    page.wait_for_selector(
        '#rehearsal-section-list .best-rep-download-button',
        timeout=5000,
    )

    expected_name = page.evaluate(
        "(idx) => window.__jam._rehearsal._bestRepFilename(idx)",
        anchor_idx,
    )

    with page.expect_download(timeout=10000) as dl_info:
        page.click('#rehearsal-section-list .best-rep-download-button')
    download = dl_info.value

    r.append(check(
        "WAV download emitted by the browser",
        download is not None,
        f"filename={download.suggested_filename!r}",
    ))
    r.append(check(
        "download filename matches _bestRepFilename(0)",
        download.suggested_filename == expected_name,
        f"got={download.suggested_filename!r} expected={expected_name!r}",
    ))

    r.append(check("(2/3/4) no console errors", not errors, "; ".join(errors[:3])))

    browser.close()
    return r


def main():
    results = []
    with sync_playwright() as pw:
        print("── Part 1: skill map auto-open (seeded localStorage → deep-link) ──")
        results += part_1_skill_map_auto_open(pw)
        print()
        print(f"── Parts 2/3/4: real session {SESSION_ID}, rehearsal drive ──")
        results += part_234_deep_link_session(pw)

    ok = sum(1 for r in results if r)
    total = len(results)
    print(f"\n{ok}/{total} checks passed")
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
