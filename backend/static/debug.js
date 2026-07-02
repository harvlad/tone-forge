/*
 * debug.js
 *
 * ToneForge Debug — single-page ML/corpus inspector.
 *
 * Three tabs:
 *   inspector : per-session view of guidance_mode decisions + raw per-stem
 *               SectionFeatures (radar + table + landmark-notes piano roll)
 *   corpus    : ground-truth-vs-predicted across song_trial_corpus.json
 *               (confusion matrix + macro-F1 + per-song status)
 *   history   : browse data/history.json with aggregate histograms
 *
 * Vanilla IIFE pattern matching jam.js — no build step, no framework,
 * no chart library. All charts are hand-rolled inline SVG.
 *
 * Data sources:
 *   GET /api/debug/sessions  → session picker catalog
 *   GET /api/session/:id     → full SessionBundle (carries debug_features
 *                              after the engine-fix-debug-#1 schema bump)
 *   GET /api/debug/corpus    → raw song_trial_corpus.json
 *   GET /api/history         → history list for the History tab
 */

(() => {
  'use strict';

  // -------------------------------------------------------- DOM helpers
  const $ = (id) => document.getElementById(id);
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const el = (svg, tag, attrs = {}, parent = null) => {
    const node = document.createElementNS(svg ? SVG_NS : null, tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined) continue;
      node.setAttribute(k, String(v));
    }
    if (parent) parent.appendChild(node);
    return node;
  };
  const svgEl = (tag, attrs, parent) => el(true, tag, attrs, parent);

  // -------------------------------------------------------- constants
  const MODES = ['chord', 'riff', 'lead'];
  const MODE_COLOR = {
    chord: 'var(--guidance-chord)',
    riff:  'var(--guidance-riff)',
    lead:  'var(--guidance-lead)',
  };
  // The 6 axes shown on the radar — kept in this order so the polygon
  // shape is comparable across sections. Each entry has the persisted
  // feature key plus a short label that fits on the chart without
  // crashing into the polygon.
  const RADAR_AXES = [
    { key: 'chord_density_per_s',  label: 'density/s' },
    { key: 'monophonic_ratio',     label: 'mono' },
    { key: 'repetition_score',     label: 'repetition' },
    { key: 'polyphony_score',      label: 'polyphony' },
    { key: 'lead_activity_score',  label: 'lead' },
    { key: 'pitch_class_diversity',label: 'pitch div' },
  ];

  // -------------------------------------------------------- tag registry
  // Mirrors the ``_TAG_REGISTRY`` + ``_detectSectionTags`` heuristics in
  // jam.js so /debug can filter/annotate sections by the same taxonomy
  // the rehearsal UI uses. Kept in lock-step by convention — if the
  // rules change in jam.js, mirror them here. Only the id + label +
  // severity are surfaced; the copy/warmup props are jam-specific.
  const TAG_REGISTRY = {
    barre:  { id: 'barre',  label: 'Barre chord',  severity: 3 },
    colour: { id: 'colour', label: 'Colour chord', severity: 2 },
    jumps:  { id: 'jumps',  label: 'Big jumps',    severity: 2 },
    quick:  { id: 'quick',  label: 'Fast changes', severity: 2 },
  };
  const TAG_ORDER = ['barre', 'colour', 'jumps', 'quick'];

  // -------------------------------------------------------- state
  const state = {
    activeTab: 'inspector',
    sessions: [],            // [{id, name, timestamp, has_debug_features, ...}]
    currentSessionId: null,
    currentBundle: null,
    selectedSectionIdx: null,
    tagFilter: null,         // null = show all; else a tag id (barre/colour/jumps/quick)
    corpus: null,            // {songs: [...]}
    history: null,           // [{...}]
    historyBundles: new Map(), // id → bundle (lazy fetched for histograms)
  };

  // ==================================================================
  // TAB CONTROLLER
  // ==================================================================
  function setActiveTab(name) {
    state.activeTab = name;
    document.querySelectorAll('.debug-tabs button').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.tab === name);
    });
    document.querySelectorAll('.tab-view').forEach((view) => {
      const match = view.id === `view-${name}`;
      view.classList.toggle('active', match);
      view.hidden = !match;
    });
    if (name === 'corpus' && !state.corpus) loadCorpus();
    if (name === 'history' && !state.history) loadHistory();
  }

  function bindTabs() {
    document.querySelectorAll('.debug-tabs button').forEach((btn) => {
      btn.addEventListener('click', () => setActiveTab(btn.dataset.tab));
    });
  }

  // ==================================================================
  // INSPECTOR TAB
  // ==================================================================
  async function loadSessions() {
    try {
      const r = await fetch('/api/debug/sessions');
      const data = await r.json();
      state.sessions = data.sessions || [];
      renderSessionPicker();
    } catch (err) {
      console.error('loadSessions failed', err);
    }
  }

  function renderSessionPicker() {
    const sel = $('session-picker');
    sel.innerHTML = '';
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = '— select a session —';
    sel.appendChild(placeholder);
    for (const s of state.sessions) {
      const opt = document.createElement('option');
      opt.value = s.id;
      const ts = s.timestamp ? new Date(s.timestamp).toLocaleString() : '';
      // Legacy sessions (no debug_features) are still selectable —
      // the Inspector renders the timeline, chord strip, decision
      // summary, and landmark notes for them; only the per-stem
      // radar/table panel shows the "re-analyze to populate"
      // placeholder. Greying them out hid useful data.
      const flag = s.has_debug_features ? '' : '  [legacy]';
      opt.textContent = `${s.name} · ${ts}${flag}`;
      sel.appendChild(opt);
    }
    sel.value = state.currentSessionId || '';
  }

  async function loadBundle(id) {
    if (!id) return;
    try {
      const r = await fetch(`/api/session/${id}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const bundle = await r.json();
      state.currentSessionId = id;
      state.currentBundle = bundle;
      state.selectedSectionIdx = null;
      state.tagFilter = null;
      renderInspector();
    } catch (err) {
      console.error('loadBundle failed', err);
    }
  }

  function renderInspector() {
    const bundle = state.currentBundle;
    if (!bundle) {
      $('inspector-content').hidden = true;
      $('inspector-empty').hidden = false;
      return;
    }
    $('inspector-empty').hidden = true;
    $('inspector-content').hidden = false;
    renderTagFilter(bundle);
    renderTimelineStrip(bundle);
    renderChordStrip(bundle);
    renderSectionDetail(bundle);
  }

  function getSections(bundle) {
    return (bundle.understanding && bundle.understanding.sections) || [];
  }
  function getChords(bundle) {
    const u = bundle.understanding || {};
    return (u.chords_beat_snapped && u.chords_beat_snapped.length)
      ? u.chords_beat_snapped
      : (u.chords || []);
  }
  function getDuration(bundle) {
    return (bundle.audio && bundle.audio.duration_s) ||
           Math.max(0, ...getSections(bundle).map((s) => s.end_s || 0));
  }

  // -------------------------------------------------------- tag detection
  // Persisted-shape port of jam.js:_detectSectionTags. Reads section
  // (start_s/end_s/landmark_notes) and the bundle-level chord list
  // (chord.start_s/end_s/symbol). Returns an array of tag records in
  // fire order. Kept intentionally close to the jam.js source so
  // future rule changes are copy-paste safe.
  function detectSectionTags(sec, chords) {
    if (!sec) return [];
    const tags = [];
    const startS = sec.start_s || 0;
    const endS = sec.end_s || 0;
    const symbolsIn = [];
    for (const c of chords) {
      if (!c || typeof c.symbol !== 'string') continue;
      const cs = typeof c.start_s === 'number' ? c.start_s : NaN;
      const ce = typeof c.end_s === 'number' ? c.end_s : NaN;
      if (!Number.isFinite(cs) || !Number.isFinite(ce)) continue;
      const mid = 0.5 * (cs + ce);
      if (mid >= startS && mid < endS) symbolsIn.push(c.symbol);
    }
    const BARRE_RE = /^(F#?|B|Bb)(m?)(?![a-z0-9])/;
    if (symbolsIn.some((sym) => BARRE_RE.test(sym))) tags.push(TAG_REGISTRY.barre);
    const COLOUR_RE = /(7|sus2|sus4|add9|maj7|m7)/i;
    if (symbolsIn.some((sym) => COLOUR_RE.test(sym))) tags.push(TAG_REGISTRY.colour);
    const landmarks = Array.isArray(sec.landmark_notes) ? sec.landmark_notes : [];
    if (landmarks.length >= 2) {
      let lo = Infinity, hi = -Infinity;
      for (const n of landmarks) {
        const p = typeof n.pitch === 'number' ? n.pitch : NaN;
        if (!Number.isFinite(p)) continue;
        if (p < lo) lo = p;
        if (p > hi) hi = p;
      }
      if (Number.isFinite(lo) && Number.isFinite(hi) && (hi - lo) > 10) {
        tags.push(TAG_REGISTRY.jumps);
      }
    }
    const dur = Math.max(0, endS - startS);
    if (dur > 0 && dur < 4) tags.push(TAG_REGISTRY.quick);
    return tags;
  }

  // Bundle-level cache — recomputed on each bundle load so we don't
  // re-run the regex sweep on every hover/click.
  function tagSummary(bundle) {
    const sections = getSections(bundle);
    const chords = getChords(bundle);
    return sections.map((sec, idx) => ({
      idx,
      startS: sec.start_s || 0,
      endS: sec.end_s || 0,
      label: sec.label || `section ${idx + 1}`,
      tags: detectSectionTags(sec, chords),
    }));
  }

  // -------------------------------------------------------- tag filter chips
  function renderTagFilter(bundle) {
    const host = $('tag-filter');
    if (!host) return;
    host.innerHTML = '';
    const summary = tagSummary(bundle);
    const counts = { barre: 0, colour: 0, jumps: 0, quick: 0 };
    for (const row of summary) {
      for (const t of row.tags) counts[t.id] = (counts[t.id] || 0) + 1;
    }
    const mkChip = (tagId, label, count, active) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'tag-filter-chip'
        + (tagId ? ` tag-${tagId}` : '')
        + (active ? ' active' : '');
      btn.textContent = count === null ? label : `${label} · ${count}`;
      btn.disabled = count === 0;
      btn.addEventListener('click', () => {
        state.tagFilter = tagId;
        renderInspector();
      });
      host.appendChild(btn);
    };
    mkChip(null, 'All', null, state.tagFilter === null);
    for (const id of TAG_ORDER) {
      mkChip(id, TAG_REGISTRY[id].label, counts[id] || 0, state.tagFilter === id);
    }
  }

  // ----------------------------------------------------- timeline strip
  function renderTimelineStrip(bundle) {
    const host = $('timeline-strip');
    host.innerHTML = '';
    const sections = getSections(bundle);
    const chords = getChords(bundle);
    const dur = getDuration(bundle) || 1;
    const W = host.clientWidth || 800;
    const H = 56;
    const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none' }, host);
    const filter = state.tagFilter;
    sections.forEach((sec, idx) => {
      const x = ((sec.start_s || 0) / dur) * W;
      const w = Math.max(2, (((sec.end_s || 0) - (sec.start_s || 0)) / dur) * W);
      const mode = sec.guidance_mode || 'chord';
      const conf = Math.max(0, Math.min(1, sec.guidance_confidence || 0));
      const tags = detectSectionTags(sec, chords);
      const matchesFilter = !filter || tags.some((t) => t.id === filter);
      const rect = svgEl('rect', {
        x, y: 0, width: w, height: H,
        fill: MODE_COLOR[mode] || MODE_COLOR.chord,
        'fill-opacity': matchesFilter ? (0.3 + 0.7 * conf) : 0.08,
        class: 'timeline-section'
          + (idx === state.selectedSectionIdx ? ' selected' : '')
          + (matchesFilter ? '' : ' filtered-out'),
      }, svg);
      rect.addEventListener('click', () => {
        state.selectedSectionIdx = idx;
        renderTimelineStrip(bundle);
        renderSectionDetail(bundle);
      });
      // Section label if there's room
      if (w > 40) {
        svgEl('text', {
          x: x + 4, y: 14, class: 'timeline-label',
        }, svg).textContent = sec.label || 'section';
      }
      // Tag dots — one small circle per tag stacked along the bottom
      // edge so long sections don't dominate the visual weight. Skips
      // dots on filtered-out sections to keep the eye on matches.
      if (matchesFilter && tags.length && w > 12) {
        const dotY = H - 8;
        tags.forEach((t, k) => {
          svgEl('circle', {
            cx: x + 6 + k * 10, cy: dotY, r: 3.5,
            class: `timeline-tag-dot tag-${t.id}`,
          }, svg);
        });
      }
    });
  }

  // ----------------------------------------------------- chord strip
  function renderChordStrip(bundle) {
    const host = $('chord-strip');
    host.innerHTML = '';
    const chords = getChords(bundle);
    const dur = getDuration(bundle) || 1;
    const W = host.clientWidth || 800;
    const H = 32;
    const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none' }, host);
    chords.forEach((c) => {
      const start = c.start_s || 0;
      const end = c.end_s || start;
      const x = (start / dur) * W;
      const w = Math.max(2, ((end - start) / dur) * W);
      svgEl('rect', {
        x: x + 0.5, y: 4, width: Math.max(1, w - 1), height: H - 8,
        class: 'chord-pill', rx: 3,
      }, svg);
      if (w > 28) {
        svgEl('text', {
          x: x + 4, y: H / 2 + 4, class: 'chord-pill-label',
        }, svg).textContent = c.symbol || '';
      }
    });
  }

  // ----------------------------------------------------- detail panel
  function renderSectionDetail(bundle) {
    const detail = $('section-detail');
    const idx = state.selectedSectionIdx;
    if (idx === null || idx === undefined) {
      detail.hidden = true;
      return;
    }
    const sec = getSections(bundle)[idx];
    if (!sec) { detail.hidden = true; return; }
    detail.hidden = false;

    // Summary
    const sum = $('detail-summary');
    sum.innerHTML = '';
    const badge = document.createElement('span');
    badge.className = `mode-badge ${sec.guidance_mode || 'chord'}`;
    badge.textContent = sec.guidance_mode || 'chord';
    sum.appendChild(badge);
    const add = (label, value) => {
      const wrap = document.createElement('span');
      wrap.innerHTML = `<span>${label}:</span> <b>${value}</b>`;
      sum.appendChild(wrap);
    };
    add('confidence', (sec.guidance_confidence || 0).toFixed(3));
    add('dominant_stem', sec.dominant_stem || '—');
    add('label', sec.label || '—');
    add('window', `${(sec.start_s || 0).toFixed(1)}s → ${(sec.end_s || 0).toFixed(1)}s`);
    if (typeof sec.bpm === 'number' && sec.bpm > 0) add('bpm', Math.round(sec.bpm));
    if (sec.guidance_reason) add('reason', escapeHTML(sec.guidance_reason));

    const tags = detectSectionTags(sec, getChords(bundle));
    if (tags.length) {
      const wrap = document.createElement('span');
      wrap.className = 'detail-tag-row';
      wrap.innerHTML = '<span>tags:</span> ';
      for (const t of tags) {
        const pill = document.createElement('span');
        pill.className = `tag-pill tag-${t.id}`;
        pill.textContent = t.label;
        wrap.appendChild(pill);
      }
      sum.appendChild(wrap);
    }

    renderPerStemTable(sec);
    renderRadar(sec);
    renderLandmarkRoll(sec);
  }

  // Section-level features are identical across stems for a given section
  // (they depend on the song's chord track, not the stem). Showing them as
  // per-stem columns wastes horizontal space; they live on the section
  // summary chip row above instead.
  const SECTION_LEVEL_FEATURES = new Set([
    'chord_density_per_s',
    'chord_count_in_section',
    'duration_s',
  ]);

  function renderPerStemTable(sec) {
    const host = $('per-stem-table');
    host.innerHTML = '';
    const features = sec.debug_features || [];
    if (!features.length) {
      host.innerHTML = '<p class="muted">No debug_features on this section (legacy entry — re-analyze to populate).</p>';
      return;
    }
    const cols = Object.keys(features[0]).filter(
      (k) => k !== 'stem_name' && !SECTION_LEVEL_FEATURES.has(k),
    );
    const table = document.createElement('table');
    const thead = document.createElement('thead');
    const trh = document.createElement('tr');
    trh.appendChild(thCell('stem'));
    cols.forEach((c) => trh.appendChild(thCell(c)));
    thead.appendChild(trh);
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    features.forEach((f) => {
      const tr = document.createElement('tr');
      if (f.stem_name === sec.dominant_stem) tr.classList.add('dominant');
      tr.appendChild(tdCell(f.stem_name));
      cols.forEach((c) => tr.appendChild(tdCell(fmtNum(f[c]))));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    host.appendChild(table);
  }

  function thCell(text) {
    const th = document.createElement('th');
    th.textContent = text;
    return th;
  }
  function tdCell(text) {
    const td = document.createElement('td');
    td.textContent = text;
    return td;
  }
  function fmtNum(v) {
    if (v === null || v === undefined) return '—';
    if (typeof v !== 'number') return String(v);
    if (Number.isInteger(v)) return String(v);
    return v.toFixed(3);
  }

  // ----------------------------------------------------- radar chart
  function renderRadar(sec) {
    const host = $('radar-chart');
    host.innerHTML = '';
    const features = sec.debug_features || [];
    if (!features.length) return;
    // Use fixed viewBox dimensions so the SVG scales predictably inside
    // its locked-height container; reading host.clientWidth before the
    // panel had laid out caused the SVG to blow up to many screens tall
    // when the percentage-height parent forced an auto height resolution.
    const W = 440, H = 240;
    const cx = W / 2, cy = H / 2;
    // Reserve generous padding around the polygon so the axis labels
    // never crash into the shape (~70 px on the sides for "polyphony" /
    // "pitch div", ~30 px on top/bottom for "density/s" / "polyphony").
    const R = Math.min(W / 2 - 70, H / 2 - 30);
    const svg = svgEl('svg', {
      viewBox: `0 0 ${W} ${H}`,
      preserveAspectRatio: 'xMidYMid meet',
    }, host);

    // Compute per-axis max across stems for within-section normalization.
    const maxes = RADAR_AXES.map((axis) =>
      Math.max(1e-9, ...features.map((f) => Math.abs(Number(f[axis.key]) || 0))),
    );

    // Concentric rings at 0.5 and 1.0 (normalized). Drawn first so the
    // axis spokes and stem polygons sit on top.
    [0.5, 1.0].forEach((r) => {
      const pts = RADAR_AXES.map((_, i) => {
        const a = (i / RADAR_AXES.length) * Math.PI * 2 - Math.PI / 2;
        return `${cx + Math.cos(a) * R * r},${cy + Math.sin(a) * R * r}`;
      }).join(' ');
      svgEl('polygon', {
        points: pts, fill: 'none',
        stroke: 'var(--border)', 'stroke-width': 1, 'stroke-dasharray': '2 3',
      }, svg);
    });

    // Axis spokes + labels with quadrant-aware text-anchor / baseline
    // so the label text sits OUTSIDE the polygon footprint regardless
    // of where on the dial it lands.
    RADAR_AXES.forEach((axis, i) => {
      const angle = (i / RADAR_AXES.length) * Math.PI * 2 - Math.PI / 2;
      const ca = Math.cos(angle), sa = Math.sin(angle);
      svgEl('line', {
        x1: cx, y1: cy,
        x2: cx + ca * R, y2: cy + sa * R,
        class: 'radar-axis-line',
      }, svg);
      const lx = cx + ca * (R + 16);
      const ly = cy + sa * (R + 16);
      let anchor = 'middle';
      if (ca > 0.35) anchor = 'start';
      else if (ca < -0.35) anchor = 'end';
      let baseline = 'middle';
      if (sa > 0.35) baseline = 'hanging';
      else if (sa < -0.35) baseline = 'auto';
      const lbl = svgEl('text', {
        x: lx, y: ly, class: 'radar-axis-label',
        'text-anchor': anchor, 'dominant-baseline': baseline,
      }, svg);
      lbl.textContent = axis.label;
    });

    // One polygon per stem.
    const stemColor = (i) => {
      const palette = ['#60a5fa', '#f97316', '#a78bfa', '#f43f5e', '#34d399', '#fbbf24'];
      return palette[i % palette.length];
    };
    features.forEach((f, fi) => {
      const pts = RADAR_AXES.map((axis, i) => {
        const v = Math.abs(Number(f[axis.key]) || 0) / maxes[i];
        const a = (i / RADAR_AXES.length) * Math.PI * 2 - Math.PI / 2;
        return `${cx + Math.cos(a) * R * v},${cy + Math.sin(a) * R * v}`;
      }).join(' ');
      const color = f.stem_name === sec.dominant_stem
        ? 'var(--accent)'
        : stemColor(fi);
      svgEl('polygon', {
        points: pts, fill: color, stroke: color, class: 'radar-poly',
      }, svg);
    });

    // Legend below
    const legend = document.createElement('div');
    legend.className = 'legend';
    features.forEach((f, fi) => {
      const span = document.createElement('span');
      span.className = 'legend-pill';
      span.style.background = f.stem_name === sec.dominant_stem
        ? 'var(--accent)'
        : stemColor(fi);
      span.style.color = 'var(--bg)';
      span.textContent = f.stem_name + (f.stem_name === sec.dominant_stem ? ' ★' : '');
      legend.appendChild(span);
    });
    host.appendChild(legend);
  }

  // ----------------------------------------------------- landmark notes
  const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
  const BLACK_KEY_PCS = new Set([1, 3, 6, 8, 10]);
  const pitchToName = (midi) => {
    const m = Math.round(midi);
    const pc = ((m % 12) + 12) % 12;
    return `${NOTE_NAMES[pc]}${Math.floor(m / 12) - 1}`;
  };

  function renderLandmarkRoll(sec) {
    const host = $('landmark-roll');
    host.innerHTML = '';
    const notes = sec.landmark_notes || [];
    if (!notes.length) {
      host.innerHTML = '<p class="muted">No landmark notes (chord-mode section or silent stem).</p>';
      return;
    }
    // Match the viewBox 1:1 to the actual rendered panel size so the
    // SVG scales without any horizontal/vertical distortion. Renders
    // happen on click, after layout, so host.clientWidth is reliable
    // (falls back to 1000 only if the panel hasn't laid out yet).
    const H = 200;
    const W = host.clientWidth || 1000;
    const PAD_L = 40, PAD_R = 10, PAD_T = 10, PAD_B = 22;
    const plotW = W - PAD_L - PAD_R;
    const plotH = H - PAD_T - PAD_B;
    const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none' }, host);

    const t0 = sec.start_s || 0;
    const t1 = sec.end_s || (t0 + 1);
    const dt = Math.max(1e-3, t1 - t0);

    // Snap pitch range to whole octaves so the C-grid is meaningful.
    const pitches = notes.map((n) => n.pitch);
    let pmin = Math.floor(Math.min(...pitches) / 12) * 12;
    let pmax = Math.ceil((Math.max(...pitches) + 1) / 12) * 12;
    if (pmax - pmin < 12) pmax = pmin + 12;
    const dp = pmax - pmin;
    const rowH = plotH / dp;

    // Plot frame.
    svgEl('rect', {
      x: PAD_L, y: PAD_T, width: plotW, height: plotH,
      fill: 'rgba(0,0,0,0.18)', stroke: 'var(--border)', 'stroke-width': 1,
    }, svg);

    // Black-key zebra stripes (only when rowH is large enough to be readable).
    if (rowH >= 4) {
      for (let p = pmin; p < pmax; p++) {
        const pc = ((p % 12) + 12) % 12;
        if (!BLACK_KEY_PCS.has(pc)) continue;
        const y = PAD_T + plotH - ((p - pmin + 1) / dp) * plotH;
        svgEl('rect', {
          x: PAD_L, y, width: plotW, height: rowH,
          fill: 'rgba(255,255,255,0.04)',
        }, svg);
      }
    }

    // Octave grid lines + C labels on the left.
    for (let p = pmin; p <= pmax; p += 12) {
      const y = PAD_T + plotH - ((p - pmin) / dp) * plotH;
      svgEl('line', {
        x1: PAD_L, x2: PAD_L + plotW, y1: y, y2: y,
        stroke: 'var(--border)', 'stroke-width': 1, 'stroke-opacity': 0.55,
      }, svg);
      const label = svgEl('text', {
        x: PAD_L - 6, y: y + 3,
        'text-anchor': 'end',
        'font-size': 10, fill: 'var(--text-dim)',
      }, svg);
      label.textContent = pitchToName(p);
    }

    // Time axis labels (start / mid / end) below the plot.
    const mkTimeLabel = (frac, txt, anchor) => {
      const x = PAD_L + frac * plotW;
      svgEl('line', {
        x1: x, x2: x, y1: PAD_T + plotH, y2: PAD_T + plotH + 4,
        stroke: 'var(--text-dim)', 'stroke-width': 1,
      }, svg);
      const t = svgEl('text', {
        x, y: H - 6,
        'text-anchor': anchor,
        'font-size': 10, fill: 'var(--text-dim)',
      }, svg);
      t.textContent = txt;
    };
    mkTimeLabel(0, `${t0.toFixed(1)}s`, 'start');
    mkTimeLabel(0.5, `${((t0 + t1) / 2).toFixed(1)}s`, 'middle');
    mkTimeLabel(1, `${t1.toFixed(1)}s`, 'end');

    // Note rects + inline name labels.
    notes.forEach((n) => {
      const x = PAD_L + ((n.start - t0) / dt) * plotW;
      const w = Math.max(3, ((n.end - n.start) / dt) * plotW);
      const y = PAD_T + plotH - ((n.pitch - pmin) / dp) * plotH - rowH / 2;
      svgEl('rect', {
        x, y, width: w, height: Math.max(4, rowH),
        fill: 'var(--accent)',
        'fill-opacity': Math.max(0.55, Math.min(1, (n.velocity || 80) / 127)),
        class: 'landmark-roll-note',
      }, svg);
      // Label inside the rect when it's tall + wide enough; else skip.
      // Landmark sets are small (~10-20 notes/section) so most rects fit.
      if (w >= 22 && rowH >= 11) {
        const label = svgEl('text', {
          x: x + 4, y: y + Math.min(rowH - 3, 11),
          'font-size': 9, 'font-weight': 700,
          fill: 'var(--bg)', 'pointer-events': 'none',
        }, svg);
        label.textContent = pitchToName(n.pitch);
      }
    });
  }

  // ==================================================================
  // CORPUS TAB
  // ==================================================================
  async function loadCorpus() {
    try {
      const [corpusR, sessR] = await Promise.all([
        fetch('/api/debug/corpus'),
        state.sessions.length ? Promise.resolve(null) : fetch('/api/debug/sessions'),
      ]);
      const corpus = await corpusR.json();
      state.corpus = Array.isArray(corpus) ? { songs: corpus } : corpus;
      if (sessR) {
        const sdata = await sessR.json();
        state.sessions = sdata.sessions || [];
      }
      renderCorpus();
    } catch (err) {
      console.error('loadCorpus failed', err);
    }
  }

  function slugify(s) {
    return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  }

  function matchSong(song) {
    // Two-tier: slug match, then fuzzy title substring.
    const corpusSlug = slugify(song.slug || song.title);
    const corpusTitle = String(song.title || '').toLowerCase();
    for (const s of state.sessions) {
      const sName = String(s.name || '').toLowerCase();
      if (slugify(s.name) === corpusSlug) return { session: s, fuzzy: false };
    }
    for (const s of state.sessions) {
      const sName = String(s.name || '').toLowerCase();
      if (corpusTitle && (sName.includes(corpusTitle) || corpusTitle.includes(sName))) {
        return { session: s, fuzzy: true };
      }
    }
    return null;
  }

  async function renderCorpus() {
    const corpus = state.corpus;
    if (!corpus) return;
    const songs = corpus.songs || [];

    // Build per-song match + (if matched) fetch bundle to align predictions.
    const rows = await Promise.all(songs.map(async (song) => {
      const match = matchSong(song);
      let bundle = null;
      if (match && match.session && match.session.has_debug_features) {
        try {
          const r = await fetch(`/api/session/${match.session.id}`);
          if (r.ok) bundle = await r.json();
        } catch (_) { /* ignore */ }
      }
      return { song, match, bundle };
    }));

    // Aggregate confusion matrix (predicted ↓ × actual →)
    const matrix = {};
    MODES.forEach((p) => { matrix[p] = {}; MODES.forEach((a) => { matrix[p][a] = 0; }); });
    let total = 0, correct = 0;
    rows.forEach(({ song, bundle }) => {
      if (!bundle) return;
      const predSections = getSections(bundle);
      const gt = song.ground_truth_sections || [];
      const n = Math.min(predSections.length, gt.length);
      for (let i = 0; i < n; i++) {
        const a = gt[i].guidance_mode;
        const p = predSections[i].guidance_mode;
        if (!MODES.includes(a) || !MODES.includes(p)) continue;
        matrix[p][a] += 1;
        total += 1;
        if (a === p) correct += 1;
      }
    });

    // Macro-F1
    let f1Sum = 0, f1Count = 0;
    MODES.forEach((m) => {
      const tp = matrix[m][m];
      const fp = MODES.reduce((s, a) => s + (a === m ? 0 : matrix[m][a]), 0);
      const fn = MODES.reduce((s, p) => s + (p === m ? 0 : matrix[p][m]), 0);
      const prec = tp + fp > 0 ? tp / (tp + fp) : 0;
      const rec = tp + fn > 0 ? tp / (tp + fn) : 0;
      const f1 = prec + rec > 0 ? (2 * prec * rec) / (prec + rec) : 0;
      f1Sum += f1; f1Count += 1;
    });
    const macroF1 = f1Count > 0 ? f1Sum / f1Count : 0;
    const acc = total > 0 ? correct / total : 0;

    // Aggregate stats
    const agg = $('corpus-aggregate');
    agg.innerHTML = `
      <div class="stat"><span class="stat-label">Accuracy</span>
        <span class="stat-value">${total ? `${correct}/${total} (${(acc * 100).toFixed(1)}%)` : '—'}</span></div>
      <div class="stat"><span class="stat-label">Macro-F1</span>
        <span class="stat-value">${total ? macroF1.toFixed(3) : '—'}</span></div>
      <div class="stat"><span class="stat-label">Songs analyzed</span>
        <span class="stat-value">${rows.filter((r) => r.bundle).length} / ${rows.length}</span></div>
    `;

    // Confusion matrix
    const cm = $('confusion-matrix');
    let cmHtml = '<table><thead><tr><th>pred \\ actual</th>';
    MODES.forEach((a) => { cmHtml += `<th>${a}</th>`; });
    cmHtml += '</tr></thead><tbody>';
    MODES.forEach((p) => {
      cmHtml += `<tr><th>${p}</th>`;
      MODES.forEach((a) => {
        const v = matrix[p][a];
        const cls = (p === a) ? 'diag' : '';
        const bg = v > 0 ? `style="background: ${MODE_COLOR[p]}; opacity: ${0.3 + 0.5 * Math.min(1, v / Math.max(1, total / 3))}"` : '';
        cmHtml += `<td class="${cls}" ${bg}>${v}</td>`;
      });
      cmHtml += '</tr>';
    });
    cmHtml += '</tbody></table>';
    cm.innerHTML = cmHtml;

    // Per-song table
    const tbl = $('corpus-table');
    let html = '<table><thead><tr><th>Title</th><th>Artist</th><th>Status</th><th>GT sections</th><th>Action</th></tr></thead><tbody>';
    rows.forEach(({ song, match, bundle }) => {
      const status = bundle
        ? `<span class="status-badge analyzed">analyzed</span>${match.fuzzy ? ' <span class="status-badge fuzzy">fuzzy</span>' : ''}`
        : match
          ? `<span class="status-badge missing">no features</span>`
          : `<span class="status-badge missing">not analyzed</span>`;
      const gtCount = (song.ground_truth_sections || []).length;
      const action = (match && bundle)
        ? `<button type="button" data-jump="${match.session.id}">View in Inspector</button>`
        : '<span class="muted">—</span>';
      html += `<tr><td>${escapeHTML(song.title || song.slug || '')}</td>
        <td>${escapeHTML(song.artist || '')}</td>
        <td>${status}</td>
        <td>${gtCount}</td>
        <td>${action}</td></tr>`;
    });
    html += '</tbody></table>';
    tbl.innerHTML = html;
    tbl.querySelectorAll('button[data-jump]').forEach((btn) => {
      btn.addEventListener('click', () => {
        setActiveTab('inspector');
        $('session-picker').value = btn.dataset.jump;
        loadBundle(btn.dataset.jump);
      });
    });
  }

  // ==================================================================
  // HISTORY TAB
  // ==================================================================
  async function loadHistory() {
    try {
      const r = await fetch('/api/history?limit=100');
      const data = await r.json();
      state.history = data.history || [];
      renderHistory();
      // Lazy-fetch bundles for the guidance/section histograms.
      setTimeout(() => fetchHistoryBundles(), 400);
    } catch (err) {
      console.error('loadHistory failed', err);
    }
  }

  function renderHistory() {
    renderHistogramsImmediate();
    renderHistoryTable();
  }

  function renderHistogramsImmediate() {
    const host = $('histograms');
    host.innerHTML = '';
    const tempos = (state.history || [])
      .map((h) => Number(h.tempo_bpm || (h.result && h.result.tempo_bpm)))
      .filter((v) => Number.isFinite(v));
    const keys = (state.history || [])
      .map((h) => h.detected_key || (h.result && h.result.detected_key))
      .filter(Boolean);
    host.appendChild(renderHistogram('Tempo (BPM)', binNumeric(tempos, 8)));
    host.appendChild(renderHistogram('Detected key', countBy(keys)));
    // Sections + guidance modes are filled in lazily when bundles arrive.
    host.appendChild(renderHistogram('Section types', countBy([]), true));
    host.appendChild(renderHistogram('Guidance modes', countBy([]), true));
  }

  async function fetchHistoryBundles() {
    const history = state.history || [];
    const batch = history.slice(0, 20);
    await Promise.all(batch.map(async (h) => {
      if (!h.id || state.historyBundles.has(h.id)) return;
      try {
        const r = await fetch(`/api/session/${h.id}`);
        if (r.ok) state.historyBundles.set(h.id, await r.json());
      } catch (_) { /* swallow */ }
    }));
    renderHistogramsWithBundles();
  }

  function renderHistogramsWithBundles() {
    const host = $('histograms');
    host.innerHTML = '';
    const tempos = (state.history || [])
      .map((h) => Number(h.tempo_bpm || (h.result && h.result.tempo_bpm)))
      .filter((v) => Number.isFinite(v));
    const keys = (state.history || [])
      .map((h) => h.detected_key || (h.result && h.result.detected_key))
      .filter(Boolean);
    const labels = [];
    const modes = [];
    state.historyBundles.forEach((bundle) => {
      getSections(bundle).forEach((sec) => {
        if (sec.label) labels.push(sec.label);
        if (sec.guidance_mode) modes.push(sec.guidance_mode);
      });
    });
    host.appendChild(renderHistogram('Tempo (BPM)', binNumeric(tempos, 8)));
    host.appendChild(renderHistogram('Detected key', countBy(keys)));
    host.appendChild(renderHistogram('Section types', countBy(labels)));
    host.appendChild(renderHistogram('Guidance modes', countBy(modes)));
  }

  function binNumeric(values, n) {
    if (!values.length) return [];
    const min = Math.min(...values), max = Math.max(...values);
    if (min === max) return [{ label: String(Math.round(min)), value: values.length }];
    const w = (max - min) / n;
    const bins = Array.from({ length: n }, (_, i) => ({
      label: `${Math.round(min + i * w)}`,
      value: 0,
    }));
    values.forEach((v) => {
      const i = Math.min(n - 1, Math.floor((v - min) / w));
      bins[i].value += 1;
    });
    return bins;
  }

  function countBy(values) {
    const counts = new Map();
    values.forEach((v) => counts.set(v, (counts.get(v) || 0) + 1));
    return Array.from(counts.entries())
      .map(([label, value]) => ({ label, value }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 12);
  }

  function renderHistogram(title, bins, pending = false) {
    const wrap = document.createElement('div');
    wrap.className = 'histogram';
    const h4 = document.createElement('h4');
    h4.textContent = title + (pending ? '  (loading…)' : '');
    wrap.appendChild(h4);
    if (!bins.length) {
      const p = document.createElement('p');
      p.className = 'muted';
      p.style.margin = '0';
      p.style.fontSize = '12px';
      p.textContent = pending ? 'fetching bundles…' : 'no data';
      wrap.appendChild(p);
      return wrap;
    }
    const W = 280, H = 80;
    const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none' }, wrap);
    const max = Math.max(...bins.map((b) => b.value)) || 1;
    const bw = W / bins.length;
    bins.forEach((b, i) => {
      const bh = (b.value / max) * (H - 18);
      svgEl('rect', {
        x: i * bw + 1, y: H - bh - 14,
        width: Math.max(1, bw - 2), height: bh,
        class: 'histogram-bar',
      }, svg);
      const lbl = svgEl('text', {
        x: i * bw + bw / 2, y: H - 2, class: 'histogram-label',
        'text-anchor': 'middle',
      }, svg);
      lbl.textContent = b.label;
    });
    return wrap;
  }

  function renderHistoryTable() {
    const host = $('history-table');
    const history = state.history || [];
    if (!history.length) {
      host.innerHTML = '<p class="muted">No history yet.</p>';
      return;
    }
    let html = '<table><thead><tr><th>Name</th><th>Date</th><th>Tempo</th><th>Key</th><th>Type</th></tr></thead><tbody>';
    history.forEach((h) => {
      const ts = h.timestamp ? new Date(h.timestamp).toLocaleString() : '';
      const tempo = h.tempo_bpm || (h.result && h.result.tempo_bpm) || '';
      const key = h.detected_key || (h.result && h.result.detected_key) || '';
      html += `<tr data-id="${h.id}">
        <td>${escapeHTML(h.name || h.filename || 'untitled')}</td>
        <td>${escapeHTML(ts)}</td>
        <td>${tempo ? Number(tempo).toFixed(1) : '—'}</td>
        <td>${escapeHTML(key || '—')}</td>
        <td>${escapeHTML(h.detected_type || '—')}</td>
      </tr>`;
    });
    html += '</tbody></table>';
    host.innerHTML = html;
    host.querySelectorAll('tr[data-id]').forEach((tr) => {
      tr.addEventListener('click', () => {
        const id = tr.dataset.id;
        setActiveTab('inspector');
        $('session-picker').value = id;
        loadBundle(id);
      });
    });
  }

  // ==================================================================
  // UTILITIES
  // ==================================================================
  function escapeHTML(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[c]);
  }

  // ==================================================================
  // INIT
  // ==================================================================
  function init() {
    bindTabs();
    $('session-picker').addEventListener('change', (e) => loadBundle(e.target.value));
    $('refresh-sessions').addEventListener('click', loadSessions);
    loadSessions();
    // Re-render charts on resize so SVGs reflow.
    let resizeTimer = null;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (state.activeTab === 'inspector' && state.currentBundle) renderInspector();
      }, 150);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
