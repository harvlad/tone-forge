/**
 * ToneArrangement - DAW-style arrangement timeline for reconstruction visualization.
 *
 * Provides:
 * - Horizontal timeline with track lanes (drums, bass, pads, leads, vocals, FX)
 * - Waveform + MIDI overlay per region
 * - Confidence heatmap coloring
 * - Region selection and re-analysis
 * - Playback cursor with loop points
 *
 * Architecture follows existing ToneForge module pattern (see waveform-trim.js).
 */
const ToneArrangement = (function () {
  'use strict';

  // Track type configuration
  const TRACK_TYPES = {
    drums: { label: 'Drums', color: '#e57373', icon: '🥁' },
    bass: { label: 'Bass', color: '#7986cb', icon: '🎸' },
    guitar: { label: 'Guitar', color: '#81c784', icon: '🎸' },
    piano: { label: 'Keys', color: '#fff176', icon: '🎹' },
    pads: { label: 'Pads', color: '#ba68c8', icon: '🎛️' },
    leads: { label: 'Leads', color: '#4fc3f7', icon: '🎵' },
    vocals: { label: 'Vocals', color: '#ffb74d', icon: '🎤' },
    other: { label: 'Synth', color: '#90a4ae', icon: '🎹' },
    fx: { label: 'FX', color: '#a1887f', icon: '✨' },
  };

  // Confidence color gradient (poor → excellent)
  const CONFIDENCE_COLORS = {
    excellent: '#2d7a4e', // Green
    good: '#4ade80',
    acceptable: '#fbbf24', // Yellow
    marginal: '#f97316', // Orange
    poor: '#ef4444', // Red
  };

  // Instance storage
  const instances = new Map();

  /**
   * State object for an arrangement instance.
   */
  function createState(container, options) {
    return {
      container,
      options: {
        pixelsPerSecond: options.pixelsPerSecond || 50,
        trackHeight: options.trackHeight || 80,
        rulerHeight: options.rulerHeight || 28,
        minZoom: options.minZoom || 0.25,
        maxZoom: options.maxZoom || 4,
        showMidi: options.showMidi !== false,
        showConfidence: options.showConfidence !== false,
        enableSelection: options.enableSelection !== false,
        onRegionSelect: options.onRegionSelect || null,
        onPlayheadChange: options.onPlayheadChange || null,
      },
      // Data
      tracks: [],
      sections: [],
      duration: 0,
      // View state
      zoom: 1,
      scrollX: 0,
      playhead: 0,
      isPlaying: false,
      // Selection
      selection: null, // { start, end, trackId }
      isDragging: false,
      dragStart: null,
      // Audio playback (single track fallback)
      audioElement: null,
      audioUrl: null,
      playbackAnimationFrame: null,
      // Multi-track audio (Web Audio API)
      audioContext: null,
      stemTracks: new Map(), // Map of stemType -> { source, gainNode, audioBuffer, audioElement }
      masterGain: null,
      useMultiTrack: false,
      // DOM elements
      elements: {},
    };
  }

  /**
   * Initialize arrangement view in container.
   */
  function init(containerId, analysisResult, options = {}) {
    const container =
      typeof containerId === 'string'
        ? document.getElementById(containerId)
        : containerId;

    if (!container) {
      console.error('ToneArrangement: Container not found:', containerId);
      return null;
    }

    // Clean up existing instance
    if (instances.has(containerId)) {
      destroy(containerId);
    }

    const state = createState(container, options);

    // Parse analysis result into tracks
    parseAnalysisResult(state, analysisResult);

    // Build DOM structure
    buildDOM(state);

    // Attach event listeners
    attachEvents(state);

    // Initial render
    render(state);

    instances.set(containerId, state);
    return state;
  }

  /**
   * Parse analysis result into track data.
   */
  function parseAnalysisResult(state, result) {
    if (!result) return;

    state.duration = result.duration_sec || 180;

    // Extract stems from deep analysis
    const stemData = result.stems_paths || result.midi_stems || {};
    const midiData = result.midi_stems || {};
    const sectionsData = result.sections || [];

    // Create tracks for each available stem
    const availableStems = new Set([
      ...Object.keys(stemData),
      ...Object.keys(midiData),
    ]);

    // Also check for detected types
    if (result.detection) {
      if (result.detection.is_drums) availableStems.add('drums');
      if (result.detection.is_bass) availableStems.add('bass');
      if (result.detection.is_guitar) availableStems.add('guitar');
      if (result.detection.is_synth) availableStems.add('other');
    }

    // Build tracks
    availableStems.forEach((stemType) => {
      const trackConfig = TRACK_TYPES[stemType] || TRACK_TYPES.other;
      const midi = midiData[stemType] || null;

      state.tracks.push({
        id: stemType,
        type: stemType,
        label: trackConfig.label,
        color: trackConfig.color,
        icon: trackConfig.icon,
        waveform: result.waveforms?.[stemType] || result.waveform || null,
        midi: midi,
        muted: false,
        solo: false,
        regions: [
          {
            start: 0,
            end: state.duration,
            confidence: result.quality?.overall_confidence || 0.75,
            label: trackConfig.label,
          },
        ],
      });
    });

    // If no stems, create a single "Full Mix" track
    if (state.tracks.length === 0) {
      state.tracks.push({
        id: 'mix',
        type: 'other',
        label: 'Full Mix',
        color: TRACK_TYPES.other.color,
        icon: '🎵',
        waveform: result.waveform || null,
        midi: result.midi || null,
        muted: false,
        solo: false,
        regions: [
          {
            start: 0,
            end: state.duration,
            confidence: result.quality?.overall_confidence || 0.75,
            label: 'Full Mix',
          },
        ],
      });
    }

    // Parse sections if available
    state.sections = sectionsData.map((s) => ({
      type: s.type || 'unknown',
      start: s.start_time || s.start || 0,
      end: s.end_time || s.end || 0,
      label: s.type || 'Section',
      color: getSectionColor(s.type),
    }));
  }

  /**
   * Get color for section type.
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
    return colors[type] || '#6b7280';
  }

  /**
   * Build DOM structure for arrangement view.
   */
  function buildDOM(state) {
    const { container, options } = state;

    container.innerHTML = '';
    container.classList.add('tone-arrangement');

    // Main layout
    const html = `
      <div class="arrangement-controls">
        <div class="arrangement-zoom">
          <button class="zoom-out" title="Zoom Out">−</button>
          <span class="zoom-level">100%</span>
          <button class="zoom-in" title="Zoom In">+</button>
        </div>
        <div class="arrangement-transport">
          <button class="transport-start" title="Go to Start">⏮</button>
          <button class="transport-play" title="Play/Pause">▶</button>
          <span class="transport-time">0:00.0</span>
        </div>
        <div class="arrangement-selection-info"></div>
      </div>
      <div class="arrangement-main">
        <div class="arrangement-viewport">
          <div class="arrangement-track-headers"></div>
          <div class="arrangement-timeline-container">
            <div class="arrangement-ruler"></div>
            <div class="arrangement-tracks"></div>
            <div class="arrangement-sections"></div>
            <div class="arrangement-playhead"></div>
            <div class="arrangement-selection-overlay"></div>
          </div>
        </div>
        <div class="arrangement-sidebar" style="display: none;">
          <div class="sidebar-header">
            <span class="sidebar-title">Region Details</span>
            <button class="sidebar-close" title="Close">&times;</button>
          </div>
          <div class="sidebar-content">
            <div class="sidebar-section sidebar-region-info"></div>
            <div class="sidebar-section sidebar-confidence"></div>
            <div class="sidebar-section sidebar-provenance"></div>
            <div class="sidebar-section sidebar-suggestions"></div>
          </div>
        </div>
      </div>
    `;

    container.innerHTML = html;

    // Cache DOM elements
    state.elements = {
      controls: container.querySelector('.arrangement-controls'),
      zoomOut: container.querySelector('.zoom-out'),
      zoomIn: container.querySelector('.zoom-in'),
      zoomLevel: container.querySelector('.zoom-level'),
      transportStart: container.querySelector('.transport-start'),
      transportPlay: container.querySelector('.transport-play'),
      transportTime: container.querySelector('.transport-time'),
      selectionInfo: container.querySelector('.arrangement-selection-info'),
      main: container.querySelector('.arrangement-main'),
      viewport: container.querySelector('.arrangement-viewport'),
      trackHeaders: container.querySelector('.arrangement-track-headers'),
      timelineContainer: container.querySelector(
        '.arrangement-timeline-container'
      ),
      ruler: container.querySelector('.arrangement-ruler'),
      tracks: container.querySelector('.arrangement-tracks'),
      sections: container.querySelector('.arrangement-sections'),
      playhead: container.querySelector('.arrangement-playhead'),
      selectionOverlay: container.querySelector(
        '.arrangement-selection-overlay'
      ),
      // Sidebar elements
      sidebar: container.querySelector('.arrangement-sidebar'),
      sidebarClose: container.querySelector('.sidebar-close'),
      sidebarTitle: container.querySelector('.sidebar-title'),
      sidebarRegionInfo: container.querySelector('.sidebar-region-info'),
      sidebarConfidence: container.querySelector('.sidebar-confidence'),
      sidebarProvenance: container.querySelector('.sidebar-provenance'),
      sidebarSuggestions: container.querySelector('.sidebar-suggestions'),
    };

    // Attach sidebar close handler
    if (state.elements.sidebarClose) {
      state.elements.sidebarClose.addEventListener('click', () => {
        hideSidebar(state);
      });
    }
  }

  /**
   * Attach event listeners.
   */
  function attachEvents(state) {
    const { elements, options } = state;

    // Zoom controls
    elements.zoomOut.addEventListener('click', () => setZoom(state, state.zoom * 0.75));
    elements.zoomIn.addEventListener('click', () => setZoom(state, state.zoom * 1.5));

    // Transport controls
    elements.transportStart.addEventListener('click', () => setPlayhead(state, 0));
    elements.transportPlay.addEventListener('click', () => togglePlayback(state));

    // Timeline click for playhead
    elements.timelineContainer.addEventListener('click', (e) => {
      if (state.isDragging) return;
      const time = getTimeFromX(state, e.offsetX);
      setPlayhead(state, time);
    });

    // Selection drag
    if (options.enableSelection) {
      elements.timelineContainer.addEventListener('mousedown', (e) => {
        if (e.button !== 0) return;
        startSelection(state, e);
      });

      document.addEventListener('mousemove', (e) => {
        if (state.isDragging) {
          updateSelection(state, e);
        }
      });

      document.addEventListener('mouseup', (e) => {
        if (state.isDragging) {
          endSelection(state, e);
        }
      });
    }

    // Horizontal scroll
    elements.timelineContainer.addEventListener('wheel', (e) => {
      if (e.ctrlKey || e.metaKey) {
        // Zoom with ctrl+wheel
        e.preventDefault();
        const zoomFactor = e.deltaY > 0 ? 0.9 : 1.1;
        setZoom(state, state.zoom * zoomFactor);
      } else {
        // Horizontal scroll
        state.scrollX = Math.max(
          0,
          Math.min(
            state.scrollX + e.deltaX,
            getTimelineWidth(state) - elements.timelineContainer.clientWidth
          )
        );
        render(state);
      }
    });
  }

  /**
   * Get timeline width in pixels.
   */
  function getTimelineWidth(state) {
    return state.duration * state.options.pixelsPerSecond * state.zoom;
  }

  /**
   * Convert X position to time.
   */
  function getTimeFromX(state, x) {
    return (x + state.scrollX) / (state.options.pixelsPerSecond * state.zoom);
  }

  /**
   * Convert time to X position.
   */
  function getXFromTime(state, time) {
    return time * state.options.pixelsPerSecond * state.zoom - state.scrollX;
  }

  /**
   * Set zoom level.
   */
  function setZoom(state, zoom) {
    state.zoom = Math.max(
      state.options.minZoom,
      Math.min(state.options.maxZoom, zoom)
    );
    state.elements.zoomLevel.textContent = `${Math.round(state.zoom * 100)}%`;
    render(state);
  }

  /**
   * Set playhead position.
   */
  function setPlayhead(state, time) {
    state.playhead = Math.max(0, Math.min(time, state.duration));
    updatePlayhead(state);
    updateTransportTime(state);

    // Sync multi-track stems if available
    if (state.useMultiTrack) {
      state.stemTracks.forEach((track) => {
        if (track.audioElement && track.audioElement.readyState >= 1) {
          track.audioElement.currentTime = state.playhead;
        }
      });
    }
    // Sync single audio element if available
    else if (state.audioElement && state.audioElement.readyState >= 1) {
      state.audioElement.currentTime = state.playhead;
    }

    if (state.options.onPlayheadChange) {
      state.options.onPlayheadChange(state.playhead);
    }
  }

  /**
   * Toggle playback.
   */
  function togglePlayback(state) {
    console.log('ToneArrangement.togglePlayback called:', {
      useMultiTrack: state.useMultiTrack,
      stemCount: state.stemTracks.size,
      hasAudio: !!state.audioElement,
    });

    // Multi-track mode
    if (state.useMultiTrack && state.stemTracks.size > 0) {
      // Resume audio context if suspended (browser autoplay policy)
      if (state.audioContext.state === 'suspended') {
        state.audioContext.resume();
      }

      state.isPlaying = !state.isPlaying;
      state.elements.transportPlay.textContent = state.isPlaying ? '⏸' : '▶';

      if (state.isPlaying) {
        // Sync all stems to playhead position and play
        state.stemTracks.forEach((track) => {
          track.audioElement.currentTime = state.playhead;
          track.audioElement.play().catch(err => {
            console.error('Stem playback failed:', err);
          });
        });
        startPlaybackAnimation(state);
      } else {
        // Pause all stems
        state.stemTracks.forEach((track) => {
          track.audioElement.pause();
        });
        stopPlaybackAnimation(state);
      }
      return;
    }

    // Single track fallback
    if (!state.audioElement) {
      console.warn('ToneArrangement: No audio source set');
      return;
    }

    state.isPlaying = !state.isPlaying;
    state.elements.transportPlay.textContent = state.isPlaying ? '⏸' : '▶';

    if (state.isPlaying) {
      // Sync audio position with playhead
      state.audioElement.currentTime = state.playhead;
      state.audioElement.play().catch(err => {
        console.error('Audio playback failed:', err);
        state.isPlaying = false;
        state.elements.transportPlay.textContent = '▶';
      });
      // Start playhead animation
      startPlaybackAnimation(state);
    } else {
      state.audioElement.pause();
      stopPlaybackAnimation(state);
    }
  }

  /**
   * Start playhead animation during playback.
   */
  function startPlaybackAnimation(state) {
    function animate() {
      if (!state.isPlaying) return;

      // Get current time from first available audio source
      let currentTime = state.playhead;
      let hasEnded = false;

      if (state.useMultiTrack && state.stemTracks.size > 0) {
        const firstTrack = state.stemTracks.values().next().value;
        if (firstTrack?.audioElement) {
          currentTime = firstTrack.audioElement.currentTime;
          hasEnded = firstTrack.audioElement.ended;
        }
      } else if (state.audioElement) {
        currentTime = state.audioElement.currentTime;
        hasEnded = state.audioElement.ended;
      }

      state.playhead = currentTime;
      updatePlayhead(state);
      updateTransportTime(state);

      // Check if reached end
      if (hasEnded) {
        state.isPlaying = false;
        state.elements.transportPlay.textContent = '▶';
        return;
      }

      state.playbackAnimationFrame = requestAnimationFrame(animate);
    }
    animate();
  }

  /**
   * Stop playhead animation.
   */
  function stopPlaybackAnimation(state) {
    if (state.playbackAnimationFrame) {
      cancelAnimationFrame(state.playbackAnimationFrame);
      state.playbackAnimationFrame = null;
    }
  }

  /**
   * Set audio source for playback.
   */
  function setAudioSource(state, source) {
    console.log('ToneArrangement.setAudioSource called:', { state: !!state, source: source?.name || source });

    // Clean up previous audio
    if (state.audioElement) {
      state.audioElement.pause();
      stopPlaybackAnimation(state);
    }
    if (state.audioUrl && state.audioUrl.startsWith('blob:')) {
      URL.revokeObjectURL(state.audioUrl);
    }

    // Create new audio element
    state.audioElement = new Audio();
    state.audioElement.preload = 'auto';

    // Handle different source types
    if (source instanceof File || source instanceof Blob) {
      state.audioUrl = URL.createObjectURL(source);
      state.audioElement.src = state.audioUrl;
    } else if (typeof source === 'string') {
      state.audioUrl = source;
      state.audioElement.src = source;
    } else {
      console.warn('ToneArrangement: Invalid audio source');
      return;
    }

    // Update duration when metadata loads
    state.audioElement.addEventListener('loadedmetadata', () => {
      if (state.duration === 0) {
        state.duration = state.audioElement.duration;
        renderTracks(state);
      }
    });

    // Handle playback end
    state.audioElement.addEventListener('ended', () => {
      state.isPlaying = false;
      state.elements.transportPlay.textContent = '▶';
      stopPlaybackAnimation(state);
    });
  }

  /**
   * Load stems for multi-track playback with mute/solo support.
   * @param {Object} state - Arrangement state
   * @param {Object} stems - Map of stem type to URL (e.g., { drums: '/api/serve-file?...', bass: '...' })
   */
  async function loadStems(state, stems) {
    if (!stems || Object.keys(stems).length === 0) {
      console.warn('ToneArrangement: No stems provided for multi-track playback');
      return;
    }

    console.log('ToneArrangement.loadStems:', Object.keys(stems));

    // Initialize Web Audio API context
    if (!state.audioContext) {
      state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
    }

    // Create master gain node
    if (!state.masterGain) {
      state.masterGain = state.audioContext.createGain();
      state.masterGain.connect(state.audioContext.destination);
    }

    // Clear existing stem tracks
    state.stemTracks.forEach((track) => {
      if (track.gainNode) {
        track.gainNode.disconnect();
      }
    });
    state.stemTracks.clear();

    // Load each stem
    const loadPromises = [];
    for (const [stemType, url] of Object.entries(stems)) {
      const loadPromise = (async () => {
        try {
          // Create audio element for this stem
          const audioElement = new Audio();
          audioElement.crossOrigin = 'anonymous';
          audioElement.preload = 'auto';
          audioElement.src = url;

          // Wait for audio to be ready
          await new Promise((resolve, reject) => {
            audioElement.addEventListener('canplaythrough', resolve, { once: true });
            audioElement.addEventListener('error', reject, { once: true });
            audioElement.load();
          });

          // Create media source and gain node
          const source = state.audioContext.createMediaElementSource(audioElement);
          const gainNode = state.audioContext.createGain();
          gainNode.gain.value = 1.0;

          // Connect: source -> gain -> master
          source.connect(gainNode);
          gainNode.connect(state.masterGain);

          // Store track info
          state.stemTracks.set(stemType, {
            audioElement,
            source,
            gainNode,
            url,
          });

          // Update track in state to mark it has audio
          const track = state.tracks.find(t => t.id === stemType || t.type === stemType);
          if (track) {
            track.hasAudio = true;
          }

          console.log(`ToneArrangement: Loaded stem ${stemType}`);
        } catch (err) {
          console.error(`ToneArrangement: Failed to load stem ${stemType}:`, err);
        }
      })();
      loadPromises.push(loadPromise);
    }

    await Promise.all(loadPromises);

    // Enable multi-track mode if we loaded any stems
    state.useMultiTrack = state.stemTracks.size > 0;

    // Get duration from first loaded stem
    if (state.stemTracks.size > 0) {
      const firstTrack = state.stemTracks.values().next().value;
      if (firstTrack?.audioElement?.duration) {
        state.duration = firstTrack.audioElement.duration;
        renderTracks(state);
      }
    }

    console.log(`ToneArrangement: Multi-track mode ${state.useMultiTrack ? 'enabled' : 'disabled'} with ${state.stemTracks.size} stems`);

    // Apply current mute/solo state
    updateStemGains(state);
  }

  /**
   * Update gain values for all stems based on mute/solo state.
   */
  function updateStemGains(state) {
    if (!state.useMultiTrack) return;

    const hasSolo = state.tracks.some(t => t.solo);

    state.tracks.forEach((track) => {
      const stemTrack = state.stemTracks.get(track.id) || state.stemTracks.get(track.type);
      if (!stemTrack) return;

      // Track is effectively muted if: explicitly muted OR (another track is solo'd and this one isn't)
      const isEffectivelyMuted = track.muted || (hasSolo && !track.solo);

      // Set gain (0 = muted, 1 = full volume)
      stemTrack.gainNode.gain.setValueAtTime(
        isEffectivelyMuted ? 0 : 1,
        state.audioContext.currentTime
      );
    });
  }

  /**
   * Update transport time display.
   */
  function updateTransportTime(state) {
    const time = state.playhead;
    const min = Math.floor(time / 60);
    const sec = (time % 60).toFixed(1);
    state.elements.transportTime.textContent = `${min}:${sec.padStart(4, '0')}`;
  }

  /**
   * Update playhead position.
   */
  function updatePlayhead(state) {
    const x = getXFromTime(state, state.playhead);
    state.elements.playhead.style.left = `${x}px`;
    state.elements.playhead.style.display = x >= 0 ? 'block' : 'none';
  }

  /**
   * Start region selection.
   */
  function startSelection(state, e) {
    const rect = state.elements.timelineContainer.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const time = getTimeFromX(state, x);

    state.isDragging = true;
    state.dragStart = { x, time };
    state.selection = { start: time, end: time, trackId: null };
  }

  /**
   * Update selection during drag.
   */
  function updateSelection(state, e) {
    if (!state.isDragging || !state.dragStart) return;

    const rect = state.elements.timelineContainer.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const time = getTimeFromX(state, x);

    state.selection = {
      start: Math.min(state.dragStart.time, time),
      end: Math.max(state.dragStart.time, time),
      trackId: null,
    };

    renderSelectionOverlay(state);
    updateSelectionInfo(state);
  }

  /**
   * End region selection.
   */
  function endSelection(state, e) {
    state.isDragging = false;

    if (
      state.selection &&
      state.selection.end - state.selection.start > 0.1
    ) {
      // Valid selection
      if (state.options.onRegionSelect) {
        state.options.onRegionSelect(state.selection);
      }
    } else {
      // Clear selection
      state.selection = null;
      renderSelectionOverlay(state);
      updateSelectionInfo(state);
    }

    state.dragStart = null;
  }

  /**
   * Render selection overlay.
   */
  function renderSelectionOverlay(state) {
    const overlay = state.elements.selectionOverlay;

    if (!state.selection) {
      overlay.style.display = 'none';
      return;
    }

    const startX = getXFromTime(state, state.selection.start);
    const endX = getXFromTime(state, state.selection.end);

    overlay.style.display = 'block';
    overlay.style.left = `${startX}px`;
    overlay.style.width = `${endX - startX}px`;
  }

  /**
   * Update selection info display.
   */
  function updateSelectionInfo(state) {
    const info = state.elements.selectionInfo;

    if (!state.selection) {
      info.innerHTML = '';
      return;
    }

    const duration = state.selection.end - state.selection.start;
    const startStr = formatTime(state.selection.start);
    const endStr = formatTime(state.selection.end);
    const durStr = formatTime(duration);

    info.innerHTML = `
      <span class="selection-range">${startStr} - ${endStr}</span>
      <span class="selection-duration">(${durStr})</span>
      <button class="selection-analyze" title="Re-analyze this region">Analyze Region</button>
      <button class="selection-clear" title="Clear selection">×</button>
    `;

    // Attach handlers
    info.querySelector('.selection-analyze')?.addEventListener('click', () => {
      if (state.options.onRegionSelect) {
        state.options.onRegionSelect(state.selection);
      }
    });

    info.querySelector('.selection-clear')?.addEventListener('click', () => {
      state.selection = null;
      renderSelectionOverlay(state);
      updateSelectionInfo(state);
    });
  }

  /**
   * Format time as m:ss.s
   */
  function formatTime(seconds) {
    const min = Math.floor(seconds / 60);
    const sec = (seconds % 60).toFixed(1);
    return `${min}:${sec.padStart(4, '0')}`;
  }

  /**
   * Get confidence color.
   */
  function getConfidenceColor(confidence) {
    if (confidence >= 0.85) return CONFIDENCE_COLORS.excellent;
    if (confidence >= 0.7) return CONFIDENCE_COLORS.good;
    if (confidence >= 0.5) return CONFIDENCE_COLORS.acceptable;
    if (confidence >= 0.3) return CONFIDENCE_COLORS.marginal;
    return CONFIDENCE_COLORS.poor;
  }

  /**
   * Render the full arrangement view.
   */
  function render(state) {
    renderRuler(state);
    renderTrackHeaders(state);
    renderTracks(state);
    renderSections(state);
    updatePlayhead(state);
    renderSelectionOverlay(state);
  }

  /**
   * Render timeline ruler.
   */
  function renderRuler(state) {
    const ruler = state.elements.ruler;
    const width = getTimelineWidth(state);
    ruler.style.width = `${width}px`;

    // Calculate tick interval based on zoom
    let tickInterval = 1; // seconds
    if (state.zoom < 0.5) tickInterval = 5;
    if (state.zoom < 0.25) tickInterval = 10;
    if (state.zoom > 2) tickInterval = 0.5;
    if (state.zoom > 4) tickInterval = 0.25;

    let html = '';
    for (let t = 0; t <= state.duration; t += tickInterval) {
      const x = getXFromTime(state, t) + state.scrollX;
      const isMajor = t % (tickInterval * 4) === 0;
      const label = isMajor ? formatTime(t) : '';

      html += `
        <div class="ruler-tick ${isMajor ? 'major' : ''}" style="left: ${x}px">
          ${label ? `<span class="ruler-label">${label}</span>` : ''}
        </div>
      `;
    }

    ruler.innerHTML = html;
  }

  /**
   * Render track headers (labels, mute/solo).
   */
  function renderTrackHeaders(state) {
    const headers = state.elements.trackHeaders;
    const { trackHeight } = state.options;

    let html = '';
    state.tracks.forEach((track) => {
      html += `
        <div class="track-header" style="height: ${trackHeight}px" data-track="${track.id}">
          <span class="track-icon">${track.icon}</span>
          <span class="track-label">${track.label}</span>
          <div class="track-controls">
            <button class="track-mute ${track.muted ? 'active' : ''}" title="Mute">M</button>
            <button class="track-solo ${track.solo ? 'active' : ''}" title="Solo">S</button>
          </div>
        </div>
      `;
    });

    headers.innerHTML = html;

    // Attach mute/solo handlers
    headers.querySelectorAll('.track-mute').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation(); // Prevent event bubbling
        const trackId = e.target.closest('.track-header').dataset.track;
        const track = state.tracks.find((t) => t.id === trackId);
        if (track) {
          track.muted = !track.muted;
          renderTrackHeaders(state);
          renderTracks(state); // Re-render tracks to show muted state
          updateStemGains(state); // Update audio gain for multi-track
        }
      });
    });

    headers.querySelectorAll('.track-solo').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation(); // Prevent event bubbling
        const trackId = e.target.closest('.track-header').dataset.track;
        const track = state.tracks.find((t) => t.id === trackId);
        if (track) {
          track.solo = !track.solo;
          renderTrackHeaders(state);
          renderTracks(state); // Re-render tracks to show solo state
          updateStemGains(state); // Update audio gain for multi-track
        }
      });
    });
  }

  /**
   * Render track lanes with waveforms and MIDI.
   */
  function renderTracks(state) {
    const container = state.elements.tracks;
    const { trackHeight, showMidi, showConfidence } = state.options;
    const width = getTimelineWidth(state);

    container.style.width = `${width}px`;

    // Determine if any track is solo'd
    const hasSolo = state.tracks.some(t => t.solo);

    let html = '';
    state.tracks.forEach((track) => {
      // Track is effectively muted if: explicitly muted OR (another track is solo'd and this one isn't)
      const isEffectivelyMuted = track.muted || (hasSolo && !track.solo);
      const mutedClass = isEffectivelyMuted ? 'track-muted' : '';

      html += `
        <div class="track-lane ${mutedClass}" style="height: ${trackHeight}px" data-track="${track.id}">
          <canvas class="track-waveform" width="${width}" height="${trackHeight}"></canvas>
          ${showMidi ? `<canvas class="track-midi" width="${width}" height="${trackHeight}"></canvas>` : ''}
          ${showConfidence ? `<div class="track-confidence-overlay"></div>` : ''}
        </div>
      `;
    });

    container.innerHTML = html;

    // Render each track
    state.tracks.forEach((track) => {
      const lane = container.querySelector(`[data-track="${track.id}"]`);
      const waveformCanvas = lane.querySelector('.track-waveform');
      const midiCanvas = lane.querySelector('.track-midi');
      const confidenceOverlay = lane.querySelector('.track-confidence-overlay');

      renderTrackWaveform(state, track, waveformCanvas);

      if (midiCanvas && track.midi) {
        renderTrackMidi(state, track, midiCanvas);
      }

      if (confidenceOverlay) {
        renderConfidenceOverlay(state, track, confidenceOverlay);
      }
    });
  }

  /**
   * Render waveform for a track.
   */
  function renderTrackWaveform(state, track, canvas) {
    const ctx = canvas.getContext('2d');
    const { width, height } = canvas;
    const dpr = window.devicePixelRatio || 1;

    // Scale for high DPI
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.scale(dpr, dpr);

    // Clear
    ctx.fillStyle = '#1a1612';
    ctx.fillRect(0, 0, width, height);

    if (!track.waveform) {
      // Draw placeholder
      ctx.fillStyle = track.color + '40';
      ctx.fillRect(0, height * 0.35, width, height * 0.3);
      return;
    }

    const peaks = track.waveform.peaks_positive || track.waveform;
    if (!peaks || peaks.length === 0) return;

    const numPeaks = peaks.length;
    const samplesPerPixel = numPeaks / width;
    const centerY = height / 2;

    ctx.fillStyle = track.color;

    for (let x = 0; x < width; x++) {
      const sampleIndex = Math.floor(x * samplesPerPixel);
      const peak = Math.abs(peaks[sampleIndex] || 0);
      const barHeight = peak * centerY * 0.9;

      ctx.fillRect(x, centerY - barHeight, 1, barHeight * 2);
    }
  }

  /**
   * Render MIDI notes for a track.
   */
  function renderTrackMidi(state, track, canvas) {
    const ctx = canvas.getContext('2d');
    const { width, height } = canvas;
    const dpr = window.devicePixelRatio || 1;

    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.scale(dpr, dpr);

    const midi = track.midi;
    if (!midi || !midi.notes) return;

    const notes = midi.notes;
    if (notes.length === 0) return;

    // Find pitch range
    let minPitch = 127,
      maxPitch = 0;
    notes.forEach((n) => {
      minPitch = Math.min(minPitch, n.pitch || n[0] || 60);
      maxPitch = Math.max(maxPitch, n.pitch || n[0] || 60);
    });

    const pitchRange = Math.max(maxPitch - minPitch, 12);
    const noteHeight = Math.max(2, height / pitchRange);

    ctx.fillStyle = '#ffffff';
    ctx.globalAlpha = 0.8;

    notes.forEach((note) => {
      const pitch = note.pitch || note[0] || 60;
      const start = note.start || note[1] || 0;
      const end = note.end || note[2] || start + 0.1;

      const x = start * state.options.pixelsPerSecond * state.zoom;
      const w = (end - start) * state.options.pixelsPerSecond * state.zoom;
      const y = height - ((pitch - minPitch) / pitchRange) * height - noteHeight;

      ctx.fillRect(x, y, Math.max(w, 2), noteHeight);
    });

    ctx.globalAlpha = 1;
  }

  /**
   * Render confidence heatmap overlay.
   */
  function renderConfidenceOverlay(state, track, overlay) {
    if (!track.regions || track.regions.length === 0) return;

    let html = '';
    track.regions.forEach((region) => {
      const startX = getXFromTime(state, region.start) + state.scrollX;
      const endX = getXFromTime(state, region.end) + state.scrollX;
      const width = endX - startX;
      const color = getConfidenceColor(region.confidence);

      html += `
        <div class="confidence-region"
             style="left: ${startX}px; width: ${width}px; background: ${color}20; border-color: ${color};"
             title="Confidence: ${Math.round(region.confidence * 100)}%">
        </div>
      `;
    });

    overlay.innerHTML = html;
  }

  /**
   * Render section markers.
   */
  function renderSections(state) {
    const container = state.elements.sections;
    const width = getTimelineWidth(state);
    container.style.width = `${width}px`;

    let html = '';
    state.sections.forEach((section) => {
      const startX = getXFromTime(state, section.start) + state.scrollX;
      const endX = getXFromTime(state, section.end) + state.scrollX;
      const width = endX - startX;

      html += `
        <div class="section-marker"
             style="left: ${startX}px; width: ${width}px; background: ${section.color}20; border-left-color: ${section.color};">
          <span class="section-label" style="color: ${section.color}">${section.label}</span>
        </div>
      `;
    });

    container.innerHTML = html;
  }

  /**
   * Update arrangement with new analysis result.
   */
  function update(containerId, analysisResult) {
    const state = instances.get(containerId);
    if (!state) return;

    parseAnalysisResult(state, analysisResult);
    render(state);
  }

  /**
   * Set sections data.
   */
  function setSections(containerId, sections) {
    const state = instances.get(containerId);
    if (!state) return;

    state.sections = sections.map((s) => ({
      type: s.type || 'unknown',
      start: s.start_time || s.start || 0,
      end: s.end_time || s.end || 0,
      label: s.type || 'Section',
      color: getSectionColor(s.type),
    }));

    renderSections(state);
  }

  /**
   * Get current selection.
   */
  function getSelection(containerId) {
    const state = instances.get(containerId);
    return state?.selection || null;
  }

  /**
   * Clear selection.
   */
  function clearSelection(containerId) {
    const state = instances.get(containerId);
    if (!state) return;

    state.selection = null;
    renderSelectionOverlay(state);
    updateSelectionInfo(state);
  }

  /**
   * Show sidebar with region analysis details.
   */
  function showSidebar(state, regionData) {
    const { elements } = state;
    if (!elements.sidebar) return;

    elements.sidebar.style.display = 'flex';

    // Region info
    if (elements.sidebarRegionInfo && regionData.bounds) {
      const bounds = regionData.bounds;
      elements.sidebarRegionInfo.innerHTML = `
        <h4>Region</h4>
        <div class="sidebar-item">
          <span class="item-label">Time Range</span>
          <span class="item-value">${formatTime(bounds.start)} - ${formatTime(bounds.end)}</span>
        </div>
        <div class="sidebar-item">
          <span class="item-label">Duration</span>
          <span class="item-value">${formatTime(bounds.duration || bounds.end - bounds.start)}</span>
        </div>
        ${regionData.section_type ? `
        <div class="sidebar-item">
          <span class="item-label">Section</span>
          <span class="item-value section-badge" style="background: ${getSectionColor(regionData.section_type)}20; color: ${getSectionColor(regionData.section_type)}">${regionData.section_type}</span>
        </div>
        ` : ''}
        <div class="sidebar-item">
          <span class="item-label">Notes Extracted</span>
          <span class="item-value">${regionData.note_count || 0}</span>
        </div>
      `;
    }

    // Confidence details
    if (elements.sidebarConfidence && regionData.confidence) {
      const conf = regionData.confidence;
      elements.sidebarConfidence.innerHTML = `
        <h4>Confidence</h4>
        <div class="sidebar-item">
          <span class="item-label">Overall</span>
          <div class="confidence-bar">
            <div class="confidence-fill" style="width: ${(conf.overall * 100)}%; background: ${getConfidenceColor(conf.overall)}"></div>
          </div>
          <span class="item-value">${Math.round(conf.overall * 100)}%</span>
        </div>
        <div class="sidebar-item">
          <span class="item-label">Note Detection</span>
          <span class="item-value">${Math.round((conf.note_confidence || 0) * 100)}%</span>
        </div>
        <div class="sidebar-item">
          <span class="item-label">Timing</span>
          <span class="item-value">${Math.round((conf.timing_confidence || 0) * 100)}%</span>
        </div>
        <div class="sidebar-item">
          <span class="item-label">Pitch</span>
          <span class="item-value">${Math.round((conf.pitch_confidence || 0) * 100)}%</span>
        </div>
        ${conf.issues ? `
        <div class="sidebar-issues">
          <span class="issues-label">Issues Detected</span>
          ${conf.issues.octave_ambiguity > 0.2 ? '<span class="issue-tag">Octave Ambiguity</span>' : ''}
          ${conf.issues.harmonic_confusion > 0.2 ? '<span class="issue-tag">Harmonic Confusion</span>' : ''}
          ${conf.issues.reverb_artifacts > 0.2 ? '<span class="issue-tag">Reverb Artifacts</span>' : ''}
          ${conf.issues.timing_drift > 0.2 ? '<span class="issue-tag">Timing Drift</span>' : ''}
        </div>
        ` : ''}
      `;
    }

    // Provenance details
    if (elements.sidebarProvenance && regionData.provenance) {
      const prov = regionData.provenance;
      elements.sidebarProvenance.innerHTML = `
        <h4>Provenance</h4>
        <div class="sidebar-item">
          <span class="item-label">Detectors</span>
          <span class="item-value">${Object.keys(prov.detector_contributions || {}).join(', ') || 'basic-pitch'}</span>
        </div>
        ${prov.cleanup_passes_applied?.length > 0 ? `
        <div class="sidebar-item">
          <span class="item-label">Cleanup Passes</span>
          <span class="item-value">${prov.cleanup_passes_applied.join(', ')}</span>
        </div>
        ` : ''}
        <div class="sidebar-item">
          <span class="item-label">Corrections Made</span>
          <span class="item-value">${prov.corrections_made || 0}</span>
        </div>
        ${prov.octave_corrections > 0 ? `
        <div class="sidebar-item">
          <span class="item-label">Octave Fixes</span>
          <span class="item-value">${prov.octave_corrections}</span>
        </div>
        ` : ''}
        <div class="sidebar-item">
          <span class="item-label">FP Risk</span>
          <span class="item-value ${prov.fp_risk > 0.3 ? 'risk-high' : 'risk-low'}">${Math.round((prov.fp_risk || 0) * 100)}%</span>
        </div>
        <div class="sidebar-item">
          <span class="item-label">FN Risk</span>
          <span class="item-value ${prov.fn_risk > 0.3 ? 'risk-high' : 'risk-low'}">${Math.round((prov.fn_risk || 0) * 100)}%</span>
        </div>
      `;
    }

    // Suggestions
    if (elements.sidebarSuggestions) {
      const suggestions = [];
      if (regionData.confidence?.needs_cleanup) {
        suggestions.push('Region may benefit from manual cleanup');
      }
      if (regionData.confidence?.suggested_passes?.length > 0) {
        suggestions.push(`Suggested cleanup: ${regionData.confidence.suggested_passes.join(', ')}`);
      }
      if (regionData.provenance?.fp_risk > 0.3) {
        suggestions.push('High false positive risk - check for ghost notes');
      }
      if (regionData.provenance?.fn_risk > 0.4) {
        suggestions.push('High false negative risk - may be missing notes');
      }

      if (suggestions.length > 0) {
        elements.sidebarSuggestions.innerHTML = `
          <h4>Suggestions</h4>
          ${suggestions.map(s => `<div class="suggestion-item">${s}</div>`).join('')}
        `;
      } else {
        elements.sidebarSuggestions.innerHTML = '';
      }
    }
  }

  /**
   * Hide sidebar.
   */
  function hideSidebar(state) {
    if (state.elements.sidebar) {
      state.elements.sidebar.style.display = 'none';
    }
  }

  /**
   * Destroy instance and clean up.
   */
  function destroy(containerId) {
    const state = instances.get(containerId);
    if (!state) return;

    // Clean up single-track audio
    if (state.audioElement) {
      state.audioElement.pause();
      stopPlaybackAnimation(state);
    }
    if (state.audioUrl && state.audioUrl.startsWith('blob:')) {
      URL.revokeObjectURL(state.audioUrl);
    }

    // Clean up multi-track audio
    if (state.stemTracks) {
      state.stemTracks.forEach((track) => {
        if (track.audioElement) {
          track.audioElement.pause();
        }
        if (track.gainNode) {
          track.gainNode.disconnect();
        }
        if (track.source) {
          track.source.disconnect();
        }
      });
      state.stemTracks.clear();
    }
    if (state.audioContext) {
      state.audioContext.close().catch(() => {});
    }

    state.container.innerHTML = '';
    state.container.classList.remove('tone-arrangement');
    instances.delete(containerId);
  }

  // Public API
  return {
    init,
    update,
    destroy,
    setZoom: (id, zoom) => {
      const state = instances.get(id);
      if (state) setZoom(state, zoom);
    },
    setPlayhead: (id, time) => {
      const state = instances.get(id);
      if (state) setPlayhead(state, time);
    },
    setAudioSource: (id, source) => {
      console.log('ToneArrangement.setAudioSource API:', { id, hasSource: !!source, instancesSize: instances.size });
      const state = instances.get(id);
      console.log('ToneArrangement.setAudioSource state lookup:', { found: !!state, keys: Array.from(instances.keys()) });
      if (state) setAudioSource(state, source);
    },
    togglePlayback: (id) => {
      const state = instances.get(id);
      if (state) togglePlayback(state);
    },
    loadStems: async (id, stems) => {
      console.log('ToneArrangement.loadStems API:', { id, stems: Object.keys(stems || {}) });
      const state = instances.get(id);
      if (state) await loadStems(state, stems);
    },
    setSections,
    getSelection,
    clearSelection,
    showSidebar: (id, regionData) => {
      const state = instances.get(id);
      if (state) showSidebar(state, regionData);
    },
    hideSidebar: (id) => {
      const state = instances.get(id);
      if (state) hideSidebar(state);
    },
    TRACK_TYPES,
    CONFIDENCE_COLORS,
  };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
  module.exports = ToneArrangement;
}
