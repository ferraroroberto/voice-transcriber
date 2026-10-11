/* Config + settings — loads /api/config, renders the Settings groups,
 * saves each setting as it changes, polls /api/status (the Services group),
 * and draws the Record and Settings header lines from them.
 */

'use strict';

import { setSwitch } from './_vendored/switch/switch.js';
import { els, state } from './state.js';
import { authFetch, promptForPassword, readErrorMessage } from './api.js';
import { capitalize, isOn, renderTextCard, setHeadLine, showToast } from './ui.js';
import { renderHistoryHeadLine } from './history.js';

export async function loadConfig() {
  // Single attempt first so we can detect 401 and prompt for the
  // password instead of silently retrying with the same stale state.
  const r = await authFetch('/api/config');
  if (r.status === 401) {
    const ok = await promptForPassword();
    if (!ok) throw new Error('login cancelled');
    return loadConfig();
  }
  if (!r.ok) throw new Error(`/api/config returned ${r.status}`);
  state.config = await r.json();
  state.configIsFallback = false;  // real server config — drop the fallback flag
  populateConfigUI();
}

export function applyConfigDefaults() {
  state.config = state.config || {
    // Empty offline fallback — the real list comes from /api/config,
    // which serves config/webapp_config.json. Hardcoding aliases here
    // would defeat the "edit JSON, no code change" rule.
    polish_model_default: '',
    polish_models_available: [],
    polish_prompt_default: 'filler-words',
    polish_prompts: [{
      id: 'filler-words',
      label: 'Filler-word cleanup',
      description: 'Remove uh/um/like, false starts, repetitions. No rephrasing.',
      system: '(prompt unavailable — server is offline)',
    }],
    languages: [{iso: 'en', label: 'English'}, {iso: 'es', label: 'Spanish'}, {iso: 'it', label: 'Italian'}],
    language_default: 'en',
    force_builtin_mic_default: false,
    preferred_mic_id: null,
    history_retention_days: 30,
    // Latency-collapse defaults must mirror the server's (see
    // src/webapp_config.py DEFAULT_*). Omitting rolling_transcription_enabled
    // here used to make openPartialStream() gate itself off whenever the
    // first /api/config failed — live partials gone for the session while
    // the final transcript still worked (issue #87).
    partial_interval_seconds: 2.0,
    rolling_transcription_enabled: true,
    vad_auto_stop_enabled: false,
    auto_stop_silence_ms: 1500,
    gain_boost_enabled: false,
    gain_boost_db: 12,
  };
  // Running on guessed defaults — a later visit re-fetches the real config
  // so the server's actual flags (and any disabled rolling) take over.
  state.configIsFallback = true;
  populateConfigUI();
}

export function polishModelLabel(id) {
  // Derive a friendly label from the hub alias by title-casing the
  // segments (claude_haiku → "Claude Haiku"). Keeping this rule
  // synthesis-only means adding a new model is a single edit in
  // config/webapp_config.sample.json with no code change here.
  return String(id || '')
    .split('_')
    .filter(Boolean)
    .map(s => s.charAt(0).toUpperCase() + s.slice(1))
    .join(' ');
}

export function populateConfigUI() {
  els.polishModel.innerHTML = '';
  for (const model of state.config.polish_models_available) {
    const opt = document.createElement('option');
    opt.value = model;
    opt.textContent = polishModelLabel(model);
    if (model === state.config.polish_model_default) opt.selected = true;
    els.polishModel.appendChild(opt);
  }
  els.polishStyle.innerHTML = '';
  const prompts = state.config.polish_prompts || [];
  for (const p of prompts) {
    const opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = p.label || p.id;
    if (p.id === state.config.polish_prompt_default) opt.selected = true;
    els.polishStyle.appendChild(opt);
  }
  refreshPromptPreview();
  els.languageSelect.innerHTML = '';
  for (const lang of state.config.languages || []) {
    // Server returns [{iso, label}]; tolerate the legacy bare-string shape
    // for back-compat with any cached client code.
    const iso = (typeof lang === 'string') ? lang : lang.iso;
    const label = (typeof lang === 'string') ? capitalize(lang) : lang.label;
    const opt = document.createElement('option');
    opt.value = iso;
    opt.textContent = label;
    if (iso === state.config.language_default) opt.selected = true;
    els.languageSelect.appendChild(opt);
  }
  for (const setting of SETTINGS) setting.show(state.config[setting.key]);
  renderRecordHeadLine();
  renderHistoryHeadLine();
}

// The persisted settings (#265). Each control saves only its own field
// through POST /api/config the moment it changes (numbers on change, i.e.
// commit or blur); a failed save shows an error toast and puts the control
// back to the stored value. Language and Translate are per-session choices
// and stay out of this table. `show` writes a stored value into the
// control, `read` turns the control back into the value to save.
const asNumber = (el, parse) => {
  const n = parse(el.value);
  return Number.isFinite(n) ? n : undefined;
};
const SETTINGS = [
  { key: 'polish_model_default', el: () => els.polishModel, event: 'change',
    show: (v) => { els.polishModel.value = v || ''; }, read: () => els.polishModel.value },
  { key: 'polish_prompt_default', el: () => els.polishStyle, event: 'change',
    show: (v) => { els.polishStyle.value = v || ''; refreshPromptPreview(); },
    read: () => els.polishStyle.value },
  { key: 'preferred_mic_id', el: () => els.micSelect, event: 'change',
    show: (v) => { els.micSelect.value = v || ''; }, read: () => els.micSelect.value || null },
  { key: 'force_builtin_mic_default', el: () => els.forceBuiltinMic, event: 'click',
    show: (v) => setSwitch(els.forceBuiltinMic, !!v), read: () => isOn(els.forceBuiltinMic) },
  { key: 'gain_boost_enabled', el: () => els.gainBoostToggle, event: 'click',
    show: (v) => setSwitch(els.gainBoostToggle, !!v), read: () => isOn(els.gainBoostToggle) },
  { key: 'gain_boost_db', el: () => els.gainBoostDb, event: 'change',
    show: (v) => { els.gainBoostDb.value = v ?? 12; }, read: () => asNumber(els.gainBoostDb, parseFloat) },
  { key: 'vad_auto_stop_enabled', el: () => els.vadAutoStopToggle, event: 'click',
    show: (v) => setSwitch(els.vadAutoStopToggle, !!v), read: () => isOn(els.vadAutoStopToggle) },
  // Shown in seconds ("1.5 s"), stored in milliseconds.
  { key: 'auto_stop_silence_ms', el: () => els.autoStopSilence, event: 'change',
    show: (v) => { els.autoStopSilence.value = String((v || 1500) / 1000); },
    read: () => {
      const sec = asNumber(els.autoStopSilence, parseFloat);
      return sec === undefined ? undefined : Math.round(sec * 1000);
    } },
  { key: 'history_retention_days', el: () => els.retentionDays, event: 'change',
    show: (v) => { els.retentionDays.value = v ?? 30; },
    read: () => asNumber(els.retentionDays, (x) => parseInt(x, 10)) },
];

async function saveSetting(setting) {
  const value = setting.read();
  const stored = state.config ? state.config[setting.key] : undefined;
  if (value === undefined) {
    setting.show(stored);
    return;
  }
  if (value === stored) return;
  try {
    const r = await authFetch('/api/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ [setting.key]: value }),
    });
    if (!r.ok) throw new Error(await readErrorMessage(r));
    const data = await r.json();
    Object.assign(state.config, data.config || { [setting.key]: value });
    setting.show(state.config[setting.key]);
    if (setting.key === 'history_retention_days') renderHistoryHeadLine();
  } catch (err) {
    console.warn(`saving ${setting.key} failed:`, err);
    setting.show(stored);
    showToast("Couldn't save that setting: " + (err.message || err), 'error');
  }
}

// Wire every persisted setting to save on change. Switches flip in app.js's
// click handler first; this listener runs after it and saves the new state.
export function bindSettings() {
  for (const setting of SETTINGS) {
    setting.el().addEventListener(setting.event, () => saveSetting(setting));
  }
}

export function refreshPromptPreview() {
  const prompts = (state.config && state.config.polish_prompts) || [];
  const id = els.polishStyle.value;
  const hit = prompts.find(p => p.id === id);
  els.polishPromptPreview.value = hit ? hit.system : '';
  const opt = els.polishStyle.selectedOptions[0];
  els.promptSheetNote.textContent =
    `Sent with your text when you tap Polish. Style: ${opt ? opt.textContent : 'none'}.`;
}

// Settings › Polish › Instructions: the prompt in a sheet (#265).
export function initPromptSheet() {
  const dlg = els.promptSheet;
  els.openPromptSheet.addEventListener('click', () => {
    refreshPromptPreview();
    dlg.showModal();
  });
  dlg.querySelector('.detail-close').addEventListener('click', () => dlg.close());
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
}

// Transcription goes to the hub first and falls back to the local whisper
// server, so it is offline only when both are down, or when ffmpeg (which
// every take is transcoded through) is missing.
function transcriptionOffline(s) {
  return (!s.whisper.running && !s.llm_hub.reachable) || !s.ffmpeg_present;
}

// Record's header line (#261): the dictation language as one plain fact,
// replaced by the single exception that matters most. Precedence, first
// wins: transcription offline (danger), Incognito (neutral), the Translate
// route (attention), Polish unavailable (attention).
export function renderRecordHeadLine() {
  const s = state.services;
  const opt = els.languageSelect.selectedOptions[0];
  const language = opt ? opt.textContent : '';
  if (s && transcriptionOffline(s)) {
    setHeadLine(els.recordHeadLine, 'Transcription offline', 'danger');
  } else if (isOn(els.incognitoToggle)) {
    setHeadLine(els.recordHeadLine, "Incognito: not saved", 'neutral');
  } else if (isOn(els.translateToggle)) {
    setHeadLine(els.recordHeadLine, `${language} → English`, 'attention');
  } else if (s && !s.llm_hub.reachable) {
    setHeadLine(els.recordHeadLine, 'Polish unavailable', 'attention');
  } else {
    setHeadLine(els.recordHeadLine, language);
  }
}

// Settings' header line (#261): "All services up", or the first failing
// service by name. A status poll that fails is said as such, never as "up".
// A stale loaded shell (#265) is said after any outage.
export function renderSettingsHeadLine() {
  const s = state.services;
  const line = els.settingsHeadLine;
  if (state.servicesFailed) setHeadLine(line, "Couldn't check services", 'attention');
  else if (!s) return;
  else if (!s.whisper.running && !s.llm_hub.reachable) setHeadLine(line, 'Transcription offline', 'danger');
  else if (!s.ffmpeg_present) setHeadLine(line, 'Audio conversion missing', 'danger');
  else if (!s.llm_hub.reachable) setHeadLine(line, 'Polish unavailable: hub not reachable', 'attention');
  else if (!s.whisper.running) setHeadLine(line, 'Local fallback down', 'attention');
  else if (state.staleShell) setHeadLine(line, 'New build, reload', 'attention');
  else setHeadLine(line, 'All services up');
}

// Settings › Services (#265): one avatar row per probe, its corner badge
// alive (success) or down (danger), the value in plain words. Unknown
// (the poll failed) shows no badge and says so.
function renderServices(s) {
  const rows = s ? [
    ['svcTranscription', s.llm_hub.reachable || s.whisper.running,
      s.llm_hub.reachable ? 'Ready' : s.whisper.running ? 'On the local fallback' : 'Down'],
    ['svcFallback', s.whisper.running, s.whisper.running ? 'Ready' : 'Not running'],
    ['svcPolish', s.llm_hub.reachable, s.llm_hub.reachable ? 'Ready' : 'Not reachable'],
    ['svcAudio', s.ffmpeg_present, s.ffmpeg_present ? 'Ready' : 'Missing'],
  ] : ['svcTranscription', 'svcFallback', 'svcPolish', 'svcAudio'].map((id) => [id, null, 'Unknown']);
  for (const [id, alive, value] of rows) {
    const avatar = document.getElementById(id);
    if (alive === null) delete avatar.dataset.badge;
    else avatar.dataset.badge = alive ? 'alive' : 'down';
    const v = document.getElementById(id + 'Value');
    v.textContent = value;
    if (alive === false) v.dataset.tone = 'danger';
    else delete v.dataset.tone;
  }
}

export async function refreshStatus() {
  try {
    const r = await authFetch('/api/status');
    if (!r.ok) throw new Error(`/api/status returned ${r.status}`);
    const s = await r.json();
    state.services = s;
    state.servicesFailed = false;
  } catch (err) {
    console.warn('status poll failed:', err);
    state.services = null;
    state.servicesFailed = true;
  }
  renderServices(state.services);
  renderSettingsHeadLine();
  renderRecordHeadLine();
  renderTextCard();
}
