/**
 * ToneForge Detection Utilities
 * Shared logic for instrument detection and visibility decisions
 */
const ToneDetection = {
  // Confidence thresholds for showing UI elements
  THRESHOLDS: {
    guitar: 0.25,   // Show guitar/amp/cab if guitar confidence >= 0.25
    bass: 0.20,
    drums: 0.15,    // Lower threshold - drums harder to detect
    synth: 0.20,
  },

  /**
   * Check if guitar analysis should be suppressed (synth dominant)
   * @param {Object} confidence - Per-instrument confidence scores
   * @returns {boolean} True if guitar should be skipped
   */
  shouldSkipGuitar(confidence) {
    if (!confidence) return false;
    return (
      (confidence.synth || 0) > 0.5 &&
      (confidence.guitar || 0) < 0.25
    );
  },

  /**
   * Check if a specific instrument type should be shown
   * @param {string} type - Instrument type: 'guitar', 'bass', 'drums', 'synth'
   * @param {Object} confidence - Per-instrument confidence scores
   * @param {Object} data - Analysis result data
   * @returns {boolean} True if the instrument UI should be visible
   */
  shouldShowInstrument(type, confidence, data) {
    if (!confidence) {
      // Fall back to checking if data exists
      return !!data?.[type] || (type === 'guitar' && !!data?.guitar);
    }

    const threshold = this.THRESHOLDS[type] || 0.2;
    const conf = confidence[type] || 0;

    // Special case: synth also shows for piano/other detected types
    if (type === 'synth') {
      const detectedType = data?.detected_type;
      if (detectedType === 'piano' || detectedType === 'other') {
        return true;
      }
    }

    // Special case: skip guitar if synth is dominant
    if (type === 'guitar' && this.shouldSkipGuitar(confidence)) {
      return false;
    }

    return conf >= threshold;
  },

  /**
   * Get the default platform/tab based on detected type
   * @param {string} detectedType - Primary detected instrument type
   * @returns {string} Platform name for default tab
   */
  getDefaultPlatform(detectedType) {
    switch (detectedType) {
      case 'drums': return 'drums';
      case 'bass': return 'bass';
      case 'piano':
      case 'synth':
      case 'other': return 'synth';
      case 'guitar':
      default: return 'helix';
    }
  },

  /**
   * Get visibility map for all platforms
   * @param {Object} confidence - Per-instrument confidence scores
   * @param {Object} data - Analysis result data
   * @returns {Object} Map of platform -> shouldShow boolean
   */
  getVisibilityMap(confidence, data) {
    return {
      helix: this.shouldShowInstrument('guitar', confidence, data) && !!data?.guitar,
      pedals: this.shouldShowInstrument('guitar', confidence, data) && !!data?.guitar,
      bass: this.shouldShowInstrument('bass', confidence, data) && !!data?.bass,
      drums: this.shouldShowInstrument('drums', confidence, data) && !!data?.drums,
      synth: this.shouldShowInstrument('synth', confidence, data) && (!!data?.synth || !!data?.piano),
    };
  },

  /**
   * Update tab visibility based on detection results
   * @param {NodeList|Array} tabs - Tab elements
   * @param {Object} confidence - Per-instrument confidence scores
   * @param {Object} data - Analysis result data
   * @returns {Element|null} First visible tab element
   */
  updateTabVisibility(tabs, confidence, data) {
    const visibility = this.getVisibilityMap(confidence, data);
    let firstVisible = null;

    tabs.forEach(tab => {
      const platform = tab.dataset.platform;
      const shouldShow = visibility[platform] ?? false;
      const conf = confidence?.[platform === 'helix' || platform === 'pedals' ? 'guitar' : platform] || 0;

      tab.classList.remove('tab--active', 'tab--detected', 'tab--low-confidence');
      tab.style.display = shouldShow ? '' : 'none';

      if (shouldShow) {
        tab.classList.add('tab--detected');
        if (conf < 0.4) {
          tab.classList.add('tab--low-confidence');
        }
        if (!firstVisible) firstVisible = tab;
      }
    });

    // Ensure at least one tab is visible (synth as fallback)
    if (!firstVisible) {
      const synthTab = Array.from(tabs).find(t => t.dataset.platform === 'synth');
      if (synthTab) {
        synthTab.style.display = '';
        synthTab.classList.add('tab--detected');
        firstVisible = synthTab;
      }
    }

    return firstVisible;
  },
};

// Make available globally
if (typeof window !== 'undefined') {
  window.ToneDetection = ToneDetection;
}
