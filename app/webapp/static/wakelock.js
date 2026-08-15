/* Screen wake lock — held for the duration of a recording.
 *
 * iOS auto-locks the screen during long records, which backgrounds the
 * page and revokes the mic. Hold a screen wake lock for the duration of
 * the take. The platform auto-releases the sentinel whenever the page is
 * hidden (and on low battery), so app.js re-acquires on
 * visibilitychange→visible while still recording; the `release` listener
 * clears our handle so that re-acquire isn't blocked by a stale sentinel.
 */

'use strict';

import { state } from './state.js';

export async function acquireWakeLock() {
  if (!('wakeLock' in navigator) || state.wakeLock) return;
  try {
    const sentinel = await navigator.wakeLock.request('screen');
    sentinel.addEventListener('release', () => { state.wakeLock = null; });
    state.wakeLock = sentinel;
  } catch (_) {
    state.wakeLock = null;
  }
}

export function releaseWakeLock() {
  if (!state.wakeLock) return;
  try { state.wakeLock.release(); } catch (_) {}
  state.wakeLock = null;
}
