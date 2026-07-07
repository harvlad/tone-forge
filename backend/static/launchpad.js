/*
 * launchpad.js — Novation Launchpad Pro MK3 integration for tone-forge Jam.
 *
 * Talks to the device over Web MIDI + SysEx from the browser. Exposes a
 * single global `window.Launchpad` object that jam.js pokes at a handful of
 * hook points (song load, chord transitions, song teardown, key detection).
 *
 * Two top-level modes:
 *   'song-verify'   — chord tile grid; pad press scores the chord tile pass
 *   'song-display'  — chord tile grid; pads are read-only light show
 *   'open-jam'      — key-aware scale grid on a guitar-fourths layout; the
 *                     current chord's pitch classes get a brightness boost
 *   'off'           — module bound to a device but not painting; used while
 *                     the enable checkbox is off but MIDIAccess is still live
 *
 * Programmer-mode pad numbering: bottom-left grid pad = 11, top-right = 88.
 * padIdx = row * 10 + col + 1, with row ∈ 1..8 (bottom → top), col ∈ 1..8
 * (left → right).
 */

(function () {
  'use strict';

  // ------------------------------------------------------------------
  // Constants
  // ------------------------------------------------------------------

  const DEVICE_NAME_HINTS = ['launchpad pro mk3', 'lppromk3', 'lp pro mk3'];
  // Prefer the MIDI port pair (not the DAW port) — MIDI is the surface
  // that carries pad-note events in Programmer Mode.
  const PORT_PREFERENCE = ['midi', 'live'];

  const SYSEX_ENTER_PROGRAMMER = [0xf0, 0x00, 0x20, 0x29, 0x02, 0x0e, 0x0e, 0x01, 0xf7];
  const SYSEX_EXIT_PROGRAMMER  = [0xf0, 0x00, 0x20, 0x29, 0x02, 0x0e, 0x0e, 0x00, 0xf7];
  // SysEx LED header up to and including the "colour spec list" opcode.
  const LED_HEADER = [0xf0, 0x00, 0x20, 0x29, 0x02, 0x0e, 0x03];
  const SYSEX_END = 0xf7;

  // LED spec type bytes.
  const LED_STATIC = 0x00;
  const LED_FLASH  = 0x01;
  const LED_PULSE  = 0x02;
  const LED_RGB    = 0x03;

  // Pitch-class name → integer.
  const PC = {
    'C':0,'B#':0,'C#':1,'Db':1,'D':2,'D#':3,'Eb':3,'E':4,'Fb':4,
    'F':5,'E#':5,'F#':6,'Gb':6,'G':7,'G#':8,'Ab':8,'A':9,'A#':10,'Bb':10,
    'B':11,'Cb':11,
  };

  const MAJOR_INTERVALS = [0, 2, 4, 5, 7, 9, 11];
  const MINOR_INTERVALS = [0, 2, 3, 5, 7, 8, 10]; // natural minor

  // Family palette IDs — hardware palette entries used for Pulse spec on
  // "next-upcoming chord" pads. These are perceptual proxies; exact
  // colours are picked from the LP Pro MK3 default palette table.
  const FAMILY_PULSE_PALETTE = {
    major: 13,   // warm yellow
    minor: 41,   // cyan-blue
    dom7: 5,     // red
    dim: 53,     // magenta
    aug: 21,     // green
    other: 3,    // white
  };

  // Family base RGB (0..127 each) for Static RGB spec. Inactive pads use
  // 25% of these; active pads use full.
  const FAMILY_RGB = {
    major: [127, 80, 0],
    minor: [0, 60, 127],
    dom7:  [127, 20, 20],
    dim:   [80, 0, 80],
    aug:   [20, 120, 20],
    other: [60, 60, 60],
  };

  // Scale-degree RGB for open-jam grid (root special-cased below).
  const DEGREE_RGB = [
    [127, 90, 0],   // I  — gold (rendered separately below as bright root)
    [40, 40, 90],   // ii — dim purple
    [40, 40, 90],   // iii
    [0, 40, 90],    // IV — dim blue
    [10, 80, 20],   // V  — dim green
    [40, 40, 90],   // vi
    [80, 10, 10],   // vii°/VII — dim red
  ];
  const ROOT_RGB = [127, 100, 0];       // bright root highlight
  const CHORD_TONE_RGB = [0, 110, 110];  // teal boost when a chord is sounding
  const CHROMATIC_DIM_RGB = [4, 4, 4];  // barely-lit out-of-key

  // Reverse table for the palette IDs used with LED_PULSE, so the
  // on-screen mirror can render pulse pads with a reasonable RGB
  // approximation of what the hardware displays.
  const PULSE_PALETTE_RGB = {
    13: [127, 100, 0],
    41: [0, 60, 127],
    5:  [127, 20, 20],
    53: [80, 0, 80],
    21: [20, 120, 20],
    3:  [60, 60, 60],
  };

  // Human-readable scale-degree labels for open-jam tooltips.
  const DEGREE_LABEL_MAJOR = ['I', 'ii', 'iii', 'IV', 'V', 'vi', 'vii°'];
  const DEGREE_LABEL_MINOR = ['i', 'ii°', 'III', 'iv', 'v', 'VI', 'VII'];
  const PC_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];

  // Open-jam layout: pad (row, col) → MIDI pitch. Standard fourths tuning.
  // Row 0 (padIdx 11..18) is E2..B2, then each row +5 semitones.
  const OPEN_JAM_BASE_MIDI = 40; // E2

  // ------------------------------------------------------------------
  // Chord symbol parsing
  // ------------------------------------------------------------------

  // Coarse chord-quality classifier. Returns one of
  // {maj, min, dom7, maj7, min7, sus, dim, aug, other}. Kept intentionally
  // small so pad assignment stays legible; jam.js's own helpers handle
  // finer-grained rendering.
  function _classifyQuality(qualityStr) {
    const q = (qualityStr || '').toLowerCase();
    if (!q || q === 'maj' || q === 'major' || q === 'add9' || q === '6' || q === 'maj6') return 'maj';
    if (q === 'maj7' || q === 'maj9' || q === 'maj13') return 'maj7';
    if (q === 'm7' || q === 'min7' || q === 'm9' || q === 'min9' || q === 'm11') return 'min7';
    if (q === 'm' || q === 'min' || q === 'minor' || q === 'm6') return 'min';
    if (q === 'sus2' || q === 'sus4' || q === 'sus') return 'sus';
    if (q === 'dim' || q === 'dim7' || q === 'm7b5' || q === 'ø') return 'dim';
    if (q === 'aug' || q === '+' || q === 'aug7') return 'aug';
    if (/^(7|9|11|13)$/.test(q) || q === 'dom7') return 'dom7';
    return 'other';
  }

  // Family colour bucket (used for pad colouring).
  function _familyForQuality(q) {
    if (q === 'maj' || q === 'maj7' || q === 'sus') return 'major';
    if (q === 'min' || q === 'min7') return 'minor';
    if (q === 'dom7') return 'dom7';
    if (q === 'dim') return 'dim';
    if (q === 'aug') return 'aug';
    return 'other';
  }

  // Parse "C#m7", "Dbmaj", "F#", "G7", "Bm7b5" into { rootPc, quality }.
  // Returns null on unrecognisable input.
  function _parseChordSymbol(sym) {
    if (typeof sym !== 'string') return null;
    const s = sym.trim();
    if (!s) return null;
    // Grab up to a two-char accidental root.
    const m = s.match(/^([A-Ga-g])([#b]?)(.*)$/);
    if (!m) return null;
    const rootName = m[1].toUpperCase() + (m[2] || '');
    const rootPc = PC[rootName];
    if (rootPc === undefined) return null;
    return { rootPc, quality: _classifyQuality(m[3]) };
  }

  // Canonical key used to dedupe enharmonic equivalents: "1:min" == C#m == Dbm.
  function _canonicalChordKey(sym) {
    const p = _parseChordSymbol(sym);
    return p ? `${p.rootPc}:${p.quality}` : null;
  }

  // Approximate pitch-class set for a chord — used only to boost open-jam
  // pads while a chord is sounding, so cheap heuristics are fine.
  function _chordPitchClasses(sym) {
    const p = _parseChordSymbol(sym);
    if (!p) return new Set();
    const pcs = new Set();
    const add = (semi) => pcs.add((p.rootPc + semi + 12) % 12);
    add(0); // root
    switch (p.quality) {
      case 'maj':
      case 'maj7':
        add(4); add(7);
        if (p.quality === 'maj7') add(11);
        break;
      case 'min':
      case 'min7':
        add(3); add(7);
        if (p.quality === 'min7') add(10);
        break;
      case 'dom7':
        add(4); add(7); add(10); break;
      case 'sus':
        add(5); add(7); break;
      case 'dim':
        add(3); add(6); break;
      case 'aug':
        add(4); add(8); break;
      default:
        add(4); add(7); break;
    }
    return pcs;
  }

  // ------------------------------------------------------------------
  // Grid layout — song-mode chord tile assignment
  // ------------------------------------------------------------------

  // Quality → column index. Fixed order so muscle memory carries over.
  const QUALITY_COLUMNS = { maj: 0, min: 1, dom7: 2, maj7: 3, min7: 4, sus: 5, dim: 6, aug: 7, other: 7 };

  // Convert (row, col) with row/col 0-indexed → Programmer-Mode padIdx.
  function _padIdx(row, col) {
    return (row + 1) * 10 + (col + 1);
  }

  // Circle-of-fifths row for a chord root pitch class relative to a key
  // root (also a pitch class). Returns 0..7 with I at row 0 (bottom).
  function _rowForRoot(chordRootPc, keyRootPc) {
    if (typeof keyRootPc !== 'number') {
      return (chordRootPc >> 1) & 7;
    }
    const diff = ((chordRootPc - keyRootPc) * 7 + 144) % 12;
    // diff = 0 → I, 5 → V, 10 → ii, 3 → vi, 8 → iii, 1 → vii°, 6 → #IV, 11 → bVII, 4 → VI, 9 → III, 2 → V/V, 7 → IV
    // Bin the 12 into 8 rows by folding 8..11 into rows 4..7 (keeps I/IV/V distinct).
    if (diff < 8) return diff;
    return 4 + ((diff - 8) & 3);
  }

  // Encounter-order layout: walk the chord list in time order and
  // assign each unique chord to the next free pad, starting at 11 and
  // wrapping left→right, bottom→up. Muscle memory is stable across
  // songs because the "first chord" always lives at pad 11.
  function _assignChordsEncounterOrder(chords) {
    const assignment = new Map();
    const reverse = new Map();
    const overflow = [];
    if (!Array.isArray(chords) || chords.length === 0) {
      return { assignment, reverse, overflow };
    }
    let cursor = 0; // 0..63
    for (const c of chords) {
      const key = _canonicalChordKey(c.symbol);
      if (!key || assignment.has(key)) continue;
      if (cursor >= 64) { overflow.push(c.symbol); continue; }
      const row = cursor >> 3;      // 0..7 bottom → top
      const col = cursor & 7;       // 0..7 left → right
      const padIdx = _padIdx(row, col);
      const p = _parseChordSymbol(c.symbol);
      const family = p ? _familyForQuality(p.quality) : 'other';
      assignment.set(key, { padIdx, family, symbol: c.symbol, cumTime: 0 });
      reverse.set(padIdx, key);
      cursor++;
    }
    return { assignment, reverse, overflow };
  }

  // Dispatch to the correct layout strategy for the current `_layout`.
  // Returns the same { assignment, reverse, overflow } shape.
  function _assignChords(chords, songKey) {
    if (_layout === 'song') return _assignChordsEncounterOrder(chords);
    if (_layout === 'pitch') return _assignChordsPitchLayout(chords, songKey);
    return _assignChordsTheoryLayout(chords, songKey);
  }

  // Pitch-height row for a chord root pitch class relative to the song
  // key root. Semitones-from-tonic (0..11) get mapped monotonically to
  // grid rows (0..7) — tonic sits at row 0 (bottom), the fifth at row
  // 4, octave-wrap at row 7. Higher root → higher row = piano-like.
  function _rowForRootPitch(chordRootPc, keyRootPc) {
    let semis;
    if (typeof keyRootPc === 'number') {
      semis = ((chordRootPc - keyRootPc) % 12 + 12) % 12;
    } else {
      semis = ((chordRootPc | 0) % 12 + 12) % 12;
    }
    // Distribute 12 pitch classes across 8 rows uniformly.
    let row = Math.round((semis / 12) * 7);
    if (row < 0) row = 0;
    if (row > 7) row = 7;
    return row;
  }

  // Pitch-height layout: rows = root pitch relative to song tonic
  // (bottom = tonic, top = octave), columns = chord quality via
  // QUALITY_COLUMNS. Piano-like — playing a rising bass line walks the
  // active pad upward on the grid. Collisions spiral outward to the
  // nearest free cell, same as the theory layout.
  function _assignChordsPitchLayout(chords, songKey) {
    const keyRootPc = songKey && typeof songKey.root === 'number' ? songKey.root : null;
    const cumTime = new Map();
    const displaySym = new Map();
    const parsed = new Map();
    for (const c of chords) {
      const key = _canonicalChordKey(c.symbol);
      if (!key) continue;
      const dur = Math.max(0, (c.endSec || 0) - (c.startSec || 0));
      cumTime.set(key, (cumTime.get(key) || 0) + dur);
      if (!displaySym.has(key)) displaySym.set(key, c.symbol);
      if (!parsed.has(key)) parsed.set(key, _parseChordSymbol(c.symbol));
    }
    // Assign in cumulative-time order so heavily-used chords land on
    // their ideal (row, col) and rarer chords take the spiraled leftovers.
    const ordered = Array.from(cumTime.keys()).sort((a, b) => cumTime.get(b) - cumTime.get(a));
    const assignment = new Map();
    const reverse = new Map();
    const overflow = [];
    const taken = new Set();

    for (const key of ordered) {
      const p = parsed.get(key);
      if (!p) continue;
      const row = _rowForRootPitch(p.rootPc, keyRootPc);
      const col = QUALITY_COLUMNS[p.quality] || 7;
      let padIdx = _padIdx(row, col);
      if (taken.has(padIdx)) {
        // Spiral outward to the nearest free cell.
        let found = false;
        outer:
        for (let ring = 1; ring < 8; ring++) {
          for (let dr = -ring; dr <= ring; dr++) {
            for (let dc = -ring; dc <= ring; dc++) {
              if (Math.max(Math.abs(dr), Math.abs(dc)) !== ring) continue;
              const rr = row + dr, cc = col + dc;
              if (rr < 0 || rr > 7 || cc < 0 || cc > 7) continue;
              const idx = _padIdx(rr, cc);
              if (!taken.has(idx)) {
                padIdx = idx;
                found = true;
                break outer;
              }
            }
          }
        }
        if (!found) {
          overflow.push(displaySym.get(key) || key);
          continue;
        }
      }
      taken.add(padIdx);
      const family = _familyForQuality(p.quality);
      assignment.set(key, {
        padIdx,
        family,
        symbol: displaySym.get(key),
        cumTime: cumTime.get(key),
      });
      reverse.set(padIdx, key);
    }
    return { assignment, reverse, overflow };
  }

  // Assign each unique chord in `chords` to a pad. Returns
  // { assignment: Map<canonicalKey, {padIdx, family, symbol, cumTime}>,
  //   reverse: Map<padIdx, canonicalKey>,
  //   overflow: Array<string> }
  function _assignChordsTheoryLayout(chords, songKey) {
    const keyRootPc = songKey && typeof songKey.root === 'number' ? songKey.root : null;
    // First pass: compute cumulative time per canonical key + pick a
    // display symbol (the first-seen variant is fine).
    const cumTime = new Map();
    const displaySym = new Map();
    const parsed = new Map();
    for (const c of chords) {
      const key = _canonicalChordKey(c.symbol);
      if (!key) continue;
      const dur = Math.max(0, (c.endSec || 0) - (c.startSec || 0));
      cumTime.set(key, (cumTime.get(key) || 0) + dur);
      if (!displaySym.has(key)) displaySym.set(key, c.symbol);
      if (!parsed.has(key)) parsed.set(key, _parseChordSymbol(c.symbol));
    }
    // Order by cumulative time descending, so on overflow the least-used
    // chords get dropped.
    const ordered = Array.from(cumTime.keys()).sort((a, b) => cumTime.get(b) - cumTime.get(a));
    const assignment = new Map();
    const reverse = new Map();
    const overflow = [];
    const taken = new Set(); // padIdx values already used

    for (const key of ordered) {
      const p = parsed.get(key);
      if (!p) continue;
      let row = _rowForRoot(p.rootPc, keyRootPc);
      let col = QUALITY_COLUMNS[p.quality] || 7;
      let padIdx = _padIdx(row, col);
      if (taken.has(padIdx)) {
        // Spiral outward from (row, col) to find a free cell.
        let found = false;
        outer:
        for (let ring = 1; ring < 8; ring++) {
          for (let dr = -ring; dr <= ring; dr++) {
            for (let dc = -ring; dc <= ring; dc++) {
              if (Math.max(Math.abs(dr), Math.abs(dc)) !== ring) continue;
              const rr = row + dr, cc = col + dc;
              if (rr < 0 || rr > 7 || cc < 0 || cc > 7) continue;
              const idx = _padIdx(rr, cc);
              if (!taken.has(idx)) {
                padIdx = idx;
                found = true;
                break outer;
              }
            }
          }
        }
        if (!found) {
          overflow.push(displaySym.get(key) || key);
          continue;
        }
      }
      taken.add(padIdx);
      const family = _familyForQuality(p.quality);
      assignment.set(key, {
        padIdx,
        family,
        symbol: displaySym.get(key),
        cumTime: cumTime.get(key),
      });
      reverse.set(padIdx, key);
    }
    return { assignment, reverse, overflow };
  }

  // ------------------------------------------------------------------
  // Module-private state
  // ------------------------------------------------------------------

  let _access = null;
  let _input = null;
  let _output = null;
  let _deviceName = null;
  // Expanded mode taxonomy. See jam.js state.settings.launchpadMode for
  // the full list + semantics; here we only need to know which grid
  // painter to invoke.
  //   'off'
  //   'song-verify' | 'song-display'    — song chord grid
  //   'theory'                          — full-grid degree colouring
  //   'instrument-synth' | 'instrument-bass'
  //   | 'instrument-drum' | 'instrument-melody'
  //                                     — instrument submodes
  //   'free-play'                       — raw scale grid, no highlighting
  //   'display'                         — passive lightshow (song grid only)
  let _mode = 'off';
  // Chord-tile spatial layout in song modes:
  //   'theory' — functional grid (circle-of-fifths rows, quality cols)
  //   'song'   — encounter-order (pad 11 = 1st chord met, then wraps)
  let _layout = 'theory';
  // Assist level (1..4). Acts as a paint filter, orthogonal to mode:
  //   1 Training Wheels: paint everything (default palette + anticipation)
  //   2 Guided:          paint current + next only
  //   3 Performance:     paint current only, no anticipation
  //   4 Blind:           paint nothing
  let _assistLevel = 2;
  // Anticipation window in beats. When > 0 the next-chord pad starts
  // pulsing this many beats before the transition and ramps intensity
  // as the transition approaches.
  let _anticipationBeats = 2;
  // Instrument submode selector — only meaningful when _mode starts
  // with 'instrument-'.
  let _instrumentSubmode = 'synth';
  let _enabled = false;             // reflects the enable checkbox
  let _initDone = false;
  let _statusCb = null;
  let _padPressCb = null;
  let _padReleaseCb = null;
  let _gridChangeCb = null;
  let _modeChangeCb = null;
  let _legendInfoCb = null;
  let _ccMessageCb = null;
  // Beat-tick state for anticipation. jam.js pushes this via
  // api.onBeatTick({ beatIdx, beatsUntilNextChord, nextSymbol }).
  let _beat = { beatIdx: 0, beatsUntilNext: null, nextSymbol: null };

  // Mirror of the last-painted pad colors, indexed by Programmer-Mode
  // padIdx (11..88). Entries are { r, g, b, kind } where kind is one of
  // 'off' | 'static' | 'pulse' | 'active'. Unused slots (10, 19, 20…)
  // stay undefined. jam.js reads this via getGridColors() to render the
  // on-screen mirror grid.
  const _gridColors = new Array(89);

  function _recordPad(padIdx, r, g, b, kind) {
    _gridColors[padIdx] = { r: r | 0, g: g | 0, b: b | 0, kind };
  }

  function _afterPaint() {
    if (typeof _gridChangeCb === 'function') {
      try { _gridChangeCb(_gridColors); } catch (e) {
        console.warn('[launchpad] onGridChange threw:', e);
      }
    }
  }

  // Song mode
  let _songChords = [];
  let _assignment = new Map();
  let _reverse = new Map();
  let _activePadIdx = null;
  let _nextPadIdx = null;

  // Open jam
  let _songKey = null;
  let _manualRoot = 0;
  let _manualScale = 'Major';
  let _outOfKeyMode = 'dim';         // 'dim' | 'off'
  let _openJamActiveChordPCs = new Set();

  // Contribute — Sample mode. jam.js pushes an array of chop metadata
  // objects via api.setChops(chops); each entry lands on one pad in
  // chopIdx order (chopIdx 0 = top-left, wrap left→right, top→bottom).
  // Chops beyond 64 are ignored; unused pads are painted off. Actual
  // playback is handled entirely in jam.js — this module only owns the
  // paint + pad-meaning half so the trigger pipeline can find the
  // right chop on press. See _launchpadContributePress in jam.js.
  let _contributeChops = [];
  // Palette used to translate the backend's colorHint strings into
  // pad RGB values. Kept dim by design so the color still reads
  // clearly on a lit pad without being blindingly bright.
  const CONTRIBUTE_PALETTE = Object.freeze({
    'red':          [255,  40,  40],
    'red-orange':   [255, 100,  20],
    'orange':       [255, 140,   0],
    'yellow-orange':[255, 180,   0],
    'yellow':       [255, 220,   0],
    'yellow-green': [180, 220,   0],
    'green':        [ 60, 220,  60],
    'cyan':         [ 60, 220, 220],
    'blue':         [ 40, 100, 255],
    'blue-violet':  [130,  60, 255],
    'violet':       [180,  60, 220],
    'magenta':      [220,  60, 180],
    'gold':         [220, 180,  40],
    'teal':         [ 40, 200, 180],
    'gray':         [ 80,  80,  80],
  });
  // Set of pad indexes that have been muted by a long-hold. Painted
  // at ~15% brightness so the user can still see which chop belongs
  // there. Mutated by setChopDisabled from jam.js. Cleared when the
  // chop list is replaced (new song / preset switch).
  const _disabledChopPads = new Set();
  // Multiplier for disabled-pad brightness. Set high enough that the
  // pad's color hint remains readable (so users can see "there's a
  // sample here, tap to activate") but low enough that active pads
  // clearly pop above it. 5% was too dark — the whole grid looked
  // dead, which read to users as "nothing loaded".
  const _DISABLED_DIM = 0.18;

  // ------------------------------------------------------------------
  // Status helpers
  // ------------------------------------------------------------------

  function _emitStatus(extra) {
    const status = {
      supported: !!(navigator && navigator.requestMIDIAccess),
      connected: !!_output && !!_input,
      deviceName: _deviceName,
      error: null,
      mode: _mode,
      ...(extra || {}),
    };
    if (typeof _statusCb === 'function') {
      try { _statusCb(status); } catch (_) {}
    }
    return status;
  }

  // ------------------------------------------------------------------
  // SysEx senders
  // ------------------------------------------------------------------

  function _send(bytes) {
    if (!_output) return;
    try {
      _output.send(bytes);
    } catch (e) {
      console.warn('[launchpad] SysEx send failed:', e);
      // Assume the port is gone; the onstatechange handler will finish
      // tearing down. Null the port reference so subsequent sends no-op.
      _output = null;
      _emitStatus({ error: 'send_failed' });
    }
  }

  // Compose one batched LED SysEx from a list of specs.
  //   specs: Array<[type, pad, ...args]>
  // Silently no-ops on an empty spec list.
  function _sendLedSpecs(specs) {
    if (!specs || specs.length === 0) return;
    const bytes = LED_HEADER.slice();
    for (const s of specs) {
      for (const b of s) bytes.push(b & 0x7f);
    }
    bytes.push(SYSEX_END);
    _send(bytes);
  }

  function _rgbSpec(padIdx, r, g, b) {
    return [LED_RGB, padIdx, r | 0, g | 0, b | 0];
  }
  function _pulseSpec(padIdx, palette) {
    return [LED_PULSE, padIdx, palette | 0];
  }
  // Sharp on/off blink between the palette color and black. Much more
  // eye-catching than LED_PULSE (which softly fades) — the LP MK3's
  // pulse is BPM-synced but so gentle it can be hard to see across a
  // room. Flash reads as an unmissable "next chord coming" cue.
  function _flashSpec(padIdx, colorA, colorB) {
    return [LED_FLASH, padIdx, colorA | 0, (colorB | 0) || 0];
  }
  function _offSpec(padIdx) {
    return [LED_STATIC, padIdx, 0];
  }

  // ------------------------------------------------------------------
  // Countdown bar (padIdx 81..88 — top row of the 8x8 grid)
  // ------------------------------------------------------------------
  // The top row of the actual pad grid is painted as a progress bar
  // that fills left→right as the playhead approaches the next distinct
  // chord change. Sits directly below the round control-button row so
  // eye + finger travel matches the visible chord palette. Gradient:
  // cyan (relaxed) → amber → red (imminent). Called from jam.js RAF at
  // ~60Hz; SysEx traffic is throttled by a change-gate + a min-interval
  // floor. When blanking, the row is repainted with the assigned chord
  // palette (or off) so the countdown doesn't leave a black band on
  // the grid whenever it goes idle.
  const COUNTDOWN_BASE_PAD = 81;
  const TOP_ROW_MIN_INTERVAL_MS = 45;
  let _topRowLastPctScaled = -1;
  let _topRowLastImm = false;
  let _topRowLastSendMs = 0;
  let _topRowBlanked = true;

  // Piecewise cyan→amber→red gradient at t∈[0,1], returns [r,g,b] in 0..255.
  function _countdownGradient(t) {
    if (t <= 0) return [77, 208, 225];   // cyan
    if (t >= 1) return [255, 82, 82];    // red
    if (t < 0.65) {
      const s = t / 0.65;
      return [
        Math.round(77 + (255 - 77) * s),
        Math.round(208 + (183 - 208) * s),
        Math.round(225 + (77 - 225) * s),
      ];
    }
    const s = (t - 0.65) / 0.35;
    return [
      255,
      Math.round(183 + (82 - 183) * s),
      Math.round(77 + (82 - 77) * s),
    ];
  }

  function _blankTopRow() {
    if (_topRowBlanked) return;
    _topRowBlanked = true;
    _topRowLastPctScaled = -1;
    _topRowLastImm = false;
    if (!_output) return;
    // Restore each of pads 81..88 to its mode-appropriate resting look
    // so the countdown row doesn't leave a black band on the grid.
    const specs = [];
    const bright = _restBrightness();
    const inSongMode = (_mode === 'song-verify' || _mode === 'song-display' || _mode === 'display');
    for (let col = 0; col < 8; col++) {
      const padIdx = COUNTDOWN_BASE_PAD + col;
      const key = inSongMode ? _reverse.get(padIdx) : null;
      const info = key ? _assignment.get(key) : null;
      if (info) {
        const base = FAMILY_RGB[info.family] || FAMILY_RGB.other;
        const [rr, gg, bb] = _scaledRgb(base, bright);
        specs.push([LED_RGB, padIdx, rr | 0, gg | 0, bb | 0]);
        _recordPad(padIdx, rr, gg, bb, 'static');
      } else {
        specs.push([LED_STATIC, padIdx, 0]);
        _recordPad(padIdx, 0, 0, 0, 'off');
      }
    }
    _sendLedSpecs(specs);
    _afterPaint();
  }

  function _paintCountdownBar(progress, imminent) {
    if (!_output) return;
    if (progress === null || progress === undefined || Number.isNaN(progress)) {
      _blankTopRow();
      return;
    }
    const pct = Math.max(0, Math.min(1, +progress));
    const pctScaled = Math.round(pct * 800);
    const imm = !!imminent;
    const now = (typeof performance !== 'undefined' && performance.now)
      ? performance.now() : Date.now();
    if (pctScaled === _topRowLastPctScaled && imm === _topRowLastImm) return;
    if (now - _topRowLastSendMs < TOP_ROW_MIN_INTERVAL_MS
        && pctScaled === _topRowLastPctScaled) return;
    const total = 8;
    const filledFloat = pct * total;
    const leadingIdx = Math.min(total - 1, Math.floor(filledFloat));
    const specs = [];
    for (let i = 0; i < total; i++) {
      const padIdx = COUNTDOWN_BASE_PAD + i;
      // Brightness envelope across the row: fully filled = 1, past the
      // fill front = 0, at the front = fractional part.
      let brightness = 0;
      if (i + 1 <= filledFloat) brightness = 1;
      else if (i < filledFloat) brightness = filledFloat - i;
      if (brightness <= 0) {
        specs.push([LED_STATIC, padIdx, 0]);
        _recordPad(padIdx, 0, 0, 0, 'off');
        continue;
      }
      const gradPos = (total > 1) ? i / (total - 1) : 0;
      const rgb = _countdownGradient(gradPos);
      if (imm && i === leadingIdx) {
        // Flash the leading pad hard between red and off in the last
        // sliver of the countdown so it reads as unmissable.
        specs.push([LED_FLASH, padIdx, 5, 0]);
        _recordPad(padIdx, 255, 40, 40, 'pulse');
        continue;
      }
      // Map 0..255 RGB → 0..127 SysEx and apply brightness.
      const to7 = (v) => Math.max(0, Math.min(127, Math.round(v * brightness / 2)));
      const rr = to7(rgb[0]);
      const gg = to7(rgb[1]);
      const bb = to7(rgb[2]);
      specs.push([LED_RGB, padIdx, rr, gg, bb]);
      // Record at the 7-bit scale (0..127); mirror multiplies back to 0..255.
      _recordPad(padIdx, rr, gg, bb, 'static');
    }
    _sendLedSpecs(specs);
    _topRowLastPctScaled = pctScaled;
    _topRowLastImm = imm;
    _topRowLastSendMs = now;
    _topRowBlanked = false;
    _afterPaint();
  }

  function _scaledRgb(rgb, k) {
    return [rgb[0] * k, rgb[1] * k, rgb[2] * k];
  }

  function _clearAllPads() {
    const specs = [];
    for (let r = 0; r < 8; r++) {
      for (let c = 0; c < 8; c++) {
        const idx = _padIdx(r, c);
        specs.push(_offSpec(idx));
        _recordPad(idx, 0, 0, 0, 'off');
      }
    }
    _sendLedSpecs(specs);
    _afterPaint();
  }

  // ------------------------------------------------------------------
  // Transport buttons (Play / Stop)
  // ------------------------------------------------------------------
  // The LP Pro MK3 has dedicated transport buttons in the round ring
  // around the pad grid. In Programmer Mode the LED-addressing scheme
  // extends over those buttons too; the SysEx LED spec accepts the
  // same padIdx values (see the Programmer's Reference for the map).
  //
  // The exact pad indices vary a bit across MK3 firmware revisions;
  // the values below are the community-reported defaults for the
  // current shipping firmware. If a physical press logs a different
  // CC/note than the value below, the console.log in _onMidi surfaces
  // it and this constant is a one-line adjust.
  // Novation's docs are inconsistent across MK3 firmware revisions and
  // the button that fires the "Play" CC/note varies. Instead of guessing
  // a single value we paint every likely candidate simultaneously — the
  // real Play button will illuminate, unused candidates address dead
  // slots and are ignored silently. After confirmation, trim to the one.
  //
  // Candidates cover both CC-based (right column / transport row) and
  // Note-based (top row / bottom row) addressing schemes reported by
  // MK3 community references.
  const TRANSPORT_PLAY_CANDIDATES = [
    19,   // bottom-right round scene-launch button (my original guess)
    20,   // LP Pro (MK2) transport CC — sometimes reused on MK3
    91,   // top-left Session/User row
    98,   // top-right Session/User row
    108,  // dedicated transport note on some firmware
    93,   // top row middle (Session button on MK3)
  ];
  let _lastPlayingState = null;    // null = never painted, false = dim, true = bright

  function _paintPlayButton(playing) {
    if (!_output) return;
    if (playing === _lastPlayingState) return;
    _lastPlayingState = playing;
    // Bright green when transport is running; dim green when idle so
    // the button is discoverable even before the user has pressed play.
    const rgb = playing ? [0, 127, 0] : [0, 24, 0];
    const specs = TRANSPORT_PLAY_CANDIDATES.map(
      (idx) => [LED_RGB, idx, rgb[0], rgb[1], rgb[2]]
    );
    _sendLedSpecs(specs);
  }

  function _blankTransportButtons() {
    if (!_output) return;
    const specs = TRANSPORT_PLAY_CANDIDATES.map((idx) => [LED_STATIC, idx, 0]);
    _sendLedSpecs(specs);
    _lastPlayingState = null;
  }

  // Right-side scene launch buttons (round buttons at col=9). In
  // Programmer Mode these fire CC messages with codes matching their
  // padIdx-equivalent (row*10 + 9). jam.js uses them as per-row loop
  // launchers in Contribute mode: rows 1..7 launch/stop that row's
  // loop clip; row 8's button is the "stop all loops" hotkey.
  const SCENE_LAUNCH_CCS = [19, 29, 39, 49, 59, 69, 79, 89];

  // Paint one scene-launch button. r/g/b in the 0..127 Launchpad range.
  // No-op when we haven't bound to a real output yet (headless test
  // harness / disabled state).
  function _paintSceneLaunch(row, r, g, b) {
    if (!_output) return;
    const cc = SCENE_LAUNCH_CCS[row - 1];
    if (typeof cc !== 'number') return;
    _sendLedSpecs([[LED_RGB, cc, r | 0, g | 0, b | 0]]);
  }

  function _blankAllSceneLaunch() {
    if (!_output) return;
    const specs = SCENE_LAUNCH_CCS.map((cc) => [LED_STATIC, cc, 0]);
    _sendLedSpecs(specs);
  }

  // Top row of round buttons (top of the device, above the pad grid).
  // In Programmer Mode these fire CCs 91..98 left→right. jam.js binds
  // them as a preset-switcher in Contribute-Sample mode. Colors are
  // driven from jam.js so the palette can match the on-screen preset
  // chips.
  const TOP_ROW_CCS = [91, 92, 93, 94, 95, 96, 97, 98];

  function _paintTopRow(col, r, g, b) {
    if (!_output) return;
    const cc = TOP_ROW_CCS[col];
    if (typeof cc !== 'number') return;
    _sendLedSpecs([[LED_RGB, cc, r | 0, g | 0, b | 0]]);
  }

  function _blankAllTopRow() {
    if (!_output) return;
    const specs = TOP_ROW_CCS.map((cc) => [LED_STATIC, cc, 0]);
    _sendLedSpecs(specs);
  }

  // ------------------------------------------------------------------
  // Song-mode painting
  // ------------------------------------------------------------------

  // Resting-palette brightness for the assigned chord pads in song mode.
  // Higher assist = brighter scaffolding; Performance keeps a faint layout.
  function _restBrightness() {
    if (_assistLevel >= 3) return 0.10;
    if (_assistLevel === 2) return 0.25;
    return 0.35;
  }

  function _paintSongGridFull() {
    const specs = [];
    for (let r = 0; r < 8; r++) {
      for (let c = 0; c < 8; c++) {
        const idx = _padIdx(r, c);
        specs.push(_offSpec(idx));
        _recordPad(idx, 0, 0, 0, 'off');
      }
    }
    // Assist level 4 (Blind) is handled upstream in _repaintForMode and
    // never reaches here. Levels 1..3 all paint the resting palette so
    // the user always has a visible chord map; brightness scales down
    // with assist level (see _restBrightness) so Performance is subtler
    // than Training.
    const restBright = _restBrightness();
    for (const info of _assignment.values()) {
      const base = FAMILY_RGB[info.family] || FAMILY_RGB.other;
      const [rr, gg, bb] = _scaledRgb(base, restBright);
      specs.push([LED_RGB, info.padIdx, rr | 0, gg | 0, bb | 0]);
      _recordPad(info.padIdx, rr, gg, bb, 'static');
    }
    _sendLedSpecs(specs);
    _afterPaint();
  }

  // Diff-based transition update. `activeIdx` is the index into
  // state.chords of the newly-active chord (or -1 in an inter-chord gap).
  function _updateSongActive(activeIdx, chords) {
    if (!_assignment.size) return;
    let newActivePad = null;
    let newActiveFamily = null;
    if (activeIdx >= 0 && chords && chords[activeIdx]) {
      const key = _canonicalChordKey(chords[activeIdx].symbol);
      const info = _assignment.get(key);
      if (info) {
        newActivePad = info.padIdx;
        newActiveFamily = info.family;
      }
    }
    // Find the next *distinct* chord after activeIdx.
    let newNextPad = null;
    let newNextFamily = null;
    if (chords && activeIdx >= 0) {
      const activeKey = activeIdx >= 0 && chords[activeIdx]
        ? _canonicalChordKey(chords[activeIdx].symbol) : null;
      for (let i = activeIdx + 1; i < chords.length; i++) {
        const k = _canonicalChordKey(chords[i].symbol);
        if (!k || k === activeKey) continue;
        const info = _assignment.get(k);
        if (info) {
          newNextPad = info.padIdx;
          newNextFamily = info.family;
        }
        break;
      }
    }
    if (newActivePad === _activePadIdx && newNextPad === _nextPadIdx) return;
    const specs = [];
    // Restore the previously-active pad to its dim family colour.
    if (_activePadIdx !== null && _activePadIdx !== newActivePad) {
      const prevKey = _reverse.get(_activePadIdx);
      const prevInfo = prevKey ? _assignment.get(prevKey) : null;
      if (prevInfo) {
        const base = FAMILY_RGB[prevInfo.family] || FAMILY_RGB.other;
        const [rr, gg, bb] = _scaledRgb(base, _restBrightness());
        specs.push(_rgbSpec(_activePadIdx, rr, gg, bb));
        _recordPad(_activePadIdx, rr, gg, bb, 'static');
      }
    }
    // Restore the previously-"next" pad if it isn't the new active/next.
    if (_nextPadIdx !== null
        && _nextPadIdx !== newActivePad
        && _nextPadIdx !== newNextPad) {
      const prevKey = _reverse.get(_nextPadIdx);
      const prevInfo = prevKey ? _assignment.get(prevKey) : null;
      if (prevInfo) {
        const base = FAMILY_RGB[prevInfo.family] || FAMILY_RGB.other;
        const [rr, gg, bb] = _scaledRgb(base, _restBrightness());
        specs.push(_rgbSpec(_nextPadIdx, rr, gg, bb));
        _recordPad(_nextPadIdx, rr, gg, bb, 'static');
      }
    }
    // Light the new active pad bright.
    if (newActivePad !== null) {
      const base = FAMILY_RGB[newActiveFamily] || FAMILY_RGB.other;
      specs.push(_rgbSpec(newActivePad, base[0], base[1], base[2]));
      _recordPad(newActivePad, base[0], base[1], base[2], 'active');
    }
    // Pulse the new "next" pad (hardware handles animation). Anticipation
    // is a distinct axis from assist level — as long as the user hasn't
    // switched to Blind (level 4) and hasn't turned the anticipation
    // slider to 0, honour it.
    const nextVisible = _assistLevel <= 3 && _anticipationBeats > 0;
    if (nextVisible && newNextPad !== null && newNextPad !== newActivePad) {
      const palette = FAMILY_PULSE_PALETTE[newNextFamily] || FAMILY_PULSE_PALETTE.other;
      // Hard-blink between the family color and black so it's obvious
      // on the physical device, not just a subtle fade.
      specs.push(_flashSpec(newNextPad, palette, 0));
      const pulseRgb = PULSE_PALETTE_RGB[palette] || [64, 64, 64];
      _recordPad(newNextPad, pulseRgb[0], pulseRgb[1], pulseRgb[2], 'pulse');
    }
    _sendLedSpecs(specs);
    _activePadIdx = newActivePad;
    _nextPadIdx = newNextPad;
    _afterPaint();
  }

  // ------------------------------------------------------------------
  // Open-jam painting
  // ------------------------------------------------------------------

  function _effectiveKey() {
    if (_songKey && typeof _songKey.root === 'number') {
      return {
        root: _songKey.root,
        scale: _songKey.scale === 'Minor' ? 'Minor' : 'Major',
        pitchClasses: _songKey.pitchClasses instanceof Set
          ? _songKey.pitchClasses
          : new Set((_songKey.scale === 'Minor' ? MINOR_INTERVALS : MAJOR_INTERVALS)
              .map(i => (_songKey.root + i) % 12)),
      };
    }
    return {
      root: _manualRoot,
      scale: _manualScale,
      pitchClasses: new Set(
        (_manualScale === 'Minor' ? MINOR_INTERVALS : MAJOR_INTERVALS)
          .map(i => (_manualRoot + i) % 12),
      ),
    };
  }

  // Return the 1-based scale-degree index (1..7) for `pc` in the effective
  // key, or null if `pc` is out-of-key.
  function _scaleDegreeInKey(pc, key) {
    const diff = ((pc - key.root) + 12) % 12;
    const intervals = key.scale === 'Minor' ? MINOR_INTERVALS : MAJOR_INTERVALS;
    const idx = intervals.indexOf(diff);
    return idx >= 0 ? idx + 1 : null;
  }

  function _midiForPad(row, col) {
    return OPEN_JAM_BASE_MIDI + row * 5 + col;
  }

  function _openJamSpecForPad(row, col, key, chordPCs) {
    const midi = _midiForPad(row, col);
    const pc = ((midi % 12) + 12) % 12;
    const idx = _padIdx(row, col);
    const inChord = chordPCs.has(pc);
    if (inChord) {
      _recordPad(idx, CHORD_TONE_RGB[0], CHORD_TONE_RGB[1], CHORD_TONE_RGB[2], 'active');
      return _rgbSpec(idx, CHORD_TONE_RGB[0], CHORD_TONE_RGB[1], CHORD_TONE_RGB[2]);
    }
    if (pc === key.root) {
      _recordPad(idx, ROOT_RGB[0], ROOT_RGB[1], ROOT_RGB[2], 'active');
      return _rgbSpec(idx, ROOT_RGB[0], ROOT_RGB[1], ROOT_RGB[2]);
    }
    const deg = _scaleDegreeInKey(pc, key);
    if (deg !== null) {
      const rgb = DEGREE_RGB[deg - 1] || DEGREE_RGB[0];
      const [rr, gg, bb] = _scaledRgb(rgb, 0.6);
      _recordPad(idx, rr, gg, bb, 'static');
      return _rgbSpec(idx, rr, gg, bb);
    }
    // Out-of-key.
    if (_outOfKeyMode === 'off') {
      _recordPad(idx, 0, 0, 0, 'off');
      return _offSpec(idx);
    }
    _recordPad(idx, CHROMATIC_DIM_RGB[0], CHROMATIC_DIM_RGB[1], CHROMATIC_DIM_RGB[2], 'static');
    return _rgbSpec(idx, CHROMATIC_DIM_RGB[0], CHROMATIC_DIM_RGB[1], CHROMATIC_DIM_RGB[2]);
  }

  function _paintOpenJamFull() {
    const key = _effectiveKey();
    const specs = [];
    for (let r = 0; r < 8; r++) {
      for (let c = 0; c < 8; c++) {
        specs.push(_openJamSpecForPad(r, c, key, _openJamActiveChordPCs));
      }
    }
    _sendLedSpecs(specs);
    _afterPaint();
  }

  // Diff-only update on chord change while in open-jam mode: repaint only
  // the pads whose PC just entered or left the sounding chord's PC set.
  function _updateOpenJamChord(prevPCs, nextPCs) {
    const key = _effectiveKey();
    const specs = [];
    const changed = new Set();
    for (const pc of prevPCs) if (!nextPCs.has(pc)) changed.add(pc);
    for (const pc of nextPCs) if (!prevPCs.has(pc)) changed.add(pc);
    if (changed.size === 0) return;
    for (let r = 0; r < 8; r++) {
      for (let c = 0; c < 8; c++) {
        const midi = _midiForPad(r, c);
        const pc = ((midi % 12) + 12) % 12;
        if (changed.has(pc)) specs.push(_openJamSpecForPad(r, c, key, nextPCs));
      }
    }
    _sendLedSpecs(specs);
    _afterPaint();
  }

  // ------------------------------------------------------------------
  // Theory Mode painting — full 8×8 grid, every pad colored by its
  // diatonic function in the current key. Uses the guitar-fourths pad
  // layout so it doubles as a playable scale grid (same physical
  // muscle memory as open-jam).
  //
  // Palette (matches jam.js documented design):
  //   I   — gold
  //   IV  — blue
  //   V   — green
  //   vi  — purple
  //   vii°— red
  //   ii, iii — desaturated purple (secondary functions)
  //   out-of-key — chromatic dim / off (respects _outOfKeyMode)
  // ------------------------------------------------------------------

  // Theory-mode degree palette. Indexed by 1-based degree (1..7); index
  // 0 is intentionally undefined. Slightly higher-contrast than the
  // open-jam DEGREE_RGB table because Theory is about function-recognition
  // rather than a chord-tone brightness boost on top of a base scale.
  const THEORY_DEGREE_RGB = {
    1: [127, 100, 0],   // I    — gold
    2: [55, 30, 90],    // ii   — dim purple
    3: [55, 30, 90],    // iii  — dim purple
    4: [0, 60, 127],    // IV   — blue
    5: [20, 120, 20],   // V    — green
    6: [90, 30, 120],   // vi   — bright purple
    7: [127, 20, 20],   // vii° — red
  };

  function _theorySpecForPad(row, col, key) {
    const midi = _midiForPad(row, col);
    const pc = ((midi % 12) + 12) % 12;
    const idx = _padIdx(row, col);
    const deg = _scaleDegreeInKey(pc, key);
    if (deg === null) {
      if (_outOfKeyMode === 'off') {
        _recordPad(idx, 0, 0, 0, 'off');
        return _offSpec(idx);
      }
      _recordPad(idx, CHROMATIC_DIM_RGB[0], CHROMATIC_DIM_RGB[1], CHROMATIC_DIM_RGB[2], 'static');
      return _rgbSpec(idx, CHROMATIC_DIM_RGB[0], CHROMATIC_DIM_RGB[1], CHROMATIC_DIM_RGB[2]);
    }
    const rgb = THEORY_DEGREE_RGB[deg] || THEORY_DEGREE_RGB[1];
    // Chord tones of the currently sounding chord (if any) get a
    // brightness boost so the theory grid also communicates "these are
    // the notes in the chord you're hearing right now".
    const inChord = _openJamActiveChordPCs.has(pc);
    const k = inChord ? 1.0 : 0.55;
    const [rr, gg, bb] = _scaledRgb(rgb, k);
    _recordPad(idx, rr, gg, bb, inChord ? 'active' : 'static');
    return _rgbSpec(idx, rr, gg, bb);
  }

  function _paintTheoryFull() {
    const key = _effectiveKey();
    const specs = [];
    for (let r = 0; r < 8; r++) {
      for (let c = 0; c < 8; c++) {
        specs.push(_theorySpecForPad(r, c, key));
      }
    }
    _sendLedSpecs(specs);
    _afterPaint();
  }

  // Free-play: raw scale grid, every in-key pad lit dim, no highlighting.
  // Purely a "what note is under my finger" reference — no anticipation,
  // no chord-tone boost.
  function _paintFreePlayFull() {
    const key = _effectiveKey();
    const specs = [];
    for (let r = 0; r < 8; r++) {
      for (let c = 0; c < 8; c++) {
        const midi = _midiForPad(r, c);
        const pc = ((midi % 12) + 12) % 12;
        const idx = _padIdx(r, c);
        const deg = _scaleDegreeInKey(pc, key);
        if (deg === null) {
          if (_outOfKeyMode === 'off') {
            specs.push(_offSpec(idx));
            _recordPad(idx, 0, 0, 0, 'off');
          } else {
            specs.push(_rgbSpec(idx, CHROMATIC_DIM_RGB[0], CHROMATIC_DIM_RGB[1], CHROMATIC_DIM_RGB[2]));
            _recordPad(idx, CHROMATIC_DIM_RGB[0], CHROMATIC_DIM_RGB[1], CHROMATIC_DIM_RGB[2], 'static');
          }
        } else {
          const [rr, gg, bb] = _scaledRgb([40, 40, 40], 1);
          specs.push(_rgbSpec(idx, rr, gg, bb));
          _recordPad(idx, rr, gg, bb, 'static');
        }
      }
    }
    _sendLedSpecs(specs);
    _afterPaint();
  }

  // ------------------------------------------------------------------
  // Instrument submode painters — chord-tone boost + next-chord
  // anticipation, painted on a guitar-fourths note grid. The submode
  // selects which subset of pads gets highlighted:
  //   'synth'   — full chord tones (root + third + fifth + seventh)
  //   'bass'    — roots + fifths only, rows 0..3 (low half of grid)
  //   'melody'  — root + third + fifth as landmark notes
  //   'drum'    — stub: fixed 4×4 kit on bottom-left quadrant
  //   'free-play' handled separately by _paintFreePlayFull
  // ------------------------------------------------------------------

  function _instrumentHighlightPCs(sym, submode) {
    if (!sym) return new Set();
    const p = _parseChordSymbol(sym);
    if (!p) return new Set();
    const add = (pcs, semi) => pcs.add((p.rootPc + semi + 12) % 12);
    const pcs = new Set();
    if (submode === 'bass') {
      // Root only. Bass players do use root+fifth patterns, but for a
      // learning grid the single lit pitch class reads unambiguously as
      // "play this note now" instead of "here are chord tones to pick
      // from". Other in-key notes remain visible (dim) so users can
      // still walk between roots when they want.
      add(pcs, 0);
      return pcs;
    }
    if (submode === 'melody') {
      add(pcs, 0);
      switch (p.quality) {
        case 'min': case 'min7': case 'dim': add(pcs, 3); break;
        case 'aug': add(pcs, 4); add(pcs, 8); break;
        default: add(pcs, 4); break;
      }
      add(pcs, 7);
      return pcs;
    }
    // 'synth' — full chord tones.
    return _chordPitchClasses(sym);
  }

  function _paintInstrumentFull() {
    const submode = _instrumentSubmode;
    if (submode === 'drum') {
      _paintDrumStubFull();
      return;
    }
    const key = _effectiveKey();
    // Current-chord symbol drives the highlight set. Grab from
    // _openJamActiveChordPCs' driving chord symbol via _songChords if
    // present, otherwise no highlight.
    const activeSym = _lastActiveSymbol;
    const highlightPCs = _instrumentHighlightPCs(activeSym, submode);
    const specs = [];
    // Restrict painted rows for 'bass' to the bottom 4 rows.
    const rowStart = submode === 'bass' ? 0 : 0;
    const rowEnd = submode === 'bass' ? 4 : 8;
    for (let r = 0; r < 8; r++) {
      for (let c = 0; c < 8; c++) {
        const idx = _padIdx(r, c);
        if (r < rowStart || r >= rowEnd) {
          specs.push(_offSpec(idx));
          _recordPad(idx, 0, 0, 0, 'off');
          continue;
        }
        const midi = _midiForPad(r, c);
        const pc = ((midi % 12) + 12) % 12;
        const inHighlight = highlightPCs.has(pc);
        if (inHighlight) {
          const rgb = pc === key.root ? ROOT_RGB : CHORD_TONE_RGB;
          specs.push(_rgbSpec(idx, rgb[0], rgb[1], rgb[2]));
          _recordPad(idx, rgb[0], rgb[1], rgb[2], 'active');
          continue;
        }
        const deg = _scaleDegreeInKey(pc, key);
        if (deg === null) {
          if (_outOfKeyMode === 'off') {
            specs.push(_offSpec(idx));
            _recordPad(idx, 0, 0, 0, 'off');
          } else {
            specs.push(_rgbSpec(idx, CHROMATIC_DIM_RGB[0], CHROMATIC_DIM_RGB[1], CHROMATIC_DIM_RGB[2]));
            _recordPad(idx, CHROMATIC_DIM_RGB[0], CHROMATIC_DIM_RGB[1], CHROMATIC_DIM_RGB[2], 'static');
          }
        } else {
          const rgb = DEGREE_RGB[deg - 1] || DEGREE_RGB[0];
          const [rr, gg, bb] = _scaledRgb(rgb, 0.4);
          specs.push(_rgbSpec(idx, rr, gg, bb));
          _recordPad(idx, rr, gg, bb, 'static');
        }
      }
    }
    // Anticipation ramp: if a next chord is known and we're within the
    // anticipation window, pulse its chord tones on the same grid.
    // Independent of assist level except at Blind (level 4).
    if (_assistLevel <= 3 && _anticipationBeats > 0
        && _beat.nextSymbol && _beat.beatsUntilNext !== null
        && _beat.beatsUntilNext <= _anticipationBeats) {
      const nextPCs = _instrumentHighlightPCs(_beat.nextSymbol, submode);
      // Choose pulse palette from the next chord's family.
      const np = _parseChordSymbol(_beat.nextSymbol);
      const nextFamily = np ? _familyForQuality(np.quality) : 'other';
      const palette = FAMILY_PULSE_PALETTE[nextFamily] || FAMILY_PULSE_PALETTE.other;
      const pulseSpecs = [];
      for (let r = rowStart; r < rowEnd; r++) {
        for (let c = 0; c < 8; c++) {
          const midi = _midiForPad(r, c);
          const pc = ((midi % 12) + 12) % 12;
          if (!nextPCs.has(pc)) continue;
          // Don't pulse pads that are already highlighted as current-chord
          // (would blank them). Just pulse the "future" tones.
          if (highlightPCs.has(pc)) continue;
          const idx = _padIdx(r, c);
          // Hard-blink for a strong visual cue on hardware.
          pulseSpecs.push(_flashSpec(idx, palette, 0));
          const rgb = PULSE_PALETTE_RGB[palette] || [64, 64, 64];
          _recordPad(idx, rgb[0], rgb[1], rgb[2], 'pulse');
        }
      }
      if (pulseSpecs.length) _sendLedSpecs(pulseSpecs);
    }
    _sendLedSpecs(specs);
    _afterPaint();
  }

  // Drum stub — a 4×4 in the bottom-left corner, distinct colors per
  // pad. Real sample playback is a future task; for now this exists
  // just so the mode boots to a legible grid.
  const DRUM_STUB_RGB = [
    [127, 40, 40],   // kick
    [40, 40, 127],   // snare
    [40, 127, 40],   // clap
    [127, 100, 40],  // hat
  ];
  function _paintDrumStubFull() {
    const specs = [];
    for (let r = 0; r < 8; r++) {
      for (let c = 0; c < 8; c++) {
        const idx = _padIdx(r, c);
        if (r < 4 && c < 4) {
          const i = (r % 2) * 2 + (c % 2);
          const rgb = DRUM_STUB_RGB[i];
          specs.push(_rgbSpec(idx, rgb[0], rgb[1], rgb[2]));
          _recordPad(idx, rgb[0], rgb[1], rgb[2], 'static');
        } else {
          specs.push(_offSpec(idx));
          _recordPad(idx, 0, 0, 0, 'off');
        }
      }
    }
    _sendLedSpecs(specs);
    _afterPaint();
  }

  // Track the last symbol driven through onActiveChordChanged so
  // instrument painters can highlight the currently-sounding chord's
  // tones without needing the caller to re-pass the symbol.
  let _lastActiveSymbol = null;

  // ------------------------------------------------------------------
  // Contribute — Sample mode paint + pad→chop map
  // ------------------------------------------------------------------
  //
  // chopIdx layout on the 8×8 grid — top row (padIdx 81..88) is
  // reserved for the countdown/anticipation bar, so chops occupy
  // rows_from_top 1..7 (56 pads total):
  //   chop 0  = second row L (row_from_top=1, col=0 → padIdx 71)
  //   chop 7  = second row R (row_from_top=1, col=7 → padIdx 78)
  //   chop 8  = third row L  (row_from_top=2, col=0 → padIdx 61)
  //   chop 55 = bottom-right (row_from_top=7, col=7 → padIdx 18)
  //
  // Reads top-to-bottom the way English text does. Chops beyond
  // index 55 are dropped. Colors come from the backend's
  // ``colorHint`` field via ``CONTRIBUTE_PALETTE``.
  const CONTRIBUTE_PAD_COUNT = 56;

  function _padIdxForChop(chopIdx) {
    const rowFromTop = 1 + Math.floor(chopIdx / 8);
    const col = chopIdx % 8;
    const row = 7 - rowFromTop; // _padIdx expects bottom-up rows
    return _padIdx(row, col);
  }

  function _chopIdxForPad(padIdx) {
    const tens = Math.floor(padIdx / 10);
    const ones = padIdx % 10;
    if (tens < 1 || tens > 8 || ones < 1 || ones > 8) return -1;
    // Top row is reserved for the countdown bar — no chop mapping.
    if (tens === 8) return -1;
    const row = tens - 1;              // 0..7 bottom-up
    const col = ones - 1;              // 0..7 left-right
    const rowFromTop = 7 - row;        // 1..7 for chop rows
    return (rowFromTop - 1) * 8 + col;
  }

  function _paintContributeFull() {
    const specs = [];
    const chops = _contributeChops || [];
    // Top row (padIdx 81..88) is reserved for the countdown/anticipation
    // bar. Paint it OFF so the countdown can take over without visible
    // leftover chop colors underneath.
    for (let col = 0; col < 8; col++) {
      const padIdx = COUNTDOWN_BASE_PAD + col;
      specs.push([LED_STATIC, padIdx, 0]);
      _recordPad(padIdx, 0, 0, 0, 'off');
    }
    for (let i = 0; i < CONTRIBUTE_PAD_COUNT; i++) {
      const padIdx = _padIdxForChop(i);
      const chop = chops[i];
      if (!chop) {
        // Empty slot — paint a very dim gray so the pad reads as
        // "present but unusable" rather than looking like a broken
        // LED. Same brightness as the muted-hold state.
        const grayRgb = CONTRIBUTE_PALETTE.gray;
        const er = Math.max(1, (grayRgb[0] * _DISABLED_DIM) | 0);
        const eg = Math.max(1, (grayRgb[1] * _DISABLED_DIM) | 0);
        const eb = Math.max(1, (grayRgb[2] * _DISABLED_DIM) | 0);
        specs.push([LED_RGB, padIdx, er, eg, eb]);
        _recordPad(padIdx, er, eg, eb, 'disabled');
        continue;
      }
      const hint = chop.colorHint || 'gray';
      const rgb = CONTRIBUTE_PALETTE[hint] || CONTRIBUTE_PALETTE.gray;
      const disabled = _disabledChopPads.has(padIdx);
      const r = disabled ? Math.max(1, (rgb[0] * _DISABLED_DIM) | 0) : rgb[0];
      const g = disabled ? Math.max(1, (rgb[1] * _DISABLED_DIM) | 0) : rgb[1];
      const b = disabled ? Math.max(1, (rgb[2] * _DISABLED_DIM) | 0) : rgb[2];
      specs.push([LED_RGB, padIdx, r, g, b]);
      _recordPad(padIdx, r, g, b, disabled ? 'disabled' : 'static');
    }
    _sendLedSpecs(specs);
    _afterPaint();
  }

  // ------------------------------------------------------------------
  // MIDI input dispatch (pad presses)
  // ------------------------------------------------------------------

  // Compute the semantic meaning of a pad press given the current mode.
  // Extracted so both incoming MIDI note-ons and on-screen click-to-play
  // presses go through the exact same interpretation, avoiding drift.
  function _meaningForPad(padIdx) {
    const tens = Math.floor(padIdx / 10), ones = padIdx % 10;
    if (tens < 1 || tens > 8 || ones < 1 || ones > 8) return null;
    if (_mode === 'contribute-sample') {
      const chopIdx = _chopIdxForPad(padIdx);
      const chop = (chopIdx >= 0) ? _contributeChops[chopIdx] : null;
      return chop
        ? { kind: 'chop', chopIdx, chop }
        : { kind: 'other' };
    }
    if (_mode === 'song-verify' || _mode === 'song-display' || _mode === 'display') {
      const key = _reverse.get(padIdx);
      const info = key ? _assignment.get(key) : null;
      return info
        ? { kind: 'chord', symbol: info.symbol, canonicalKey: key, family: info.family }
        : { kind: 'other' };
    }
    // Note-grid modes: theory, all instrument submodes, free-play.
    if (_mode === 'theory' || _mode === 'free-play' || _mode.startsWith('instrument-')) {
      // Drum submode uses a fixed 4×4 pad→slot mapping in the bottom-left.
      if (_mode === 'instrument-drum') {
        if (tens <= 4 && ones <= 4) {
          const row = tens - 1, col = ones - 1;
          const slot = (row % 2) * 2 + (col % 2);
          return { kind: 'drum', slot };
        }
        return { kind: 'other' };
      }
      const midi = OPEN_JAM_BASE_MIDI + (tens - 1) * 5 + (ones - 1);
      const pc = ((midi % 12) + 12) % 12;
      const key = _effectiveKey();
      const deg = _scaleDegreeInKey(pc, key);
      return { kind: 'note', midi, pitchClass: pc, degree: deg };
    }
    return { kind: 'other' };
  }

  function _onMidi(evt) {
    const data = evt.data;
    if (!data || data.length < 2) return;
    const status = data[0] & 0xf0;
    if (status === 0xb0) {
      // Control Change — used by the ring of round buttons around the
      // pad grid in Programmer Mode (top/bottom rows + side columns +
      // dedicated transport buttons). Value 0 = release, non-zero = press.
      const cc = data[1];
      const value = data[2] || 0;
      // Verbose log so the caller can identify which CC each button
      // fires on their unit (Novation's docs are inconsistent across
      // MK3 firmware revisions).
      console.log('[launchpad] cc', cc, 'value', value);
      if (typeof _ccMessageCb === 'function') {
        try { _ccMessageCb({ cc, value }); }
        catch (e) { console.warn('[launchpad] cc callback threw:', e); }
      }
      return;
    }
    // Note Off (status 0x80) and Note On with velocity 0 both count
    // as a release. The Launchpad Pro MK3 uses the latter shape when
    // pads are released in Programmer Mode. Dispatch to the release
    // callback so jam.js can cancel any pending hold-timer for the
    // pad and clean up the retrigger bookkeeping.
    if (status === 0x80 || (status === 0x90 && (data[2] || 0) === 0)) {
      const note = data[1];
      const meaning = _meaningForPad(note);
      if (meaning && typeof _padReleaseCb === 'function') {
        try {
          _padReleaseCb({ padIdx: note, note, meaning });
        } catch (e) {
          console.warn('[launchpad] pad-release callback threw:', e);
        }
      }
      return;
    }
    if (status !== 0x90) return; // Note On only
    const velocity = data[2] || 0;
    if (velocity === 0) return;  // defensive: should be handled above
    const note = data[1];
    const meaning = _meaningForPad(note);
    if (!meaning) {
      // Not a grid pad — log so the caller can identify function-row
      // / side-column / transport-note buttons that fire outside 11..88.
      console.log('[launchpad] note (non-grid)', note, 'vel', velocity);
      if (typeof _ccMessageCb === 'function') {
        // Dispatch as a synthetic "cc" so a single callback covers both
        // Note-based and CC-based buttons around the grid. Note-based
        // buttons get cc = note + 1000 so they can't collide with real CCs.
        try { _ccMessageCb({ cc: note + 1000, note, value: velocity, kind: 'note' }); }
        catch (e) { console.warn('[launchpad] cc callback threw:', e); }
      }
      return;
    }
    if (typeof _padPressCb === 'function') {
      try {
        _padPressCb({ padIdx: note, note, velocity, meaning });
      } catch (e) {
        console.warn('[launchpad] pad-press callback threw:', e);
      }
    }
  }

  // ------------------------------------------------------------------
  // MIDI port binding + lifecycle
  // ------------------------------------------------------------------

  function _matchesLaunchpad(port) {
    if (!port || !port.name) return false;
    const n = port.name.toLowerCase();
    return DEVICE_NAME_HINTS.some(hint => n.includes(hint));
  }

  function _portPreferenceRank(port) {
    const n = (port.name || '').toLowerCase();
    for (let i = 0; i < PORT_PREFERENCE.length; i++) {
      if (n.includes(PORT_PREFERENCE[i])) return i;
    }
    return PORT_PREFERENCE.length;
  }

  function _scanPorts() {
    if (!_access) return { input: null, output: null };
    const ins = [];
    const outs = [];
    _access.inputs.forEach(p => { if (_matchesLaunchpad(p)) ins.push(p); });
    _access.outputs.forEach(p => { if (_matchesLaunchpad(p)) outs.push(p); });
    ins.sort((a, b) => _portPreferenceRank(a) - _portPreferenceRank(b));
    outs.sort((a, b) => _portPreferenceRank(a) - _portPreferenceRank(b));
    return { input: ins[0] || null, output: outs[0] || null };
  }

  function _detachInput() {
    if (_input) {
      try { _input.onmidimessage = null; } catch (_) {}
    }
    _input = null;
  }

  function _bindPorts() {
    const { input, output } = _scanPorts();
    if (!input || !output) {
      _detachInput();
      _output = null;
      _deviceName = null;
      _emitStatus({ error: 'device_not_connected' });
      return false;
    }
    _detachInput();
    _input = input;
    _output = output;
    _deviceName = output.name || input.name || 'Launchpad Pro MK3';
    try {
      _input.onmidimessage = _onMidi;
    } catch (e) {
      console.warn('[launchpad] failed to attach onmidimessage:', e);
    }
    _send(SYSEX_ENTER_PROGRAMMER);
    _emitStatus({});
    // Immediately paint the current mode's grid so the device isn't
    // left blank between connect and the next chord transition.
    _repaintForMode();
    // Light the Play transport button dim green so the user has a
    // clear "you can start the song from here" affordance the moment
    // the device connects, without having to touch the mouse first.
    _lastPlayingState = null;
    _paintPlayButton(false);
    return true;
  }

  function _repaintForMode() {
    _activePadIdx = null;
    _nextPadIdx = null;
    // Assist-level 4 (Blind): paint nothing regardless of mode.
    if (_assistLevel >= 4) {
      _clearAllPads();
      return;
    }
    if (_mode === 'song-verify' || _mode === 'song-display' || _mode === 'display') {
      _paintSongGridFull();
    } else if (_mode === 'theory') {
      _paintTheoryFull();
    } else if (_mode === 'free-play') {
      _paintFreePlayFull();
    } else if (_mode.startsWith('instrument-')) {
      _paintInstrumentFull();
    } else if (_mode === 'contribute-sample') {
      _paintContributeFull();
    } else if (_mode === 'open-jam') {
      // Legacy path retained in case callers still poke it directly.
      _paintOpenJamFull();
    } else {
      _clearAllPads();
    }
  }

  function _onStateChange(_evt) {
    // Re-scan on any port change. If we lose our bound ports mid-session,
    // null them out and emit disconnected status.
    const bound = _output && _input;
    const { input, output } = _scanPorts();
    if (!bound && input && output && _enabled) {
      _bindPorts();
      return;
    }
    if (bound && (!input || !output)) {
      _detachInput();
      _output = null;
      _deviceName = null;
      _emitStatus({ error: 'disconnected' });
    }
  }

  async function _requestAccess() {
    if (!navigator || !navigator.requestMIDIAccess) {
      _emitStatus({ supported: false });
      return false;
    }
    try {
      _access = await navigator.requestMIDIAccess({ sysex: true });
    } catch (e) {
      console.warn('[launchpad] requestMIDIAccess rejected:', e);
      _access = null;
      _emitStatus({ error: 'permission_denied' });
      return false;
    }
    try { _access.onstatechange = _onStateChange; } catch (_) {}
    return true;
  }

  // ------------------------------------------------------------------
  // Public API
  // ------------------------------------------------------------------

  const api = {
    init(opts) {
      if (_initDone) {
        // Allow late callback registration if the panel initialises
        // after the main enable flow (order-of-DOMContentLoaded).
        if (opts) {
          if (opts.onStatusChange && !_statusCb) _statusCb = opts.onStatusChange;
          if (opts.onPadPress && !_padPressCb) _padPressCb = opts.onPadPress;
          if (opts.onPadRelease && !_padReleaseCb) _padReleaseCb = opts.onPadRelease;
          if (opts.onGridChange && !_gridChangeCb) _gridChangeCb = opts.onGridChange;
          if (opts.onModeChange && !_modeChangeCb) _modeChangeCb = opts.onModeChange;
          if (opts.onLegendInfo && !_legendInfoCb) _legendInfoCb = opts.onLegendInfo;
          if (opts.onCcMessage && !_ccMessageCb) _ccMessageCb = opts.onCcMessage;
        }
        return Promise.resolve(_emitStatus({}));
      }
      _initDone = true;
      _statusCb = (opts && opts.onStatusChange) || null;
      _padPressCb = (opts && opts.onPadPress) || null;
      _padReleaseCb = (opts && opts.onPadRelease) || null;
      _gridChangeCb = (opts && opts.onGridChange) || null;
      _modeChangeCb = (opts && opts.onModeChange) || null;
      _legendInfoCb = (opts && opts.onLegendInfo) || null;
      _ccMessageCb = (opts && opts.onCcMessage) || null;
      // Leave Programmer Mode cleanly on unload.
      try {
        window.addEventListener('beforeunload', () => {
          try { api.disable(); } catch (_) {}
        });
      } catch (_) {}
      return Promise.resolve(_emitStatus({}));
    },

    async enable() {
      _enabled = true;
      if (!_access) {
        const ok = await _requestAccess();
        if (!ok) { _enabled = false; return _emitStatus({}); }
      }
      _bindPorts();
      return _emitStatus({});
    },

    disable() {
      _enabled = false;
      if (_output) {
        _blankTopRow();
        _blankTransportButtons();
        _blankAllSceneLaunch();
        _blankAllTopRow();
        _clearAllPads();
        _send(SYSEX_EXIT_PROGRAMMER);
      }
      _detachInput();
      _output = null;
      _deviceName = null;
      _activePadIdx = null;
      _nextPadIdx = null;
      _emitStatus({});
    },

    isSupported() { return !!(navigator && navigator.requestMIDIAccess); },
    isConnected() { return !!_output && !!_input; },
    getStatus() { return _emitStatus({}); },
    getMode() { return _mode; },

    // Paint a single LED by its Programmer-Mode index — works for pads
    // (11..88) AND the ring of round buttons (top row 91..98, right
    // column 89..19, bottom row 1..8, left column 80..10). Used by
    // jam.js to light auxiliary buttons like the octave arrows.
    // r,g,b are 0..127 (SysEx range). No-op when disconnected.
    paintButton(index, r, g, b) {
      if (!_output) return;
      const idx = index | 0;
      if (idx < 1 || idx > 99) return;
      _sendLedSpecs([_rgbSpec(idx, r | 0, g | 0, b | 0)]);
    },
    blankButton(index) {
      if (!_output) return;
      const idx = index | 0;
      if (idx < 1 || idx > 99) return;
      _sendLedSpecs([_offSpec(idx)]);
    },

    setMode(mode) {
      const allowed = [
        'off',
        'song-verify', 'song-display',
        'theory',
        'instrument-synth', 'instrument-bass', 'instrument-drum', 'instrument-melody',
        'free-play',
        'display',
        // Contribute (song-sourced sample) mode. Pad meanings are
        // { kind: 'chop', chopIdx, chop } and jam.js handles the
        // sample playback pipeline.
        'contribute-sample',
        // Legacy alias kept so old panel wiring doesn't silently fail.
        'open-jam',
      ];
      if (!allowed.includes(mode)) return;
      // Migrate legacy 'open-jam' → 'instrument-synth' at the boundary
      // so all internal painters see the new taxonomy.
      if (mode === 'open-jam') mode = 'instrument-synth';
      if (mode === _mode) return;
      _mode = mode;
      // Keep the submode-hint pointer in sync with the mode enum so
      // getLegendInfo / painters can key off _instrumentSubmode without
      // parsing _mode.
      if (mode.startsWith('instrument-')) {
        _instrumentSubmode = mode.slice('instrument-'.length);
      } else if (mode === 'free-play') {
        _instrumentSubmode = 'free-play';
      }
      _repaintForMode();
      _emitStatus({});
      if (typeof _modeChangeCb === 'function') {
        try { _modeChangeCb(_mode); } catch (_) {}
      }
      if (typeof _legendInfoCb === 'function') {
        try { _legendInfoCb(api.getLegendInfo()); } catch (_) {}
      }
    },

    // Layout picks the chord-tile spatial strategy for song modes.
    // 'theory' — functional circle-of-fifths grid (default).
    // 'song'   — encounter-order: pad 11 = first chord met.
    // 'pitch'  — piano-like: rows = root pitch (bottom = tonic, top =
    //            octave), columns = quality. Rising bass → rising row.
    setLayout(layout) {
      if (layout !== 'theory' && layout !== 'song' && layout !== 'pitch') return;
      if (layout === _layout) return;
      _layout = layout;
      // Re-run the assignment with the same chord list so the pads move.
      if (_songChords.length) {
        const { assignment, reverse, overflow } = _assignChords(_songChords, _songKey);
        _assignment = assignment;
        _reverse = reverse;
        if (overflow.length) {
          console.warn('[launchpad] chord overflow after layout change:', overflow);
        }
      }
      if (_mode === 'song-verify' || _mode === 'song-display' || _mode === 'display') {
        _paintSongGridFull();
      }
    },

    // Assist level 1..4. See jam.js state.settings.launchpadAssistLevel
    // for semantics.
    setAssistLevel(level) {
      const v = level | 0;
      if (v < 1 || v > 4) return;
      if (v === _assistLevel) return;
      _assistLevel = v;
      _repaintForMode();
    },

    // Anticipation window in beats (0..8). 0 disables anticipation.
    setAnticipationBeats(beats) {
      if (typeof beats !== 'number' || beats < 0 || beats > 8) return;
      _anticipationBeats = beats;
    },

    // Explicit instrument-submode setter — the mode enum can also be
    // pushed via setMode('instrument-<sub>'), but this lets the panel
    // change submode without cycling the mode.
    setInstrumentSubmode(submode) {
      const allowed = new Set(['synth', 'bass', 'drum', 'melody', 'free-play']);
      if (!allowed.has(submode)) return;
      _instrumentSubmode = submode;
      if (_mode.startsWith('instrument-')) {
        _mode = 'instrument-' + submode;
        _repaintForMode();
        if (typeof _modeChangeCb === 'function') {
          try { _modeChangeCb(_mode); } catch (_) {}
        }
        if (typeof _legendInfoCb === 'function') {
          try { _legendInfoCb(api.getLegendInfo()); } catch (_) {}
        }
      }
    },

    // Beat-tick hook. jam.js calls this on every beat with:
    //   beatIdx           — global beat counter (monotonic)
    //   beatsUntilNext    — how many beats until the *next* chord change
    //                       (fractional allowed). null if unknown.
    //   nextSymbol        — the upcoming chord symbol (string) or null
    // Painters that support anticipation read this state on their next
    // repaint. Song mode already uses the hardware pulse palette so we
    // don't have to do continuous ramp work; instrument painters
    // repaint here to re-run their anticipation loop.
    onBeatTick(info) {
      if (!info) return;
      _beat.beatIdx = info.beatIdx | 0;
      _beat.beatsUntilNext = (typeof info.beatsUntilNextChord === 'number')
        ? info.beatsUntilNextChord : null;
      _beat.nextSymbol = info.nextSymbol || null;
      // Only instrument painters currently ramp per-beat; song mode
      // handles its own pulse via hardware.
      if (_mode.startsWith('instrument-') && _mode !== 'instrument-drum') {
        _paintInstrumentFull();
      }
    },

    // ---- Song mode ----
    onChordsLoaded(chords) {
      _songChords = Array.isArray(chords) ? chords : [];
      const { assignment, reverse, overflow } = _assignChords(_songChords, _songKey);
      _assignment = assignment;
      _reverse = reverse;
      if (overflow.length) {
        console.warn('[launchpad] chord overflow (>64 unique):', overflow);
      }
      if (_mode === 'song-verify' || _mode === 'song-display' || _mode === 'display') {
        _paintSongGridFull();
      }
    },

    // Called by jam.js on every chord transition. Updates painters that
    // key off the currently-sounding chord: song grid diff, open-jam
    // chord-tone boost, theory-mode chord-tone overlay, instrument
    // submode current-chord highlight.
    onActiveChordChanged(activeIdx, chords) {
      const list = chords || _songChords;
      const sym = (activeIdx >= 0 && list && list[activeIdx])
        ? list[activeIdx].symbol : null;
      _lastActiveSymbol = sym;
      if (_mode === 'song-verify' || _mode === 'song-display' || _mode === 'display') {
        _updateSongActive(activeIdx, list);
        return;
      }
      // Note-grid modes share the same "current chord pitch class set"
      // input signal, so compute it once and dispatch.
      const nextPCs = sym ? _chordPitchClasses(sym) : new Set();
      if (_mode === 'open-jam') {
        _updateOpenJamChord(_openJamActiveChordPCs, nextPCs);
        _openJamActiveChordPCs = nextPCs;
        return;
      }
      if (_mode === 'theory') {
        _openJamActiveChordPCs = nextPCs;
        _paintTheoryFull();
        return;
      }
      if (_mode.startsWith('instrument-')) {
        _openJamActiveChordPCs = nextPCs;
        _paintInstrumentFull();
        return;
      }
      // free-play / display don't react to chord changes.
    },

    onSongUnloaded() {
      _songChords = [];
      _assignment = new Map();
      _reverse = new Map();
      _activePadIdx = null;
      _nextPadIdx = null;
      _openJamActiveChordPCs = new Set();
      _lastActiveSymbol = null;
      _beat.beatsUntilNext = null;
      _beat.nextSymbol = null;
      _blankTopRow();
      _repaintForMode();
    },

    // Called from jam.js RAF. `progress` ∈ [0, 1] fills the top-row
    // countdown bar left→right; pass null to blank it (e.g. no song or
    // no upcoming distinct chord). `imminent` flashes the leading pad.
    setCountdownProgress(progress, imminent) {
      _paintCountdownBar(progress, imminent);
    },

    // Called from jam.js on every play/pause/stop edge so the hardware
    // Play button reflects transport state. Bright green when running,
    // dim green when idle. No-op if the module isn't bound to a device.
    setTransportPlaying(playing) {
      _paintPlayButton(!!playing);
    },

    // ---- Key / scale ----
    onSongKey(songKey) {
      _songKey = songKey || null;
      // Key changes affect: song-mode pad assignment (theory layout only),
      // and every note-grid painter's degree colouring.
      if ((_mode === 'song-verify' || _mode === 'song-display' || _mode === 'display')
          && _songChords.length) {
        const { assignment, reverse } = _assignChords(_songChords, _songKey);
        _assignment = assignment;
        _reverse = reverse;
        _paintSongGridFull();
      } else {
        _repaintForMode();
      }
    },

    setOpenJamRoot(rootPc, scale) {
      if (typeof rootPc === 'number') _manualRoot = ((rootPc % 12) + 12) % 12;
      if (scale === 'Major' || scale === 'Minor') _manualScale = scale;
      // Any note-grid painter needs to update on key change.
      if (!_songKey || typeof _songKey.root !== 'number') {
        if (_mode === 'open-jam' || _mode === 'theory' || _mode === 'free-play'
            || _mode.startsWith('instrument-')) {
          _repaintForMode();
        }
      }
    },

    setOpenJamOutOfKey(mode) {
      if (mode === 'off' || mode === 'dim') {
        _outOfKeyMode = mode;
        _repaintForMode();
      }
    },

    // Push the chop list for Contribute — Sample mode. jam.js fetches
    // the metadata from /api/song/{id}/chops and hands it here. Each
    // entry is a chop dict from the backend ``build_chops`` slicer.
    // Up to 64 entries are used; the rest are ignored. Calling this
    // when the current mode isn't 'contribute-sample' just stores the
    // list — the paint happens on the next setMode('contribute-sample')
    // call. When *is* in contribute-sample mode, repaints immediately.
    setChops(chops) {
      _contributeChops = Array.isArray(chops) ? chops.slice(0, CONTRIBUTE_PAD_COUNT) : [];
      // New chop list ⇒ old disabled marks no longer meaningful.
      _disabledChopPads.clear();
      if (_mode === 'contribute-sample') {
        _paintContributeFull();
      }
    },

    // Toggle a single contribute pad's disabled state. Called from
    // jam.js's hold-timer when a Contribute pad has been held long
    // enough to count as a mute. Repaints the pad grid so the pad
    // is rendered dim (or restored to full brightness on re-enable).
    setChopDisabled(padIdx, disabled) {
      if (disabled) _disabledChopPads.add(padIdx);
      else _disabledChopPads.delete(padIdx);
      if (_mode === 'contribute-sample') {
        _paintContributeFull();
      }
    },

    // Reports whether a given pad is currently muted. jam.js reads
    // this to decide whether a fresh press should retrigger or first
    // un-mute the pad. (Kept as a read accessor rather than exposing
    // the internal Set so consumers can't accidentally mutate it.)
    isChopDisabled(padIdx) {
      return _disabledChopPads.has(padIdx);
    },

    // Mark every chop pad disabled in one shot and repaint once.
    // Returns the array of pad indices that were disabled so the
    // caller can mirror them into its own bookkeeping (jam.js keeps
    // a parallel disabledPads Set for the press-to-unmute check).
    // Preferred over a loop of setChopDisabled() calls because that
    // would trigger 56 sequential repaints.
    disableAllChops() {
      const chops = _contributeChops || [];
      const pads = [];
      for (let i = 0; i < chops.length && i < CONTRIBUTE_PAD_COUNT; i++) {
        if (!chops[i]) continue;
        const padIdx = _padIdxForChop(i);
        _disabledChopPads.add(padIdx);
        pads.push(padIdx);
      }
      if (_mode === 'contribute-sample') _paintContributeFull();
      return pads;
    },

    // ---- Scene launch buttons (right-side column) ----
    //
    // These are the round buttons at col=9. In Contribute mode jam.js
    // uses them as per-row loop launchers: rows 1..7 toggle a loop
    // clip pulled from that row's chops; row 8 is the "stop all
    // loops" hotkey. Colors + on/off state are decided by jam.js —
    // this module just draws whatever RGB it's told.
    setSceneLaunchRgb(row, r, g, b) { _paintSceneLaunch(row, r, g, b); },
    blankAllSceneLaunch() { _blankAllSceneLaunch(); },

    // ---- Top row (utility buttons above the pad grid) ----
    //
    // Same shape as setSceneLaunchRgb but for CCs 91..98. jam.js
    // paints the first five as preset-picker LEDs (Vocal / Drums /
    // Bass / Harmonic / Sections) so the hardware surfaces which
    // preset is active.
    setTopRowRgb(col, r, g, b) { _paintTopRow(col, r, g, b); },
    blankAllTopRow() { _blankAllTopRow(); },

    // ---- On-screen mirror integration ----

    // Called by jam.js when the user clicks a pad on the on-screen mirror.
    // Routes through the exact same code path as an incoming hardware
    // Note On so the downstream synth + verifier see identical events.
    pressPadFromScreen(padIdx) {
      const meaning = _meaningForPad(padIdx);
      if (!meaning) return;
      if (typeof _padPressCb === 'function') {
        try {
          _padPressCb({ padIdx, note: padIdx, velocity: 100, meaning });
        } catch (e) {
          console.warn('[launchpad] pad-press callback threw (on-screen):', e);
        }
      }
    },

    // Called by jam.js on pointer-up over an on-screen mirror pad
    // so click-hold ⇒ mute works the same way as hardware press-hold.
    releasePadFromScreen(padIdx) {
      const meaning = _meaningForPad(padIdx);
      if (!meaning) return;
      if (typeof _padReleaseCb === 'function') {
        try {
          _padReleaseCb({ padIdx, note: padIdx, meaning });
        } catch (e) {
          console.warn('[launchpad] pad-release callback threw (on-screen):', e);
        }
      }
    },

    // Snapshot (by reference) of the internal per-pad color buffer for
    // the mirror UI. Consumers should not mutate the returned array.
    getGridColors() {
      return _gridColors;
    },

    // Human-friendly descriptor of what a click on `padIdx` would mean.
    // Used by the panel to build native title tooltips on each mirror pad.
    getPadMeaning(padIdx) {
      const meaning = _meaningForPad(padIdx);
      if (!meaning || meaning.kind === 'other') return null;
      if (meaning.kind === 'chord') {
        const key = _effectiveKey();
        // Derive the degree of the chord's root within the current key
        // (song mode reuses _songKey here) so tooltips can label
        // "Am — vi in C major".
        const p = _parseChordSymbol(meaning.symbol);
        let degreeLabel = null;
        if (p && key && typeof key.root === 'number') {
          const deg = _scaleDegreeInKey(p.rootPc, key);
          if (deg !== null) {
            const labels = key.scale === 'Minor' ? DEGREE_LABEL_MINOR : DEGREE_LABEL_MAJOR;
            degreeLabel = labels[deg - 1];
          }
        }
        return { kind: 'chord', symbol: meaning.symbol, family: meaning.family, degreeLabel };
      }
      if (meaning.kind === 'drum') {
        const names = ['Kick', 'Snare', 'Clap', 'Hat'];
        return { kind: 'drum', slot: meaning.slot, name: names[meaning.slot] || 'Drum' };
      }
      // Note-grid modes (open-jam / theory / instrument / free-play).
      const key = _effectiveKey();
      const labels = key.scale === 'Minor' ? DEGREE_LABEL_MINOR : DEGREE_LABEL_MAJOR;
      const degreeLabel = (meaning.degree !== null && meaning.degree !== undefined)
        ? labels[meaning.degree - 1]
        : null;
      const octave = Math.floor(meaning.midi / 12) - 1; // MIDI 60 = C4
      const noteName = PC_NAMES[meaning.pitchClass] + octave;
      return { kind: 'note', midi: meaning.midi, pitchClass: meaning.pitchClass, degreeLabel, noteName };
    },

    // Descriptor for the on-screen legend contents. Swaps content when
    // the mode changes; also fetched proactively by the panel at mount.
    getLegendInfo() {
      if (_mode === 'song-verify' || _mode === 'song-display' || _mode === 'display') {
        let layoutCaption;
        if (_layout === 'song') {
          layoutCaption = 'Encounter-order: pad 11 = first chord, wraps left→right, bottom→up.';
        } else if (_layout === 'pitch') {
          layoutCaption = 'Rows: root pitch (bottom = tonic, top = octave). Cols: quality.';
        } else {
          layoutCaption = 'Rows: I → vii° (circle of fifths). Cols: quality.';
        }
        return {
          kind: 'song',
          chips: [
            { name: 'Major',       rgb: FAMILY_RGB.major },
            { name: 'Minor',       rgb: FAMILY_RGB.minor },
            { name: 'Dominant 7',  rgb: FAMILY_RGB.dom7 },
            { name: 'Diminished',  rgb: FAMILY_RGB.dim },
            { name: 'Augmented',   rgb: FAMILY_RGB.aug },
            { name: 'Other',       rgb: FAMILY_RGB.other },
          ],
          caption: layoutCaption + ' Bright = now, pulse = next.',
        };
      }
      if (_mode === 'theory') {
        return {
          kind: 'theory',
          chips: [
            { name: 'I (tonic)',   rgb: THEORY_DEGREE_RGB[1] },
            { name: 'IV',          rgb: THEORY_DEGREE_RGB[4] },
            { name: 'V',           rgb: THEORY_DEGREE_RGB[5] },
            { name: 'vi',          rgb: THEORY_DEGREE_RGB[6] },
            { name: 'vii°',        rgb: THEORY_DEGREE_RGB[7] },
            { name: 'ii / iii',    rgb: THEORY_DEGREE_RGB[2] },
            { name: 'Out of key',  rgb: _outOfKeyMode === 'off' ? [0, 0, 0] : CHROMATIC_DIM_RGB },
          ],
          caption: 'Every pad colored by its diatonic function. Brighter = notes in the current chord.',
        };
      }
      if (_mode === 'free-play') {
        return {
          kind: 'free-play',
          chips: [
            { name: 'In-key note',  rgb: [80, 80, 80] },
            { name: 'Out of key',   rgb: _outOfKeyMode === 'off' ? [0, 0, 0] : CHROMATIC_DIM_RGB },
          ],
          caption: 'Raw scale grid. Rows +5 semitones. Bottom-left = E2.',
        };
      }
      if (_mode === 'instrument-drum') {
        return {
          kind: 'instrument-drum',
          chips: [
            { name: 'Kick',   rgb: DRUM_STUB_RGB[0] },
            { name: 'Snare',  rgb: DRUM_STUB_RGB[1] },
            { name: 'Clap',   rgb: DRUM_STUB_RGB[2] },
            { name: 'Hat',    rgb: DRUM_STUB_RGB[3] },
          ],
          caption: 'Drum pads on the bottom-left 4×4. Sample playback stub.',
        };
      }
      if (_mode.startsWith('instrument-')) {
        const sub = _mode.slice('instrument-'.length);
        const captions = {
          synth:  'Chord tones lit under your fingers; next-chord tones pulse in as the transition approaches.',
          bass:   'Roots + fifths on the bottom half of the grid. Next chord anticipates.',
          melody: 'Root, third, fifth landmarks. Ideal for improvising over the changes.',
        };
        return {
          kind: 'instrument',
          chips: [
            { name: 'Key root',   rgb: ROOT_RGB },
            { name: 'Chord tone', rgb: CHORD_TONE_RGB },
            { name: 'Next-chord', rgb: PULSE_PALETTE_RGB[13] },
            { name: 'Scale',      rgb: DEGREE_RGB[1] },
            { name: 'Out of key', rgb: _outOfKeyMode === 'off' ? [0, 0, 0] : CHROMATIC_DIM_RGB },
          ],
          caption: captions[sub] || 'Instrument mode.',
        };
      }
      return { kind: 'off', chips: [], caption: 'Launchpad is off — pick a mode above to see the grid.' };
    },
  };

  // Exposed helpers for jam.js verifier (canonical symbol match).
  api._canonicalChordKey = _canonicalChordKey;

  window.Launchpad = api;
})();
