/* Live mic level — VU meter bar plus VAD (voice-activity-detection)
 * auto-stop. Both read the same AnalyserNode tap on the recording
 * stream, so they share one setup/teardown pair.
 *
 * Auto-stop doesn't call back into the recording state machine
 * directly — the caller passes an `onAutoStop` callback (recorder.js
 * hands it `stopRecording`) so this module has no dependency on
 * recorder.js and can't create an import cycle.
 */

'use strict';

import { els, state } from './state.js';
import { formatBytes, isOn } from './ui.js';

export function setupLevelMeter(stream, onAutoStop) {
  try {
    state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const src = state.audioCtx.createMediaStreamSource(stream);
    const analyser = state.audioCtx.createAnalyser();
    analyser.fftSize = 512;
    src.connect(analyser);
    state.analyser = analyser;
    const data = new Uint8Array(analyser.frequencyBinCount);
    // VAD threshold: byte-time-domain values are 0..255 centred on
    // 128, so |sample-128| is the analyser's peak deviation per
    // ~10 ms frame. Threshold tuning notes:
    //   ~3-8  → typical quiet-room floor with mic AGC engaged.
    //          (Bug found 2026-05-13: 6 was too tight, silence
    //          accumulator never advanced because room noise kept
    //          tripping it.)
    //   ~15   → "barely audible" — fits speech pauses and tail
    //          silence; matches roughly 23% on the level bar.
    //   ~25+  → "actually quiet" — risk of late triggering when
    //          a quiet mumble follows a strong sentence.
    // 15 is a deliberate compromise; expose as a config knob later
    // if it needs per-mic tuning.
    const VAD_LOUDNESS_THRESHOLD = 15;
    state.levelTimer = setInterval(() => {
      analyser.getByteTimeDomainData(data);
      let max = 0;
      for (let i = 0; i < data.length; i++) {
        const v = Math.abs(data[i] - 128);
        if (v > max) max = v;
      }
      const pct = Math.min(100, (max / 128) * 200);
      els.levelFill.style.width = pct + '%';
      maybeFireAutoStop(max, VAD_LOUDNESS_THRESHOLD, onAutoStop);
    }, 80);
  } catch (err) {
    console.warn('VU meter setup failed', err);
  }
}

export function teardownLevelMeter() {
  if (state.levelTimer) clearInterval(state.levelTimer);
  state.levelTimer = null;
  if (state.audioCtx) {
    try { state.audioCtx.close(); } catch (err) {}
    state.audioCtx = null;
  }
}

function maybeFireAutoStop(loudness, threshold, onAutoStop) {
  // Pillar 3 — client-side VAD auto-stop. The toggle in the Settings
  // panel is the live source of truth (like the existing Translate /
  // Append / Incognito toggles): flip it and the next take honours
  // it without tapping Save. Save persists the choice as the default
  // for fresh page loads.
  const enabled = isOn(els.vadAutoStopToggle);
  if (!enabled) return;
  if (state.mode !== 'recording' || state.vadStopFired) return;
  const now = Date.now();
  // Ignore the first ~600 ms — the AnalyserNode warms up and a fresh
  // mic stream can flat-line briefly before audio reaches it.
  if (now - state.startedAt < 600) return;
  const trigger = parseInt(
    (els.autoStopSilenceMs && els.autoStopSilenceMs.value) || '1500', 10,
  ) || 1500;
  if (loudness > threshold) {
    if (state.vadSilenceSince) state.vadSilenceSince = 0;
    // Live peak readout — once a second so the user can see the mic
    // floor and pick a sensible threshold. Held for 250 ms so the
    // recording-byte-counter writer doesn't immediately stamp on it.
    if (now - state.vadStatusOwnedUntil > 800) {
      els.recordStatus.textContent =
        `VAD peak=${loudness} (silence trips ≤ ${threshold}) · ${formatBytes(state.bytesSent)}`;
      state.vadStatusOwnedUntil = now + 250;
    }
    return;
  }
  if (!state.vadSilenceSince) {
    state.vadSilenceSince = now;
    return;
  }
  const silentFor = now - state.vadSilenceSince;
  // Live indicator so the user can see the detector working — hold
  // status-line ownership for 250 ms so the byte-counter writer
  // doesn't immediately overwrite it.
  els.recordStatus.textContent =
    `Silence ${silentFor} ms / ${trigger} ms`;
  state.vadStatusOwnedUntil = now + 250;
  if (silentFor >= trigger) {
    state.vadStopFired = true;
    els.recordStatus.textContent =
      'Auto-stop on silence — keep talking to cancel…';
    // 500 ms grace before actually stopping; if the user resumes
    // talking the next tick clears state.vadStopFired's effect.
    setTimeout(() => {
      if (!state.vadStopFired || state.mode !== 'recording') return;
      // One last loudness probe — if the user resumed talking during
      // the grace window, abort the stop.
      if (state.analyser) {
        const probe = new Uint8Array(state.analyser.frequencyBinCount);
        state.analyser.getByteTimeDomainData(probe);
        let pmax = 0;
        for (let i = 0; i < probe.length; i++) {
          const v = Math.abs(probe[i] - 128);
          if (v > pmax) pmax = v;
        }
        if (pmax > threshold) {
          state.vadStopFired = false;
          state.vadSilenceSince = 0;
          els.recordStatus.textContent = 'Recording…';
          return;
        }
      }
      onAutoStop();
    }, 500);
  }
}
