/* Recording lifecycle — mic capture, chunked upload, background-finalise
 * / resume. The record → upload → finish state machine: idle → recording
 * → uploading → transcribing → idle.
 *
 * The VU meter + VAD auto-stop live in level.js, the rolling-transcription
 * SSE stream lives in partials.js, and the screen wake lock lives in
 * wakelock.js — this module owns only the state-machine transitions and
 * wires those three in at the right points.
 */

'use strict';

import { els, state } from './state.js';
import { authFetch } from './api.js';
import { formatBytes, isOn, renderTranscript, showToast, tryAutoCopy, truncate } from './ui.js';
import { refreshHistory } from './history.js';
import { setupLevelMeter, teardownLevelMeter } from './level.js';
import { openPartialStream, closePartialStream } from './partials.js';
import { acquireWakeLock, releaseWakeLock } from './wakelock.js';

function setMode(m) { state.mode = m; }

// Record-button label states — CSS glyphs (no emoji / glyph-font
// characters, per the fleet design system). Matches the idle markup
// shipped in index.html.
const LABEL_RECORD =
  '<span class="rec-glyph rec-glyph-dot" aria-hidden="true"></span> RECORD';
const LABEL_STOP =
  '<span class="rec-glyph rec-glyph-square" aria-hidden="true"></span> STOP';
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
  if (state.mode === 'idle') return startRecording();
  if (state.mode === 'recording') return stopRecording();
}

async function startRecording() {
  setMode('starting');
  hideResumeButton();

  const constraints = buildAudioConstraints();
  const wantedKey = JSON.stringify(constraints);
  let stream = state.stream;

  if (stream && state.streamKey === wantedKey && stream.getAudioTracks().every(t => t.readyState === 'live')) {
    // Reuse the existing grant — no permission prompt.
    els.recordStatus.textContent = 'Reusing mic…';
  } else {
    releaseCachedStream();
    els.recordStatus.textContent = 'Requesting mic…';
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: constraints,
        video: false,
      });
    } catch (err) {
      setMode('idle');
      els.recordStatus.textContent = '';
      showToast('Mic permission denied', 'error');
      return;
    }
    state.stream = stream;
    state.streamKey = wantedKey;
  }

  // Re-enumerate now that labels may be visible (iOS reveals after grant).
  populateMics();

  // If we left an incognito session lingering from a previous take,
  // clean it up before starting a new one so disk stays tidy.
  await cleanupIncognitoSession();

  const incognito = isOn(els.incognitoToggle);
  const sessionRes = await authFetch('/api/sessions', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      language: els.languageSelect.value,
      incognito,
      source: 'webapp',
    }),
  });
  if (!sessionRes.ok) {
    stream.getTracks().forEach(t => t.stop());
    setMode('idle');
    showToast('Could not create session', 'error');
    return;
  }
  const session = await sessionRes.json();
  state.sessionId = session.session_id;
  if (incognito) state.incognitoSessionId = session.session_id;

  const mimeType = pickMimeType();
  state.mimeType = mimeType;
  state.uploadChain = Promise.resolve();
  state.pendingUploads = 0;
  state.bytesSent = 0;
  state.hadDroppedChunk = false;
  state.recorder = new MediaRecorder(stream, mimeType ? {mimeType} : undefined);
  state.stream = stream;

  // Stream every chunk to disk on the PC the moment it arrives — if the
  // phone dies mid-record, the partial recording is still recoverable.
  state.recorder.ondataavailable = e => {
    if (!e.data || e.data.size === 0) return;
    enqueueChunkUpload(e.data);
  };
  state.recorder.onstop = () => onRecorderStopped(mimeType);
  state.recorder.start(1000); // 1 s chunk cadence — survives connection drops

  state.startedAt = Date.now();
  setMode('recording');

  // Reset latency-collapse plumbing per-take.
  state.partialVersion = 0;
  state.partialBaseTranscript =
    (appendActive() && state.transcript)
      ? state.transcript.replace(/\s+$/, '')
      : '';
  state.vadSilenceSince = 0;
  state.vadStopFired = false;
  state.backgroundFinalized = false;

  setupLevelMeter(stream, stopRecording);
  acquireWakeLock();
  startTimer();
  openPartialStream(state.sessionId);

  els.recordLabel.innerHTML = LABEL_STOP;
  els.recordTimer.hidden = false;
  els.recordTimer.textContent = '00:00';
  els.recordStatus.textContent = 'Recording…';
  els.recordBtn.setAttribute('aria-pressed', 'true');
}

function stopRecording() {
  if (!state.recorder) return;
  setMode('uploading');
  els.recordStatus.textContent = 'Uploading…';
  els.recordLabel.innerHTML = LABEL_BUSY;
  els.recordBtn.disabled = true;
  state.recorder.stop();
  // Keep `state.stream` alive so the next record reuses the grant —
  // the iOS mic indicator will linger but no re-prompt. Released on
  // visibilitychange/pagehide or when the mic selection changes.
  stopTimer();
  teardownLevelMeter();
  releaseWakeLock();
  // The /finish call will broadcast a `final` SSE event then close
  // the worker; closing here too is harmless and frees the connection
  // a fraction earlier.
  closePartialStream();
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
  if (state.mode !== 'recording') return;
  state.backgroundFinalized = true;
  stopRecording();
}

// The yellow Resume button — shown only after a take was finalised
// by backgrounding (issue #14). It starts a fresh take that
// force-appends onto the existing transcript regardless of the
// Append toggle, so the seam across the app-switch is invisible.
export function resumeRecording() {
  if (state.mode !== 'idle') return;
  state.forceAppend = true;
  startRecording();
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
// becomes unrecoverable by ffmpeg (voice-transcriber#192). Retrying
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

async function onRecorderStopped(mimeType) {
  // Captured before the finally clears the flag — drives whether the
  // Resume button is offered once this take settles.
  const wasBackgrounded = state.backgroundFinalized;
  try {
    // Wait for any in-flight chunks to land before asking the server
    // to transcode + transcribe.
    const startWait = Date.now();
    const flushStatus = setInterval(() => {
      if (state.pendingUploads > 0) {
        els.recordStatus.textContent =
          `Finalising upload · ${state.pendingUploads} chunk${state.pendingUploads === 1 ? '' : 's'} left`;
      }
    }, 200);
    els.recordStatus.textContent = 'Finalising upload…';
    await state.uploadChain;
    clearInterval(flushStatus);

    const elapsedSec = Math.max(0, (Date.now() - state.startedAt) / 1000);
    els.recordStatus.textContent =
      `Server: ffmpeg → whisper · ${formatDuration(elapsedSec)} of audio…`;
    const t0 = Date.now();
    const translate = isOn(els.translateToggle);
    const finishUrl =
      `/api/sessions/${state.sessionId}/finish` +
      `?language=${encodeURIComponent(els.languageSelect.value)}` +
      `&translate=${translate ? 'true' : 'false'}`;
    if (translate) {
      els.recordStatus.textContent =
        `Server: ffmpeg → translate (cold-start ~5 s on first call)…`;
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
      showToast('Empty audio — nothing transcribed', 'success');
      refreshHistory();
      return;
    }
    // Merge against the pre-take base, not state.transcript (which
    // already includes any partial that arrived via SSE — using it
    // would double-append the take onto the base prefix). The take
    // already lives on disk, so renderTranscript's saveTranscript
    // disable is correct here too (saving again would duplicate it).
    renderTranscript(mergeForAppend(state.partialBaseTranscript, data.transcript || ''));
    // Auto-copy reads from the textarea so what lands on the clipboard
    // is exactly what's on screen — including the merged accumulator
    // when Append is on.
    const transcriptForCopy = els.transcript.value;
    if (transcriptForCopy) await tryAutoCopy(transcriptForCopy, els.copyTranscript);
    const speed = elapsedSec > 0 ? (elapsedSec / (serverMs / 1000)).toFixed(1) : '?';
    els.recordStatus.textContent = state.backgroundFinalized
      ? 'Saved while you were away — tap Resume to continue'
      : `Done in ${(serverMs / 1000).toFixed(1)} s · ${speed}× realtime — tap Copy or Polish`;
    updateModelRoute(data.served_model, data.served_host);
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
    els.levelFill.style.width = '0%';
    setMode('idle');
    state.backgroundFinalized = false;
    state.forceAppend = false;
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

// Shows which STT backend/host actually served the last take (#156) — the
// hub's routing (parakeet-first, whisper failover) is otherwise invisible.
// Missing/empty fields (older server, or the silent-skip branch which never
// calls whisper at all) leave whatever was shown before untouched, rather
// than blanking a still-accurate previous answer.
function updateModelRoute(servedModel, servedHost) {
  if (!els.modelRoute || !servedModel) return;
  els.modelRoute.textContent = servedHost ? `via ${servedModel} · ${servedHost}` : `via ${servedModel}`;
  els.modelRoute.hidden = false;
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
