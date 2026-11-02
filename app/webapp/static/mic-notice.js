/* The recorder's mic notice — the input's name ("Mic: AirPods"), the live
 * level strip and, when the input carries no sound, a warning under them.
 *
 * The strip and the silent verdict both come from the vendored rec-wave, which
 * reads the stream the recorder already holds: no second getUserMedia and no
 * new permission prompt. This module only names the input and words the
 * warning. recorder.js calls it at the three points a take can start or end.
 */

'use strict';

import { els } from './state.js';
import { attachRecWave, createLevelSource } from './_vendored/rec-wave/rec-wave.js';

// How long the take may carry no sound before it is flagged. iOS routes the
// mic to a Bluetooth headset that can carry nothing, and the strip stays flat.
export const SILENT_MS = 3000;

const SILENT_TEXT = 'No sound from the microphone — check Bluetooth/AirPods';
// A Remote Desktop session names its mic "Remote Audio", and Bluetooth is not
// what is wrong there.
const SILENT_TEXT_REMOTE = 'No sound from the microphone — check Remote Desktop';

let levelSource = null;
let stopWave = null;

// The name the browser gives the live input ("AirPods", "iPhone Microphone"),
// or '' when it withholds the label.
export function inputLabel(stream) {
  try {
    const tracks = stream && stream.getAudioTracks ? stream.getAudioTracks() : [];
    return (tracks[0] && tracks[0].label) || '';
  } catch (_) {
    return '';
  }
}

export function silentText(label) {
  return /^remote audio\b/i.test((label || '').trim()) ? SILENT_TEXT_REMOTE : SILENT_TEXT;
}

// Open the level reader inside the tap: iOS resumes an AudioContext only
// within a user gesture, and the stream arrives later, after the permission
// prompt.
export function primeLevelSource() {
  clearTakeLevel();
  levelSource = createLevelSource();
}

function setSilent(silent, label) {
  els.micSilent.textContent = silent ? silentText(label) : '';
  els.micSilent.hidden = !silent;
}

// Show the input name and the strip for a take and flag a dead route within
// seconds rather than at finish.
export function watchTakeLevel(stream) {
  const label = inputLabel(stream);
  els.micInput.textContent = label ? 'Mic: ' + label : '';
  els.micInput.hidden = !label;
  setSilent(false, label);
  // The strip runs for the whole take, so the notice shows even when the
  // browser withholds the input's name.
  els.micNotice.hidden = false;
  stopWave = attachRecWave(els.micWave, stream, {
    source: levelSource || undefined,
    silenceMs: SILENT_MS,
    height: 20,
    timer: false,
    onSilent: function () {
      console.info('[recorder] no sound from the input after the first seconds');
      setSilent(true, label);
    },
    onSignal: function () { setSilent(false, label); },
  });
}

// Safe to call at any point, twice, or with no take running.
export function clearTakeLevel() {
  if (stopWave) { stopWave(); stopWave = null; }
  if (levelSource) { levelSource.close(); levelSource = null; }
  els.micNotice.hidden = true;
  els.micInput.textContent = '';
  setSilent(false, '');
}
