/**
 * Shared export functionality for ToneForge
 * Used by both main app and studio pages
 */

const ToneExport = {
  // Current result data (set by the page)
  currentResult: null,

  /**
   * Set the current analysis result for export
   */
  setResult(result) {
    this.currentResult = result;
  },

  /**
   * Download a file from content
   */
  downloadFile(filename, content, contentType) {
    let blob;
    const binaryTypes = [
      'application/x-ableton-live-set',
      'application/x-ableton-wavetable',
      'application/x-ableton-analog',
      'audio/midi',
      'application/zip',
    ];

    if (binaryTypes.includes(contentType)) {
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
  },

  /**
   * Export to a specific format
   */
  async export(format, stem = '', onStatus) {
    if (!this.currentResult) {
      onStatus?.('error', 'No analysis data available');
      return;
    }

    let chain = [], descriptor = {}, recommendations, machineMatch, fullResult, midiData;
    const result = this.currentResult;

    // MIDI export
    if (format === 'midi') {
      if (result.midi_stems && stem && result.midi_stems[stem]) {
        midiData = result.midi_stems[stem];
      } else if (stem === 'all' && result.midi_stems) {
        // For "all" - use first available stem or combined
        const stems = Object.keys(result.midi_stems);
        if (stems.length > 0) {
          midiData = result.midi_stems[stems[0]];
        }
      } else if (result.midi) {
        midiData = result.midi;
      }

      if (!midiData) {
        onStatus?.('error', 'No MIDI data available');
        return;
      }
    } else if (format === 'project_bundle') {
      if (!result.midi_stems || Object.keys(result.midi_stems).length === 0) {
        onStatus?.('error', 'Project Bundle requires MIDI stems');
        return;
      }
      fullResult = result;
      descriptor = result.descriptor || {};
    } else if (format === 'ableton_live_set') {
      if (!result.midi_stems || Object.keys(result.midi_stems).length === 0) {
        onStatus?.('error', 'Ableton Live Set requires MIDI stems');
        return;
      }
      fullResult = result;
      descriptor = result.descriptor || result.synth?.descriptor || {};
    } else if (format === 'text') {
      fullResult = result;
      descriptor = result.descriptor || result.guitar?.descriptor || result.synth?.descriptor || {};
    } else if (format === 'json') {
      fullResult = result;
      descriptor = result.descriptor || {};
    } else if ((format === 'ableton_wavetable' || format === 'ableton_analog') && result.synth) {
      fullResult = result;
      descriptor = result.synth.descriptor;
    } else if (format === 'bass' && result.bass) {
      descriptor = result.bass.descriptor;
      recommendations = result.bass.recommendations || [];
    } else if (format === 'drums' && result.drums) {
      descriptor = result.drums.descriptor;
      machineMatch = result.drums.machine_match;
    } else if (format.startsWith('synth') && result.synth) {
      descriptor = result.synth.descriptor;
    } else if (result.guitar) {
      chain = result.guitar.platforms?.helix || [];
      descriptor = result.guitar.descriptor;
    } else {
      descriptor = result.descriptor || {};
    }

    const sourceName = result.source_name || result.filename || descriptor?.source?.filename || 'preset';
    const presetName = sourceName.replace(/\.[^/.]+$/, '').substring(0, 50);

    onStatus?.('working', `Exporting ${format}...`);

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
      this.downloadFile(data.filename, data.content, data.content_type);
      onStatus?.('success', `Exported: ${data.filename}`);
    } catch (err) {
      onStatus?.('error', `Export failed: ${err.message}`);
      console.error(err);
    }
  },

  /**
   * Render export table into a container element
   * @param {HTMLElement} container - Container to render into
   * @param {Object} options - Options like onStatus callback
   */
  renderTable(container, options = {}) {
    const { onStatus } = options;

    const rows = [
      { header: 'General' },
      { name: 'Text Analysis', desc: 'Readable summary with all analysis data', format: 'text' },
      { name: 'JSON', desc: 'Universal format for backup or custom tools', format: 'json' },

      { header: 'Guitar Modelers' },
      { name: 'Helix .hlx', desc: 'Line 6 Helix Floor/LT/Rack preset', format: 'hlx' },
      { name: 'HX Stomp', desc: 'HX Stomp preset (6 blocks max)', format: 'hlx_stomp' },
      { name: 'Quad Cortex', desc: 'Neural DSP Quad Cortex preset', format: 'neural_dsp' },

      { header: 'Synth & Keys' },
      { name: 'Synth (Serum/Vital)', desc: 'Soft synth parameters', format: 'synth_serum' },
      { name: 'Analog .adv', desc: 'Ableton Analog preset', format: 'ableton_analog' },

      { header: 'DAW Integration' },
      { name: 'MIDI (All)', desc: 'Combined MIDI from all stems', format: 'midi', stem: 'all' },
      { name: 'Live Set .als', desc: 'Ableton project with MIDI tracks per stem', format: 'ableton_live_set' },
      { name: 'Project Bundle', desc: 'ZIP with all MIDI stems + analysis', format: 'project_bundle' },
    ];

    const table = document.createElement('table');
    table.className = 'export-table';

    const tbody = document.createElement('tbody');

    for (const row of rows) {
      const tr = document.createElement('tr');
      tr.className = row.header ? 'export-row export-row--header' : 'export-row';

      if (row.header) {
        const td = document.createElement('td');
        td.className = 'export-cell export-cell--category';
        td.colSpan = 3;
        td.textContent = row.header;
        tr.appendChild(td);
      } else {
        const tdName = document.createElement('td');
        tdName.className = 'export-cell export-cell--name';
        tdName.textContent = row.name;

        const tdDesc = document.createElement('td');
        tdDesc.className = 'export-cell export-cell--desc';
        tdDesc.textContent = row.desc;

        const tdAction = document.createElement('td');
        tdAction.className = 'export-cell export-cell--action';

        const btn = document.createElement('button');
        btn.className = 'btn-export';
        btn.textContent = 'Export';
        btn.addEventListener('click', () => {
          this.export(row.format, row.stem || '', onStatus);
        });

        tdAction.appendChild(btn);
        tr.appendChild(tdName);
        tr.appendChild(tdDesc);
        tr.appendChild(tdAction);
      }

      tbody.appendChild(tr);
    }

    table.appendChild(tbody);
    container.innerHTML = '';
    container.appendChild(table);

    // Add per-stem MIDI rows if available
    if (this.currentResult?.midi_stems) {
      this.addMidiStemRows(tbody, onStatus);
    }
  },

  /**
   * Add MIDI rows for each stem
   */
  addMidiStemRows(tbody, onStatus) {
    if (!this.currentResult?.midi_stems) return;

    // Find the "MIDI (All)" row and insert stem rows after it
    const rows = tbody.querySelectorAll('.export-row');
    let midiAllRow = null;

    for (const row of rows) {
      const nameCell = row.querySelector('.export-cell--name');
      if (nameCell && nameCell.textContent === 'MIDI (All)') {
        midiAllRow = row;
        break;
      }
    }

    if (!midiAllRow) return;

    for (const [stemName, midiData] of Object.entries(this.currentResult.midi_stems)) {
      if (!midiData?.content || midiData.note_count === 0) continue;

      const tr = document.createElement('tr');
      tr.className = 'export-row export-row--midi-stem';

      const tdName = document.createElement('td');
      tdName.className = 'export-cell export-cell--name';
      tdName.textContent = `↳ ${stemName}`;

      const tdDesc = document.createElement('td');
      tdDesc.className = 'export-cell export-cell--desc';
      tdDesc.textContent = `${midiData.note_count} notes extracted`;

      const tdAction = document.createElement('td');
      tdAction.className = 'export-cell export-cell--action';

      const btn = document.createElement('button');
      btn.className = 'btn-export';
      btn.textContent = 'Export';
      btn.addEventListener('click', () => {
        this.export('midi', stemName, onStatus);
      });

      tdAction.appendChild(btn);
      tr.appendChild(tdName);
      tr.appendChild(tdDesc);
      tr.appendChild(tdAction);

      midiAllRow.parentNode.insertBefore(tr, midiAllRow.nextSibling);
      midiAllRow = tr; // Insert next one after this
    }
  }
};

// Export for module usage if needed
if (typeof module !== 'undefined' && module.exports) {
  module.exports = ToneExport;
}
