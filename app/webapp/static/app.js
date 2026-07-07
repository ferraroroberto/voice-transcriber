/* Entry point — mobile-first voice transcriber.
 *
 * This module wires the others together: it captures the URL token,
 * binds every DOM event to its handler, and runs the boot sequence.
 * The feature logic lives in the imported modules; nothing here does
 * real work beyond sequencing.
 */

'use strict';

import { initNavTabs } from './_vendored/nav/nav-tabs.js';
import { setSwitch } from './_vendored/switch/switch.js';
import { els, state, captureTokenFromURL } from './state.js';
import { authFetch } from './api.js';
import { copyText, isOn, showToast } from './ui.js';
import {
  applyConfigDefaults,
  loadConfig,
  onSaveSettings,
  refreshPromptPreview,
  refreshStatus,
} from './config.js';
import {
  acquireWakeLock,
  closePartialStream,
  finalizeForBackground,
  onRecordToggle,
  populateMics,
  releaseCachedStream,
  resumeRecording,
} from './recorder.js';
import {
  loadMoreHistory,
  onCleanAll,
  onCopySelection,
  refreshHistory,
} from './history.js';
import { onPolish, onReset, onSaveTranscript } from './polish.js';

captureTokenFromURL();

init().catch(err => {
  console.error(err);
  showToast('Init failed: ' + err.message + ' — pull down to retry', 'error');
});

async function init() {
  // Each step is wrapped so a single transient blip (iOS waking the
  // tailnet, Safari dropping a stale TLS connection) doesn't leave the
  // page dead. We bind events even if config/status fail so the user
  // can pull-to-refresh manually.
  bindEvents();
  try {
    await loadConfig();
  } catch (err) {
    console.warn('loadConfig failed, using defaults:', err);
    applyConfigDefaults();
    showToast('Config load failed — using defaults · pull to retry', 'error');
  }
  try { await populateMics(); } catch (err) { console.warn('populateMics:', err); }
  refreshStatus();
  refreshHistory();
  loadVersion();
}

// Surface the loaded build in the Settings panel so "is the phone
// running the current code?" is answerable at a glance — see issue #13.
async function loadVersion() {
  if (!els.buildInfo) return;
  try {
    const r = await authFetch('/api/version');
    if (!r.ok) throw new Error(String(r.status));
    const v = await r.json();
    const when = String(v.built_at || '')
      .replace('T', ' ')
      .replace(/(\+00:00|Z)$/, ' UTC');
    els.buildInfo.textContent =
      `Build: ${v.git_sha || '?'} · ${when}`.trim();
  } catch (_) {
    // Non-critical — leave the placeholder rather than alarming the user.
    els.buildInfo.textContent = 'Build: unavailable';
  }
}

function bindEvents() {
  els.recordBtn.addEventListener('click', onRecordToggle);
  if (els.resumeBtn) els.resumeBtn.addEventListener('click', resumeRecording);
  els.copyTranscript.addEventListener('click', () => copyText(state.transcript, els.copyTranscript));
  els.copyPolished.addEventListener('click', () => copyText(state.polished, els.copyPolished));

  // Keep state in sync with manual edits — paste, typing, deletion all flow through here.
  els.transcript.addEventListener('input', () => {
    state.transcript = els.transcript.value;
    els.copyTranscript.disabled = !state.transcript;
    els.polishBtn.disabled = !state.transcript;
    // Save is only meaningful for pasted text that doesn't yet belong
    // to a session — a real recording already lives in History.
    els.saveTranscript.disabled = !state.transcript || !!state.sessionId;
  });
  els.polished.addEventListener('input', () => {
    state.polished = els.polished.value;
    els.copyPolished.disabled = !state.polished;
  });

  els.resetBtn.addEventListener('click', onReset);
  els.polishBtn.addEventListener('click', onPolish);
  els.saveTranscript.addEventListener('click', onSaveTranscript);
  els.polishStyle.addEventListener('change', refreshPromptPreview);

  // Fleet bottom-tab nav (vendored component — _vendored/nav/). Refresh the
  // status readout whenever the Settings tab is activated, replacing the
  // disclosure-toggle listener from the old single-scroll layout.
  initNavTabs({
    storageKey: 'voice-transcriber.tab',
    scrollResetSelector: '.app',
    onChange: (tab) => {
      if (tab === 'settings') refreshStatus();
    },
  });
  els.saveSettings.addEventListener('click', onSaveSettings);

  els.refreshHistory.addEventListener('click', refreshHistory);
  els.copySelection.addEventListener('click', onCopySelection);
  els.cleanAll.addEventListener('click', onCleanAll);
  els.loadMoreHistory.addEventListener('click', loadMoreHistory);

  // Fleet switches (vendored component): a tap flips the state through the
  // one setSwitch() write path; reads everywhere go through isOn(). Forcing
  // built-in also invalidates the cached stream so the next record() picks
  // up the new constraints (and re-prompts only if iOS deems the new device
  // a different permission scope) — same reason micSelect stays on 'change'.
  const flipOnTap = (el, after) => el.addEventListener('click', () => {
    setSwitch(el, !isOn(el));
    if (after) after();
  });
  flipOnTap(els.translateToggle);
  flipOnTap(els.gainBoostToggle);
  flipOnTap(els.vadAutoStopToggle);
  flipOnTap(els.forceBuiltinMic, releaseCachedStream);

  // Header chips (append / incognito) are compact role="switch" buttons —
  // same aria-checked state contract, own look (not the track switch), so
  // they flip aria-checked directly rather than through setSwitch().
  for (const chip of [els.appendToggle, els.incognitoToggle]) {
    chip.addEventListener('click', () => {
      chip.setAttribute('aria-checked', isOn(chip) ? 'false' : 'true');
    });
  }

  // Theme toggle (app-launcher #355 pattern): the pre-paint snippet in
  // index.html already stamped html[data-theme]; the button just flips it.
  // The sun/moon glyph swap is pure CSS keyed on the attribute.
  els.themeToggle.addEventListener('click', () => {
    const dark = document.documentElement.dataset.theme !== 'dark';
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    try { localStorage.setItem('voice-transcriber.theme', dark ? 'dark' : 'light'); } catch (_) {}
  });

  els.micSelect.addEventListener('change', releaseCachedStream);

  // Release the mic when the page goes away so the iOS recording
  // indicator turns off — iOS keeps the permission grant for the
  // document lifetime, so the next record won't re-prompt within
  // the same page session. Also opportunistically reload config when
  // the tab becomes visible again, in case Tailscale was asleep.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      if (state.mode === 'recording') {
        // App switch / screen lock mid-record — finalise the take so it
        // is saved rather than dying silently. See finalizeForBackground.
        finalizeForBackground();
      } else if (state.mode === 'idle') {
        releaseCachedStream();
      }
    } else if (document.visibilityState === 'visible') {
      if (state.backgroundFinalized && state.mode === 'uploading') {
        els.recordStatus.textContent =
          '⏸️ Paused while you were away — finalising…';
      }
      // The platform auto-releases the screen wake lock whenever the page
      // is hidden; re-acquire if we came back while still recording (the
      // Android background-record case the original one-shot missed).
      if (state.mode === 'recording') acquireWakeLock();
      // Re-fetch config when it never loaded, or when we're running on the
      // offline fallback (a cold-Tailscale first load) — so the real server
      // flags, including rolling transcription, replace the guesses once the
      // link is warm. See issue #87.
      if (!state.config || state.configIsFallback) loadConfig().catch(() => {});
    }
  });
  window.addEventListener('pagehide', () => {
    // pagehide can mean the page is being discarded outright — make a
    // best-effort finalise so the take is saved, not just left as
    // streamed chunks for History → Redo to recover.
    if (state.mode === 'recording') finalizeForBackground();
    releaseCachedStream();
    closePartialStream();
  });
}
