/* Pure VAD helpers — no DOM, no imports, so tests can load this file
 * under bare Node. level.js owns the live detector and calls these.
 */

'use strict';

export const DEFAULT_SILENCE_MS = 1500;

// How long the mic must stay quiet before auto-stop fires. The stored
// `auto_stop_silence_ms` (kept in sync by config.js when the Settings ›
// Sound › Silence length field changes) is the source of truth; the
// field itself is in seconds, so don't read it here.
export function silenceTriggerMs(config) {
  const ms = Number(config && config.auto_stop_silence_ms);
  return Number.isFinite(ms) && ms > 0 ? ms : DEFAULT_SILENCE_MS;
}
