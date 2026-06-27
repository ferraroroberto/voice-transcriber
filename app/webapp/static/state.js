/* Shared DOM handles + mutable state for the voice transcriber SPA.
 *
 * `els` is the single DOM lookup table; `state` is the single mutable
 * store. Every other module imports from here — nothing else holds
 * module-level mutable state. ES modules are deferred, so the
 * getElementById calls below run after index.html has fully parsed.
 */

'use strict';

export const els = {
  recordBtn:        document.getElementById('recordBtn'),
  resumeBtn:        document.getElementById('resumeBtn'),
  recordLabel:      document.getElementById('recordLabel'),
  recordTimer:      document.getElementById('recordTimer'),
  recordStatus:     document.getElementById('recordStatus'),
  levelFill:        document.getElementById('levelFill'),

  transcript:       document.getElementById('transcript'),
  copyTranscript:   document.getElementById('copyTranscript'),
  saveTranscript:   document.getElementById('saveTranscript'),

  polishModel:        document.getElementById('polishModel'),
  polishStyle:        document.getElementById('polishStyle'),
  polishPromptPreview: document.getElementById('polishPromptPreview'),
  polishBtn:          document.getElementById('polishBtn'),
  copyPolished:       document.getElementById('copyPolished'),
  polished:           document.getElementById('polished'),

  historyCount:     document.getElementById('historyCount'),
  historyList:      document.getElementById('historyList'),
  refreshHistory:   document.getElementById('refreshHistory'),
  copySelection:    document.getElementById('copySelection'),
  cleanAll:         document.getElementById('cleanAll'),
  loadMoreHistory:  document.getElementById('loadMoreHistory'),

  resetBtn:         document.getElementById('resetBtn'),
  appendToggle:     document.getElementById('appendToggle'),
  incognitoToggle:  document.getElementById('incognitoToggle'),

  loginOverlay:     document.getElementById('loginOverlay'),
  loginForm:        document.getElementById('loginForm'),
  loginPassword:    document.getElementById('loginPassword'),
  loginError:       document.getElementById('loginError'),

  settingsPanel:    document.getElementById('settingsPanel'),
  languageSelect:   document.getElementById('languageSelect'),
  translateToggle:  document.getElementById('translateToggle'),
  micSelect:        document.getElementById('micSelect'),
  forceBuiltinMic:  document.getElementById('forceBuiltinMic'),
  vadAutoStopToggle:       document.getElementById('vadAutoStopToggle'),
  autoStopSilenceMs:       document.getElementById('autoStopSilenceMs'),
  retentionDays:    document.getElementById('retentionDays'),
  saveSettings:     document.getElementById('saveSettings'),
  statusReadout:    document.getElementById('statusReadout'),
  buildInfo:        document.getElementById('buildInfo'),

  toast:            document.getElementById('toast'),
};

export const state = {
  sessionId:   null,
  // Tracks the most recent incognito session so we can DELETE it
  // from the server when the user starts another recording or hits
  // Reset — keeps incognito sessions from piling up on disk even
  // though they're already filtered out of the History list.
  incognitoSessionId: null,
  recorder:    null,
  stream:      null,
  streamKey:   '',          // constraints fingerprint for the cached stream
  chunks:      [],          // queued chunks awaiting upload
  uploadChain: Promise.resolve(),  // serializes uploads so server appends in order
  pendingUploads: 0,
  bytesSent:   0,           // running total of chunk bytes uploaded
  startedAt:   0,
  timer:       null,
  levelTimer:  null,
  audioCtx:    null,
  analyser:    null,
  wakeLock:    null,        // active screen WakeLockSentinel while recording
  mode:        'idle',
  config:      null,
  configIsFallback: false,  // true while running on applyConfigDefaults()'s
                            // offline defaults — a later visit re-fetches
                            // the real server config (issue #87)
  transcript:  '',
  polished:    '',
  mimeType:    null,
  // Latency-collapse plumbing (issue #5).
  eventSource: null,         // open SSE stream against /api/sessions/{id}/events
  partialVersion: 0,         // last partial version we accepted
  partialBaseTranscript: '', // transcript prefix when Append mode is on
  vadSilenceSince: 0,        // ms timestamp of the last loud sample (0 = never)
  vadStopFired: false,       // guard so the auto-stop fires once per take
  vadStatusOwnedUntil: 0,    // VAD owns the status line until this ms timestamp
  backgroundFinalized: false, // take was finalised because the app got backgrounded
  forceAppend: false,         // this take appends regardless of the Append toggle (Resume)
};

// Pick up a `?token=…` from the URL on first load (typical when the
// user opens the tokenised URL the tray copied or `last_tunnel_url.txt`
// emitted), persist it to localStorage, and strip it from the visible
// URL so the bookmark / Home Screen icon stays clean. From then on
// every API fetch attaches `Authorization: Bearer <token>`.
export const TOKEN_KEY = 'vt_auth_token';

export function getStoredToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ''; }
  catch (_) { return ''; }
}

export function captureTokenFromURL() {
  try {
    const url = new URL(window.location.href);
    const tok = url.searchParams.get('token');
    if (!tok) return;
    try { localStorage.setItem(TOKEN_KEY, tok); } catch (_) {}
    url.searchParams.delete('token');
    const clean = url.pathname + (url.search ? url.search : '') + url.hash;
    window.history.replaceState({}, '', clean);
  } catch (_) {}
}
