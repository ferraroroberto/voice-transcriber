/* The browser take lifecycle — the one place that says which states a take
 * can be in and which moves between them are legal.
 *
 *   idle ──► starting ──► recording ──► finishing ──► idle
 *               └────────────────────────────────────► idle (start failed)
 *
 * Imports nothing but state.js so every reader can use it without a cycle.
 * `state.mode` is written here and nowhere else: recorder.js drives the
 * moves through `transition()`, every other module asks `isTake()`.
 * docs/take-lifecycle.md has the table and the readers.
 */

'use strict';

import { state } from './state.js';

// The closed set. Adding a state means adding it here, to TRANSITIONS, and
// handling it in every reader.
export const TAKE_STATES = Object.freeze({
  idle: 'no take in flight; Record starts one',
  starting: 'Record tapped; the mic grant and the server session are being set up',
  recording: 'the mic is live and chunks stream to the PC',
  finishing: 'capture is over; chunks flush, then the server runs ffmpeg and whisper',
});

const TRANSITIONS = Object.freeze({
  idle: ['starting'],
  starting: ['recording', 'idle'],
  recording: ['finishing'],
  finishing: ['idle'],
});

// True when the take is in `name`. An unknown name is a bug at the call site,
// so it throws rather than quietly answering "no".
export function isTake(name) {
  if (!(name in TAKE_STATES)) throw new Error(`unknown take state: ${name}`);
  return state.mode === name;
}

// Move to `next`, leaving an info breadcrumb. An illegal move is refused with
// a console error and changes nothing; the caller gets false.
export function transition(next, reason) {
  const from = state.mode;
  if (!(next in TAKE_STATES) || !(TRANSITIONS[from] || []).includes(next)) {
    console.error(`[take] refused ${from} -> ${next} (${reason || 'no reason'})`);
    return false;
  }
  state.mode = next;
  console.info(`[take] ${from} -> ${next} (${reason || 'no reason'})`);
  return true;
}

// The per-take facts, reset together as a take starts. `forceAppend` is the
// Resume button's way of continuing the transcript whatever the Append
// toggle says; it is an input of the take, never carried over from the last.
export function beginTake({ forceAppend = false } = {}) {
  state.forceAppend = forceAppend;
  state.backgroundFinalized = false;
  state.vadStopFired = false;
  state.vadSilenceSince = 0;
  state.hadDroppedChunk = false;
  state.partialVersion = 0;
  state.pendingUploads = 0;
  state.bytesSent = 0;
  state.uploadChain = Promise.resolve();
}

// The flags that only mean something while a take is in flight, cleared
// once it has settled.
export function endTake() {
  state.backgroundFinalized = false;
  state.forceAppend = false;
}
