"""Smoke-test /debug loads cleanly and exposes the new tag surface."""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"

def main():
    errors = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        page = b.new_context().new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(f"[console.{m.type}] {m.text}") if m.type == "error" else None)
        page.goto(f"{BASE}/debug", wait_until="domcontentloaded")

        # Wait for the tab bar to render — proves debug.js parsed.
        page.wait_for_selector("#debug-tabs button.active", timeout=10000)

        checks = []
        def add(name, cond, detail=""):
            checks.append((name, cond, detail))
            print(f"[{'OK  ' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))

        # The tag-filter placeholder must exist (added in this commit).
        add("#tag-filter div exists",
            page.locator("#tag-filter").count() == 1)

        # Sanity: the CSS variables we defined should resolve on :root.
        for tok in ["--tag-barre", "--tag-colour", "--tag-jumps", "--tag-quick"]:
            v = page.evaluate(f"() => getComputedStyle(document.documentElement).getPropertyValue('{tok}').trim()")
            add(f"CSS token {tok} defined", bool(v), f"value={v!r}")

        add("no uncaught page errors", not errors, "; ".join(errors[:3]))

        b.close()

    ok = sum(1 for _, c, _ in checks if c)
    total = len(checks)
    print(f"\n{ok}/{total} checks passed")
    sys.exit(0 if ok == total else 1)

if __name__ == "__main__":
    main()
