"""M8 — minimal self-contained report.html.

Summary + phrase-score table (naive vs trajopt) + the demo-phrase contact sheet
(naive row over trajopt row) with PNGs embedded as base64 data URIs so the file
opens standalone (no external assets).

Run: python3 build_html.py   # writes results/report.html
"""
from __future__ import annotations
import os, sys, json, base64

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")
RENDERS = os.path.join(RES, "renders")
DEMO = "one_shift_scale"


def data_uri(path):
    if not os.path.exists(path):
        return None
    b = base64.b64encode(open(path, "rb").read()).decode()
    return f"data:image/png;base64,{b}"


def img_cell(name):
    # best-* includes the neck, so the hand's position along it is visible
    # (essential for judging the shift). Fixed phrase camera across frames.
    uri = data_uri(os.path.join(RENDERS, f"best-{name}.png"))
    if uri is None:
        return f'<td class="miss">missing<br>{name}</td>'
    return f'<td><img src="{uri}" alt="{name}"><div class="cap">{name}</div></td>'


def main():
    naive = json.load(open(os.path.join(RES, "movement_report.json")))
    trajopt = json.load(open(os.path.join(RES, "trajopt_report.json")))
    pids = list(naive["phrases"])

    rows = []
    for p in pids:
        b, t = naive["phrases"][p], trajopt["phrases"][p]
        bm, tm = b["metrics"], t["metrics"]
        rows.append(
            f"<tr><td>{p}</td>"
            f"<td>{bm['shift_count']['count']}</td><td>{tm['shift_count']['count']}</td>"
            f"<td>{bm['root_travel']['total_mm']:.0f}</td><td>{tm['root_travel']['total_mm']:.0f}</td>"
            f"<td>{bm['fingertip_travel']['total_mm']:.0f}</td><td>{tm['fingertip_travel']['total_mm']:.0f}</td>"
            f"<td>{b['score']:.3f}</td><td>{t['score']:.3f}</td></tr>")

    n_knots = len(trajopt["trajectories"][DEMO]["knots"])
    naive_imgs = "".join(img_cell(f"{DEMO}_naive_{i}") for i in range(n_knots))
    traj_imgs = "".join(img_cell(f"{DEMO}_trajopt_{i}") for i in range(n_knots))

    meta = trajopt["metadata"]
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Phrase-Motion Benchmark — naive vs trajopt</title>
<style>
 body{{font:14px system-ui,sans-serif;background:#0e0e12;color:#d8d8e0;margin:24px;max-width:1000px}}
 h1{{font-size:20px}} h2{{font-size:15px;color:#9aa;margin-top:28px}}
 table{{border-collapse:collapse;margin:8px 0}} td,th{{border:1px solid #333;padding:5px 9px;text-align:center}}
 th{{background:#1a1a22;color:#aab}} .win{{color:#7fdc8f}}
 .sheet td{{border:1px solid #222}} .sheet img{{width:150px;height:150px;object-fit:cover;background:#000}}
 .cap{{font-size:10px;color:#889}} .miss{{color:#c66}}
 .lbl{{writing-mode:vertical-lr;transform:rotate(180deg);font-weight:bold;color:#9aa}}
 code{{color:#8ab}}
</style></head><body>
<h1>Phrase-Motion Benchmark — visual gate (M8)</h1>
<p>Benchmark <code>{meta['benchmark_version']}</code> · metric <code>{meta['metric_version']}</code>
 · solver <code>{meta['solver_version']}</code> · planner <b>trajopt</b> vs <b>naive</b>.</p>
<p>Core result: whole-phrase optimization eliminates naive's unnecessary hand relocation on
 <b>{DEMO}</b> (shift 2→1). Judge below whether that reads as visibly better motion — the
 non-gameable human gate. All contacts feasible (hard gate PASS both planners).</p>

<h2>Per-phrase scores (naive → trajopt)</h2>
<table>
<tr><th>phrase</th><th>shift n</th><th>shift t</th><th>root n</th><th>root t</th>
<th>tip n</th><th>tip t</th><th>score n</th><th>score t</th></tr>
{''.join(rows)}
</table>

<h2>Demo contact sheet — {DEMO} (frets 2,4 → shift → 9,11)</h2>
<table class="sheet">
<tr><td class="lbl">naive</td>{naive_imgs}</tr>
<tr><td class="lbl">trajopt</td>{traj_imgs}</tr>
</table>
<p class="cap">Each column = one note in sequence. Watch the hand position (neck location)
 across the row: naive relocates twice, trajopt commits and relocates once.</p>
</body></html>"""
    dst = os.path.join(RES, "report.html")
    open(dst, "w").write(html)
    print(f"wrote {dst}  ({len(html)} bytes, {len(pids)} phrases, {n_knots} demo knots)")


if __name__ == "__main__":
    main()
