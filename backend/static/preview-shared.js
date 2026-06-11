/**
 * Shared tone preview functionality for ToneForge
 * Used by both main app and studio pages
 */

const TonePreview = {
  /**
   * Convert base64 string to Blob
   */
  base64ToBlob(base64, mimeType) {
    const binaryString = atob(base64);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    return new Blob([bytes], { type: mimeType });
  },

  /**
   * Generate a tone preview for a stem
   * @param {Object} options
   * @param {Object} options.descriptor - Tone descriptor from analysis
   * @param {string} options.stemName - Name of the stem (guitar, bass, vocals, other, drums)
   * @param {string|null} options.midiContent - Base64 encoded MIDI content
   * @param {Function} options.onStart - Called when generation starts
   * @param {Function} options.onSuccess - Called with audio blob URL on success
   * @param {Function} options.onError - Called with error message on failure
   */
  async generate({ descriptor, stemName, midiContent, onStart, onSuccess, onError }) {
    // Determine preset type based on stem
    let previewType = 'guitar';
    if (stemName === 'bass') previewType = 'bass';
    else if (stemName === 'other' || stemName === 'vocals') previewType = 'synth';
    else if (stemName === 'drums') previewType = 'synth';

    if (!descriptor) {
      onError?.('No descriptor available for preview');
      return;
    }

    onStart?.();

    // Debug: log what we're sending
    console.log('TonePreview.generate called:', {
      hasDescriptor: !!descriptor,
      descriptorKeys: descriptor ? Object.keys(descriptor) : 'null',
      stemName,
      previewType,
    });

    if (!descriptor) {
      onError?.('No tone descriptor available for this stem');
      return;
    }

    try {
      const resp = await fetch('/api/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          descriptor,
          preset_type: previewType,
          midi_content: midiContent,
        }),
      });

      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        const detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
        throw new Error(detail || `Preview failed: HTTP ${resp.status}`);
      }

      const data = await resp.json();

      if (data.audio_b64) {
        const audioBlob = this.base64ToBlob(data.audio_b64, 'audio/wav');
        const audioUrl = URL.createObjectURL(audioBlob);
        onSuccess?.(audioUrl, data);
      } else {
        throw new Error('No audio returned from preview');
      }
    } catch (err) {
      console.error('Preview generation failed:', err);
      onError?.(err.message);
    }
  },

  /**
   * Create a preview button element with built-in state management
   * @param {Object} options
   * @param {string} options.stemName - Name of the stem
   * @param {Object} options.descriptor - Tone descriptor
   * @param {string|null} options.midiContent - Base64 MIDI content
   * @param {HTMLAudioElement} options.audioEl - Audio element to play into
   * @param {Function} options.onStatusChange - Status callback (optional)
   * @returns {HTMLButtonElement}
   */
  createPreviewButton({ stemName, descriptor, midiContent, audioEl, onStatusChange }) {
    const btn = document.createElement('button');
    btn.className = 'btn-preview-tone';
    btn.innerHTML = '&#9658; Preview Tone';
    btn.title = 'Generate reconstructed tone preview';
    btn.style.cssText = `
      font-family: var(--mono, monospace);
      font-size: 11px;
      padding: 6px 12px;
      background: transparent;
      border: 1px solid var(--primary, #d97706);
      color: var(--primary, #d97706);
      cursor: pointer;
      border-radius: 4px;
      transition: all 0.2s;
    `;

    btn.onmouseenter = () => {
      btn.style.background = 'var(--primary, #d97706)';
      btn.style.color = 'var(--bg, #1a1a2e)';
    };
    btn.onmouseleave = () => {
      if (!btn.disabled) {
        btn.style.background = 'transparent';
        btn.style.color = 'var(--primary, #d97706)';
      }
    };

    btn.addEventListener('click', () => {
      this.generate({
        descriptor,
        stemName,
        midiContent,
        onStart: () => {
          btn.disabled = true;
          btn.innerHTML = '⏳ Generating...';
          btn.style.opacity = '0.6';
          onStatusChange?.('generating');
        },
        onSuccess: (audioUrl) => {
          btn.disabled = false;
          btn.innerHTML = '&#9658; Preview Tone';
          btn.style.opacity = '1';
          audioEl.src = audioUrl;
          audioEl.hidden = false;
          audioEl.play().catch(() => {});
          onStatusChange?.('success');
        },
        onError: (msg) => {
          btn.disabled = false;
          btn.innerHTML = '⚠ Preview Failed';
          btn.style.opacity = '1';
          btn.style.borderColor = 'var(--error, #f44)';
          btn.style.color = 'var(--error, #f44)';
          btn.title = `Error: ${msg}`;
          console.error('Preview error:', msg);
          onStatusChange?.('error', msg);
          // Reset after 3 seconds
          setTimeout(() => {
            btn.innerHTML = '&#9658; Preview Tone';
            btn.style.borderColor = 'var(--primary, #d97706)';
            btn.style.color = 'var(--primary, #d97706)';
            btn.title = 'Generate reconstructed tone preview';
          }, 3000);
        },
      });
    });

    return btn;
  }
};

// Export for module usage if needed
if (typeof module !== 'undefined' && module.exports) {
  module.exports = TonePreview;
}
