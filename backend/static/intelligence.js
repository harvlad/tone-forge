/**
 * ToneIntelligence - Reconstruction intelligence panel for ToneForge Studio.
 *
 * Provides 4 tabs:
 * 1. Reconstruction - Synth suggestions, plugin recommendations, FX chain hints
 * 2. Technical - Provenance, detector arbitration, FP/FN analysis
 * 3. Arrangement - Section visualization, energy curve, density analysis
 * 4. Preset - Synth family detection, "sounds like" references, hardware matches
 */
const ToneIntelligence = (function () {
  'use strict';

  // Tab configuration
  const TABS = {
    reconstruction: { label: 'Reconstruction', icon: '🎛️' },
    technical: { label: 'Technical', icon: '⚙️' },
    arrangement: { label: 'Arrangement', icon: '📊' },
    preset: { label: 'Preset', icon: '🎹' },
  };

  // Synth family classifications
  const SYNTH_FAMILIES = {
    subtractive: { label: 'Subtractive', examples: ['Minimoog', 'Prophet', 'Juno'] },
    fm: { label: 'FM', examples: ['DX7', 'FM8', 'Dexed'] },
    wavetable: { label: 'Wavetable', examples: ['Serum', 'Massive', 'Vital'] },
    granular: { label: 'Granular', examples: ['Granulator', 'Quanta', 'Portal'] },
    sample: { label: 'Sample-based', examples: ['Kontakt', 'Sampler', 'ROMpler'] },
    additive: { label: 'Additive', examples: ['Harmor', 'Razor', 'Alchemy'] },
    physical: { label: 'Physical Modeling', examples: ['Chromaphone', 'Sculpture'] },
  };

  // Amp family classifications for guitar
  const AMP_FAMILIES = {
    fender: { label: 'Fender Clean', examples: ['Twin Reverb', 'Deluxe Reverb', 'Princeton'] },
    vox: { label: 'Vox Chime', examples: ['AC30', 'AC15', 'AC10'] },
    marshall: { label: 'Marshall Crunch', examples: ['JCM800', 'Plexi', 'JVM'] },
    mesa: { label: 'Mesa High-Gain', examples: ['Dual Rectifier', 'Mark V', 'Triple Crown'] },
    bogner: { label: 'Bogner Modern', examples: ['Ecstasy', 'Uberschall', 'Shiva'] },
    orange: { label: 'Orange Thick', examples: ['Rockerverb', 'TH30', 'AD30'] },
    soldano: { label: 'Soldano Lead', examples: ['SLO-100', 'Hot Rod 50', 'Astro 20'] },
    peavey: { label: 'Peavey Metal', examples: ['5150', '6505', 'Invective'] },
    matchless: { label: 'Matchless Boutique', examples: ['DC-30', 'Chieftain', 'Lightning'] },
  };

  // Plugin recommendations by role
  const PLUGIN_RECOMMENDATIONS = {
    bass_foundation: [
      { name: 'Serum', reason: 'Precise bass with wavetable depth' },
      { name: 'Massive X', reason: 'Rich analog-style bass' },
      { name: 'Diva', reason: 'Authentic analog bass tones' },
    ],
    lead_melody: [
      { name: 'Sylenth1', reason: 'Classic lead sounds' },
      { name: 'Serum', reason: 'Versatile lead design' },
      { name: 'Omnisphere', reason: 'Complex evolving leads' },
    ],
    pad_atmosphere: [
      { name: 'Omnisphere', reason: 'Lush atmospheric pads' },
      { name: 'Vital', reason: 'Modern wavetable pads' },
      { name: 'Pigments', reason: 'Textured evolving sounds' },
    ],
    pluck_stab: [
      { name: 'Serum', reason: 'Punchy plucks and stabs' },
      { name: 'Massive', reason: 'Sharp attack transients' },
      { name: 'Spire', reason: 'Bright pluck sounds' },
    ],
    arp_sequence: [
      { name: 'Phase Plant', reason: 'Complex arp patterns' },
      { name: 'Serum', reason: 'Precise sequenced sounds' },
      { name: 'Synthmaster', reason: 'Versatile arps' },
    ],
    fx_texture: [
      { name: 'Portal', reason: 'Granular textures' },
      { name: 'Quanta', reason: 'Experimental FX' },
      { name: 'Thermal', reason: 'Distorted textures' },
    ],
  };

  // Instance state
  let instance = null;

  /**
   * Initialize intelligence panel.
   */
  function init(containerId, options = {}) {
    const container =
      typeof containerId === 'string'
        ? document.getElementById(containerId)
        : containerId;

    if (!container) {
      console.error('ToneIntelligence: Container not found');
      return null;
    }

    instance = {
      container,
      options: {
        onPluginSelect: options.onPluginSelect || null,
        onPresetMatch: options.onPresetMatch || null,
      },
      currentTab: 'reconstruction',
      data: null,
    };

    buildDOM(instance);
    attachEvents(instance);

    return instance;
  }

  /**
   * Build DOM structure.
   */
  function buildDOM(state) {
    state.container.innerHTML = `
      <div class="tone-intelligence">
        <div class="intelligence-tabs">
          ${Object.entries(TABS)
            .map(
              ([id, tab]) => `
            <button class="intelligence-tab ${id === 'reconstruction' ? 'active' : ''}" data-tab="${id}">
              <span class="tab-icon">${tab.icon}</span>
              <span class="tab-label">${tab.label}</span>
            </button>
          `
            )
            .join('')}
        </div>
        <div class="intelligence-panels">
          <div class="intelligence-panel active" data-panel="reconstruction">
            <div class="panel-content reconstruction-content">
              <div class="empty-state">Analyze audio to see reconstruction suggestions</div>
            </div>
          </div>
          <div class="intelligence-panel" data-panel="technical">
            <div class="panel-content technical-content">
              <div class="empty-state">Analyze audio to see technical details</div>
            </div>
          </div>
          <div class="intelligence-panel" data-panel="arrangement">
            <div class="panel-content arrangement-content">
              <div class="empty-state">Analyze audio to see arrangement analysis</div>
            </div>
          </div>
          <div class="intelligence-panel" data-panel="preset">
            <div class="panel-content preset-content">
              <div class="empty-state">Analyze audio to see preset matches</div>
            </div>
          </div>
        </div>
      </div>
    `;

    state.elements = {
      tabs: state.container.querySelectorAll('.intelligence-tab'),
      panels: state.container.querySelectorAll('.intelligence-panel'),
      reconstructionContent: state.container.querySelector('.reconstruction-content'),
      technicalContent: state.container.querySelector('.technical-content'),
      arrangementContent: state.container.querySelector('.arrangement-content'),
      presetContent: state.container.querySelector('.preset-content'),
    };
  }

  /**
   * Attach event handlers.
   */
  function attachEvents(state) {
    state.elements.tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        switchTab(state, tab.dataset.tab);
      });
    });
  }

  /**
   * Switch to a tab.
   */
  function switchTab(state, tabId) {
    state.currentTab = tabId;

    state.elements.tabs.forEach((tab) => {
      tab.classList.toggle('active', tab.dataset.tab === tabId);
    });

    state.elements.panels.forEach((panel) => {
      panel.classList.toggle('active', panel.dataset.panel === tabId);
    });
  }

  /**
   * Update with analysis data.
   */
  function update(data) {
    if (!instance) return;

    instance.data = data;

    renderReconstructionTab(instance, data);
    renderTechnicalTab(instance, data);
    renderArrangementTab(instance, data);
    renderPresetTab(instance, data);
  }

  /**
   * Render reconstruction tab.
   */
  function renderReconstructionTab(state, data) {
    const content = state.elements.reconstructionContent;
    if (!data) {
      content.innerHTML = '<div class="empty-state">Analyze audio to see reconstruction suggestions</div>';
      return;
    }

    const role = data.role?.primary_role || data.role?.role || 'unknown';
    const synthFamily = detectSynthFamily(data);
    const recommendations = getPluginRecommendations(role, data);
    const fxChain = suggestFXChain(data);

    content.innerHTML = `
      <div class="intel-section">
        <h4>Detected Role</h4>
        <div class="role-badge">${formatRole(role)}</div>
        ${data.role?.confidence ? `<span class="confidence-tag">${Math.round(data.role.confidence * 100)}% confident</span>` : ''}
      </div>

      <div class="intel-section">
        <h4>Synth Family</h4>
        <div class="synth-family">
          <span class="family-name">${synthFamily.label}</span>
          <span class="family-examples">${synthFamily.examples.join(', ')}</span>
        </div>
      </div>

      <div class="intel-section">
        <h4>Plugin Recommendations</h4>
        <div class="plugin-list">
          ${recommendations
            .map(
              (p) => `
            <div class="plugin-card" data-plugin="${p.name}">
              <span class="plugin-name">${p.name}</span>
              <span class="plugin-reason">${p.reason}</span>
            </div>
          `
            )
            .join('')}
        </div>
      </div>

      <div class="intel-section">
        <h4>Suggested FX Chain</h4>
        <div class="fx-chain">
          ${fxChain
            .map(
              (fx) => `
            <div class="fx-item">
              <span class="fx-name">${fx.name}</span>
              <span class="fx-settings">${fx.settings}</span>
            </div>
          `
            )
            .join('<span class="fx-arrow">→</span>')}
        </div>
      </div>

      ${data.layering_recommendations ? `
      <div class="intel-section">
        <h4>Layering Suggestions</h4>
        <div class="layering-list">
          ${data.layering_recommendations.map((l) => `<div class="layer-item">${l}</div>`).join('')}
        </div>
      </div>
      ` : ''}
    `;

    // Attach plugin click handlers
    content.querySelectorAll('.plugin-card').forEach((card) => {
      card.addEventListener('click', () => {
        if (state.options.onPluginSelect) {
          state.options.onPluginSelect(card.dataset.plugin);
        }
      });
    });
  }

  /**
   * Render technical tab.
   */
  function renderTechnicalTab(state, data) {
    const content = state.elements.technicalContent;
    if (!data) {
      content.innerHTML = '<div class="empty-state">Analyze audio to see technical details</div>';
      return;
    }

    const provenance = data.provenance || {};
    const midiStats = data.midi_stats || {};
    const quality = data.quality_report || {};

    content.innerHTML = `
      <div class="intel-section">
        <h4>Extraction Summary</h4>
        <div class="summary-grid">
          <div class="summary-item">
            <span class="item-value">${midiStats.note_count || 0}</span>
            <span class="item-label">Notes Extracted</span>
          </div>
          <div class="summary-item">
            <span class="item-value">${midiStats.tempo?.toFixed(0) || 120}</span>
            <span class="item-label">BPM</span>
          </div>
          <div class="summary-item">
            <span class="item-value">${Math.round((quality.overall_confidence || 0) * 100)}%</span>
            <span class="item-label">Confidence</span>
          </div>
          <div class="summary-item">
            <span class="item-value">${((midiStats.total_time_ms || 0) / 1000).toFixed(2)}s</span>
            <span class="item-label">Extraction Time</span>
          </div>
        </div>
      </div>

      ${provenance.detectors_used ? `
      <div class="intel-section">
        <h4>Detector Contributions</h4>
        <div class="detector-list">
          ${Object.entries(provenance.detector_weights || {})
            .map(
              ([name, weight]) => `
            <div class="detector-item">
              <span class="detector-name">${name}</span>
              <div class="detector-bar">
                <div class="detector-fill" style="width: ${weight * 100}%"></div>
              </div>
              <span class="detector-value">${Math.round(weight * 100)}%</span>
            </div>
          `
            )
            .join('')}
        </div>
      </div>
      ` : ''}

      ${provenance.cleanup_passes ? `
      <div class="intel-section">
        <h4>Cleanup Passes Applied</h4>
        <div class="pass-list">
          ${provenance.cleanup_passes
            .map(
              (pass) => `
            <span class="pass-tag">${formatPassName(pass)}</span>
          `
            )
            .join('')}
        </div>
      </div>
      ` : ''}

      <div class="intel-section">
        <h4>Risk Assessment</h4>
        <div class="risk-grid">
          <div class="risk-item">
            <span class="risk-label">False Positive Risk</span>
            <div class="risk-bar">
              <div class="risk-fill ${(provenance.fp_risk || 0) > 0.3 ? 'high' : 'low'}"
                   style="width: ${(provenance.fp_risk || 0) * 100}%"></div>
            </div>
            <span class="risk-value">${Math.round((provenance.fp_risk || 0) * 100)}%</span>
          </div>
          <div class="risk-item">
            <span class="risk-label">False Negative Risk</span>
            <div class="risk-bar">
              <div class="risk-fill ${(provenance.fn_risk || 0) > 0.3 ? 'high' : 'low'}"
                   style="width: ${(provenance.fn_risk || 0) * 100}%"></div>
            </div>
            <span class="risk-value">${Math.round((provenance.fn_risk || 0) * 100)}%</span>
          </div>
        </div>
      </div>

      ${quality.warnings?.length > 0 ? `
      <div class="intel-section">
        <h4>Quality Warnings</h4>
        <div class="warning-list">
          ${quality.warnings
            .map(
              (w) => `
            <div class="warning-item ${w.level || 'info'}">
              <span class="warning-message">${w.message}</span>
              ${w.recommendation ? `<span class="warning-rec">${w.recommendation}</span>` : ''}
            </div>
          `
            )
            .join('')}
        </div>
      </div>
      ` : ''}
    `;
  }

  /**
   * Render arrangement tab.
   */
  function renderArrangementTab(state, data) {
    const content = state.elements.arrangementContent;
    if (!data) {
      content.innerHTML = '<div class="empty-state">Analyze audio to see arrangement analysis</div>';
      return;
    }

    const sections = data.sections || [];
    const duration = data.duration_sec || 0;

    // Store sections and duration for playback
    state.arrangementData = { sections, duration };

    content.innerHTML = `
      <div class="intel-section">
        <h4>Song Structure</h4>
        <div class="playback-controls">
          <button class="play-btn" id="arrangementPlayBtn" title="Play/Pause">▶</button>
          <span class="playback-time" id="arrangementTime">0:00</span>
          <span class="playback-section" id="arrangementSection">-</span>
        </div>
        <div class="structure-timeline" id="structureTimeline">
          <div class="timeline-playhead" id="timelinePlayhead"></div>
          ${sections
            .map(
              (s, i) => {
                const widthPercent = ((s.end_time - s.start_time) / duration) * 100;
                const leftPercent = (s.start_time / duration) * 100;
                return `
                <div class="section-block" data-index="${i}" data-start="${s.start_time}" data-end="${s.end_time}"
                     style="left: ${leftPercent}%; width: ${widthPercent}%; background: ${getSectionColor(s.type)}40; border-color: ${getSectionColor(s.type)};"
                     title="${s.type}: ${formatTime(s.start_time)} - ${formatTime(s.end_time)}">
                  <span class="section-name">${s.type}</span>
                </div>
              `;
              }
            )
            .join('')}
        </div>
        <div class="timeline-labels">
          <span>0:00</span>
          <span>${formatTime(duration / 2)}</span>
          <span>${formatTime(duration)}</span>
        </div>
        <audio id="arrangementAudio" style="display:none;"></audio>
      </div>

      <div class="intel-section">
        <h4>Section Details</h4>
        <div class="section-table">
          <div class="section-row header">
            <span>Type</span>
            <span>Start</span>
            <span>End</span>
            <span>Duration</span>
            <span>Energy</span>
          </div>
          ${sections
            .map(
              (s) => `
            <div class="section-row">
              <span class="section-type" style="color: ${getSectionColor(s.type)}">${s.type}</span>
              <span>${formatTime(s.start_time)}</span>
              <span>${formatTime(s.end_time)}</span>
              <span>${formatTime(s.end_time - s.start_time)}</span>
              <span>${Math.round((s.energy_mean || 0) * 100)}%</span>
            </div>
          `
            )
            .join('')}
        </div>
      </div>

      <div class="intel-section">
        <h4>Energy Curve</h4>
        <p class="intel-help">Loudness over time. Peaks indicate high-energy sections (choruses, drops). Dips show quieter moments (breakdowns, intros). Colored regions match section types above.</p>
        <canvas id="energyCurveCanvas" width="400" height="80"></canvas>
      </div>

      <div class="intel-section">
        <h4>Complexity Analysis</h4>
        <p class="intel-help">Musical complexity metrics derived from the audio analysis.</p>
        <div class="complexity-grid">
          <div class="complexity-item">
            <span class="complexity-label">Harmonic Density</span>
            <div class="complexity-bar">
              <div class="complexity-fill" style="width: ${(data.harmonic_density || 0.5) * 100}%"></div>
            </div>
            <span class="complexity-hint">Notes/chords layered together</span>
          </div>
          <div class="complexity-item">
            <span class="complexity-label">Rhythmic Complexity</span>
            <div class="complexity-bar">
              <div class="complexity-fill" style="width: ${(data.rhythmic_complexity || 0.5) * 100}%"></div>
            </div>
            <span class="complexity-hint">Syncopation and timing variation</span>
          </div>
          <div class="complexity-item">
            <span class="complexity-label">Dynamic Range</span>
            <div class="complexity-bar">
              <div class="complexity-fill" style="width: ${(data.dynamic_range || 0.5) * 100}%"></div>
            </div>
            <span class="complexity-hint">Quiet-to-loud variation</span>
          </div>
        </div>
      </div>
    `;

    // Render energy curve if data available
    if (data.energy_curve && data.energy_curve.length > 0) {
      renderEnergyCurve(
        content.querySelector('#energyCurveCanvas'),
        data.energy_curve,
        sections,
        duration
      );
    }

    // Setup playback controls
    setupArrangementPlayback(state, data, content, sections, duration);
  }

  /**
   * Setup playback controls for arrangement timeline.
   */
  function setupArrangementPlayback(state, data, content, sections, duration) {
    const audio = content.querySelector('#arrangementAudio');
    const playBtn = content.querySelector('#arrangementPlayBtn');
    const timeDisplay = content.querySelector('#arrangementTime');
    const sectionDisplay = content.querySelector('#arrangementSection');
    const timeline = content.querySelector('#structureTimeline');
    const playhead = content.querySelector('#timelinePlayhead');
    const sectionBlocks = content.querySelectorAll('.section-block');

    if (!audio || !playBtn) return;

    // Get audio source from stems (prefer guitar/other, fallback to any)
    const stems = data.stems_paths || data.stems || {};
    const audioSrc = stems.guitar || stems.other || stems.vocals || stems.bass || stems.drums || Object.values(stems)[0];

    if (audioSrc) {
      audio.src = audioSrc;
      audio.load();
    } else {
      playBtn.disabled = true;
      playBtn.title = 'No audio available';
      return;
    }

    let isPlaying = false;
    let animationFrame = null;

    // Play/pause toggle
    playBtn.addEventListener('click', () => {
      if (isPlaying) {
        audio.pause();
        playBtn.textContent = '▶';
        isPlaying = false;
        cancelAnimationFrame(animationFrame);
      } else {
        audio.play();
        playBtn.textContent = '⏸';
        isPlaying = true;
        updatePlayhead();
      }
    });

    // Update playhead position and current section
    function updatePlayhead() {
      if (!isPlaying) return;

      const currentTime = audio.currentTime;
      const percent = (currentTime / duration) * 100;
      playhead.style.left = `${percent}%`;
      timeDisplay.textContent = formatTime(currentTime);

      // Find and highlight current section
      let currentSection = null;
      sectionBlocks.forEach((block, i) => {
        const start = parseFloat(block.dataset.start);
        const end = parseFloat(block.dataset.end);
        const isCurrent = currentTime >= start && currentTime < end;
        block.classList.toggle('playing', isCurrent);
        if (isCurrent) {
          currentSection = sections[i];
        }
      });

      if (currentSection) {
        sectionDisplay.textContent = currentSection.type;
        sectionDisplay.style.color = getSectionColor(currentSection.type);
      }

      animationFrame = requestAnimationFrame(updatePlayhead);
    }

    // Click on timeline to seek
    timeline.addEventListener('click', (e) => {
      const rect = timeline.getBoundingClientRect();
      const clickX = e.clientX - rect.left;
      const percent = clickX / rect.width;
      const seekTime = percent * duration;
      audio.currentTime = seekTime;
      playhead.style.left = `${percent * 100}%`;
      timeDisplay.textContent = formatTime(seekTime);

      // Update section highlight
      sectionBlocks.forEach((block, i) => {
        const start = parseFloat(block.dataset.start);
        const end = parseFloat(block.dataset.end);
        block.classList.toggle('playing', seekTime >= start && seekTime < end);
      });
    });

    // Click on section row to play that section
    sectionBlocks.forEach((block) => {
      block.style.cursor = 'pointer';
      block.addEventListener('click', (e) => {
        e.stopPropagation();
        const start = parseFloat(block.dataset.start);
        audio.currentTime = start;
        if (!isPlaying) {
          audio.play();
          playBtn.textContent = '⏸';
          isPlaying = true;
          updatePlayhead();
        }
      });
    });

    // Handle audio end
    audio.addEventListener('ended', () => {
      isPlaying = false;
      playBtn.textContent = '▶';
      playhead.style.left = '0%';
      timeDisplay.textContent = '0:00';
      sectionBlocks.forEach(b => b.classList.remove('playing'));
      cancelAnimationFrame(animationFrame);
    });

    // Store audio reference for cleanup
    state.arrangementAudio = audio;
  }

  /**
   * Render preset tab.
   */
  function renderPresetTab(state, data) {
    const content = state.elements.presetContent;
    if (!data) {
      content.innerHTML = '<div class="empty-state">Analyze audio to see preset matches</div>';
      return;
    }

    const detectedType = data.detected_type || 'guitar';
    const isGuitar = detectedType === 'guitar' || detectedType === 'bass';
    const similarTones = data.similar_tones || [];
    const hardwareMatches = data.hardware_matches || [];

    // Show amp family for guitar, synth family for synth
    let familySection = '';
    if (isGuitar) {
      const ampFamily = detectAmpFamily(data);
      familySection = `
        <div class="intel-section">
          <h4>Amp Family Detection</h4>
          <div class="synth-family-card">
            <div class="family-main">${ampFamily.label}</div>
            <div class="family-desc">${ampFamily.description || ''}</div>
            <div class="family-similar">
              <span class="similar-label">Similar amps:</span>
              ${ampFamily.examples.map((e) => `<span class="synth-tag">${e}</span>`).join('')}
            </div>
          </div>
        </div>`;
    } else {
      const synthFamily = detectSynthFamily(data);
      familySection = `
        <div class="intel-section">
          <h4>Synth Family Detection</h4>
          <div class="synth-family-card">
            <div class="family-main">${synthFamily.label}</div>
            <div class="family-desc">${synthFamily.description || ''}</div>
            <div class="family-similar">
              <span class="similar-label">Similar synths:</span>
              ${synthFamily.examples.map((e) => `<span class="synth-tag">${e}</span>`).join('')}
            </div>
          </div>
        </div>`;
    }

    content.innerHTML = `
      ${familySection}

      ${(data.timbral || data.synth?.descriptor || data.descriptor) ? `
      <div class="intel-section">
        <h4>Timbral Characteristics</h4>
        <div class="timbre-grid">
          ${(() => {
            // Get timbral data from flat timbral field, or extract from nested descriptors
            const timbralSource = data.timbral || data.synth?.descriptor || data.descriptor || {};
            return Object.entries(timbralSource)
              .filter(([k, v]) => typeof v === 'number' && v >= 0 && v <= 1)
              .slice(0, 6)
              .map(
                ([key, value]) => `
              <div class="timbre-item">
                <span class="timbre-label">${formatKey(key)}</span>
                <div class="timbre-bar">
                  <div class="timbre-fill" style="width: ${value * 100}%"></div>
                </div>
                <span class="timbre-value">${Math.round(value * 100)}%</span>
              </div>
            `
              )
              .join('');
          })()}
        </div>
      </div>
      ` : ''}

      ${similarTones.length > 0 ? `
      <div class="intel-section">
        <h4>Sounds Like</h4>
        <div class="similar-list">
          ${similarTones
            .map(
              (tone) => `
            <div class="similar-card" data-tone="${tone.id || tone.name}">
              <span class="similar-name">${tone.name}</span>
              <span class="similar-match">${Math.round((tone.similarity || 0) * 100)}% match</span>
              ${tone.artist ? `<span class="similar-artist">${tone.artist}</span>` : ''}
            </div>
          `
            )
            .join('')}
        </div>
      </div>
      ` : ''}

      ${hardwareMatches.length > 0 ? `
      <div class="intel-section">
        <h4>Hardware Matches</h4>
        <div class="hardware-list">
          ${hardwareMatches
            .map(
              (hw) => `
            <div class="hardware-card">
              <span class="hw-name">${hw.name}</span>
              <span class="hw-type">${hw.type}</span>
              ${hw.settings ? `<span class="hw-settings">${hw.settings}</span>` : ''}
            </div>
          `
            )
            .join('')}
        </div>
      </div>
      ` : ''}
    `;

    // Attach similar tone click handlers
    content.querySelectorAll('.similar-card').forEach((card) => {
      card.addEventListener('click', () => {
        if (state.options.onPresetMatch) {
          state.options.onPresetMatch(card.dataset.tone);
        }
      });
    });
  }

  /**
   * Detect amp family from analysis data (for guitar).
   */
  function detectAmpFamily(data) {
    if (!data || !data.descriptor) {
      return { ...AMP_FAMILIES.fender, description: 'Clean tube amplifier tones' };
    }

    const desc = data.descriptor;
    const ampFamily = desc.amp?.family || desc.amp_family || '';
    const gain = desc.gain || desc.amp?.gain || 0.5;

    // Match by detected amp family name
    const familyLower = ampFamily.toLowerCase();
    if (familyLower.includes('bogner')) {
      return { ...AMP_FAMILIES.bogner, description: 'Modern high-gain with tight low end' };
    }
    if (familyLower.includes('mesa') || familyLower.includes('rectifier')) {
      return { ...AMP_FAMILIES.mesa, description: 'Aggressive high-gain with scooped mids' };
    }
    if (familyLower.includes('marshall') || familyLower.includes('plexi')) {
      return { ...AMP_FAMILIES.marshall, description: 'British crunch with midrange presence' };
    }
    if (familyLower.includes('vox') || familyLower.includes('ac30')) {
      return { ...AMP_FAMILIES.vox, description: 'Chimey British clean with top-end sparkle' };
    }
    if (familyLower.includes('fender') || familyLower.includes('twin')) {
      return { ...AMP_FAMILIES.fender, description: 'American clean with warm headroom' };
    }
    if (familyLower.includes('orange')) {
      return { ...AMP_FAMILIES.orange, description: 'Thick British crunch with fuzzy saturation' };
    }
    if (familyLower.includes('peavey') || familyLower.includes('5150')) {
      return { ...AMP_FAMILIES.peavey, description: 'Tight metal tones with aggressive attack' };
    }
    if (familyLower.includes('soldano')) {
      return { ...AMP_FAMILIES.soldano, description: 'Smooth lead tones with singing sustain' };
    }
    if (familyLower.includes('matchless')) {
      return { ...AMP_FAMILIES.matchless, description: 'Boutique clean with 3D depth' };
    }

    // Fallback: guess by gain level
    if (gain > 0.7) {
      return { ...AMP_FAMILIES.mesa, description: 'High-gain tone detected' };
    } else if (gain > 0.4) {
      return { ...AMP_FAMILIES.marshall, description: 'Moderate gain crunch tone' };
    }
    return { ...AMP_FAMILIES.fender, description: 'Clean to light breakup tone' };
  }

  /**
   * Detect synth family from analysis data.
   */
  function detectSynthFamily(data) {
    if (!data || (!data.descriptor && !data.synth?.descriptor)) {
      return SYNTH_FAMILIES.subtractive;
    }

    const desc = data.synth?.descriptor || data.descriptor;

    // Heuristic family detection based on timbral characteristics
    if (desc.fm_ratio > 0.6 || desc.metallic > 0.5) {
      return { ...SYNTH_FAMILIES.fm, description: 'Complex harmonic ratios and metallic tones' };
    }
    if (desc.spectral_flux > 0.7 || desc.evolving > 0.6) {
      return { ...SYNTH_FAMILIES.wavetable, description: 'Evolving spectral content and movement' };
    }
    if (desc.granular > 0.5 || desc.textured > 0.6) {
      return { ...SYNTH_FAMILIES.granular, description: 'Textured, grain-based synthesis' };
    }
    if (desc.warmth > 0.6 && desc.analog > 0.5) {
      return { ...SYNTH_FAMILIES.subtractive, description: 'Warm analog-style filtering' };
    }

    return { ...SYNTH_FAMILIES.subtractive, description: 'Classic synthesizer architecture' };
  }

  /**
   * Get plugin recommendations based on role.
   */
  function getPluginRecommendations(role, data) {
    const roleKey = role.replace(/\s+/g, '_').toLowerCase();
    let recommendations = PLUGIN_RECOMMENDATIONS[roleKey] || PLUGIN_RECOMMENDATIONS.lead_melody;

    // Filter based on detected characteristics
    if (data?.descriptor?.bass > 0.6) {
      recommendations = PLUGIN_RECOMMENDATIONS.bass_foundation;
    }

    return recommendations.slice(0, 4);
  }

  /**
   * Suggest FX chain based on analysis.
   */
  function suggestFXChain(data) {
    const chain = [];

    if (!data) return chain;

    const desc = data.descriptor || {};

    // EQ suggestion
    chain.push({
      name: 'EQ',
      settings: desc.bass > 0.5 ? 'Cut lows below 40Hz, boost 100Hz' : 'Shape highs, cut mud at 300Hz',
    });

    // Saturation for analog character
    if (desc.warmth > 0.4 || desc.analog > 0.4) {
      chain.push({
        name: 'Saturation',
        settings: 'Light tube warmth, 10-20% drive',
      });
    }

    // Compression
    chain.push({
      name: 'Compressor',
      settings: desc.transient_attack > 0.5 ? 'Fast attack, preserve punch' : 'Slow attack, add sustain',
    });

    // Reverb/Delay
    if (desc.space > 0.3 || desc.reverb > 0.3) {
      chain.push({
        name: 'Reverb',
        settings: desc.space > 0.6 ? 'Hall, long tail' : 'Room, short decay',
      });
    }

    if (desc.delay > 0.3) {
      chain.push({
        name: 'Delay',
        settings: 'Stereo ping-pong, sync to tempo',
      });
    }

    return chain;
  }

  /**
   * Render energy curve visualization.
   */
  function renderEnergyCurve(canvas, energyCurve, sections, durationSec) {
    if (!canvas || !energyCurve || energyCurve.length === 0) return;

    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;
    const dpr = window.devicePixelRatio || 1;

    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.scale(dpr, dpr);

    // Background
    ctx.fillStyle = '#1a1612';
    ctx.fillRect(0, 0, width, height);

    // Use actual duration in seconds for section positioning
    const duration = durationSec || energyCurve.length * 0.1; // fallback: assume 10 samples/sec

    // Draw section backgrounds
    sections.forEach((s) => {
      const startX = (s.start_time / duration) * width;
      const endX = (s.end_time / duration) * width;
      ctx.fillStyle = getSectionColor(s.type) + '20';
      ctx.fillRect(startX, 0, endX - startX, height);
    });

    // Draw energy curve
    ctx.beginPath();
    ctx.strokeStyle = '#b8501a';
    ctx.lineWidth = 2;

    energyCurve.forEach((value, i) => {
      const x = (i / energyCurve.length) * width;
      const y = height - value * height * 0.9;
      if (i === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    });

    ctx.stroke();
  }

  /**
   * Get section color.
   */
  function getSectionColor(type) {
    const colors = {
      intro: '#9ca3af',
      verse: '#60a5fa',
      chorus: '#f472b6',
      drop: '#f97316',
      breakdown: '#a78bfa',
      bridge: '#34d399',
      outro: '#9ca3af',
    };
    return colors[type?.toLowerCase()] || '#6b7280';
  }

  /**
   * Format time as m:ss.
   */
  function formatTime(seconds) {
    const min = Math.floor(seconds / 60);
    const sec = Math.floor(seconds % 60);
    return `${min}:${sec.toString().padStart(2, '0')}`;
  }

  /**
   * Format role name.
   */
  function formatRole(role) {
    return role
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  /**
   * Format cleanup pass name.
   */
  function formatPassName(pass) {
    return pass
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  /**
   * Format key name.
   */
  function formatKey(key) {
    return key
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  /**
   * Destroy instance.
   */
  function destroy() {
    if (instance) {
      instance.container.innerHTML = '';
      instance = null;
    }
  }

  // Public API
  return {
    init,
    update,
    destroy,
    switchTab: (tabId) => {
      if (instance) switchTab(instance, tabId);
    },
    SYNTH_FAMILIES,
    PLUGIN_RECOMMENDATIONS,
  };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
  module.exports = ToneIntelligence;
}
