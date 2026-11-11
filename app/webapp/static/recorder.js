/* Recording lifecycle — mic capture, chunked upload, background-finalise
 * / resume. The states and their legal moves are declared in take.js
 * (idle → starting → recording → finishing → idle); this module drives them.
 *
 * The VU meter + VAD auto-stop live in level.js, the rolling-transcription
 * SSE stream lives in partials.js, and the screen wake lock lives in
 * wakelock.js — this module owns the moves between states and the one
 * teardown (endCapture) that every way out of `recording` goes through.
 */

'use strict';

import { icon } from './_vendored/icons/icons.js';
import { els, state } from './state.js';
import { authFetch } from './api.js';
import {
  formatBytes, isOn, renderTextCard, renderTranscript, showToast, tryAutoCopy, truncate,
} from './ui.js';
import { refreshHistory } from './history.js';
import { setupLevelMeter, teardownLevelMeter } from './level.js';
import { createLevelSource } from './_vendored/rec-wave/rec-wave.js';
import { attachMicNotice } from './_vendored/mic-notice/mic-notice.js';
import { openPartialStream, closePartialStream } from './partials.js';
import { acquireWakeLock, releaseWakeLock } from './wakelock.js';
import { beginTake, endTake, isTake, transition } from './take.js';

// The take's mic notice (input name, level strip, silent-mic warning) is the
// vendored mic-notice; this module only owns when it starts and ends.
let levelSource = null;
let stopNotice = null;

// Built inside the tap: iOS resumes an AudioContext only within a gesture, and
// the stream arrives later, after the permission prompt.
function primeLevelSource() {
  clearTakeLevel();
  levelSource = createLevelSource();
}

function watchTakeLevel(stream) {
  stopNotice = attachMicNotice(els.micNotice, stream, {
    source: levelSource || undefined,
    timer: false,
    onSilent: () => console.info('[recorder] no sound from the input after the first seconds'),
  });
}

// Safe to call at any point, twice, or with no take running.
function clearTakeLevel() {
  if (stopNotice) { stopNotice(); stopNotice = null; }
  if (levelSource) { levelSource.close(); levelSource = null; }
}

// Record-button label states, sentence case: the mic glyph over
// "Record" at rest (matches the idle markup in index.html), a stop square
// over "Stop" while the mic is live, a spinner while the take finishes.
const LABEL_RECORD = icon('mic') + 'Record';
const LABEL_STOP = '<span class="rec-stop" aria-hidden="true"></span>Stop';
const LABEL_BUSY = '<span class="rec-spinner" aria-hidden="true"></span>';

// Append is active when the Append toggle is on, or when this take was
// started via the Resume button (state.forceAppend) — Resume
// continues the transcript no matter how the toggle is set.
export function appendActive() {
  return isOn(els.appendToggle) || state.forceAppend;
}

// When append is active, glue the new take onto the existing
// transcript with a blank-line separator. Otherwise replace.
export function mergeForAppend(prev, next) {
  if (!appendActive()) return next;
  const prevTrimmed = (prev || '').replace(/\s+$/, '');
  if (!prevTrimmed) return next;
  if (!next) return prevTrimmed;
  return prevTrimmed + '\n\n' + next;
}

export function releaseCachedStream() {
  if (state.stream) {
    try { state.stream.getTracks().forEach(t => t.stop()); } catch (_) {}
  }
  state.stream = null;
  state.streamKey = '';
}

// ----------------------------------------------------- mic enumeration

export async function populateMics() {
  els.micSelect.innerHTML = '';
  const sysOpt = document.createElement('option');
  sysOpt.value = '';
  sysOpt.textContent = 'System default';
  els.micSelect.appendChild(sysOpt);

  if (!('mediaDevices' in navigator) || !navigator.mediaDevices.enumerateDevices) {
    return;
  }
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const inputs = devices.filter(d => d.kind === 'audioinput');
    for (const d of inputs) {
      const opt = document.createElement('option');
      opt.value = d.deviceId;
      opt.textContent = d.label || `Mic ${opt.value.slice(0, 6)}`;
      if (state.config && state.config.preferred_mic_id === d.deviceId) {
        opt.selected = true;
      }
      els.micSelect.appendChild(opt);
    }
  } catch (err) { /* iOS sometimes refuses pre-grant — fine */ }
}

// ----------------------------------------------------- record flow

export async function onRecordToggle() {
  if (isTake('idle')) return startRecording();
  if (isTake('recording')) return stopRecording('stop');
}

// A start that failed for a reason worth telling the user; the message is the
// toast. Anything else thrown during start gets the generic one.
class StartError extends Error {}

async function startRecording({ forceAppend = false } = {}) {
  if (!transition('starting', 'record tapped')) return;
  beginTake({ forceAppend });
  finishStarted = false;
  finishNote = '';
  hideResumeButton();
  try {
    await openTake();
  } catch (err) {
    failTake(err);
  }
}

// Everything between the tap and the mic being live. Any throw lands in
// failTake, so no step has to reset the mode by hand.
async function openTake() {
  // Built inside the tap: iOS resumes an AudioContext only within a gesture.
  primeLevelSource();

  const stream = await acquireStream();

  // Re-enumerate now that labels may be visible (iOS reveals after grant).
  populateMics();

  // If we left an incognito session lingering from a previous take,
  // clean it up before starting a new one so disk stays tidy.
  await cleanupIncognitoSession();

  const incognito = isOn(els.incognitoToggle);
  const session = await createSession(incognito);
  if (incognito) state.incognitoSessionId = session.session_id;

  state.partialBaseTranscript =
    (appendActive() && state.transcript)
      ? state.transcript.replace(/\s+$/, '')
      : '';
  armRecorder(stream);
  state.sessionId = session.session_id;

  state.startedAt = Date.now();
  transition('recording', 'mic live');
  // Live partials land in the transcript, so show it.
  state.textView = 'transcript';
  renderTextCard();

  setupLevelMeter(stream, () => stopRecording('silence'));
  watchTakeLevel(stream);
  acquireWakeLock();
  startTimer();
  openPartialStream(state.sessionId);

  els.recordLabel.innerHTML = LABEL_STOP;
  els.recordTimer.hidden = false;
  els.recordTimer.textContent = '00:00';
  els.recordStatus.textContent = 'Recording…';
  els.recordBtn.setAttribute('aria-pressed', 'true');
}

// A throw anywhere in openTake. Before the mic went live the take is dropped
// and the button is usable again; once recording the recorder is running, so
// the take finishes the normal way and what was streamed is kept.
function failTake(err) {
  console.error('[recorder] starting the take failed:', err);
  if (isTake('recording')) {
    showToast('Recording hit a problem — finishing the take', 'error');
    stopRecording('setup failed');
    return;
  }
  endCapture();
  disarmRecorder();
  releaseCachedStream();
  els.recordStatus.textContent = '';
  showToast(err instanceof StartError ? err.message : 'Could not start recording', 'error');
  transition('idle', 'start failed');
}

// The live mic stream for the current constraints: the cached grant when it
// still matches, otherwise a fresh getUserMedia. Throws a StartError when the
// user denies the permission.
async function acquireStream() {
  const constraints = buildAudioConstraints();
  const wantedKey = JSON.stringify(constraints);
  let stream = state.stream;

  if (stream && state.streamKey === wantedKey && stream.getAudioTracks().every(t => t.readyState === 'live')) {
    // Reuse the existing grant — no permission prompt.
    els.recordStatus.textContent = 'Reusing mic…';
    return stream;
  }
  releaseCachedStream();
  els.recordStatus.textContent = 'Requesting mic…';
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: constraints,
      video: false,
    });
  } catch (err) {
    console.warn('mic permission denied:', err);
    throw new StartError('Mic permission denied');
  }
  state.stream = stream;
  state.streamKey = wantedKey;
  return stream;
}

// POST the server-side session for this take. Throws a StartError when the
// request fails.
async function createSession(incognito) {
  try {
    const sessionRes = await authFetch('/api/sessions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        language: els.languageSelect.value,
        incognito,
        source: 'webapp',
      }),
    });
    if (!sessionRes.ok) throw new Error(`HTTP ${sessionRes.status}`);
    return await sessionRes.json();
  } catch (err) {
    console.warn('creating the session failed:', err);
    throw new StartError('Could not create session');
  }
}

function armRecorder(stream) {
  const mimeType = pickMimeType();
  state.mimeType = mimeType;
  state.recorder = new MediaRecorder(stream, mimeType ? {mimeType} : undefined);
  state.stream = stream;

  // Stream every chunk to disk on the PC the moment it arrives — if the
  // phone dies mid-record, the partial recording is still recoverable.
  state.recorder.ondataavailable = e => {
    if (!e.data || e.data.size === 0) return;
    enqueueChunkUpload(e.data);
  };
  state.recorder.onstop = () => onRecorderStopped(mimeType);
  // The recorder can end a take on its own (unsupported state, encoder
  // failure) and so can the mic (unplugged, permission revoked); both take
  // the same way out as a tap on Stop.
  state.recorder.onerror = e => {
    console.error('[recorder] MediaRecorder error', e && e.error);
    stopRecording('recorder-error');
  };
  watchMicEnd(stream);
  state.recorder.start(1000); // 1 s chunk cadence — survives connection drops
}

// Drop the recorder's handlers and the reference so a stale recorder can't
// finish a later take.
function disarmRecorder() {
  const rec = state.recorder;
  state.recorder = null;
  if (!rec) return;
  rec.ondataavailable = null;
  rec.onstop = null;
  rec.onerror = null;
  if (rec.state !== 'inactive') {
    try { rec.stop(); } catch (_) {}
  }
}

// The cached stream is reused across takes, so the listener lives only as
// long as this take (removed in endCapture).
let stopWatchingMic = null;

function watchMicEnd(stream) {
  const onEnded = () => stopRecording('mic-lost');
  const tracks = stream.getAudioTracks();
  tracks.forEach(t => t.addEventListener('ended', onEnded));
  stopWatchingMic = () => tracks.forEach(t => t.removeEventListener('ended', onEnded));
}

// Everything a take owes on the way out of `recording`, whatever ended it:
// a tap, silence, backgrounding, the recorder or the mic. Safe to call twice.
// `state.stream` stays cached so the next record reuses the grant — the iOS
// mic indicator lingers but there is no re-prompt; it is released on
// visibilitychange/pagehide or when the mic selection changes.
function endCapture() {
  stopTimer();
  teardownLevelMeter();
  clearTakeLevel();
  releaseWakeLock();
  // The /finish call will broadcast a `final` SSE event then close the
  // worker; closing here too frees the connection a fraction earlier.
  closePartialStream();
  if (stopWatchingMic) { stopWatchingMic(); stopWatchingMic = null; }
}

// Why a take ended without the user asking, shown in the status line and a
// toast so it never looks like a normal stop.
const SELF_STOP_NOTES = {
  'recorder-stopped': 'The recorder stopped',
  'recorder-error': 'The recorder hit an error',
  'mic-lost': 'The mic was disconnected',
};

// The reason the current take finished, when it was not a normal stop.
let finishNote = '';

// onRecorderStopped has begun for this take; its second call is a no-op.
let finishStarted = false;

function withNote(text) {
  return finishNote ? `${finishNote} — ${text}` : text;
}

// recording → finishing: the button goes busy and the capture is torn down.
// False when the take was not recording (already finishing, or never live).
function beginFinish(reason) {
  if (!transition('finishing', reason)) return false;
  finishNote = SELF_STOP_NOTES[reason] || '';
  if (finishNote) showToast(finishNote + ' — finishing the take', 'error');
  els.recordStatus.textContent = withNote('Uploading…');
  els.recordLabel.innerHTML = LABEL_BUSY;
  els.recordBtn.disabled = true;
  endCapture();
  return true;
}

function stopRecording(reason = 'stop') {
  if (!state.recorder || !beginFinish(reason)) return;
  if (state.recorder.state === 'inactive') {
    // Already stopped (an errored recorder): its stop event may be past.
    onRecorderStopped(state.mimeType);
    return;
  }
  try { state.recorder.stop(); } catch (err) { onRecorderStopped(state.mimeType); }
}

// Mobile browsers can't keep a web page recording in the background.
// iOS suspends the page and revokes the mic the moment you switch apps
// or lock the screen; there is no web API to capture audio in the
// background. (Android Chrome can keep a mic-capturing tab alive, but
// relying on that would make the two platforms behave differently.)
// So when the page is backgrounded mid-record we finalise the take
// now — the audio streamed so far is transcribed and saved instead of
// silently lost. With Append on, the next take continues the same
// transcript when the user returns.
export function finalizeForBackground() {
  if (!isTake('recording')) return;
  state.backgroundFinalized = true;
  stopRecording('background');
}

// "Continue this text" — shown only after a take was finalised by
// backgrounding, beside a smaller Record. It starts a fresh take that
// force-appends onto the existing transcript regardless of the
// Append toggle, so the seam across the app-switch is invisible.
export function resumeRecording() {
  if (!isTake('idle')) return;
  startRecording({ forceAppend: true });
}

function showResumeButton() {
  if (els.resumeBtn) els.resumeBtn.hidden = false;
}

export function hideResumeButton() {
  if (els.resumeBtn) els.resumeBtn.hidden = true;
}

// Delays (ms) between retry attempts on a failed /chunk POST. A dropped
// chunk isn't just missing audio — if it's the *first* chunk, the WebM
// container loses its EBML/Segment/Tracks header and the whole take
// becomes unrecoverable by ffmpeg. Retrying
// in-place preserves upload order since chunks are chained.
const CHUNK_RETRY_DELAYS_MS = [300, 800, 1500];

async function uploadChunkOnce(chunk) {
  const r = await authFetch(`/api/sessions/${state.sessionId}/chunk`, {
    method: 'POST',
    headers: { 'Content-Type': chunk.type || state.mimeType || 'audio/webm' },
    body: chunk,
  });
  if (!r.ok) {
    throw new Error(`HTTP ${r.status}: ${await r.text().catch(() => '')}`);
  }
}

function enqueueChunkUpload(chunk) {
  state.pendingUploads += 1;
  state.uploadChain = state.uploadChain.then(async () => {
    try {
      let lastErr = null;
      for (let attempt = 0; attempt <= CHUNK_RETRY_DELAYS_MS.length; attempt += 1) {
        try {
          await uploadChunkOnce(chunk);
          state.bytesSent += chunk.size;
          lastErr = null;
          break;
        } catch (err) {
          lastErr = err;
          const delay = CHUNK_RETRY_DELAYS_MS[attempt];
          if (delay !== undefined) {
            console.warn(`chunk upload failed, retrying in ${delay}ms`, err);
            await new Promise(res => setTimeout(res, delay));
          }
        }
      }
      if (lastErr) {
        // Every retry exhausted — this chunk is permanently lost. Flag
        // the take so the user knows the transcript may be corrupt
        // instead of silently discarding it.
        state.hadDroppedChunk = true;
        console.error('chunk upload permanently failed after retries', lastErr);
        showToast('Recording upload glitch — audio may be incomplete', 'error');
      }
    } finally {
      state.pendingUploads -= 1;
    }
  });
}

// The recorder's stop event for this take; runs once however the take ended.
async function onRecorderStopped(mimeType) {
  if (finishStarted) return;
  finishStarted = true;
  // A recorder that stopped by itself arrives here still `recording`.
  if (isTake('recording')) beginFinish('recorder-stopped');
  // Captured before the finally clears the flag — drives whether the
  // Resume button is offered once this take settles.
  const wasBackgrounded = state.backgroundFinalized;
  try {
    // Wait for any in-flight chunks to land before asking the server
    // to transcode + transcribe.
    const flushStatus = setInterval(() => {
      if (state.pendingUploads > 0) {
        els.recordStatus.textContent = withNote(
          `Finalising upload · ${state.pendingUploads} chunk${state.pendingUploads === 1 ? '' : 's'} left`);
      }
    }, 200);
    els.recordStatus.textContent = withNote('Finalising upload…');
    await state.uploadChain;
    clearInterval(flushStatus);

    const elapsedSec = Math.max(0, (Date.now() - state.startedAt) / 1000);
    els.recordStatus.textContent =
      `Server: ffmpeg, then whisper · ${formatDuration(elapsedSec)} of audio…`;
    const t0 = Date.now();
    const translate = isOn(els.translateToggle);
    const finishUrl =
      `/api/sessions/${state.sessionId}/finish` +
      `?language=${encodeURIComponent(els.languageSelect.value)}` +
      `&translate=${translate ? 'true' : 'false'}`;
    if (translate) {
      els.recordStatus.textContent =
        `Server: ffmpeg, then translate (cold-start ~5 s on first call)…`;
    }
    const r = await authFetch(finishUrl, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ duration_seconds: elapsedSec }),
      // When the take is being finalised because the app was backgrounded,
      // keepalive lets the request outlive an iOS page freeze/discard.
      keepalive: state.backgroundFinalized,
    });
    if (!r.ok) {
      const text = await r.text();
      throw new Error(text || `${r.status}`);
    }
    const data = await r.json();
    const serverMs = Date.now() - t0;
    if (data.silent) {
      // Recording was below the silence threshold — whisper was skipped
      // so it can't hallucinate. Don't touch the transcript box; the
      // user may still have accumulated text from earlier takes.
      els.recordStatus.textContent =
        `Empty audio (${data.dbfs} dBFS) — skipped`;
      showToast('Empty audio — nothing transcribed');
      refreshHistory();
      return;
    }
    // Merge against the pre-take base, not state.transcript (which
    // already includes any partial that arrived via SSE — using it
    // would double-append the take onto the base prefix). The take
    // already lives on disk, so renderTranscript's saveTranscript
    // rule is correct here too (saving again would duplicate it).
    renderTranscript(
      mergeForAppend(state.partialBaseTranscript, data.transcript || ''),
      { at: new Date(), seconds: elapsedSec },
    );
    // Auto-copy reads from the textarea so what lands on the clipboard
    // is exactly what's on screen — including the merged accumulator
    // when Append is on.
    const transcriptForCopy = els.transcript.value;
    if (transcriptForCopy) await tryAutoCopy(transcriptForCopy, els.copyText);
    const speed = elapsedSec > 0 ? (elapsedSec / (serverMs / 1000)).toFixed(1) : '?';
    els.recordStatus.textContent = state.backgroundFinalized
      ? 'Saved while you were away — Continue adds to the same text'
      : withNote(`Done in ${(serverMs / 1000).toFixed(1)} s · ${speed}× realtime — tap Copy or Polish`);
    refreshHistory();
  } catch (err) {
    console.error(err);
    els.recordStatus.textContent = 'Failed — recording is still on the PC, see History';
    showToast('Transcribe failed: ' + truncate(err.message || String(err), 120), 'error');
    refreshHistory();
  } finally {
    els.recordBtn.disabled = false;
    els.recordLabel.innerHTML = LABEL_RECORD;
    els.recordTimer.hidden = true;
    els.recordBtn.setAttribute('aria-pressed', 'false');
    state.recorder = null;
    finishNote = '';
    transition('idle', 'take settled');
    renderTextCard();
    endTake();
    // Offer the one-tap continue only when this take ended because the
    // app was backgrounded and there is a transcript to continue.
    if (wasBackgrounded && state.transcript) showResumeButton();
  }
}

function buildAudioConstraints() {
  const wantBuiltin = isOn(els.forceBuiltinMic);
  const deviceId = els.micSelect.value;
  if (deviceId) return { deviceId: { exact: deviceId } };
  if (wantBuiltin) {
    // best-effort: if labels are visible, prefer one whose label hints "built-in"
    const opts = Array.from(els.micSelect.options).filter(o => o.value);
    const hit = opts.find(o => /built[- ]?in|iphone microphone|internal/i.test(o.textContent));
    if (hit) return { deviceId: { exact: hit.value } };
  }
  return true;
}

function pickMimeType() {
  if (!('MediaRecorder' in window)) return null;
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/mp4;codecs=mp4a.40.2', // iOS Safari often only supports this
    'audio/mp4',
  ];
  for (const m of candidates) {
    if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m)) return m;
  }
  return null;
}

// ----------------------------------------------------- timer

function startTimer() {
  state.timer = setInterval(() => {
    const elapsedMs = Date.now() - state.startedAt;
    const elapsed = Math.floor(elapsedMs / 1000);
    const mm = String(Math.floor(elapsed / 60)).padStart(2, '0');
    const ss = String(elapsed % 60).padStart(2, '0');
    els.recordTimer.textContent = `${mm}:${ss}`;
    // Don't trample the VAD status line during its update window —
    // peak / silence readouts would flicker every 250 ms otherwise.
    if (Date.now() >= state.vadStatusOwnedUntil) {
      els.recordStatus.textContent =
        `Recording · ${formatBytes(state.bytesSent)} streamed to PC`;
    }
  }, 250);
}

function formatDuration(sec) {
  if (sec < 60) return `${sec.toFixed(1)} s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec - m * 60);
  return `${m}m ${s}s`;
}

function stopTimer() {
  if (state.timer) clearInterval(state.timer);
  state.timer = null;
}

export async function cleanupIncognitoSession() {
  const id = state.incognitoSessionId;
  if (!id) return;
  state.incognitoSessionId = null;
  try {
    await authFetch(`/api/sessions/${id}`, { method: 'DELETE' });
  } catch (_) {
    // best-effort — server-side retention will reap it eventually
  }
}
