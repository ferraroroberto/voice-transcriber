/* Recording lifecycle — mic capture, chunked upload, VU meter, VAD
 * auto-stop, rolling-transcription SSE, background-finalise / resume.
 *
 * State machine: idle → recording → uploading → transcribing → idle.
 */

'use strict';

import { els, state, getStoredToken } from './state.js';
import { authFetch } from './api.js';
import { isOn, showToast, tryAutoCopy, truncate } from './ui.js';
import { refreshHistory } from './history.js';

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
  state.chunks = [];
  state.uploadChain = Promise.resolve();
  state.pendingUploads = 0;
  state.bytesSent = 0;
  state.recorder = new MediaRecorder(stream, mimeType ? {mimeType} : undefined);
  state.stream = stream;

  // Stream every chunk to disk on the PC the moment it arrives — if the
  // phone dies mid-record, the partial recording is still recoverable.
  state.recorder.ondataavailable = e => {
    if (!e.data || e.data.size === 0) return;
    state.chunks.push(e.data);
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

  setupLevelMeter(stream);
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

function enqueueChunkUpload(chunk) {
  state.pendingUploads += 1;
  state.uploadChain = state.uploadChain.then(async () => {
    try {
      const r = await authFetch(`/api/sessions/${state.sessionId}/chunk`, {
        method: 'POST',
        headers: { 'Content-Type': chunk.type || state.mimeType || 'audio/webm' },
        body: chunk,
      });
      if (r.ok) {
        state.bytesSent += chunk.size;
      } else {
        console.warn('chunk upload failed', r.status, await r.text().catch(() => ''));
      }
    } catch (err) {
      console.warn('chunk upload errored', err);
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
    // would double-append the take onto the base prefix).
    state.transcript = mergeForAppend(
      state.partialBaseTranscript, data.transcript || '',
    );
    state.polished = '';
    els.transcript.value = state.transcript;
    els.polished.value = '';
    els.copyTranscript.disabled = !state.transcript;
    els.copyPolished.disabled = true;
    els.polishBtn.disabled = !state.transcript;
    // The take already lives on disk — saving again would duplicate it.
    els.saveTranscript.disabled = true;
    // Auto-copy reads from the textarea so what lands on the clipboard
    // is exactly what's on screen — including the merged accumulator
    // when Append is on.
    const transcriptForCopy = els.transcript.value;
    if (transcriptForCopy) await tryAutoCopy(transcriptForCopy, els.copyTranscript);
    const speed = elapsedSec > 0 ? (elapsedSec / (serverMs / 1000)).toFixed(1) : '?';
    els.recordStatus.textContent = state.backgroundFinalized
      ? 'Saved while you were away — tap Resume to continue'
      : `Done in ${(serverMs / 1000).toFixed(1)} s · ${speed}× realtime — tap Copy or Polish`;
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

// ----------------------------------------------------- timer + VU

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

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
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

function setupLevelMeter(stream) {
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
      maybeFireAutoStop(max, VAD_LOUDNESS_THRESHOLD);
    }, 80);
  } catch (err) {
    console.warn('VU meter setup failed', err);
  }
}

function maybeFireAutoStop(loudness, threshold) {
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
      stopRecording();
    }, 500);
  }
}

function openPartialStream(sessionId) {
  closePartialStream();
  // Subscribe unless the server *explicitly* reports rolling transcription
  // disabled. A missing flag — e.g. a transient /api/config failure that
  // fell back to client defaults, common on a cold-waking Tailscale link —
  // must NOT silently kill live partials: chunk upload + /finish still
  // deliver the final transcript, which masks the loss. See issue #87.
  if (state.config && state.config.rolling_transcription_enabled === false) return;
  if (!('EventSource' in window)) return;
  const tok = getStoredToken();
  let url = `/api/sessions/${sessionId}/events`;
  if (tok) url += `?token=${encodeURIComponent(tok)}`;
  let es;
  try {
    es = new EventSource(url);
  } catch (err) {
    console.warn('EventSource open failed', err);
    return;
  }
  state.eventSource = es;
  es.addEventListener('partial', (e) => {
    try {
      const data = JSON.parse(e.data);
      applyPartial(data);
    } catch (_) {}
  });
  es.addEventListener('final', (e) => {
    // Final transcript arrived via SSE — leave the user-facing
    // settling to onRecorderStopped which also handles status, history
    // refresh, and the auto-copy. Close the stream so we don't hold
    // the connection open after the take ends.
    closePartialStream();
  });
  es.onerror = () => {
    // Browser will auto-retry; we just leave the handle in place so
    // the next reconnect drops its events in.
  };
}

export function closePartialStream() {
  if (state.eventSource) {
    try { state.eventSource.close(); } catch (_) {}
    state.eventSource = null;
  }
}

function applyPartial(data) {
  if (!data || typeof data.transcript !== 'string') return;
  if (typeof data.version === 'number') {
    if (data.version < state.partialVersion) return; // stale
    state.partialVersion = data.version;
  }
  const merged = state.partialBaseTranscript
    ? state.partialBaseTranscript + '\n\n' + data.transcript
    : data.transcript;
  state.transcript = merged;
  els.transcript.value = merged;
  els.copyTranscript.disabled = !merged;
  els.polishBtn.disabled = !merged;
  els.saveTranscript.disabled = true;
  if (state.mode === 'recording' && Date.now() >= state.vadStatusOwnedUntil) {
    els.recordStatus.textContent =
      `Recording · partial v${state.partialVersion} · ${formatBytes(state.bytesSent)} streamed`;
  }
}

function teardownLevelMeter() {
  if (state.levelTimer) clearInterval(state.levelTimer);
  state.levelTimer = null;
  if (state.audioCtx) {
    try { state.audioCtx.close(); } catch (err) {}
    state.audioCtx = null;
  }
}

// ----------------------------------------------------- screen wake lock

// iOS auto-locks the screen during long records, which backgrounds the
// page and revokes the mic. Hold a screen wake lock for the duration of
// the take. The platform auto-releases the sentinel whenever the page is
// hidden (and on low battery), so app.js re-acquires on
// visibilitychange→visible while still recording; the `release` listener
// clears our handle so that re-acquire isn't blocked by a stale sentinel.
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
