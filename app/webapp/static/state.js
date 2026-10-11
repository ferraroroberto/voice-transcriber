/* Shared DOM handles + mutable state for the voice transcriber SPA.
 *
 * `els` is the single DOM lookup table; `state` is the single mutable
 * store. Every other module imports from here — nothing else holds
 * module-level mutable state. ES modules are deferred, so the
 * getElementById calls below run after index.html has fully parsed.
 */

'use strict';

export const els = {
  recordHeadLine:   document.getElementById('recordHeadLine'),
  recordBtn:        document.getElementById('recordBtn'),
  resumeBtn:        document.getElementById('resumeBtn'),
  recordLabel:      document.getElementById('recordLabel'),
  recordTimer:      document.getElementById('recordTimer'),
  recordStatus:     document.getElementById('recordStatus'),
  levelFill:        document.getElementById('levelFill'),

  transcript:       document.getElementById('transcript'),
  polished:         document.getElementById('polished'),
  textView:         document.getElementById('textView'),
  textMeta:         document.getElementById('textMeta'),
  textVerbs:        document.getElementById('textVerbs'),
  copyText:         document.getElementById('copyText'),
  saveTranscript:   document.getElementById('saveTranscript'),

  polishModel:        document.getElementById('polishModel'),
  polishStyle:        document.getElementById('polishStyle'),
  polishPromptPreview: document.getElementById('polishPromptPreview'),
  polishBtn:          document.getElementById('polishBtn'),

  historyHeadLine:  document.getElementById('historyHeadLine'),
  analyticsSummary: document.getElementById('analyticsSummary'),
  latestCard:       document.getElementById('latestCard'),
  latestWhen:       document.getElementById('latestWhen'),
  latestText:       document.getElementById('latestText'),
  latestMeta:       document.getElementById('latestMeta'),
  latestCopy:       document.getElementById('latestCopy'),
  latestMore:       document.getElementById('latestMore'),
  historyGroups:    document.getElementById('historyGroups'),
  selectBar:        document.getElementById('selectBar'),
  selectCount:      document.getElementById('selectCount'),
  selectCancel:     document.getElementById('selectCancel'),
  copySelection:    document.getElementById('copySelection'),
  cleanAll:         document.getElementById('cleanAll'),
  loadMoreHistory:  document.getElementById('loadMoreHistory'),
  takeSheet:        document.getElementById('takeSheet'),
  takeSheetTitle:   document.getElementById('takeSheetTitle'),
  takeSheetText:    document.getElementById('takeSheetText'),
  takeCopy:         document.getElementById('takeCopy'),
  takeRedo:         document.getElementById('takeRedo'),
  takeLength:       document.getElementById('takeLength'),
  takeLanguage:     document.getElementById('takeLanguage'),
  takeSource:       document.getElementById('takeSource'),
  takeServedBy:     document.getElementById('takeServedBy'),
  takeDelete:       document.getElementById('takeDelete'),

  resetBtn:         document.getElementById('resetBtn'),
  appendToggle:     document.getElementById('appendToggle'),
  incognitoToggle:  document.getElementById('incognitoToggle'),

  loginOverlay:     document.getElementById('loginOverlay'),
  loginForm:        document.getElementById('loginForm'),
  loginPassword:    document.getElementById('loginPassword'),
  loginError:       document.getElementById('loginError'),

  languageSelect:   document.getElementById('languageSelect'),
  translateToggle:  document.getElementById('translateToggle'),
  micSelect:        document.getElementById('micSelect'),
  forceBuiltinMic:  document.getElementById('forceBuiltinMic'),
  gainBoostToggle:  document.getElementById('gainBoostToggle'),
  gainBoostDb:      document.getElementById('gainBoostDb'),
  vadAutoStopToggle:       document.getElementById('vadAutoStopToggle'),
  autoStopSilenceMs:       document.getElementById('autoStopSilenceMs'),
  retentionDays:    document.getElementById('retentionDays'),
  saveSettings:     document.getElementById('saveSettings'),
  statusReadout:    document.getElementById('statusReadout'),
  settingsHeadLine: document.getElementById('settingsHeadLine'),
  buildInfo:        document.getElementById('buildInfo'),

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
  uploadChain: Promise.resolve(),  // serializes uploads so server appends in order
  pendingUploads: 0,
  bytesSent:   0,           // running total of chunk bytes uploaded
  hadDroppedChunk: false,   // a /chunk POST exhausted its retries this take (#192)
  startedAt:   0,
  timer:       null,
  levelTimer:  null,
  audioCtx:    null,
  analyser:    null,
  wakeLock:    null,        // active screen WakeLockSentinel while recording
  mode:        'idle',
  config:      null,
  services:    null,          // last /api/status reply; null until one lands (or after a failed poll)
  historyTotal: null,         // the History list's `total`, which its header line counts
  historyTakes: [],           // every loaded History row, newest first (#264)
  historySelecting: false,    // History is in Select mode
  historySelected: new Set(), // session ids picked in Select mode
  configIsFallback: false,  // true while running on applyConfigDefaults()'s
                            // offline defaults — a later visit re-fetches
                            // the real server config (issue #87)
  transcript:  '',
  polished:    '',
  textView:    'transcript',  // which version the Text card shows: 'transcript' | 'polished'
  take:        null,          // {at: Date, seconds: number} for the take in the Text card, if any
  polishStyle: '',            // label of the style the shown polish used
  polishing:   false,         // a polish request is in flight
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
