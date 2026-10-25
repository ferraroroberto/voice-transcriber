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
import { initUsageLog } from './_vendored/usage-log/usage-log.js';
import { bindTextSize } from './_vendored/text-size/text-size.js';
import { els, state, captureTokenFromURL, getStoredToken } from './state.js';
import { authFetch } from './api.js';
import { copyText, isOn, renderTextCard, setTextView, shownText, showToast } from './ui.js';
import { icon } from './_vendored/icons/icons.js';
import {
  applyConfigDefaults,
  bindSettings,
  initPromptSheet,
  loadConfig,
  refreshPromptPreview,
  refreshStatus,
  renderRecordHeadLine,
  renderSettingsHeadLine,
} from './config.js';
import {
  finalizeForBackground,
  onRecordToggle,
  populateMics,
  releaseCachedStream,
  resumeRecording,
} from './recorder.js';
import { closePartialStream } from './partials.js';
import { acquireWakeLock } from './wakelock.js';
import {
  initDeleteAll,
  initSelectMode,
  initTakeSheet,
  loadMoreHistory,
  refreshHistory,
} from './history.js';
import { onPolish, onReset, onSaveTranscript } from './polish.js';

captureTokenFromURL();

// Local-only view/tap log: records which views and controls get used
// (never text, names or audio details) into this app's own SQLite file. The
// bearer token rides in a header because a phone on the tunnel is not
// loopback; data-track ids on the controls feed it.
const usage = initUsageLog({
  headers: () => {
    const tok = getStoredToken();
    return tok ? { Authorization: 'Bearer ' + tok } : {};
  },
});

init().catch(err => {
  console.error(err);
  showToast('Init failed: ' + err.message + ' — pull down to retry', 'error');
});

// Show the Settings pane over the current tab. The vendored nav only manages
// its own tabs' panes, so hide them here and leave no tab selected; the nav's
// next setTab (any tab tap) shows that tab's pane again and onChange hides
// this one. The stored tab is untouched, so a reload from Settings reopens
// the last real tab (same shape as app-launcher's openSettings).
function openSettings() {
  usage.trackView('settings');
  document.querySelectorAll('main.app > section.pane').forEach((pane) => {
    pane.hidden = pane.id !== 'paneSettings';
  });
  const nav = document.querySelector('nav.tabs');
  nav.querySelectorAll('.tab').forEach((tab) => {
    tab.classList.remove('active');
    tab.setAttribute('aria-selected', 'false');
  });
  nav.dataset.activeTab = 'settings';
  const scroller = document.querySelector('.app');
  if (scroller) scroller.scrollTop = 0;
  window.scrollTo(0, 0);
  refreshStatus();
}

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

// Surface the loaded build in Settings › About so "is the phone running the
// current code?" is answerable. Format + stale-shell reload
// guard mirror home-automation's main.js.
function fmtBuildTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).replace('T', ' ').slice(0, 16);
  const pad = function (n) { return String(n).padStart(2, '0'); };
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
    ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}

const ASSET_HASH_KEY = 'voice-transcriber.assetHash';
const ASSET_RELOAD_KEY = 'voice-transcriber.assetReloadedFor';

async function loadVersion() {
  if (!els.buildInfo) return;
  try {
    const r = await authFetch('/api/version');
    if (!r.ok) throw new Error(String(r.status));
    const v = await r.json();
    const sha = v.git_sha || '?';
    const assetHash = v.asset_hash || '';
    const previousHash = localStorage.getItem(ASSET_HASH_KEY) || '';
    if (
      assetHash && previousHash && previousHash !== assetHash &&
      sessionStorage.getItem(ASSET_RELOAD_KEY) !== assetHash
    ) {
      // iOS standalone PWAs can cling to an old shell even with stamped
      // asset URLs — the cached index.html keeps loading pre-deploy
      // modules while this live /api/version fetch shows the new SHA.
      // One automatic reload per deploy picks the fresh shell up.
      localStorage.setItem(ASSET_HASH_KEY, assetHash);
      sessionStorage.setItem(ASSET_RELOAD_KEY, assetHash);
      window.location.reload();
      return;
    }
    if (assetHash) localStorage.setItem(ASSET_HASH_KEY, assetHash);
    // This module was loaded as app.js?v=<its asset hash>; a different hash
    // from the server means the shell is stale even after the one automatic
    // reload, so Settings' header line asks for a reload.
    const loaded = new URL(import.meta.url).searchParams.get('v');
    state.staleShell = !!(assetHash && loaded && loaded !== assetHash);
    renderSettingsHeadLine();
    const ts = fmtBuildTime(v.built_at || '');
    els.buildInfo.textContent = ts ? `${sha} · ${ts}` : sha;
  } catch (_) {
    // Non-critical — say it plainly rather than alarming the user.
    els.buildInfo.textContent = 'Unavailable';
  }
}

function bindEvents() {
  els.recordBtn.addEventListener('click', onRecordToggle);
  if (els.resumeBtn) els.resumeBtn.addEventListener('click', resumeRecording);
  // Copy takes whichever version the Text card is showing.
  els.copyText.addEventListener('click', () => copyText(shownText(), els.copyText));
  els.textView.addEventListener('click', (e) => {
    const seg = e.target.closest('.segmented-item');
    if (seg) setTextView(seg.dataset.view);
  });

  // Keep state in sync with manual edits — paste, typing, deletion all flow
  // through here; both versions keep their edits across a view switch.
  els.transcript.addEventListener('input', () => {
    state.transcript = els.transcript.value;
    renderTextCard();
  });
  els.polished.addEventListener('input', () => {
    state.polished = els.polished.value;
    renderTextCard();
  });

  els.resetBtn.addEventListener('click', onReset);
  els.polishBtn.addEventListener('click', onPolish);
  els.saveTranscript.addEventListener('click', onSaveTranscript);
  els.polishStyle.addEventListener('change', refreshPromptPreview);

  bindNav();
  els.loadMoreHistory.addEventListener('click', loadMoreHistory);
  initSelectMode();
  initTakeSheet();
  initDeleteAll();
  initPromptSheet();

  bindSwitches();
  bindAppearance();
  els.micSelect.addEventListener('change', releaseCachedStream);
  els.languageSelect.addEventListener('change', renderRecordHeadLine);

  bindPageLifecycle();
}

function bindNav() {
  // Fleet bottom-tab nav (vendored component — _vendored/nav/). Settings is
  // no tab (fleet NAV-03): every pane's header gear opens it (openSettings),
  // and choosing any tab leaves it, so onChange hides the pane.
  initNavTabs({
    storageKey: 'voice-transcriber.tab',
    scrollResetSelector: '.app',
    onChange: (tab) => {
      usage.trackView(tab);
      document.getElementById('paneSettings').hidden = true;
      // History refreshes itself on open — no Refresh button.
      if (tab === 'history') refreshHistory();
    },
  });
  for (const gear of document.querySelectorAll('.home-settings')) {
    gear.addEventListener('click', openSettings);
  }
}

function bindSwitches() {
  // Fleet switches (vendored component): a tap flips the state through the
  // one setSwitch() write path; reads everywhere go through isOn(). Forcing
  // built-in also invalidates the cached stream so the next record() picks
  // up the new constraints (and re-prompts only if iOS deems the new device
  // a different permission scope) — same reason micSelect stays on 'change'.
  const flipOnTap = (el, after) => el.addEventListener('click', () => {
    setSwitch(el, !isOn(el));
    if (after) after();
  });
  flipOnTap(els.translateToggle, renderRecordHeadLine);
  flipOnTap(els.gainBoostToggle);
  flipOnTap(els.vadAutoStopToggle);
  flipOnTap(els.forceBuiltinMic, releaseCachedStream);
  // Persisted settings save as they change; registered after the
  // flips above so a switch saves its new state.
  bindSettings();

  // The recorder's mode toggles (append / incognito) are labelled
  // role="switch" pills — same aria-checked state contract, own look (not
  // the track switch), so they flip aria-checked directly rather than
  // through setSwitch().
  for (const chip of [els.appendToggle, els.incognitoToggle]) {
    chip.addEventListener('click', () => {
      chip.setAttribute('aria-checked', isOn(chip) ? 'false' : 'true');
      renderRecordHeadLine();
    });
  }
}

function bindAppearance() {
  // Theme toggle (the fleet pattern), one per pane header: the
  // pre-paint snippet in index.html already stamped html[data-theme]; a button
  // just flips it. The sun/moon glyph swap is pure CSS keyed on the attribute.
  for (const toggle of document.querySelectorAll('.theme-toggle')) {
    toggle.addEventListener('click', () => {
      const dark = document.documentElement.dataset.theme !== 'dark';
      document.documentElement.dataset.theme = dark ? 'dark' : 'light';
      try { localStorage.setItem('voice-transcriber.theme', dark ? 'dark' : 'light'); } catch (_) {}
    });
  }

  // Text size (fleet escape from the zoom lock): persists to
  // voice-transcriber.textsize and stamps html[data-textsize] on every tap, so
  // there is nothing for Save to do. The boot script handles first paint.
  bindTextSize(document.getElementById('textSizeControl'), 'voice-transcriber');
}

function bindPageLifecycle() {
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
        els.recordStatus.innerHTML =
          icon('pause') + ' Paused while you were away — finalising…';
      }
      // The platform auto-releases the screen wake lock whenever the page
      // is hidden; re-acquire if we came back while still recording (the
      // Android background-record case the original one-shot missed).
      if (state.mode === 'recording') acquireWakeLock();
      // Re-fetch config when it never loaded, or when we're running on the
      // offline fallback (a cold-Tailscale first load) — so the real server
      // flags, including rolling transcription, replace the guesses once the
      // link is warm.
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
