/* Rolling-transcription SSE — subscribes to the per-session partial
 * stream and merges incoming partials into the transcript box live,
 * so the user sees text appear while still talking instead of only
 * after the take finishes.
 */

'use strict';

import { els, state, getStoredToken } from './state.js';
import { formatBytes } from './ui.js';

export function openPartialStream(sessionId) {
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
