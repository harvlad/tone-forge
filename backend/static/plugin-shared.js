/**
 * Shared plugin matching utility for ToneForge.
 * Used by both main app and Studio to display "You have this" badges.
 */

const TonePlugins = {
  localEngineUrl: 'http://127.0.0.1:7777',
  mappings: {},       // block_family -> plugin info
  matches: null,      // raw matches from API
  available: false,   // whether local engine is available

  /**
   * Set whether local engine is available
   */
  setAvailable(isAvailable) {
    this.available = isAvailable;
  },

  /**
   * Fetch plugin matches for a descriptor
   */
  async fetchMatches(descriptor) {
    if (!this.available || !descriptor) return;

    try {
      const resp = await fetch(`${this.localEngineUrl}/api/plugins/match`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(descriptor),
      });

      if (resp.ok) {
        const data = await resp.json();
        this.matches = data.matches;

        // Build mapping from block_family to plugin
        this.mappings = {};
        for (const slot of ['amp', 'cab', 'effects']) {
          if (this.matches?.[slot]) {
            for (const plugin of this.matches[slot]) {
              const family = plugin.block_mapping?.block_family;
              if (family && !this.mappings[family]) {
                this.mappings[family] = plugin;
              }
            }
          }
        }

        console.log('Plugin matches found:', this.matches);
      }
    } catch (e) {
      console.warn('Failed to fetch plugin matches:', e);
    }
  },

  /**
   * Get a matching plugin for a block family
   */
  getMatch(blockFamily) {
    if (!blockFamily || !this.mappings) return null;

    // Direct match
    if (this.mappings[blockFamily]) {
      return this.mappings[blockFamily];
    }

    // Partial match (e.g., marshall_jcm matches marshall_*)
    const familyBase = blockFamily.split('_')[0];
    for (const [key, plugin] of Object.entries(this.mappings)) {
      if (key.startsWith(familyBase)) {
        return plugin;
      }
    }

    return null;
  },

  /**
   * Create HTML badge for a matching plugin
   */
  createBadge(plugin) {
    if (!plugin) return '';
    const name = plugin.name || 'Local plugin';
    return `<span class="plugin-badge" title="You have ${name}">${name}</span>`;
  },

  /**
   * Get badge HTML for a block family (convenience method)
   */
  getBadgeFor(blockFamily) {
    const plugin = this.getMatch(blockFamily);
    return this.createBadge(plugin);
  }
};
