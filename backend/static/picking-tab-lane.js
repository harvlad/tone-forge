/*
 * picking-tab-lane.js — scrolling guitar-tab lane.
 *
 * Renders a 6-string tab where notes scroll right-to-left past a
 * fixed playhead anchored at ~30% of the lane width. Each note is
 * placed at its absolute time position; the inner <g> is translated
 * per frame so the playhead lines up with the current playback
 * time. This trades a one-time build for cheap per-frame updates
 * (one SVG transform mutation).
 *
 * Replaces the static renderLeadTabSVG path (chord_diagrams.js:500)
 * for sections where users want a moving "next-pluck" anticipation
 * window rather than a wall of fret numbers spread across the full
 * section.
 *
 * Public API:
 *   createTabLane(host, opts)
 *     -> {
 *          svg,              // attached to host as the only child
 *          update(t),        // per-frame: align playhead to time t
 *          setNotes(notes),
 *          setGlyph(g),      // "dot" | "fret" | "note"
 *          setLookahead(s),  // seconds shown to the right of playhead
 *          dispose()
 *        }
 *
 * Scope cuts (prototype):
 *   * String/fret assignment is single-position heuristic
 *     (prefers fret 5 vicinity in standard tuning). No fingering
 *     re-inference per chord. Acceptable for visualisation; would
 *     need refinement for an actual practice tool.
 *   * Whole-song notes are placed up front; we rely on the SVG clip
 *     to hide off-screen glyphs. Songs with thousands of notes will
 *     still build all glyphs once — that's fine for typical
 *     guitar-stem MIDI (<2000 notes per song).
 */

import { STANDARD_TUNING } from "/static/chord_diagrams.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

/**
 * Map a MIDI pitch to a (string, fret) on standard guitar tuning.
 * Returns null if the pitch is below low-E or above the 17th fret
 * on every string.
 *
 * Bias: pick the string whose resulting fret is closest to fret 5
 * (typical mid-neck position). Keeps glyphs spread across the
 * string rows for a picked sequence rather than clustering at the
 * top or bottom row.
 */
function pitchToStringFret(pitch) {
  let best = null;
  let bestScore = Infinity;
  for (let s = 0; s < 6; s++) {
    const open = STANDARD_TUNING[s];
    const fret = pitch - open;
    if (fret < 0 || fret > 17) continue;
    const score = Math.abs(fret - 5);
    if (score < bestScore) {
      bestScore = score;
      best = { string: s, fret };
    }
  }
  return best;
}

function pitchToName(pitch) {
  return NOTE_NAMES[((pitch % 12) + 12) % 12];
}

export function createTabLane(host, opts = {}) {
  if (!host) throw new Error("createTabLane: host is required");

  const width = opts.width || 640;
  const height = opts.height || 110;
  const padLeft = opts.padLeft != null ? opts.padLeft : 36;
  const padRight = opts.padRight != null ? opts.padRight : 12;
  const padTop = opts.padTop != null ? opts.padTop : 14;
  const padBottom = opts.padBottom != null ? opts.padBottom : 14;
  const usableWidth = width - padLeft - padRight;

  // Playhead at 30% of usable area → ~70% of horizontal space is
  // lookahead, ~30% is "just passed". Anticipation-biased.
  const playheadFracX = opts.playheadFracX != null ? opts.playheadFracX : 0.3;
  const playheadX = padLeft + playheadFracX * usableWidth;

  let lookaheadS = opts.lookaheadS || 2.0;
  let glyph = opts.glyph || "dot";
  let notes = Array.isArray(opts.notes) ? opts.notes : [];

  // Px per second is computed from lookaheadS over the lookahead
  // portion of the usable area. setLookahead() updates this by
  // re-running rebuildNotes().
  function pxPerSec() {
    return ((1 - playheadFracX) * usableWidth) / lookaheadS;
  }

  const stringTop = padTop;
  const stringBottom = height - padBottom;
  const stringSpacing = (stringBottom - stringTop) / 5;
  // Tab convention: high-E on top, low-E on bottom. Visual row
  // index = 5 - stringIdx (string 0 = low E).
  function stringYForRow(stringIdx) {
    const visualRow = 5 - stringIdx;
    return stringTop + visualRow * stringSpacing;
  }

  // ─── Build SVG ────────────────────────────────────────────────
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.setAttribute("class", "picking-tab-lane");

  // <defs> with a clip-path so notes scrolling off the left don't
  // bleed into the string-label gutter. Unique id per instance so
  // multiple lanes can coexist on the same page (Phase 2 multi-stem).
  const clipId = `tab-clip-${Math.random().toString(36).slice(2, 9)}`;
  const defs = document.createElementNS(SVG_NS, "defs");
  const clipPath = document.createElementNS(SVG_NS, "clipPath");
  clipPath.setAttribute("id", clipId);
  const clipRect = document.createElementNS(SVG_NS, "rect");
  clipRect.setAttribute("x", String(padLeft));
  clipRect.setAttribute("y", "0");
  clipRect.setAttribute("width", String(width - padLeft - padRight));
  clipRect.setAttribute("height", String(height));
  clipPath.appendChild(clipRect);
  defs.appendChild(clipPath);
  svg.appendChild(defs);

  // Background group: 6 string lines + string letter labels +
  // playhead vertical line. Doesn't transform with the notes.
  const bgGroup = document.createElementNS(SVG_NS, "g");
  bgGroup.setAttribute("class", "tab-bg");
  // String labels match tab convention (top-to-bottom): E B G D A E.
  const stringNames = ["E", "B", "G", "D", "A", "E"];
  for (let visualRow = 0; visualRow < 6; visualRow++) {
    const y = stringTop + visualRow * stringSpacing;
    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", String(padLeft));
    line.setAttribute("y1", String(y));
    line.setAttribute("x2", String(width - padRight));
    line.setAttribute("y2", String(y));
    line.setAttribute("class", "tab-string");
    bgGroup.appendChild(line);

    const lbl = document.createElementNS(SVG_NS, "text");
    lbl.setAttribute("x", String(padLeft - 6));
    lbl.setAttribute("y", String(y + 3));
    lbl.setAttribute("text-anchor", "end");
    lbl.setAttribute("class", "tab-string-label");
    lbl.textContent = stringNames[visualRow];
    bgGroup.appendChild(lbl);
  }
  // Playhead.
  const playLine = document.createElementNS(SVG_NS, "line");
  playLine.setAttribute("x1", String(playheadX));
  playLine.setAttribute("y1", String(stringTop - 4));
  playLine.setAttribute("x2", String(playheadX));
  playLine.setAttribute("y2", String(stringBottom + 4));
  playLine.setAttribute("class", "tab-playhead");
  bgGroup.appendChild(playLine);
  svg.appendChild(bgGroup);

  // Notes group — gets translated per frame. Clipped so notes
  // exiting on the left disappear at the label gutter rather than
  // overdrawing the string-letter column.
  const notesGroup = document.createElementNS(SVG_NS, "g");
  notesGroup.setAttribute("class", "tab-notes");
  notesGroup.setAttribute("clip-path", `url(#${clipId})`);
  svg.appendChild(notesGroup);

  // ─── Note glyph build (one-shot per data/glyph/lookahead change) ─
  function rebuildNotes() {
    while (notesGroup.firstChild) notesGroup.removeChild(notesGroup.firstChild);
    const pps = pxPerSec();
    for (const n of notes) {
      const pitch = typeof n.pitch === "number" ? n.pitch : null;
      if (pitch === null) continue;
      const sf = pitchToStringFret(pitch);
      if (!sf) continue;
      const ns =
        typeof n.start === "number"
          ? n.start
          : typeof n.start_s === "number"
            ? n.start_s
            : null;
      if (ns === null) continue;

      // Absolute X: ns × pps. Per-frame transform shifts the group
      // so the playhead lands at currentT.
      const x = ns * pps;
      const yLine = stringYForRow(sf.string);

      let glyphEl;
      if (glyph === "fret") {
        glyphEl = document.createElementNS(SVG_NS, "text");
        glyphEl.setAttribute("text-anchor", "middle");
        glyphEl.setAttribute("class", "tab-note-fret");
        glyphEl.setAttribute("x", String(x));
        glyphEl.setAttribute("y", String(yLine + 3.5));
        glyphEl.textContent = String(sf.fret);
      } else if (glyph === "note") {
        glyphEl = document.createElementNS(SVG_NS, "text");
        glyphEl.setAttribute("text-anchor", "middle");
        glyphEl.setAttribute("class", "tab-note-name");
        glyphEl.setAttribute("x", String(x));
        glyphEl.setAttribute("y", String(yLine + 3.5));
        glyphEl.textContent = pitchToName(pitch);
      } else {
        // Default: filled dot.
        glyphEl = document.createElementNS(SVG_NS, "circle");
        glyphEl.setAttribute("class", "tab-note-dot");
        glyphEl.setAttribute("cx", String(x));
        glyphEl.setAttribute("cy", String(yLine));
        glyphEl.setAttribute("r", "4");
      }
      notesGroup.appendChild(glyphEl);
    }
  }

  rebuildNotes();
  host.appendChild(svg);

  // ─── Per-frame update ─────────────────────────────────────────
  function update(currentT) {
    const pps = pxPerSec();
    // Want: glyph at absolute time = currentT lands at playheadX.
    // visible_x = absolute_x + tx
    // playheadX = currentT * pps + tx  →  tx = playheadX - currentT * pps
    const tx = playheadX - currentT * pps;
    notesGroup.setAttribute("transform", `translate(${tx} 0)`);
  }

  function setNotes(newNotes) {
    notes = Array.isArray(newNotes) ? newNotes : [];
    rebuildNotes();
  }
  function setGlyph(newGlyph) {
    if (newGlyph !== "dot" && newGlyph !== "fret" && newGlyph !== "note") return;
    glyph = newGlyph;
    rebuildNotes();
  }
  function setLookahead(s) {
    const v = Number(s);
    if (!isFinite(v) || v <= 0) return;
    lookaheadS = v;
    rebuildNotes();
  }
  function dispose() {
    if (svg.parentNode) {
      try {
        svg.parentNode.removeChild(svg);
      } catch (_) {
        /* already gone */
      }
    }
  }

  return { svg, update, setNotes, setGlyph, setLookahead, dispose };
}
