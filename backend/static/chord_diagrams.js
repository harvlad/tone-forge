/**
 * Chord diagram + lead-tab renderer for the Jam UI.
 *
 * Exports:
 *   loadChordShapes()                 — fetches + caches chord_shapes.json
 *   normalizeSymbol(symbol)           — "Am" -> { root: "A", quality: "min" }
 *   lookupShape(symbol, registry)     — curated → algorithmic → null
 *   generateAlgorithmicShape(symbol)  — movable barre voicing fallback
 *   midiToFret(pitch, tuning?)        — MIDI int -> { string, fret }
 *   renderChordDiagramSVG(symbol, shape, opts?)  — DOM: <svg> for queue slot
 *   renderLeadTabSVG(notes, t0, t1, opts?)        — DOM: <svg> for tab lane
 *
 * Pure functions (normalizeSymbol, lookupShape, generateAlgorithmicShape,
 * midiToFret) are testable in node without a DOM. The renderers use
 * document.createElementNS and are exercised at runtime in the browser.
 *
 * Module format: ES module. jam.js loads it via dynamic `import()` so
 * the existing non-module script loading isn't disturbed.
 */

// String indexing: frets[0] = low E (lowest pitch), frets[5] = high E.
export const STANDARD_TUNING = [40, 45, 50, 55, 59, 64]; // E2 A2 D3 G3 B3 E4

const ROOT_PC = {
  C: 0, "C#": 1, Db: 1, D: 2, "D#": 3, Eb: 3,
  E: 4, F: 5, "F#": 6, Gb: 6, G: 7, "G#": 8,
  Ab: 8, A: 9, "A#": 10, Bb: 10, B: 11,
};

// Canonical ascending-sharp names indexed by pitch class. Used for
// algorithmic-shape labelling so the rendered diagram's title matches
// what the chord detector emits (which uses sharps not flats).
const PC_NAME = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

// Suffix → canonical quality. Matches keys in chord_shapes.json.
// Order matters: "maj7" must be checked before "maj" / "m" / "7".
const QUALITY_SUFFIXES = [
  ["maj7", "maj7"],
  ["m7", "m7"],
  ["dim7", "dim"], // collapse to dim — close enough voicing
  ["dim", "dim"],
  ["aug", "aug"],
  ["sus2", "sus2"],
  ["sus4", "sus4"],
  ["maj", "maj"],
  ["min", "min"],
  ["m", "min"],
  ["5", "5"],
  ["7", "7"],
  ["", "maj"], // bare symbol like "C" or "F#" → major
];

const QUALITY_INTERVALS = {
  maj:  [0, 4, 7],
  min:  [0, 3, 7],
  "5":  [0, 7],
  "7":  [0, 4, 7, 10],
  m7:   [0, 3, 7, 10],
  maj7: [0, 4, 7, 11],
  sus2: [0, 2, 7],
  sus4: [0, 5, 7],
  dim:  [0, 3, 6],
  aug:  [0, 4, 8],
};

// Movable-barre voicing templates: fret offsets relative to the
// barre/root fret. -1 = muted. Index = string (0..5, low E..high E).
const E_SHAPE_PATTERNS = {
  maj:  [0, 2, 2, 1, 0, 0],
  min:  [0, 2, 2, 0, 0, 0],
  "5":  [0, 2, 2, -1, -1, -1],
  "7":  [0, 2, 0, 1, 0, 0],
  m7:   [0, 2, 0, 0, 0, 0],
  maj7: [0, 2, 1, 1, 0, 0],
  sus2: [0, 2, 4, -1, 0, 0],
  sus4: [0, 2, 2, 2, 0, 0],
  dim:  [0, 1, 2, 0, -1, -1],
  aug:  [0, 3, 2, 1, 1, 0],
};

const A_SHAPE_PATTERNS = {
  maj:  [-1, 0, 2, 2, 2, 0],
  min:  [-1, 0, 2, 2, 1, 0],
  "5":  [-1, 0, 2, 2, -1, -1],
  "7":  [-1, 0, 2, 0, 2, 0],
  m7:   [-1, 0, 2, 0, 1, 0],
  maj7: [-1, 0, 2, 1, 2, 0],
  sus2: [-1, 0, 2, 2, 0, 0],
  sus4: [-1, 0, 2, 2, 3, 0],
  dim:  [-1, 0, 1, 2, 1, -1],
  aug:  [-1, 0, 3, 2, 2, 1],
};


// ---------------------------------------------------------------------
// Symbol parsing
// ---------------------------------------------------------------------

/**
 * Parse a chord symbol like "C", "Am", "G7", "F#5", "Cmaj7" into
 * { root, rootPc, quality }. Returns null for unrecognised input.
 */
export function normalizeSymbol(symbol) {
  if (typeof symbol !== "string" || !symbol) return null;

  // Longest-match the root: try two chars first (sharp/flat), then one.
  let root = null;
  if (symbol.length >= 2 && (symbol[1] === "#" || symbol[1] === "b")) {
    const candidate = symbol.slice(0, 2);
    if (candidate in ROOT_PC) root = candidate;
  }
  if (root === null) {
    const candidate = symbol[0];
    if (candidate in ROOT_PC) root = candidate;
  }
  if (root === null) return null;

  const suffix = symbol.slice(root.length);
  let quality = null;
  for (const [sfx, q] of QUALITY_SUFFIXES) {
    if (suffix === sfx) {
      quality = q;
      break;
    }
  }
  if (quality === null) return null;

  return { root, rootPc: ROOT_PC[root], quality };
}


// ---------------------------------------------------------------------
// Curated lookup + algorithmic fallback
// ---------------------------------------------------------------------

/**
 * Lookup a chord shape by symbol. Tries the curated registry first,
 * falls back to the algorithmic generator. Returns null when neither
 * yields a valid voicing.
 */
export function lookupShape(symbol, registry) {
  const parsed = normalizeSymbol(symbol);
  if (parsed === null) return null;

  if (registry && registry.shapes) {
    // Try the parsed root first (handles canonical sharps from the
    // detector), then a flat alternative if applicable.
    const key = `${parsed.root}:${parsed.quality}`;
    if (key in registry.shapes) return registry.shapes[key];

    const sharpName = PC_NAME[parsed.rootPc];
    const altKey = `${sharpName}:${parsed.quality}`;
    if (altKey !== key && altKey in registry.shapes) {
      return registry.shapes[altKey];
    }
  }

  return generateAlgorithmicShape(symbol);
}

/**
 * Synthesize a movable-barre voicing for any supported quality.
 * Tries the E-shape (root on string 0) and A-shape (root on string 1)
 * and picks the one with the smaller max fret. Returns null when the
 * quality isn't supported.
 */
export function generateAlgorithmicShape(symbol) {
  const parsed = normalizeSymbol(symbol);
  if (parsed === null) return null;

  const eShape = buildBarreShape(E_SHAPE_PATTERNS[parsed.quality], parsed.rootPc, 0);
  const aShape = buildBarreShape(A_SHAPE_PATTERNS[parsed.quality], parsed.rootPc, 1);

  const candidates = [eShape, aShape].filter((s) => s !== null);
  if (candidates.length === 0) return null;

  // The voicing picker (jam.js) consumes `candidates` ordering via
  // listVoicings(); preserve the original order before sorting so we
  // can label "E-shape barre" vs "A-shape barre" by index. Sort below
  // is for the single-shape generateAlgorithmicShape contract.

  // Prefer the voicing with the smaller highest fret; tie-break on
  // lower lowest fret (more open-position).
  candidates.sort((a, b) => {
    const aMax = Math.max(...a.frets.filter((f) => f >= 0));
    const bMax = Math.max(...b.frets.filter((f) => f >= 0));
    if (aMax !== bMax) return aMax - bMax;
    const aMin = Math.min(...a.frets.filter((f) => f >= 0));
    const bMin = Math.min(...b.frets.filter((f) => f >= 0));
    return aMin - bMin;
  });

  return candidates[0];
}

/**
 * Enumerate available voicings for a chord symbol. Returns an ordered
 * list of `{name, shape}` objects suitable for a UI picker:
 *
 *   • Curated open position (when present in the registry)
 *   • E-shape barre (algorithmic, root on low E)
 *   • A-shape barre (algorithmic, root on A)
 *
 * The list is de-duplicated by fret pattern so a curated entry that
 * happens to equal one of the algorithmic shapes doesn't appear twice.
 * Returns an empty array when no voicing is producible.
 */
export function listVoicings(symbol, registry) {
  const parsed = normalizeSymbol(symbol);
  if (parsed === null) return [];

  const out = [];
  const seen = new Set();
  const push = (name, shape) => {
    if (!shape || !Array.isArray(shape.frets)) return;
    const sig = shape.frets.join(",");
    if (seen.has(sig)) return;
    seen.add(sig);
    out.push({ name, shape });
  };

  // 1. Curated open / canonical (whatever the registry has).
  if (registry && registry.shapes) {
    const key = `${parsed.root}:${parsed.quality}`;
    const sharpKey = `${PC_NAME[parsed.rootPc]}:${parsed.quality}`;
    const curated = registry.shapes[key] || registry.shapes[sharpKey];
    if (curated) push("Open / canonical", curated);
  }

  // 2. & 3. Algorithmic E-shape + A-shape barres.
  const eShape = buildBarreShape(E_SHAPE_PATTERNS[parsed.quality], parsed.rootPc, 0);
  const aShape = buildBarreShape(A_SHAPE_PATTERNS[parsed.quality], parsed.rootPc, 1);
  push("E-shape barre", eShape);
  push("A-shape barre", aShape);

  return out;
}

function buildBarreShape(pattern, rootPc, rootStringIdx) {
  if (!pattern) return null;
  const openPc = STANDARD_TUNING[rootStringIdx] % 12;
  let barreFret = (rootPc - openPc + 12) % 12;

  // If barre fret would be 0 and the pattern's root entry is already
  // at fret 0, the open voicing IS the canonical one. Otherwise add
  // the barre fret to every non-muted entry.
  const frets = pattern.map((f) => (f === -1 ? -1 : f + barreFret));

  // Validate: every fret must be in 0..15 (playable hand range).
  for (const f of frets) {
    if (f !== -1 && (f < 0 || f > 15)) return null;
  }

  return { frets, fingers: null, barre: barreFret > 0 ? {
    fret: barreFret,
    from_string: rootStringIdx,
    to_string: 5,
  } : null };
}


// ---------------------------------------------------------------------
// MIDI → fret mapping
// ---------------------------------------------------------------------

/**
 * Map a MIDI pitch to its (string, fret) position on the given tuning.
 * Returns the assignment with the smallest fret across all candidate
 * strings, or null if the pitch is out of range. Smallest-fret matches
 * the convention guitar tab readers expect: notes sit at the lowest
 * playable position.
 */
export function midiToFret(pitch, tuning = STANDARD_TUNING) {
  let best = null;
  for (let s = 0; s < tuning.length; s++) {
    const fret = pitch - tuning[s];
    if (fret < 0 || fret > 22) continue;
    if (best === null || fret < best.fret) {
      best = { string: s, fret };
    }
  }
  return best;
}


// ---------------------------------------------------------------------
// Registry loading (browser only)
// ---------------------------------------------------------------------

let _cachedRegistry = null;
let _cachedRegistryPromise = null;

/**
 * Fetch the chord_shapes.json registry once and cache it. Subsequent
 * calls return the cached value. Pass an explicit URL to override the
 * default `/static/chord_shapes.json`.
 */
export function loadChordShapes(url = "/static/chord_shapes.json") {
  if (_cachedRegistry !== null) return Promise.resolve(_cachedRegistry);
  if (_cachedRegistryPromise !== null) return _cachedRegistryPromise;
  _cachedRegistryPromise = fetch(url)
    .then((res) => {
      if (!res.ok) throw new Error(`chord_shapes.json HTTP ${res.status}`);
      return res.json();
    })
    .then((reg) => {
      _cachedRegistry = reg;
      return reg;
    })
    .catch((err) => {
      _cachedRegistryPromise = null;
      throw err;
    });
  return _cachedRegistryPromise;
}


// ---------------------------------------------------------------------
// SVG rendering (browser only)
// ---------------------------------------------------------------------

const SVG_NS = "http://www.w3.org/2000/svg";

/**
 * Build an SVG chord diagram. 90×110 viewport, 6 vertical strings,
 * 5 frets visible. Uses CSS custom properties for theming so the
 * diagram inherits the Jam UI palette.
 *
 * opts.highlighted (bool) toggles the .is-active class on the wrapper
 *   so CSS can apply the accent border / drop-shadow.
 * opts.hideTitle (bool) skips the in-SVG chord-symbol title (used in
 *   the NOW PLAYING / NEXT UP layout where the symbol is rendered as
 *   sibling HTML text at a larger size). When true, the viewBox is
 *   trimmed so the diagram fills its container without the title gap.
 */
export function renderChordDiagramSVG(symbol, shape, opts = {}) {
  const svg = document.createElementNS(SVG_NS, "svg");
  const hideTitle = !!opts.hideTitle;
  // Trim the top 18px reserved for the title text when hideTitle is set
  // (viewBox y-offset by 18). Strings/frets/dots stay in their existing
  // coordinates; the viewBox crops the empty title row out.
  svg.setAttribute(
    "viewBox",
    hideTitle ? "0 18 90 92" : "0 0 90 110",
  );
  svg.setAttribute("class", "chord-diagram-svg");
  if (opts.highlighted) svg.classList.add("is-active");

  if (!hideTitle) {
    const title = document.createElementNS(SVG_NS, "text");
    title.setAttribute("x", "45");
    title.setAttribute("y", "14");
    title.setAttribute("text-anchor", "middle");
    title.setAttribute("class", "chord-diagram-title");
    title.textContent = symbol;
    svg.appendChild(title);
  }

  if (!shape) {
    // No voicing — render symbol only as a graceful fallback.
    const placeholder = document.createElementNS(SVG_NS, "text");
    placeholder.setAttribute("x", "45");
    placeholder.setAttribute("y", "65");
    placeholder.setAttribute("text-anchor", "middle");
    placeholder.setAttribute("class", "chord-diagram-placeholder");
    placeholder.textContent = "—";
    svg.appendChild(placeholder);
    return svg;
  }

  // Determine the visible fret window: if any pressed fret > 4, shift
  // the window so the lowest pressed fret sits near the top.
  const pressed = shape.frets.filter((f) => f > 0);
  const minPressed = pressed.length ? Math.min(...pressed) : 0;
  const startFret = minPressed > 4 ? minPressed : 0;
  const FRETS_VISIBLE = 5;

  // Layout
  const gridLeft = 16;
  const gridTop = 28;
  const gridWidth = 60;
  const gridHeight = 60;
  const stringSpacing = gridWidth / 5;
  const fretSpacing = gridHeight / FRETS_VISIBLE;

  // Strings (vertical lines, low E on left → high E on right)
  for (let s = 0; s < 6; s++) {
    const x = gridLeft + s * stringSpacing;
    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", x);
    line.setAttribute("y1", gridTop);
    line.setAttribute("x2", x);
    line.setAttribute("y2", gridTop + gridHeight);
    line.setAttribute("class", "chord-diagram-string");
    svg.appendChild(line);
  }

  // Frets (horizontal lines). The top fret line is thicker when the
  // window starts at fret 0 (nut indicator).
  for (let f = 0; f <= FRETS_VISIBLE; f++) {
    const y = gridTop + f * fretSpacing;
    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", gridLeft);
    line.setAttribute("y1", y);
    line.setAttribute("x2", gridLeft + gridWidth);
    line.setAttribute("y2", y);
    line.setAttribute("class",
      (f === 0 && startFret === 0) ? "chord-diagram-nut" : "chord-diagram-fret");
    svg.appendChild(line);
  }

  // Starting-fret label when the window isn't at the nut.
  if (startFret > 0) {
    const fretLabel = document.createElementNS(SVG_NS, "text");
    fretLabel.setAttribute("x", String(gridLeft + gridWidth + 4));
    fretLabel.setAttribute("y", String(gridTop + fretSpacing * 0.7));
    fretLabel.setAttribute("class", "chord-diagram-fret-label");
    fretLabel.textContent = `${startFret}fr`;
    svg.appendChild(fretLabel);
  }

  // Barre indicator
  if (shape.barre) {
    const barreY = gridTop + (shape.barre.fret - startFret - 0.5) * fretSpacing;
    const barreX1 = gridLeft + shape.barre.from_string * stringSpacing;
    const barreX2 = gridLeft + shape.barre.to_string * stringSpacing;
    if (barreY >= gridTop && barreY <= gridTop + gridHeight) {
      const barre = document.createElementNS(SVG_NS, "rect");
      barre.setAttribute("x", String(barreX1 - 4));
      barre.setAttribute("y", String(barreY - 4));
      barre.setAttribute("width", String(barreX2 - barreX1 + 8));
      barre.setAttribute("height", "8");
      barre.setAttribute("rx", "4");
      barre.setAttribute("class", "chord-diagram-barre");
      svg.appendChild(barre);
    }
  }

  // Finger dots / open / mute markers
  for (let s = 0; s < 6; s++) {
    const fret = shape.frets[s];
    const x = gridLeft + s * stringSpacing;
    if (fret === -1) {
      // Mute marker above the nut.
      const muteText = document.createElementNS(SVG_NS, "text");
      muteText.setAttribute("x", String(x));
      muteText.setAttribute("y", String(gridTop - 4));
      muteText.setAttribute("text-anchor", "middle");
      muteText.setAttribute("class", "chord-diagram-mute");
      muteText.textContent = "×";
      svg.appendChild(muteText);
    } else if (fret === 0) {
      // Open string marker.
      const openCircle = document.createElementNS(SVG_NS, "circle");
      openCircle.setAttribute("cx", String(x));
      openCircle.setAttribute("cy", String(gridTop - 6));
      openCircle.setAttribute("r", "3");
      openCircle.setAttribute("class", "chord-diagram-open");
      svg.appendChild(openCircle);
    } else {
      // Pressed fret dot.
      const dotY = gridTop + (fret - startFret - 0.5) * fretSpacing;
      if (dotY >= gridTop && dotY <= gridTop + gridHeight) {
        const dot = document.createElementNS(SVG_NS, "circle");
        dot.setAttribute("cx", String(x));
        dot.setAttribute("cy", String(dotY));
        dot.setAttribute("r", "5");
        dot.setAttribute("class", "chord-diagram-dot");
        svg.appendChild(dot);

        // Finger number label inside the dot.
        if (shape.fingers && shape.fingers[s] > 0) {
          const fingerText = document.createElementNS(SVG_NS, "text");
          fingerText.setAttribute("x", String(x));
          fingerText.setAttribute("y", String(dotY + 3));
          fingerText.setAttribute("text-anchor", "middle");
          fingerText.setAttribute("class", "chord-diagram-finger");
          fingerText.textContent = String(shape.fingers[s]);
          svg.appendChild(fingerText);
        }
      }
    }
  }

  return svg;
}

/**
 * Build an SVG horizontal-fretboard tab for a single chord's time
 * window. Renders 6 horizontal lines (strings, high E on top per
 * standard tab convention) with fret-number labels at the time
 * position of each note.
 *
 * Notes is expected to be the per-stem ``notes`` array shape:
 *   { pitch: int, start: float (s), end: float (s), ... }
 *
 * t0, t1 are the time window (typically the active chord's startSec /
 * endSec). Notes outside the window are filtered out before render.
 */
export function renderLeadTabSVG(notes, t0, t1, opts = {}) {
  const width = 320;
  const height = 90;
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("class", "chord-tab-svg");
  if (opts.highlighted) svg.classList.add("is-active");

  const padLeft = 24;
  const padRight = 12;
  const padTop = 14;
  const padBottom = 14;
  const stringTop = padTop;
  const stringBottom = height - padBottom;
  const stringSpacing = (stringBottom - stringTop) / 5;

  // Six strings (high E on top — tab convention).
  for (let s = 0; s < 6; s++) {
    const y = stringTop + s * stringSpacing;
    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", String(padLeft));
    line.setAttribute("y1", String(y));
    line.setAttribute("x2", String(width - padRight));
    line.setAttribute("y2", String(y));
    line.setAttribute("class", "chord-tab-string");
    svg.appendChild(line);
  }

  // "TAB" label on the left edge.
  const label = document.createElementNS(SVG_NS, "text");
  label.setAttribute("x", "4");
  label.setAttribute("y", String(stringTop + 2.5 * stringSpacing + 4));
  label.setAttribute("class", "chord-tab-clef");
  label.textContent = "T";
  svg.appendChild(label);
  const label2 = document.createElementNS(SVG_NS, "text");
  label2.setAttribute("x", "4");
  label2.setAttribute("y", String(stringTop + 2.5 * stringSpacing + 14));
  label2.setAttribute("class", "chord-tab-clef");
  label2.textContent = "A";
  svg.appendChild(label2);
  const label3 = document.createElementNS(SVG_NS, "text");
  label3.setAttribute("x", "4");
  label3.setAttribute("y", String(stringTop + 2.5 * stringSpacing + 24));
  label3.setAttribute("class", "chord-tab-clef");
  label3.textContent = "B";
  svg.appendChild(label3);

  const dt = Math.max(0.001, t1 - t0);
  const usableWidth = width - padLeft - padRight;

  // Filter + sort notes inside the window.
  const windowNotes = (notes || [])
    .filter((n) => {
      const ns = typeof n.start === "number" ? n.start : n.start_s;
      const ne = typeof n.end === "number" ? n.end : n.end_s;
      if (typeof ns !== "number" || typeof ne !== "number") return false;
      return ns < t1 && ne > t0;
    })
    .sort((a, b) => (a.start ?? a.start_s) - (b.start ?? b.start_s));

  if (windowNotes.length === 0) {
    const placeholder = document.createElementNS(SVG_NS, "text");
    placeholder.setAttribute("x", String(width / 2));
    placeholder.setAttribute("y", String(height / 2 + 4));
    placeholder.setAttribute("text-anchor", "middle");
    placeholder.setAttribute("class", "chord-tab-placeholder");
    placeholder.textContent = "(no lead notes for this region)";
    svg.appendChild(placeholder);
    return svg;
  }

  for (const note of windowNotes) {
    const pitch = typeof note.pitch === "number" ? note.pitch : null;
    if (pitch === null) continue;
    const fretAssign = midiToFret(pitch);
    if (fretAssign === null) continue;

    const ns = typeof note.start === "number" ? note.start : note.start_s;
    const x = padLeft + Math.max(0, Math.min(1, (ns - t0) / dt)) * usableWidth;
    // String 0 = low E is rendered at the BOTTOM line (tab convention),
    // so flip the index: visual row = 5 - string.
    const y = stringTop + (5 - fretAssign.string) * stringSpacing + 3;

    const fretText = document.createElementNS(SVG_NS, "text");
    fretText.setAttribute("x", String(x));
    fretText.setAttribute("y", String(y));
    fretText.setAttribute("text-anchor", "middle");
    fretText.setAttribute("class", "chord-tab-fret");
    fretText.textContent = String(fretAssign.fret);
    svg.appendChild(fretText);
  }

  return svg;
}
