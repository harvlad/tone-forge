// Tone Forge — frontend logic. Plain JS, no framework.
// Version: 2026-05-24-v2 (preview fix)

const $ = (sel) => document.querySelector(sel);

/** Format seconds to MM:SS or H:MM:SS */
function formatTimestamp(totalSeconds) {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  }
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

const dropzone = $('#upload-form');
const fileInput = $('#file-input');
const status = $('#status');
const resultEmpty = $('#result-empty');
const resultEl = $('#result');

// URL input
const urlInput = $('#url-input');
const urlSubmit = $('#url-submit');

// Recording
const recordBtn = $('#record-btn');
const recordLabel = $('#record-label');
const recordTime = $('#record-time');
let mediaRecorder = null;
let recordedChunks = [];
let recordingStartTime = null;
let recordingTimer = null;

// Store the full analysis result for tab switching
let currentResult = null;

// Trim preview state
let trimState = {
  file: null,           // Pending file to analyze
  url: null,            // Pending URL to analyze
  waveformData: null,   // Waveform data from preview
  trimmerInstance: null // WaveformTrimmer instance
};

// Trim preview elements
const trimPreview = $('#trim-preview');
const trimCanvas = $('#trim-canvas');
const trimRange = $('#trim-range');
const analyzeSelectionBtn = $('#analyze-selection');
const analyzeFullBtn = $('#analyze-full');
const cancelTrimBtn = $('#cancel-trim');

// Deep analysis checkbox - persist preference in localStorage
const deepAnalysisCheckbox = $('#deep-analysis');
if (deepAnalysisCheckbox) {
  // Restore from localStorage
  const savedPref = localStorage.getItem('toneforge_deep_analysis');
  if (savedPref !== null) {
    deepAnalysisCheckbox.checked = savedPref === 'true';
  }
  // Save on change
  deepAnalysisCheckbox.addEventListener('change', () => {
    localStorage.setItem('toneforge_deep_analysis', deepAnalysisCheckbox.checked);
  });
}

// -----------------------------------------------------------------------------
// Local Engine Detection
// -----------------------------------------------------------------------------

const LOCAL_ENGINE_URL = 'http://127.0.0.1:7777';
let localEngineAvailable = false;
let localEngineInfo = null;

async function checkLocalEngine() {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 1000);

    const resp = await fetch(`${LOCAL_ENGINE_URL}/health`, {
      signal: controller.signal,
    });
    clearTimeout(timeout);

    if (resp.ok) {
      // Get full info
      const infoResp = await fetch(`${LOCAL_ENGINE_URL}/`);
      localEngineInfo = await infoResp.json();
      localEngineAvailable = true;
      TonePlugins.setAvailable(true);
      updateLocalEngineUI(true);
      console.log('Local engine connected:', localEngineInfo);
      return true;
    }
  } catch (e) {
    // Local engine not running - this is fine
    localEngineAvailable = false;
    TonePlugins.setAvailable(false);
    localEngineInfo = null;
    updateLocalEngineUI(false);
  }
  return false;
}

function updateLocalEngineUI(connected) {
  // Update or create the processing mode indicator
  let indicator = $('#local-engine-indicator');

  if (!indicator) {
    const sourceCol = $('#col-source');
    if (sourceCol) {
      indicator = document.createElement('div');
      indicator.id = 'local-engine-indicator';
      indicator.className = 'local-engine-indicator';
      sourceCol.insertBefore(indicator, sourceCol.firstChild);
    }
  }

  if (indicator) {
    if (connected && localEngineInfo) {
      // Local GPU mode - premium feel
      const device = localEngineInfo.device?.device_name || 'Local GPU';
      indicator.innerHTML = `
        <span class="processing-mode">
          <span class="processing-mode__label">Processing</span>
          <span class="processing-mode__value processing-mode__value--local">
            <span class="processing-dot processing-dot--active"></span>
            ${device}
          </span>
        </span>
      `;
      indicator.classList.add('local-engine-indicator--connected');
      indicator.classList.remove('local-engine-indicator--cloud');
      indicator.title = 'Local GPU acceleration enabled — ~2x faster on full tracks';
    } else {
      // Cloud mode - still feels complete, upgrade is optional
      indicator.innerHTML = `
        <span class="processing-mode">
          <span class="processing-mode__label">Processing</span>
          <span class="processing-mode__value processing-mode__value--cloud">
            <span class="processing-dot"></span>
            Cloud
          </span>
        </span>
        <a href="/api/local-engine/download" class="processing-upgrade">
          Get ToneForge Studio
        </a>
      `;
      indicator.classList.remove('local-engine-indicator--connected');
      indicator.classList.add('local-engine-indicator--cloud');
      indicator.title = 'Cloud processing active — ToneForge Studio enables faster local reconstruction';
    }
  }
}

// Check local engine on load and periodically
checkLocalEngine();
setInterval(checkLocalEngine, 10000); // Check every 10 seconds

// -----------------------------------------------------------------------------
// Plugin Matching (uses shared TonePlugins utility)
// -----------------------------------------------------------------------------

// Wrapper functions for backward compatibility
async function fetchPluginMatches(descriptor) {
  return TonePlugins.fetchMatches(descriptor);
}

function getPluginMatch(blockFamily) {
  return TonePlugins.getMatch(blockFamily);
}

function createPluginBadge(plugin) {
  return TonePlugins.createBadge(plugin);
}

// -----------------------------------------------------------------------------
// Local Engine Deep Analysis
// -----------------------------------------------------------------------------

async function analyzeWithLocalEngine(file) {
  const form = new FormData();
  form.append('file', file);

  setStatus('working', `Analyzing on local GPU...`);

  try {
    const resp = await fetch(`${LOCAL_ENGINE_URL}/api/analyze-deep`, {
      method: 'POST',
      body: form,
    });

    if (!resp.ok) {
      throw new Error(`Local engine error: ${resp.status}`);
    }

    // Read SSE stream
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalData = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Process complete SSE messages
      const lines = buffer.split('\n\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const msg = JSON.parse(line.slice(6));
            if (msg.type === 'progress') {
              const percent = msg.progress ? ` (${Math.round(msg.progress * 100)}%)` : '';
              setStatus('working', `${msg.message}${percent}`);
            } else if (msg.type === 'result') {
              finalData = msg.data;
            } else if (msg.type === 'error') {
              throw new Error(msg.message);
            }
          } catch (e) {
            if (e.message !== msg?.message) {
              console.warn('SSE parse error:', e);
            } else {
              throw e;
            }
          }
        }
      }
    }

    if (!finalData) {
      throw new Error('No result received from local engine');
    }

    // Save to main server history to get admin_url
    try {
      const saveResp = await fetch('/api/history/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: file.name,
          result: finalData,
        }),
      });
      if (saveResp.ok) {
        const saved = await saveResp.json();
        finalData.history_id = saved.id;
        finalData.admin_url = `/studio?analysis=${saved.id}`;
      }
    } catch (e) {
      console.warn('Could not save to history:', e);
    }

    return finalData;

  } catch (err) {
    // Fall back to cloud if local engine fails
    console.warn('Local engine failed, falling back to cloud:', err);
    localEngineAvailable = false;
    updateLocalEngineUI(false);
    throw err;
  }
}

// -----------------------------------------------------------------------------
// Upload handling
// -----------------------------------------------------------------------------

dropzone.addEventListener('click', (e) => {
  // Don't double-fire when clicking the hidden input or radio labels.
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'LABEL') return;
  fileInput.click();
});
dropzone.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); }
});

['dragenter', 'dragover'].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.add('is-dragging');
  })
);
['dragleave', 'drop'].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.remove('is-dragging');
  })
);
dropzone.addEventListener('drop', (e) => {
  const file = e.dataTransfer?.files?.[0];
  if (file) handleFile(file);
});
fileInput.addEventListener('change', () => {
  const file = fileInput.files?.[0];
  if (file) handleFile(file);
});

// -----------------------------------------------------------------------------
// Trim Preview Helpers
// -----------------------------------------------------------------------------

function formatTrimTime(seconds) {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  const ms = Math.floor((seconds % 1) * 100);
  if (mins > 0) {
    return `${mins}:${secs.toString().padStart(2, '0')}.${ms.toString().padStart(2, '0')}`;
  }
  return `${secs}.${ms.toString().padStart(2, '0')}`;
}

function showTrimPreview(waveformData) {
  trimState.waveformData = waveformData;
  console.log('showTrimPreview:', { duration: waveformData.duration_sec, hasTrimCanvas: !!trimCanvas, hasWaveformTrimmer: typeof WaveformTrimmer !== 'undefined' });

  // Show trim preview section
  if (trimPreview) {
    trimPreview.hidden = false;
  }

  // Initialize waveform trimmer
  if (trimCanvas && typeof WaveformTrimmer !== 'undefined') {
    // Destroy existing instance
    if (trimState.trimmerInstance) {
      WaveformTrimmer.destroy(trimCanvas);
    }

    trimState.trimmerInstance = WaveformTrimmer.init(trimCanvas, waveformData, {
      mode: 'trim',
      initialStart: waveformData.start_timestamp || 0,
      initialEnd: waveformData.duration_sec,
      onChange: updateTrimInfo,
    });
    console.log('WaveformTrimmer initialized:', !!trimState.trimmerInstance);

    // Initial update
    updateTrimInfo(trimState.trimmerInstance.getSelection());
  } else {
    console.warn('WaveformTrimmer not available:', { trimCanvas: !!trimCanvas, WaveformTrimmer: typeof WaveformTrimmer });
  }
}

function updateTrimInfo(selection) {
  if (trimRange) {
    const start = formatTrimTime(selection.startTime);
    const end = formatTrimTime(selection.endTime);
    const dur = formatTrimTime(selection.duration);
    trimRange.textContent = `${start} - ${end} (${dur} selected)`;
  }
}

function hideTrimPreview() {
  if (trimPreview) {
    trimPreview.hidden = true;
  }

  // Destroy trimmer instance
  if (trimState.trimmerInstance && trimCanvas) {
    WaveformTrimmer.destroy(trimCanvas);
    trimState.trimmerInstance = null;
  }

  // Clear state
  trimState.file = null;
  trimState.url = null;
  trimState.waveformData = null;
}

async function analyzeWithTrim(startTime, endTime) {
  console.log('analyzeWithTrim called:', { startTime, endTime, hasFile: !!trimState.file, hasUrl: !!trimState.url, url: trimState.url });

  // Store URL/file before hiding (hideTrimPreview clears them!)
  const url = trimState.url;
  const file = trimState.file;

  hideTrimPreview();

  if (file) {
    // Analyze file with trim params
    await analyzeFileWithTrim(file, startTime, endTime);
  } else if (url) {
    // Analyze URL with trim params
    await analyzeUrlWithTrim(url, startTime, endTime);
  } else {
    console.error('analyzeWithTrim: No file or URL available');
    setStatus('error', 'No file or URL to analyze');
  }
}

// Trim button handlers
if (analyzeSelectionBtn) {
  analyzeSelectionBtn.addEventListener('click', () => {
    console.log('Analyze Selection clicked:', {
      hasTrimmer: !!trimState.trimmerInstance,
      hasUrl: !!trimState.url,
      hasFile: !!trimState.file
    });
    if (trimState.trimmerInstance) {
      const sel = trimState.trimmerInstance.getSelection();
      console.log('Selection:', sel);
      analyzeWithTrim(sel.startTime, sel.endTime);
    } else if (trimState.url || trimState.file) {
      // Fallback: analyze full track if trimmer failed to init
      console.warn('Trimmer not initialized, analyzing full track');
      analyzeWithTrim(null, null);
    } else {
      console.error('No file or URL to analyze');
      setStatus('error', 'No file or URL to analyze');
    }
  });
}

if (analyzeFullBtn) {
  analyzeFullBtn.addEventListener('click', () => {
    if (trimState.waveformData) {
      analyzeWithTrim(null, null); // Full track
    }
  });
}

if (cancelTrimBtn) {
  cancelTrimBtn.addEventListener('click', () => {
    hideTrimPreview();
    setStatus('idle', 'Cancelled');
  });
}

// -----------------------------------------------------------------------------
// File Handling with Trim Preview
// -----------------------------------------------------------------------------

async function handleFile(file) {
  const deepAnalysis = $('#deep-analysis')?.checked || false;

  // For local engine analysis, skip trim preview (local engine has its own UI)
  if (deepAnalysis && localEngineAvailable) {
    setStatus('working', `Analyzing ${file.name} on local GPU...`);

    try {
      const data = await analyzeWithLocalEngine(file);
      currentResult = data;
      renderUnifiedResult(data);

      const duration = data.descriptor?.source?.duration_sec || data.descriptor?.duration_sec || 0;
      const detected = data.detection?.summary ? ` (${data.detection.summary})` : '';
      const device = data.processing?.device_name || 'GPU';

      let adminLink = '';
      if (data.admin_url) {
        adminLink = ` <a href="${data.admin_url}" target="_blank" class="admin-link">Open in Studio →</a>`;
      }
      setStatus('idle', `Done - ${duration.toFixed(1)}s analyzed${detected} [${device}].${adminLink}`, !!data.admin_url);
      return;
    } catch (err) {
      console.warn('Local engine failed, using cloud:', err);
      setStatus('working', `Local engine unavailable, using cloud...`);
    }
  }

  // Generate waveform preview first
  setStatus('working', `Generating preview for ${file.name}...`);

  try {
    const previewForm = new FormData();
    previewForm.append('file', file);

    const previewResp = await fetch('/api/preview-waveform', {
      method: 'POST',
      body: previewForm,
    });

    if (!previewResp.ok) {
      const body = await previewResp.json().catch(() => ({}));
      throw new Error(body.detail || `Preview failed: HTTP ${previewResp.status}`);
    }

    const waveformData = await previewResp.json();

    // Store file for later analysis
    trimState.file = file;
    trimState.url = null;

    // Show trim preview
    showTrimPreview(waveformData);
    setStatus('idle', `Select region to analyze (${waveformData.duration_sec?.toFixed(1)}s total)`);

  } catch (err) {
    setStatus('error', `Preview failed: ${err.message}`);
    console.error(err);
  }
}

async function analyzeFileWithTrim(file, startTime, endTime) {
  const deepAnalysis = $('#deep-analysis')?.checked || false;

  setStatus('working', `Analyzing ${file.name}...`);

  const form = new FormData();
  form.append('file', file);
  form.append('source_kind', 'auto');
  form.append('platform', 'auto');
  form.append('fast_mode', deepAnalysis ? 'false' : 'true');
  // Deep analysis must also set analysis_mode=deep so the backend picks
  // PipelineConfig.deep() (with stem separation), not standard.
  form.append('analysis_mode', deepAnalysis ? 'deep' : 'studio');

  // Add trim parameters if specified
  if (startTime !== null) {
    form.append('start_time', startTime.toString());
  }
  if (endTime !== null) {
    form.append('end_time', endTime.toString());
  }

  try {
    const resp = await fetch('/api/analyze-stream', { method: 'POST', body: form });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${resp.status}`);
    }

    // Process SSE stream
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const msg = JSON.parse(line.slice(6));
            if (msg.type === 'progress') {
              const percent = msg.percent ? ` (${msg.percent}%)` : '';
              setStatus('working', `${msg.message}${percent}`);
            } else if (msg.type === 'result') {
              currentResult = msg.data;
              renderUnifiedResult(msg.data);
              const duration = msg.data.descriptor?.source?.duration_sec || msg.data.descriptor?.duration_sec || 0;
              const detected = msg.data.detection?.summary ? ` (${msg.data.detection.summary})` : '';
              let adminLink = '';
              if (msg.data.history_id) {
                adminLink = ` <a href="/studio?analysis=${msg.data.history_id}" target="_blank" class="admin-link">Open in Studio →</a>`;
              }
              setStatus('idle', `Done - ${duration.toFixed(1)}s analyzed${detected}.${adminLink}`, !!msg.data.history_id);
            } else if (msg.type === 'error') {
              throw new Error(msg.message);
            }
          } catch (parseErr) {
            if (parseErr.message !== 'Unexpected end of JSON input') {
              console.warn('Parse error:', parseErr);
            }
          }
        }
      }
    }
  } catch (err) {
    setStatus('error', `Failed: ${err.message}`);
    console.error(err);
  }
}

function setStatus(kind, text, allowHtml = false) {
  status.className = 'status' + (kind === 'working' ? ' is-working' : kind === 'error' ? ' is-error' : '');
  // Use innerHTML when explicitly allowed (for admin links etc)
  const hasLink = text.includes('<a');
  console.log('setStatus:', { kind, allowHtml, hasLink, textLength: text.length });
  if (allowHtml && hasLink) {
    console.log('Using innerHTML for admin link');
    status.innerHTML = text;
  } else {
    status.textContent = text;
  }
}

function humanBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 ** 2).toFixed(1)} MB`;
}

// -----------------------------------------------------------------------------
// Rendering
// -----------------------------------------------------------------------------

// Tab switching
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('tab') && currentResult) {
    const platform = e.target.dataset.platform;
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('tab--active'));
    e.target.classList.add('tab--active');
    renderForPlatform(currentResult, platform);
  }
});

// Export buttons
document.addEventListener('click', async (e) => {
  if (e.target.classList.contains('btn--export') && currentResult) {
    const format = e.target.dataset.format;
    const stem = e.target.dataset.stem || '';
    await exportPreset(format, stem);
  }
});

async function exportPreset(format, stem = '') {
  if (!currentResult) return;

  // Get current chain based on active tab
  const activeTab = document.querySelector('.tab--active');
  const platform = activeTab?.dataset.platform || 'helix';

  let chain, descriptor, recommendations, machineMatch, fullResult, midiData;

  // MIDI export uses pre-extracted MIDI data from analysis
  if (format === 'midi') {
    if (currentResult.midi_stems && stem && currentResult.midi_stems[stem]) {
      // Per-stem MIDI (bass or melody)
      midiData = currentResult.midi_stems[stem];
    } else if (currentResult.midi) {
      // Legacy single MIDI result
      midiData = currentResult.midi;
    }

    if (!midiData) {
      setStatus('error', 'No MIDI data. Enable "Deep analysis" for MIDI extraction, or upload a file directly.');
      return;
    }
    chain = [];
    descriptor = {};
  // End-to-end reconstruction: extracted MIDI + matched preset → .als
  } else if (format === 'reconstruction') {
    // Find the best available MIDI to attach. Prefer the single-pass MIDI
    // result, then fall back to any present stem.
    let recoMidi = currentResult.midi || null;
    if (!recoMidi && currentResult.midi_stems) {
      const preferred = ['bass', 'vocals', 'other', 'drums'];
      for (const key of preferred) {
        if (currentResult.midi_stems[key]?.content) {
          recoMidi = currentResult.midi_stems[key];
          break;
        }
      }
    }
    if (!recoMidi) {
      setStatus('error', 'Reconstruction requires MIDI extraction. Enable "Deep analysis" or re-analyze with MIDI on.');
      return;
    }
    midiData = recoMidi;
    fullResult = currentResult;
    chain = [];
    descriptor = currentResult.descriptor || {};
  // Project Bundle needs deep analysis with MIDI stems
  } else if (format === 'project_bundle') {
    if (!currentResult.midi_stems || Object.keys(currentResult.midi_stems).length === 0) {
      setStatus('error', 'Project Bundle requires "Deep analysis" with stem separation.');
      return;
    }
    fullResult = currentResult;
    chain = [];
    descriptor = currentResult.descriptor || {};
  // Ableton Live Set needs deep analysis with MIDI stems
  } else if (format === 'ableton_live_set') {
    if (!currentResult.midi_stems || Object.keys(currentResult.midi_stems).length === 0) {
      setStatus('error', 'Ableton Live Set requires "Deep analysis" with stem separation.');
      return;
    }
    fullResult = currentResult;
    chain = [];
    descriptor = currentResult.descriptor || currentResult.synth?.descriptor || {};
  // Text Analysis needs the full result
  } else if (format === 'text') {
    fullResult = currentResult;
    descriptor = currentResult.descriptor || currentResult.guitar?.descriptor || currentResult.synth?.descriptor || {};
    chain = [];
  } else if ((format === 'ableton_wavetable' || format === 'ableton_analog') && currentResult.synth) {
    // Synth preset export needs the synth descriptor and full result for fallback
    fullResult = currentResult;
    chain = [];
    descriptor = currentResult.synth.descriptor;
  } else if (format === 'ableton_drums' && currentResult.drums) {
    chain = [];
    descriptor = currentResult.drums.descriptor;
    machineMatch = currentResult.drums.machine_match;
  } else if (format === 'ableton_synth' && currentResult.synth) {
    chain = [];
    descriptor = currentResult.synth.descriptor;
  } else if (format === 'ableton' && currentResult.guitar) {
    chain = currentResult.guitar.platforms[platform] || currentResult.guitar.platforms.helix || [];
    descriptor = currentResult.guitar.descriptor;
  } else if (format === 'bass' && currentResult.bass) {
    chain = [];
    descriptor = currentResult.bass.descriptor;
    recommendations = currentResult.bass.recommendations || [];
  } else if (format === 'drums' && currentResult.drums) {
    chain = [];
    descriptor = currentResult.drums.descriptor;
    machineMatch = currentResult.drums.machine_match;
  } else if (format.startsWith('synth') && currentResult.synth) {
    chain = [];
    descriptor = currentResult.synth.descriptor;
  } else if (currentResult.guitar) {
    chain = currentResult.guitar.platforms[platform] || currentResult.guitar.platforms.helix || [];
    descriptor = currentResult.guitar.descriptor;
  } else {
    chain = currentResult.chain || [];
    descriptor = currentResult.descriptor || {};
  }

  // Get preset name from source_name (video title) or fallback to filename
  const sourceName = currentResult.source_name || descriptor?.source?.filename || 'preset';
  const presetName = sourceName.replace(/\.[^/.]+$/, '').substring(0, 50);

  try {
    const resp = await fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chain,
        descriptor,
        format,
        preset_name: presetName,
        recommendations,
        machine_match: machineMatch,
        full_result: fullResult,
        midi_data: midiData,
      }),
    });

    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Export failed: HTTP ${resp.status}`);
    }

    const data = await resp.json();

    // Download the file
    downloadFile(data.filename, data.content, data.content_type);

    setStatus('idle', `Exported: ${data.filename}`);
  } catch (err) {
    setStatus('error', `Export failed: ${err.message}`);
    console.error(err);
  }
}

function downloadFile(filename, content, contentType) {
  let blob;

  // Handle binary content (base64 encoded) for Ableton files (.als, .adv), MIDI, and ZIP bundles
  const binaryTypes = [
    'application/x-ableton-live-set',
    'application/x-ableton-wavetable',
    'application/x-ableton-analog',
    'application/octet-stream',
    'audio/midi',
    'application/zip',
  ];

  if (binaryTypes.includes(contentType)) {
    // Decode base64 to binary
    const binaryString = atob(content);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    blob = new Blob([bytes], { type: contentType });
  } else {
    blob = new Blob([content], { type: contentType });
  }

  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// Build a chain card from a descriptor (for deep analysis results that lack chain)
function buildChainFromDescriptor(descriptor) {
  if (!descriptor) return { picks: [], tweak_hints: [] };

  const picks = [];

  // Amp
  if (descriptor.amp) {
    const amp = descriptor.amp;
    const voicing = amp.voicing || {};
    picks.push({
      slot: 'amp',
      block_id: `amp_${amp.family || 'unknown'}`,
      display: (amp.family || 'Unknown').replace(/_/g, ' ').toUpperCase(),
      params: {
        drive: Math.round((amp.gain || 0.5) * 10 * 10) / 10,
        bass: Math.round((voicing.bass || 0.5) * 10 * 10) / 10,
        mid: Math.round((voicing.mid || 0.5) * 10 * 10) / 10,
        treble: Math.round((voicing.treble || 0.5) * 10 * 10) / 10,
        presence: Math.round((voicing.presence || 0.5) * 10 * 10) / 10,
        master: 6.5,
      },
      rationale: `Detected ${amp.family || 'unknown'} amp family`,
      block_family: amp.family,
    });
  }

  // Cab
  if (descriptor.cab) {
    const cab = descriptor.cab;
    picks.push({
      slot: 'cab',
      block_id: `cab_${cab.configuration || '4x12'}`,
      display: `${cab.configuration || '4x12'} ${(cab.speaker_character || 'british').replace(/_/g, ' ')}`.toUpperCase(),
      params: { mic: '57 Dynamic', distance: 1.0 },
      rationale: `${cab.speaker_character || 'Unknown'} speaker character`,
      block_family: 'cab_ir',
    });
  }

  // Effects
  const effects = descriptor.effects || {};

  // Handle both overdrive_pedal (standard format) and overdrive (deep analysis format)
  const od = effects.overdrive_pedal || effects.overdrive;
  if (od && (od.drive > 0.05 || od.style)) {
    picks.push({
      slot: 'drive',
      block_id: `od_${od.style || 'tube_screamer'}`,
      display: (od.style || 'Overdrive').replace(/_/g, ' ').toUpperCase(),
      params: { drive: Math.round((od.drive || 0.5) * 10 * 10) / 10, level: Math.round((od.level || 0.5) * 10 * 10) / 10 },
      rationale: `${od.style || 'Overdrive'} pedal detected`,
      block_family: `overdrive_${od.style || 'generic'}`,
    });
  }

  // Compressor
  if (effects.compressor && effects.compressor.amount > 0.1) {
    const comp = effects.compressor;
    picks.push({
      slot: 'compressor',
      block_id: `comp_${comp.character || 'studio'}`,
      display: `${(comp.character || 'Studio').toUpperCase()} COMPRESSOR`,
      params: { amount: Math.round(comp.amount * 100) },
      rationale: `${comp.character || 'Studio'} compression detected`,
      block_family: 'compressor',
    });
  }

  if (effects.delay && effects.delay.type !== 'none' && effects.delay.mix > 0.03) {
    const dl = effects.delay;
    picks.push({
      slot: 'delay',
      block_id: `delay_${dl.type}`,
      display: `${dl.type} DELAY`.toUpperCase(),
      params: { time_ms: Math.round(dl.time_ms || 300), feedback: Math.round((dl.feedback || 0.3) * 10 * 10) / 10, mix: Math.round((dl.mix || 0.2) * 100) },
      rationale: `${dl.type} delay at ${Math.round(dl.time_ms || 300)}ms`,
      block_family: `delay_${dl.type}`,
    });
  }

  if (effects.reverb && effects.reverb.type !== 'none' && effects.reverb.mix > 0.03) {
    const rv = effects.reverb;
    picks.push({
      slot: 'reverb',
      block_id: `reverb_${rv.type}`,
      display: `${rv.type} REVERB`.toUpperCase(),
      params: { decay: Math.round((rv.size || 0.4) * 10 * 10) / 10, mix: Math.round((rv.mix || 0.2) * 100) },
      rationale: `${rv.type} reverb detected`,
      block_family: `reverb_${rv.type}`,
    });
  }

  if (effects.modulation && effects.modulation.type !== 'none' && effects.modulation.depth > 0.05) {
    const mod = effects.modulation;
    picks.push({
      slot: 'modulation',
      block_id: `mod_${mod.type}`,
      display: `${mod.type}`.toUpperCase(),
      params: { rate: Math.round((mod.rate || 0.5) * 10 * 10) / 10, depth: Math.round((mod.depth || 0.5) * 10 * 10) / 10 },
      rationale: `${mod.type} modulation detected`,
      block_family: `modulation_${mod.type}`,
    });
  }

  return { picks, tweak_hints: [] };
}

function renderUnifiedResult(data) {
  // Normalize deep analysis format (has tone but no guitar) to unified format
  if (data.tone && !data.guitar) {
    const tone = data.tone;
    // Deep analysis stores amp/cab/effects directly in tone, not in tone.descriptor
    const descriptor = {
      amp: tone.amp,
      cab: tone.cab,
      effects: tone.effects,
      guitar: tone.guitar,
      confidence: tone.confidence,
    };
    // Build guitar structure from tone data
    data.guitar = {
      descriptor: descriptor,
      platforms: {
        helix: buildChainFromDescriptor(descriptor),
      },
      tweak_hints: tone.tweak_hints || [],
    };
    // Set detected type if not set
    if (!data.detected_type) {
      data.detected_type = 'guitar';
    }
  }

  resultEmpty.hidden = true;
  resultEl.hidden = false;

  // Re-trigger animation
  resultEl.style.animation = 'none';
  void resultEl.offsetWidth;
  resultEl.style.animation = '';

  // Show detection info badge
  const detectionInfo = $('#detection-info');
  const detectionBadge = $('#detection-badge');
  if (data.detection) {
    detectionBadge.textContent = data.detection.summary;
    detectionBadge.className = 'detection-badge detection-badge--' + data.detected_type;
    detectionInfo.hidden = false;
  } else {
    detectionInfo.hidden = true;
  }

  // Use shared detection utility for tab visibility
  const conf = data.detection?.confidence || {};
  const tabs = document.querySelectorAll('.tab');

  // Update tab visibility using shared utility
  const firstVisibleTab = ToneDetection.updateTabVisibility(tabs, conf, data);

  // Default to primary detected type's tab
  const defaultPlatform = ToneDetection.getDefaultPlatform(data.detected_type);

  // Find the default tab - if it's hidden, use first visible tab
  let defaultTab = document.querySelector(`.tab[data-platform="${defaultPlatform}"]`);
  if (defaultTab && defaultTab.style.display === 'none') {
    const firstVisible = document.querySelector('.tab:not([style*="display: none"])');
    if (firstVisible) defaultTab = firstVisible;
  }
  if (defaultTab) defaultTab.classList.add('tab--active');

  renderForPlatform(data, defaultTab?.dataset.platform || defaultPlatform);

  // Update MIDI button state - now shows per-stem options
  updateMidiButtons(data);

  // Update project bundle button state
  updateProjectBundleButton(data);

  // Update ALS button state
  updateALSButton(data);

  // Update Reconstruction button state
  updateReconstructionButton(data);
}

function updateMidiButtons(data) {
  const midiBtn = document.getElementById('btn-midi');
  if (!midiBtn) return;

  // Remove any previously added stem rows
  const oldStemRows = document.querySelectorAll('.export-row--midi-stem');
  oldStemRows.forEach(row => row.remove());

  if (data.midi_stems && Object.keys(data.midi_stems).length > 0) {
    // We have per-stem MIDI - update main button and add stem rows
    const totalNotes = Object.values(data.midi_stems).reduce((sum, s) => sum + (s.note_count || 0), 0);
    const stemCount = Object.keys(data.midi_stems).length;
    midiBtn.classList.remove('btn--disabled');
    midiBtn.disabled = false;
    midiBtn.textContent = `${totalNotes} notes`;
    midiBtn.dataset.stem = 'all';

    // Add individual stem rows after the MIDI row
    const midiRow = midiBtn.closest('.export-row');
    if (midiRow) {
      const stemOrder = ['drums', 'bass', 'guitar', 'piano', 'other', 'vocals'];
      let insertAfter = midiRow;

      for (const stemKey of stemOrder) {
        if (data.midi_stems[stemKey]) {
          const stemData = data.midi_stems[stemKey];
          const label = stemData.label || stemKey.charAt(0).toUpperCase() + stemKey.slice(1);

          const row = document.createElement('tr');
          row.className = 'export-row export-row--midi-stem';
          row.innerHTML = `
            <td class="export-cell export-cell--name" style="padding-left: 20px;">↳ ${label}</td>
            <td class="export-cell export-cell--desc">${stemData.note_count} notes from isolated stem</td>
            <td class="export-cell export-cell--action">
              <button class="btn btn--export btn--midi-stem" data-format="midi" data-stem="${stemKey}">Export</button>
            </td>
          `;
          insertAfter.after(row);
          insertAfter = row;
        }
      }
    }

  } else if (data.midi) {
    // Legacy single MIDI result - show note count
    midiBtn.classList.remove('btn--disabled');
    midiBtn.disabled = false;
    midiBtn.textContent = `${data.midi.note_count} notes`;
    midiBtn.dataset.stem = '';
  } else {
    // No MIDI data - show export button (always available for polyphonic extraction)
    midiBtn.classList.remove('btn--disabled');
    midiBtn.disabled = false;
    midiBtn.textContent = 'Export';
    midiBtn.dataset.stem = '';
  }
}

function updateProjectBundleButton(data) {
  const bundleBtn = document.getElementById('btn-project-bundle');
  if (!bundleBtn) return;

  const hasMidiStems = data.midi_stems && Object.keys(data.midi_stems).length > 0;
  const row = bundleBtn.closest('.export-row');

  if (hasMidiStems) {
    const stemCount = Object.keys(data.midi_stems).length;
    bundleBtn.classList.remove('btn--disabled');
    bundleBtn.disabled = false;
    bundleBtn.textContent = `${stemCount} stems`;
    if (row) row.classList.remove('export-row--deep');
  } else {
    bundleBtn.classList.add('btn--disabled');
    bundleBtn.disabled = true;
    bundleBtn.textContent = 'Deep only';
    if (row) row.classList.add('export-row--deep');
  }
}

function updateALSButton(data) {
  const alsBtn = document.getElementById('btn-als');
  if (!alsBtn) return;

  const hasMidiStems = data.midi_stems && Object.keys(data.midi_stems).length > 0;
  const row = alsBtn.closest('.export-row');

  if (hasMidiStems) {
    const stemCount = Object.keys(data.midi_stems).length;
    alsBtn.classList.remove('btn--disabled');
    alsBtn.disabled = false;
    alsBtn.textContent = `${stemCount} tracks`;
    if (row) row.classList.remove('export-row--deep');
  } else {
    alsBtn.classList.add('btn--disabled');
    alsBtn.disabled = true;
    alsBtn.textContent = 'Deep only';
    if (row) row.classList.add('export-row--deep');
  }
}

function updateReconstructionButton(data) {
  const btn = document.getElementById('btn-reconstruction');
  if (!btn) return;

  // Reconstruction needs at least one usable MIDI source. Accept either a
  // single-pass MIDI result or any per-stem MIDI with notes.
  const hasSingleMidi = !!(data.midi && data.midi.content);
  const hasAnyStemMidi = !!(
    data.midi_stems &&
    Object.values(data.midi_stems).some((s) => s && s.content)
  );

  if (hasSingleMidi || hasAnyStemMidi) {
    btn.classList.remove('btn--disabled');
    btn.disabled = false;
    btn.textContent = 'Download .als';
  } else {
    btn.classList.add('btn--disabled');
    btn.disabled = true;
    btn.textContent = 'Needs MIDI';
  }
}

function renderForPlatform(data, platform) {
  const timestamp = data.source_timestamp;
  const sourceUrl = data.source_url;
  const sourceName = data.source_name;

  // Hide synth hardware section by default
  $('#synth-hardware-section').hidden = true;

  if (platform === 'synth' && data.synth) {
    renderSynthChain(data.synth);
    updateHeader(data.synth.descriptor, 'synth', timestamp, sourceUrl, sourceName);
  } else if (platform === 'bass' && data.bass) {
    renderBassChain(data.bass);
    updateHeader(data.bass.descriptor, 'bass', timestamp, sourceUrl, sourceName);
    renderHints(data.bass.tweak_hints);
  } else if (platform === 'drums' && data.drums) {
    renderDrumsChain(data.drums);
    updateHeader(data.drums.descriptor, 'drums', timestamp, sourceUrl, sourceName);
    renderHints(data.drums.tweak_hints);
  } else if (data.guitar && data.guitar.platforms[platform]) {
    renderGuitarChain(data.guitar.platforms[platform], platform);
    updateHeader(data.guitar.descriptor, 'guitar', timestamp, sourceUrl, sourceName);
    renderHints(data.guitar.tweak_hints);
  } else if (data.guitar && platform === 'helix') {
    // Fallback to helix
    renderGuitarChain(data.guitar.platforms.helix, 'helix');
    updateHeader(data.guitar.descriptor, 'guitar', timestamp, sourceUrl, sourceName);
    renderHints(data.guitar.tweak_hints);
  } else {
    // No data for this platform - show empty state with appropriate message
    const platformName = platform.charAt(0).toUpperCase() + platform.slice(1);
    $('#chain-list').innerHTML = `<li class="chain__item"><div class="chain__body">No ${platformName} analysis available. Deep analysis currently focuses on the guitar/other stem.</div></li>`;

    // Update header to reflect selected platform (show N/A values)
    const displayName = sourceName || 'Analysis';
    $('#r-filename').textContent = displayName;

    // Update labels based on platform type
    if (platform === 'drums') {
      $('#r-family-label').textContent = 'machine';
      $('#r-gain-label').textContent = 'tempo';
    } else if (platform === 'synth') {
      $('#r-family-label').textContent = 'oscillator';
      $('#r-gain-label').textContent = 'brightness';
    } else if (platform === 'bass') {
      $('#r-family-label').textContent = 'amp family';
      $('#r-gain-label').textContent = 'gain';
    } else {
      $('#r-family-label').textContent = 'amp family';
      $('#r-gain-label').textContent = 'gain';
    }

    // Show dashes for values since no data
    $('#r-amp-family').textContent = '-';
    $('#r-gain').textContent = '-';
    $('#r-conf').textContent = '-';
    $('#r-duration').textContent = data.duration_sec ? `${data.duration_sec.toFixed(1)}s` : '-';

    renderHints([]);
  }

  // Raw JSON
  $('#raw-json').textContent = JSON.stringify(data, null, 2);
}

function updateHeader(descriptor, type, timestamp = null, sourceUrl = null, sourceName = null) {
  const timestampSuffix = timestamp ? ` @ ${formatTimestamp(timestamp)}` : '';
  const filenameEl = $('#r-filename');

  // Helper to set filename as link or text
  function setFilename(name) {
    filenameEl.innerHTML = '';
    const displayName = (sourceName || name) + timestampSuffix;

    if (sourceUrl) {
      const link = document.createElement('a');
      link.href = sourceUrl;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = displayName;
      link.className = 'source-link';
      filenameEl.appendChild(link);
    } else {
      filenameEl.textContent = displayName;
    }
  }

  if (type === 'synth') {
    setFilename('Synth Analysis');
    $('#r-family-label').textContent = 'oscillator';
    $('#r-amp-family').textContent = descriptor.oscillator?.type || '-';
    $('#r-gain-label').textContent = 'brightness';
    $('#r-gain').textContent = (descriptor.brightness || 0).toFixed(2);
    // Use detection confidence from currentResult if available
    const synthConf = currentResult?.detection?.confidence?.instrument;
    $('#r-conf').textContent = synthConf ? `${Math.round(synthConf * 100)}%` : '-';
    $('#r-duration').textContent = `${(descriptor.duration_sec || 0).toFixed(1)}s`;
  } else if (type === 'bass') {
    const filename = descriptor.source?.filename || 'Bass Analysis';
    setFilename(filename);
    $('#r-family-label').textContent = 'amp family';
    $('#r-amp-family').textContent = (descriptor.amp?.family || '-').replace(/_/g, ' ');
    $('#r-gain-label').textContent = 'gain';
    $('#r-gain').textContent = (descriptor.amp?.gain || 0).toFixed(2);
    $('#r-conf').textContent = `${Math.round((descriptor.confidence?.amp_family || 0) * 100)}%`;
    $('#r-duration').textContent = `${(descriptor.source?.duration_sec || 0).toFixed(1)}s`;
  } else if (type === 'drums') {
    const filename = descriptor.source?.filename || 'Drum Analysis';
    setFilename(filename);
    $('#r-family-label').textContent = 'machine';
    $('#r-amp-family').textContent = descriptor.matched_machine?.replace(/_/g, ' ') || '-';
    $('#r-gain-label').textContent = 'tempo';
    $('#r-gain').textContent = `${(descriptor.overall?.tempo_bpm || 0).toFixed(0)} BPM`;
    $('#r-conf').textContent = `${Math.round((descriptor.confidence?.style || 0) * 100)}%`;
    $('#r-duration').textContent = `${(descriptor.source?.duration_sec || 0).toFixed(1)}s`;
  } else {
    const filename = descriptor.source?.filename || '-';
    setFilename(filename);
    $('#r-family-label').textContent = 'amp family';
    $('#r-amp-family').textContent = (descriptor.amp?.family || '-').replace(/_/g, ' ');
    $('#r-gain-label').textContent = 'gain';
    $('#r-gain').textContent = (descriptor.amp?.gain || 0).toFixed(2);
    $('#r-conf').textContent = `${Math.round((descriptor.confidence?.amp_family || 0) * 100)}%`;
    $('#r-duration').textContent = `${(descriptor.source?.duration_sec || 0).toFixed(1)}s`;
  }
}

function renderGuitarChain(chain, platform) {
  const list = $('#chain-list');
  list.innerHTML = '';
  let n = 0;

  for (const pick of chain) {
    const isAlt = pick.slot === 'amp_alt';
    if (!isAlt) n += 1;

    const li = document.createElement('li');
    li.className = 'chain__item' + (isAlt ? ' chain__item--alt' : '');

    const numEl = document.createElement('div');
    numEl.className = 'chain__num' + (isAlt ? ' chain__num--alt' : '');
    numEl.textContent = isAlt ? 'A/B' : String(n).padStart(2, '0');

    const body = document.createElement('div');
    body.className = 'chain__body';

    const slotEl = document.createElement('div');
    slotEl.className = 'chain__slot';
    slotEl.textContent = isAlt ? 'alternate amp pick' : pick.slot;

    const dispEl = document.createElement('div');
    dispEl.className = 'chain__display';
    dispEl.textContent = pick.display;

    // Add price badge for pedals
    if (pick.price_estimate) {
      const priceEl = document.createElement('span');
      priceEl.className = 'chain__price';
      priceEl.textContent = pick.price_estimate;
      dispEl.appendChild(priceEl);
    }

    const ratEl = document.createElement('div');
    ratEl.className = 'chain__rationale';
    ratEl.textContent = pick.rationale;

    body.appendChild(slotEl);
    body.appendChild(dispEl);
    body.appendChild(ratEl);

    if (!isAlt && pick.params && Object.keys(pick.params).length) {
      const params = document.createElement('div');
      params.className = 'params';
      for (const [k, v] of Object.entries(pick.params)) {
        const chip = document.createElement('span');
        chip.className = 'param';
        chip.innerHTML =
          `<span class="param__key">${k.replace(/_/g, ' ')}</span>` +
          `<span class="param__val">${formatParamVal(v)}</span>`;
        params.appendChild(chip);
      }
      body.appendChild(params);
    }

    li.appendChild(numEl);
    li.appendChild(body);
    list.appendChild(li);
  }
}

function renderSynthChain(synthData) {
  const desc = synthData.descriptor;
  const list = $('#chain-list');
  list.innerHTML = '';

  // Oscillator
  const oscLi = createChainItem(1, 'oscillator', `${(desc.oscillator?.type || 'unknown').toUpperCase()} Wave`, getSynthOscRationale(desc));
  list.appendChild(oscLi);

  // Filter
  if (desc.filter && desc.filter.cutoff_normalized < 0.95) {
    const filtLi = createChainItem(2, 'filter', `Lowpass @ ${Math.round(desc.filter.cutoff_hz)}Hz`,
      `Resonance: ${(desc.filter.resonance * 100).toFixed(0)}%`);
    list.appendChild(filtLi);
  }

  // Envelope
  if (desc.amp_envelope) {
    const envLi = createChainItem(3, 'envelope',
      `A:${Math.round(desc.amp_envelope.attack_ms)}ms D:${Math.round(desc.amp_envelope.decay_ms)}ms`,
      `S:${(desc.amp_envelope.sustain * 100).toFixed(0)}% R:${Math.round(desc.amp_envelope.release_ms)}ms`);
    list.appendChild(envLi);
  }

  // LFO
  if (desc.lfo && desc.lfo.rate_hz > 0) {
    const lfoLi = createChainItem(4, 'lfo',
      `${desc.lfo.rate_hz.toFixed(1)}Hz -> ${desc.lfo.target}`,
      `Depth: ${(desc.lfo.depth * 100).toFixed(0)}%`);
    list.appendChild(lfoLi);
  }

  // Effects
  const effects = [];
  if (desc.has_chorus) effects.push('Chorus');
  if (desc.has_phaser) effects.push('Phaser');
  if (desc.has_reverb) effects.push('Reverb');
  if (desc.has_delay) effects.push('Delay');
  if (effects.length > 0) {
    const fxLi = createChainItem(5, 'effects', effects.join(' + '), 'Detected in audio');
    list.appendChild(fxLi);
  }

  // Hints
  renderHints(synthData.tweak_hints);

  // Show synth hardware section
  $('#synth-hardware-section').hidden = false;
}

// -----------------------------------------------------------------------------
// Bass Chain
// -----------------------------------------------------------------------------

function renderBassChain(bassData) {
  const list = $('#chain-list');
  list.innerHTML = '';

  const desc = bassData.descriptor;
  const recommendations = bassData.recommendations || [];

  // Group recommendations by category
  const ampRec = recommendations.find(r => r.category === 'amp');
  const cabRec = recommendations.find(r => r.category === 'cab');
  const driveRec = recommendations.find(r => r.category === 'drive');
  const compRec = recommendations.find(r => r.category === 'compressor');
  const modRec = recommendations.find(r => r.category === 'modulation');
  const octRec = recommendations.find(r => r.category === 'octaver');

  let idx = 0;

  // Bass Amp
  if (ampRec) {
    const params = ampRec.params || {};
    const paramStr = Object.entries(params)
      .map(([k, v]) => `${k}: ${typeof v === 'number' ? v.toFixed(1) : v}`)
      .join(', ');
    const li = createChainItem(idx++, 'amp',
      ampRec.display,
      paramStr || ampRec.models,
      ampRec.price_estimate);
    list.appendChild(li);
  } else if (desc.amp) {
    const li = createChainItem(idx++, 'amp',
      desc.amp.family.replace(/_/g, ' ').toUpperCase(),
      `Gain: ${desc.amp.gain.toFixed(1)} · ${desc.amp.voicing} voicing`,
      null);
    list.appendChild(li);
  }

  // Bass Cab
  if (cabRec) {
    const li = createChainItem(idx++, 'cab',
      cabRec.display,
      cabRec.models,
      cabRec.price_estimate);
    list.appendChild(li);
  } else if (desc.cab) {
    const li = createChainItem(idx++, 'cab',
      desc.cab.config,
      desc.cab.character,
      null);
    list.appendChild(li);
  }

  // Compressor (if detected)
  if (compRec) {
    const params = compRec.params || {};
    const paramStr = Object.entries(params)
      .map(([k, v]) => `${k}: ${typeof v === 'number' ? v.toFixed(1) : v}`)
      .join(', ');
    const li = createChainItem(idx++, 'comp',
      compRec.display,
      paramStr || compRec.models,
      compRec.price_estimate);
    list.appendChild(li);
  } else if (desc.effects?.compression > 0.3) {
    const li = createChainItem(idx++, 'comp',
      'Compression',
      `Amount: ${(desc.effects.compression * 100).toFixed(0)}%`,
      null);
    list.appendChild(li);
  }

  // Drive/Overdrive
  if (driveRec) {
    const params = driveRec.params || {};
    const paramStr = Object.entries(params)
      .map(([k, v]) => `${k}: ${typeof v === 'number' ? v.toFixed(1) : v}`)
      .join(', ');
    const li = createChainItem(idx++, 'drive',
      driveRec.display,
      paramStr || driveRec.models,
      driveRec.price_estimate);
    list.appendChild(li);
  } else if (desc.effects?.overdrive > 0.2) {
    const li = createChainItem(idx++, 'drive',
      'Overdrive',
      `Amount: ${(desc.effects.overdrive * 100).toFixed(0)}%`,
      null);
    list.appendChild(li);
  }

  // Chorus/Modulation
  if (modRec) {
    const params = modRec.params || {};
    const paramStr = Object.entries(params)
      .map(([k, v]) => `${k}: ${typeof v === 'number' ? v.toFixed(1) : v}`)
      .join(', ');
    const li = createChainItem(idx++, 'mod',
      modRec.display,
      paramStr || modRec.models,
      modRec.price_estimate);
    list.appendChild(li);
  } else if (desc.effects?.chorus > 0.2) {
    const li = createChainItem(idx++, 'mod',
      'Chorus',
      `Amount: ${(desc.effects.chorus * 100).toFixed(0)}%`,
      null);
    list.appendChild(li);
  }

  // Octaver
  if (octRec) {
    const params = octRec.params || {};
    const paramStr = Object.entries(params)
      .map(([k, v]) => `${k}: ${typeof v === 'number' ? v.toFixed(1) : v}`)
      .join(', ');
    const li = createChainItem(idx++, 'oct',
      octRec.display,
      paramStr || octRec.models,
      octRec.price_estimate);
    list.appendChild(li);
  } else if (desc.effects?.octaver > 0.2) {
    const li = createChainItem(idx++, 'oct',
      'Octaver',
      `Amount: ${(desc.effects.octaver * 100).toFixed(0)}%`,
      null);
    list.appendChild(li);
  }

  // Technique badge
  if (desc.technique && desc.technique !== 'fingerstyle') {
    const techLi = document.createElement('li');
    techLi.className = 'chain__item chain__item--badge';
    techLi.innerHTML = `<div class="chain__badge">${desc.technique.toUpperCase()}</div>`;
    list.appendChild(techLi);
  }
}

// -----------------------------------------------------------------------------
// Drums Chain
// -----------------------------------------------------------------------------

function renderDrumsChain(drumsData) {
  const list = $('#chain-list');
  list.innerHTML = '';

  const desc = drumsData.descriptor;
  const machineMatch = drumsData.machine_match;

  let idx = 0;

  // Matched Drum Machine
  if (machineMatch) {
    const li = createChainItem(idx++, 'machine',
      machineMatch.display,
      machineMatch.description,
      machineMatch.price_estimate);
    list.appendChild(li);

    // Show match score
    if (machineMatch.match_score) {
      const scoreLi = document.createElement('li');
      scoreLi.className = 'chain__item chain__item--note';
      scoreLi.innerHTML = `<div class="chain__note">Match confidence: ${Math.round(machineMatch.match_score * 100)}%</div>`;
      list.appendChild(scoreLi);
    }
  }

  // Kick characteristics
  if (desc.kick) {
    const kickInfo = [];
    if (desc.kick.pitch_hz) kickInfo.push(`Pitch: ${desc.kick.pitch_hz.toFixed(0)}Hz`);
    if (desc.kick.decay_ms) kickInfo.push(`Decay: ${desc.kick.decay_ms.toFixed(0)}ms`);
    if (desc.kick.saturation !== undefined) kickInfo.push(`Saturation: ${(desc.kick.saturation * 100).toFixed(0)}%`);

    if (kickInfo.length > 0) {
      const li = createChainItem(idx++, 'kick',
        'Kick',
        kickInfo.join(' · '),
        null);
      list.appendChild(li);
    }
  }

  // Snare characteristics
  if (desc.snare) {
    const snareInfo = [];
    if (desc.snare.pitch_hz) snareInfo.push(`Pitch: ${desc.snare.pitch_hz.toFixed(0)}Hz`);
    if (desc.snare.noise !== undefined) snareInfo.push(`Noise: ${(desc.snare.noise * 100).toFixed(0)}%`);
    if (desc.snare.snap !== undefined) snareInfo.push(`Snap: ${(desc.snare.snap * 100).toFixed(0)}%`);

    if (snareInfo.length > 0) {
      const li = createChainItem(idx++, 'snare',
        'Snare',
        snareInfo.join(' · '),
        null);
      list.appendChild(li);
    }
  }

  // Hi-hat characteristics
  if (desc.hihat) {
    const hihatInfo = [];
    if (desc.hihat.open_closed_ratio !== undefined) {
      const ratio = desc.hihat.open_closed_ratio;
      const state = ratio > 0.7 ? 'Mostly Open' : ratio < 0.3 ? 'Mostly Closed' : 'Mixed';
      hihatInfo.push(state);
    }
    if (desc.hihat.decay_ms) hihatInfo.push(`Decay: ${desc.hihat.decay_ms.toFixed(0)}ms`);

    if (hihatInfo.length > 0) {
      const li = createChainItem(idx++, 'hihat',
        'Hi-Hat',
        hihatInfo.join(' · '),
        null);
      list.appendChild(li);
    }
  }

  // Tempo and swing
  const tempoInfo = [];
  if (desc.tempo_bpm) tempoInfo.push(`${desc.tempo_bpm.toFixed(0)} BPM`);
  if (desc.swing !== undefined && desc.swing > 0.1) tempoInfo.push(`Swing: ${(desc.swing * 100).toFixed(0)}%`);

  if (tempoInfo.length > 0) {
    const li = createChainItem(idx++, 'tempo',
      'Tempo',
      tempoInfo.join(' · '),
      null);
    list.appendChild(li);
  }

  // Compression
  if (desc.compression !== undefined && desc.compression > 0.2) {
    const li = createChainItem(idx++, 'comp',
      'Compression',
      `Amount: ${(desc.compression * 100).toFixed(0)}%`,
      null);
    list.appendChild(li);
  }

  // Machine parameters (if matched and has actual params)
  if (machineMatch && machineMatch.suggested_params && Object.keys(machineMatch.suggested_params).length > 0) {
    const paramsHtml = Object.entries(machineMatch.suggested_params)
      .map(([section, params]) => {
        const paramList = Object.entries(params)
          .map(([k, v]) => `<span class="param">${k}: ${typeof v === 'number' ? v.toFixed(1) : v}</span>`)
          .join(' ');
        return `<div class="param-section"><strong>${section}:</strong> ${paramList}</div>`;
      })
      .join('');

    // Only add if we actually have param HTML
    if (paramsHtml) {
      const paramsLi = document.createElement('li');
      paramsLi.className = 'chain__item chain__item--params';
      paramsLi.innerHTML = `
        <div class="chain__head">
          <span class="chain__index">${idx++}</span>
          <span class="chain__type">Suggested Settings</span>
        </div>
        <div class="chain__body chain__body--params">${paramsHtml}</div>
      `;
      list.appendChild(paramsLi);
    }
  }
}

// -----------------------------------------------------------------------------
// Synth Hardware
// -----------------------------------------------------------------------------

const synthHardwareSelect = $('#synth-hardware-select');
const synthHardwareSection = $('#synth-hardware-section');
const synthHardwareConfig = $('#synth-hardware-config');

async function loadSynthHardwareOptions() {
  try {
    const resp = await fetch('/api/synth-hardware');
    if (!resp.ok) return;
    const data = await resp.json();

    synthHardwareSelect.innerHTML = '<option value="">Select a hardware synth...</option>';
    for (const hw of data.hardware) {
      const option = document.createElement('option');
      option.value = hw.id;
      option.textContent = `${hw.name} ${hw.price ? '(' + hw.price + ')' : ''}`;
      synthHardwareSelect.appendChild(option);
    }
  } catch (err) {
    console.error('Failed to load synth hardware:', err);
  }
}

synthHardwareSelect.addEventListener('change', async () => {
  const hardwareId = synthHardwareSelect.value;
  if (!hardwareId || !currentResult?.synth?.descriptor) {
    synthHardwareConfig.hidden = true;
    return;
  }

  try {
    const resp = await fetch('/api/synth-hardware', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        synth_descriptor: currentResult.synth.descriptor,
        hardware_id: hardwareId,
      }),
    });

    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || 'Failed to translate');
    }

    const config = await resp.json();
    renderSynthHardwareConfig(config);
  } catch (err) {
    console.error('Failed to get hardware config:', err);
    synthHardwareConfig.hidden = true;
  }
});

// Map of synth models to their SVG panel files
const SYNTH_PANELS = {
  'volca_keys': '/static/synth-panels/volca-keys.svg',
};

async function renderSynthHardwareConfig(config) {
  $('#synth-hw-name').textContent = config.synth_name;
  $('#synth-hw-desc').textContent = config.description;

  // Load and render SVG panel if available
  const panelContainer = $('#synth-panel-container');
  if (SYNTH_PANELS[config.synth_model]) {
    try {
      const resp = await fetch(SYNTH_PANELS[config.synth_model]);
      const svgText = await resp.text();
      panelContainer.innerHTML = svgText;
      panelContainer.hidden = false;

      // Update knob positions based on control values
      updateSynthPanelKnobs(config.controls);
    } catch (err) {
      console.error('Failed to load synth panel:', err);
      panelContainer.hidden = true;
    }
  } else {
    panelContainer.innerHTML = '';
    panelContainer.hidden = true;
  }

  // Render controls as knob-like displays (fallback/additional info)
  const controlsEl = $('#synth-controls');
  controlsEl.innerHTML = '';

  for (const ctrl of config.controls) {
    const div = document.createElement('div');
    div.className = 'synth-control';
    div.innerHTML = `
      <div class="synth-control__name">${ctrl.name}</div>
      <div class="synth-control__value">${ctrl.display}</div>
      ${ctrl.note ? `<div class="synth-control__note">${ctrl.note}</div>` : ''}
    `;
    controlsEl.appendChild(div);
  }

  // Render notes
  const notesEl = $('#synth-notes');
  notesEl.innerHTML = '';
  for (const note of config.notes) {
    const li = document.createElement('li');
    li.textContent = note;
    notesEl.appendChild(li);
  }

  synthHardwareConfig.hidden = false;
}

function updateSynthPanelKnobs(controls) {
  const panelContainer = $('#synth-panel-container');
  const svg = panelContainer.querySelector('svg');
  if (!svg) return;

  // Create a map of control names to values
  const controlMap = {};
  for (const ctrl of controls) {
    controlMap[ctrl.name] = ctrl.value;
  }

  // Find all knobs in the SVG and rotate them based on values
  const knobGroups = svg.querySelectorAll('.knob');
  knobGroups.forEach(knob => {
    const controlName = knob.getAttribute('data-control');
    if (!controlName) return;

    // Find matching control value
    let value = controlMap[controlName];
    if (value === undefined) {
      // Try partial match
      for (const [name, val] of Object.entries(controlMap)) {
        if (name.includes(controlName) || controlName.includes(name)) {
          value = val;
          break;
        }
      }
    }

    if (value !== undefined) {
      // Convert value (0-127) to rotation angle (-135 to +135 degrees)
      const maxValue = 127;
      const normalizedValue = Math.min(value / maxValue, 1);
      const angle = -135 + (normalizedValue * 270);

      // Find the indicator line and rotate it
      const indicator = knob.querySelector('.knob-indicator');
      if (indicator) {
        // Get the center of the knob (cx, cy of the circle)
        const circle = knob.querySelector('circle');
        const cx = parseFloat(circle.getAttribute('cx')) || 0;
        const cy = parseFloat(circle.getAttribute('cy')) || 0;
        indicator.setAttribute('transform', `rotate(${angle}, ${cx}, ${cy})`);
      }

      // Update the value text if present
      const valueText = knob.querySelector('.knob-value');
      if (valueText) {
        valueText.textContent = Math.round(value);
      }
    }
  });
}

// Hide synth hardware section when switching to non-synth tabs
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('tab')) {
    const platform = e.target.dataset.platform;
    if (platform !== 'synth') {
      synthHardwareSection.hidden = true;
    }
  }
});

// Load hardware options on page load
loadSynthHardwareOptions();

function renderHints(hints) {
  const hintsWrap = $('#hints-wrap');
  const hintsList = $('#hints-list');
  hintsList.innerHTML = '';
  if (hints && hints.length) {
    for (const h of hints) {
      const li = document.createElement('li');
      li.textContent = h;
      hintsList.appendChild(li);
    }
    hintsWrap.hidden = false;
  } else {
    hintsWrap.hidden = true;
  }
}

// Legacy render function for backward compatibility
function renderResult({ descriptor, chain, tweak_hints, platform, alternatives }) {
  resultEmpty.hidden = true;
  resultEl.hidden = false;

  // Re-trigger animation when re-rendering.
  resultEl.style.animation = 'none';
  void resultEl.offsetWidth;
  resultEl.style.animation = '';

  $('#r-filename').textContent = descriptor.source.filename || '-';
  $('#r-amp-family').textContent = descriptor.amp.family.replace(/_/g, ' ');
  $('#r-gain').textContent = descriptor.amp.gain.toFixed(2);
  $('#r-conf').textContent = `${Math.round(descriptor.confidence.amp_family * 100)}%`;
  $('#r-duration').textContent = `${descriptor.source.duration_sec.toFixed(1)}s`;

  // Chain list
  const list = $('#chain-list');
  list.innerHTML = '';
  let n = 0;
  for (const pick of chain) {
    const isAlt = pick.slot === 'amp_alt';
    if (!isAlt) n += 1;

    const li = document.createElement('li');
    li.className = 'chain__item' + (isAlt ? ' chain__item--alt' : '');

    const numEl = document.createElement('div');
    numEl.className = 'chain__num' + (isAlt ? ' chain__num--alt' : '');
    numEl.textContent = isAlt ? 'A/B' : String(n).padStart(2, '0');

    const body = document.createElement('div');
    body.className = 'chain__body';

    const slotEl = document.createElement('div');
    slotEl.className = 'chain__slot';
    slotEl.textContent = isAlt ? 'alternate amp pick' : pick.slot;

    const dispEl = document.createElement('div');
    dispEl.className = 'chain__display';
    dispEl.textContent = pick.display;

    // Add price badge for pedals
    if (pick.price_estimate) {
      const priceEl = document.createElement('span');
      priceEl.className = 'chain__price';
      priceEl.textContent = pick.price_estimate;
      dispEl.appendChild(priceEl);
    }

    const ratEl = document.createElement('div');
    ratEl.className = 'chain__rationale';
    ratEl.textContent = pick.rationale;

    body.appendChild(slotEl);
    body.appendChild(dispEl);
    body.appendChild(ratEl);

    if (!isAlt && pick.params && Object.keys(pick.params).length) {
      const params = document.createElement('div');
      params.className = 'params';
      for (const [k, v] of Object.entries(pick.params)) {
        const chip = document.createElement('span');
        chip.className = 'param';
        chip.innerHTML =
          `<span class="param__key">${k.replace(/_/g, ' ')}</span>` +
          `<span class="param__val">${formatParamVal(v)}</span>`;
        params.appendChild(chip);
      }
      body.appendChild(params);
    }

    li.appendChild(numEl);
    li.appendChild(body);
    list.appendChild(li);
  }

  // Hints
  const hintsWrap = $('#hints-wrap');
  const hintsList = $('#hints-list');
  hintsList.innerHTML = '';
  if (tweak_hints && tweak_hints.length) {
    for (const h of tweak_hints) {
      const li = document.createElement('li');
      li.textContent = h;
      hintsList.appendChild(li);
    }
    hintsWrap.hidden = false;
  } else {
    hintsWrap.hidden = true;
  }

  // Raw JSON
  $('#raw-json').textContent = JSON.stringify({ descriptor, chain, tweak_hints, platform, alternatives }, null, 2);
}

function renderSynthResult({ descriptor, chain, tweak_hints }) {
  resultEmpty.hidden = true;
  resultEl.hidden = false;

  // Re-trigger animation
  resultEl.style.animation = 'none';
  void resultEl.offsetWidth;
  resultEl.style.animation = '';

  // Update header for synth
  $('#r-filename').textContent = 'Synth Analysis';
  $('#r-amp-family').textContent = descriptor.oscillator.type;
  $('#r-gain').textContent = descriptor.brightness.toFixed(2);
  $('#r-conf').textContent = '-';
  $('#r-duration').textContent = `${descriptor.duration_sec.toFixed(1)}s`;

  // Build synth chain display
  const list = $('#chain-list');
  list.innerHTML = '';

  // Oscillator
  const oscLi = createChainItem(1, 'oscillator', `${descriptor.oscillator.type.toUpperCase()} Wave`, getSynthOscRationale(descriptor));
  list.appendChild(oscLi);

  // Filter
  if (descriptor.filter.cutoff_normalized < 0.95) {
    const filtLi = createChainItem(2, 'filter', `Lowpass @ ${Math.round(descriptor.filter.cutoff_hz)}Hz`,
      `Resonance: ${(descriptor.filter.resonance * 100).toFixed(0)}%`);
    list.appendChild(filtLi);
  }

  // Envelope
  const envLi = createChainItem(3, 'envelope',
    `A:${Math.round(descriptor.amp_envelope.attack_ms)}ms D:${Math.round(descriptor.amp_envelope.decay_ms)}ms`,
    `S:${(descriptor.amp_envelope.sustain * 100).toFixed(0)}% R:${Math.round(descriptor.amp_envelope.release_ms)}ms`);
  list.appendChild(envLi);

  // LFO if present
  if (descriptor.lfo && descriptor.lfo.rate_hz > 0) {
    const lfoLi = createChainItem(4, 'lfo',
      `${descriptor.lfo.rate_hz.toFixed(1)}Hz -> ${descriptor.lfo.target}`,
      `Depth: ${(descriptor.lfo.depth * 100).toFixed(0)}%`);
    list.appendChild(lfoLi);
  }

  // Effects
  const effects = [];
  if (descriptor.has_chorus) effects.push('Chorus');
  if (descriptor.has_phaser) effects.push('Phaser');
  if (descriptor.has_reverb) effects.push('Reverb');
  if (descriptor.has_delay) effects.push('Delay');
  if (effects.length > 0) {
    const fxLi = createChainItem(5, 'effects', effects.join(' + '), 'Detected in audio');
    list.appendChild(fxLi);
  }

  // Hints
  const hintsWrap = $('#hints-wrap');
  const hintsList = $('#hints-list');
  hintsList.innerHTML = '';
  if (tweak_hints && tweak_hints.length) {
    for (const h of tweak_hints) {
      const li = document.createElement('li');
      li.textContent = h;
      hintsList.appendChild(li);
    }
    hintsWrap.hidden = false;
  } else {
    hintsWrap.hidden = true;
  }

  // Raw JSON
  $('#raw-json').textContent = JSON.stringify({ descriptor, tweak_hints }, null, 2);
}

function createChainItem(num, slot, display, rationale, price = null, blockFamily = null) {
  const li = document.createElement('li');
  li.className = 'chain__item';

  const numEl = document.createElement('div');
  numEl.className = 'chain__num';
  numEl.textContent = String(num).padStart(2, '0');

  const body = document.createElement('div');
  body.className = 'chain__body';

  const slotEl = document.createElement('div');
  slotEl.className = 'chain__slot';
  slotEl.textContent = slot;

  const dispEl = document.createElement('div');
  dispEl.className = 'chain__display';
  dispEl.textContent = display;

  // Check for matching plugin
  const matchingPlugin = blockFamily ? getPluginMatch(blockFamily) : null;
  if (matchingPlugin) {
    const badge = document.createElement('span');
    badge.className = 'plugin-badge';
    badge.title = `You have: ${matchingPlugin.name}`;
    badge.textContent = matchingPlugin.name;
    dispEl.appendChild(badge);
  }

  const ratEl = document.createElement('div');
  ratEl.className = 'chain__rationale';
  ratEl.textContent = rationale;

  body.appendChild(slotEl);
  body.appendChild(dispEl);
  body.appendChild(ratEl);

  // Add price if provided
  if (price) {
    const priceEl = document.createElement('div');
    priceEl.className = 'chain__price';
    priceEl.textContent = price;
    body.appendChild(priceEl);
  }

  li.appendChild(numEl);
  li.appendChild(body);
  return li;
}

function getSynthOscRationale(desc) {
  const parts = [];
  if (desc.oscillator.num_voices > 1) {
    parts.push(`${desc.oscillator.num_voices} voices unison`);
    if (desc.oscillator.detune > 0) {
      parts.push(`${desc.oscillator.detune.toFixed(0)}c detune`);
    }
  }
  if (desc.oscillator.sub_osc) parts.push('sub oscillator');
  return parts.length > 0 ? parts.join(', ') : 'Single oscillator';
}

function formatParamVal(v) {
  if (typeof v === 'number') return Number.isInteger(v) ? v.toString() : v.toFixed(1);
  return String(v);
}

// -----------------------------------------------------------------------------
// YouTube URL handling
// -----------------------------------------------------------------------------

urlSubmit.addEventListener('click', handleUrl);
urlInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') handleUrl();
});

async function handleUrl() {
  const url = urlInput.value.trim();
  if (!url) return;

  setStatus('working', 'Downloading preview...');
  urlSubmit.disabled = true;

  try {
    // First get waveform preview
    const previewResp = await fetch('/api/preview-waveform-url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });

    if (!previewResp.ok) {
      const body = await previewResp.json().catch(() => ({}));
      throw new Error(body.detail || `Preview failed: HTTP ${previewResp.status}`);
    }

    const waveformData = await previewResp.json();

    // Store URL for later analysis
    trimState.url = url;
    trimState.file = null;

    // Show trim preview (respecting URL timestamp if present)
    showTrimPreview(waveformData);
    setStatus('idle', `Select region to analyze (${waveformData.duration_sec?.toFixed(1)}s downloaded)`);

  } catch (err) {
    setStatus('error', `Preview failed: ${err.message}`);
    console.error(err);
  } finally {
    urlSubmit.disabled = false;
  }
}

async function analyzeUrlWithTrim(url, startTime, endTime) {
  const deepAnalysis = $('#deep-analysis')?.checked || false;
  const fastMode = !deepAnalysis;

  console.log('analyzeUrlWithTrim:', { url, startTime, endTime, deepAnalysis, fastMode });

  setStatus('working', 'Analyzing...');
  urlSubmit.disabled = true;

  try {
    const requestBody = {
      url,
      source_kind: 'auto',
      platform: 'auto',
      fast_mode: fastMode,
      // Deep analysis must also set analysis_mode=deep so the backend
      // picks PipelineConfig.deep() (with stem separation), not standard.
      analysis_mode: deepAnalysis ? 'deep' : 'studio',
    };

    // Add trim parameters if specified
    if (startTime !== null && startTime !== undefined) {
      requestBody.start_time = startTime;
    }
    if (endTime !== null && endTime !== undefined) {
      requestBody.end_time = endTime;
    }

    console.log('Request body:', requestBody);

    const resp = await fetch('/api/analyze-url-stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody),
    });

    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${resp.status}`);
    }

    // Read SSE stream
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalData = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Process complete SSE messages
      const lines = buffer.split('\n\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const msg = JSON.parse(line.slice(6));
            if (msg.type === 'progress') {
              setStatus('working', msg.message);
            } else if (msg.type === 'result') {
              finalData = msg.data;
            } else if (msg.type === 'error') {
              throw new Error(msg.message);
            }
          } catch (e) {
            console.warn('SSE parse error:', e);
          }
        }
      }
    }

    if (!finalData) {
      throw new Error('No result received from server');
    }

    currentResult = finalData;
    renderUnifiedResult(finalData);

    const duration = finalData.descriptor?.source?.duration_sec || finalData.descriptor?.duration_sec || finalData.duration_sec || 0;
    const detected = finalData.detection?.summary ? ` (${finalData.detection.summary})` : '';
    const mode = fastMode ? ' (fast)' : ' (deep)';

    let adminLink = '';
    if (finalData.admin_url) {
      adminLink = ` <a href="${finalData.admin_url}" target="_blank" class="admin-link">Open in Studio →</a>`;
    }
    setStatus('idle', `Done - ${duration.toFixed(1)}s analyzed${detected}${mode}.${adminLink}`, !!finalData.admin_url);
    urlInput.value = '';
  } catch (err) {
    setStatus('error', `Failed: ${err.message}`);
    console.error(err);
  } finally {
    urlSubmit.disabled = false;
  }
}

// -----------------------------------------------------------------------------
// Audio Recording
// -----------------------------------------------------------------------------

recordBtn.addEventListener('click', toggleRecording);

async function toggleRecording() {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    stopRecording();
  } else {
    await startRecording();
  }
}

async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
        sampleRate: 44100,
      }
    });

    recordedChunks = [];
    mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) recordedChunks.push(e.data);
    };

    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      const blob = new Blob(recordedChunks, { type: 'audio/webm' });
      await analyzeRecording(blob);
    };

    mediaRecorder.start(100); // collect data every 100ms
    recordingStartTime = Date.now();
    updateRecordingTime();
    recordingTimer = setInterval(updateRecordingTime, 100);

    recordBtn.classList.add('is-recording');
    recordLabel.textContent = 'Stop';
    setStatus('working', 'Recording...');

  } catch (err) {
    setStatus('error', `Microphone access denied: ${err.message}`);
    console.error(err);
  }
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
  }
  clearInterval(recordingTimer);
  recordBtn.classList.remove('is-recording');
  recordLabel.textContent = 'Record';
  recordTime.textContent = '';
}

function updateRecordingTime() {
  const elapsed = Date.now() - recordingStartTime;
  const secs = Math.floor(elapsed / 1000);
  const mins = Math.floor(secs / 60);
  const displaySecs = secs % 60;
  recordTime.textContent = `${mins}:${displaySecs.toString().padStart(2, '0')}`;
}

async function analyzeRecording(blob) {
  setStatus('working', `Analyzing recording (${humanBytes(blob.size)})...`);

  const form = new FormData();
  const file = new File([blob], 'recording.webm', { type: 'audio/webm' });
  form.append('file', file);
  form.append('source_kind', 'auto');
  form.append('platform', 'auto');

  try {
    const resp = await fetch('/api/analyze', { method: 'POST', body: form });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    currentResult = data;
    renderUnifiedResult(data);

    const duration = data.descriptor?.source?.duration_sec || data.descriptor?.duration_sec || 0;
    const detected = data.detected_type === 'synth' ? ' (detected as synth)' : '';
    setStatus('idle', `Done - ${duration.toFixed(1)}s analyzed${detected}.`);
  } catch (err) {
    setStatus('error', `Failed: ${err.message}`);
    console.error(err);
  }
}

// -----------------------------------------------------------------------------
// History
// -----------------------------------------------------------------------------

const historyList = $('#history-list');
const historySearch = $('#history-search');
const clearHistoryBtn = $('#clear-history');
let historySearchTimeout = null;

async function loadHistory(query = '') {
  try {
    const url = query ? `/api/history?q=${encodeURIComponent(query)}` : '/api/history';
    const resp = await fetch(url);
    if (!resp.ok) return;
    const data = await resp.json();
    renderHistory(data.history);
  } catch (err) {
    console.error('Failed to load history:', err);
  }
}

function renderHistory(history) {
  historyList.innerHTML = '';

  if (!history || history.length === 0) {
    historyList.innerHTML = '<li class="history-empty">No history yet</li>';
    clearHistoryBtn.hidden = true;
    return;
  }

  clearHistoryBtn.hidden = false;

  for (const entry of history) {
    const li = document.createElement('li');
    li.className = 'history-item';
    li.dataset.id = entry.id;

    const time = new Date(entry.timestamp);
    const timeStr = time.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });

    li.innerHTML = `
      <div class="history-item__main">
        <span class="history-item__name">${escapeHtml(entry.name)}</span>
        <span class="history-item__badge history-item__badge--${entry.detected_type}">${entry.detected_type}</span>
      </div>
      <div class="history-item__meta">
        ${entry.amp_family ? `<span class="history-item__amp">${entry.amp_family.replace(/_/g, ' ')}</span>` : ''}
        <span class="history-item__time">${timeStr}</span>
      </div>
      <button class="history-item__delete" title="Delete">&times;</button>
    `;

    li.style.cursor = 'pointer';
    li.addEventListener('click', (e) => {
      if (e.target.classList.contains('history-item__delete')) {
        e.stopPropagation();
        deleteHistoryEntry(entry.id);
      } else {
        loadHistoryItem(entry.id, entry.name);
      }
    });

    historyList.appendChild(li);
  }
}

async function loadHistoryItem(id, name, silent = false) {
  if (!silent) {
    setStatus('working', `Loading ${name}...`);
  }
  try {
    const resp = await fetch(`/api/history/${id}`);
    if (!resp.ok) {
      throw new Error('History item not found');
    }
    const data = await resp.json();
    if (data.result) {
      currentResult = data.result;
      // Ensure source_name is set (for older history entries that don't have it)
      if (!currentResult.source_name && name) {
        currentResult.source_name = name;
      }
      // Set history_id from the entry so URL gets updated
      currentResult.history_id = data.id || id;
      renderUnifiedResult(data.result);
      // Show admin link
      const adminLink = ` <a href="/studio?analysis=${data.id || id}" target="_blank" class="admin-link">Open in Studio →</a>`;
      setStatus('idle', `Loaded: ${name}.${adminLink}`, true);
      return true;
    } else {
      if (!silent) {
        setStatus('error', 'This history item has no saved result (older entry)');
      }
      return false;
    }
  } catch (err) {
    if (!silent) {
      setStatus('error', `Failed to load: ${err.message}`);
    }
    console.error('Failed to load history item:', err);
    return false;
  }
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

async function deleteHistoryEntry(id) {
  try {
    await fetch(`/api/history/${id}`, { method: 'DELETE' });
    loadHistory(historySearch.value);
  } catch (err) {
    console.error('Failed to delete:', err);
  }
}

async function clearAllHistory() {
  if (!confirm('Clear all history?')) return;
  try {
    await fetch('/api/history', { method: 'DELETE' });
    loadHistory();
  } catch (err) {
    console.error('Failed to clear:', err);
  }
}

historySearch.addEventListener('input', () => {
  clearTimeout(historySearchTimeout);
  historySearchTimeout = setTimeout(() => {
    loadHistory(historySearch.value);
  }, 300);
});

clearHistoryBtn.addEventListener('click', clearAllHistory);

// Load history on page load
loadHistory();

// Check if URL contains an analysis ID and load it
async function checkUrlForAnalysis() {
  const match = window.location.pathname.match(/^\/analysis\/([a-zA-Z0-9]+)$/);
  if (match) {
    const analysisId = match[1];
    const loaded = await loadHistoryItem(analysisId, 'Shared analysis', true);
    if (!loaded) {
      // Analysis not found - redirect to home silently
      window.history.replaceState({}, '', '/');
    }
  }
}

// Update URL when analysis completes (without page reload)
function updateUrlWithAnalysisId(historyId) {
  if (historyId) {
    const newUrl = `/analysis/${historyId}`;
    // Only update if we're not already on this URL
    if (window.location.pathname !== newUrl) {
      window.history.pushState({ analysisId: historyId }, '', newUrl);
    }
  }
}

// Handle browser back/forward navigation
window.addEventListener('popstate', (event) => {
  if (event.state && event.state.analysisId) {
    loadHistoryItem(event.state.analysisId, 'Analysis');
  } else if (window.location.pathname === '/') {
    // Back to home - clear result
    resultEmpty.hidden = false;
    resultEl.hidden = true;
    currentResult = null;
  }
});

// Refresh history after analysis
const originalRenderUnifiedResult = renderUnifiedResult;
renderUnifiedResult = function(data) {
  originalRenderUnifiedResult(data);
  // Update URL with history ID for sharing
  if (data.history_id) {
    updateUrlWithAnalysisId(data.history_id);
  }
  // Refresh history after a short delay to allow backend to save
  setTimeout(() => loadHistory(historySearch.value), 500);
};

// Check URL on page load
checkUrlForAnalysis();

// -----------------------------------------------------------------------------
// Tone Preview (per-stem) - uses shared TonePreview utility
// -----------------------------------------------------------------------------

const previewSection = $('#preview-section');
const stemPreviewsContainer = $('#stem-previews');

// Show/hide preview section and create stem preview items
function updatePreviewSection(data) {
  if (!previewSection || !stemPreviewsContainer) return;

  // Clear existing previews
  stemPreviewsContainer.innerHTML = '';

  // Get descriptor for preview generation
  // Try various locations where descriptor might be stored
  const descriptor = data.guitar?.descriptor
    || data.bass?.descriptor
    || data.synth?.descriptor
    || data.descriptor
    || data.tone;  // Deep analysis stores tone data here

  // Debug logging
  console.log('updatePreviewSection descriptor:', {
    hasDescriptor: !!descriptor,
    fromGuitarDescriptor: !!data.guitar?.descriptor,
    fromBassDescriptor: !!data.bass?.descriptor,
    fromSynthDescriptor: !!data.synth?.descriptor,
    fromDescriptor: !!data.descriptor,
    fromTone: !!data.tone,
    descriptorKeys: descriptor ? Object.keys(descriptor) : 'null'
  });

  // Collect stems with MIDI data
  const stemsWithMidi = [];
  if (data.midi_stems) {
    for (const [stemName, midiData] of Object.entries(data.midi_stems)) {
      if (midiData?.content && midiData.note_count > 0) {
        stemsWithMidi.push({ name: stemName, noteCount: midiData.note_count, content: midiData.content });
      }
    }
  }

  // If no stems with MIDI, hide section
  if (stemsWithMidi.length === 0) {
    previewSection.hidden = true;
    return;
  }

  previewSection.hidden = false;

  // Create preview item for each stem using shared TonePreview utility
  for (const stem of stemsWithMidi) {
    const item = document.createElement('div');
    item.className = 'stem-preview-item';

    const nameEl = document.createElement('span');
    nameEl.className = 'stem-name';
    nameEl.textContent = stem.name;

    const audioEl = document.createElement('audio');
    audioEl.controls = true;
    audioEl.hidden = true;

    const statusEl = document.createElement('span');
    statusEl.className = 'preview-status';
    statusEl.textContent = `${stem.noteCount} notes`;

    // Use shared TonePreview to create the button
    const btn = TonePreview.createPreviewButton({
      stemName: stem.name,
      descriptor,
      midiContent: stem.content,
      audioEl,
      onStatusChange: (status, msg) => {
        if (status === 'generating') {
          setStatus('working', `Generating ${stem.name} preview...`);
        } else if (status === 'success') {
          setStatus('idle', `${stem.name} preview generated`);
        } else if (status === 'error') {
          setStatus('error', `Preview failed: ${msg}`);
        }
      },
    });
    // Override button class to match main page style
    btn.className = 'btn btn--preview';
    btn.innerHTML = '<span class="preview-icon">&#9658;</span> Preview';

    item.appendChild(nameEl);
    item.appendChild(btn);
    item.appendChild(audioEl);
    item.appendChild(statusEl);
    stemPreviewsContainer.appendChild(item);
  }
}

// Hook into renderUnifiedResult to show/hide preview section and fetch plugin matches
const _originalRenderUnifiedResult = renderUnifiedResult;
renderUnifiedResult = async function(data) {
  // Fetch plugin matches before rendering
  const descriptor = data.guitar?.descriptor || data.bass?.descriptor || data.synth?.descriptor || data.descriptor;
  if (descriptor && localEngineAvailable) {
    await fetchPluginMatches(descriptor);
  }

  _originalRenderUnifiedResult(data);
  updatePreviewSection(data);

  // Render plugin matches section
  renderPluginMatchesSection(data);
};

function renderPluginMatchesSection(data) {
  // Remove existing section if any
  const existing = document.getElementById('plugin-matches-section');
  if (existing) existing.remove();

  // Don't show separate section - plugins are shown as inline badges on chain items
  // This avoids the confusing generic list
}
