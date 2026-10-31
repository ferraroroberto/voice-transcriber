/* Pure label helpers — no DOM, no imports, so Vitest can load this file
 * under bare Node. config.js renders the labels into the Settings selects.
 */

'use strict';

export function polishModelLabel(id) {
  // Derive a friendly label from the hub alias by title-casing the
  // segments (claude_haiku → "Claude Haiku"). Keeping this rule
  // synthesis-only means adding a new model is a single edit in
  // config/webapp_config.sample.json with no code change here.
  return String(id || '')
    .split('_')
    .filter(Boolean)
    .map(s => s.charAt(0).toUpperCase() + s.slice(1))
    .join(' ');
}
