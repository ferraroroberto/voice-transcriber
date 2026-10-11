/* Config + settings — loads /api/config, renders the Settings panel
 * dropdowns, persists changes, polls /api/status, and draws the Record and
 * Settings header lines from them.
 */

'use strict';

import { setSwitch } from './_vendored/switch/switch.js';
import { els, state } from './state.js';
import { authFetch, promptForPassword } from './api.js';
import { capitalize, isOn, setHeadLine, showToast } from './ui.js';
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
  setSwitch(els.forceBuiltinMic, !!state.config.force_builtin_mic_default);
  els.retentionDays.value = state.config.history_retention_days;
  if (els.vadAutoStopToggle) {
    setSwitch(els.vadAutoStopToggle, !!state.config.vad_auto_stop_enabled);
  }
  if (els.autoStopSilenceMs) {
    els.autoStopSilenceMs.value = state.config.auto_stop_silence_ms || 1500;
  }
  if (els.gainBoostToggle) {
    setSwitch(els.gainBoostToggle, !!state.config.gain_boost_enabled);
  }
  if (els.gainBoostDb) {
    els.gainBoostDb.value = state.config.gain_boost_db ?? 12;
  }
  renderRecordHeadLine();
  renderHistoryHeadLine();
}

export function refreshPromptPreview() {
  const prompts = (state.config && state.config.polish_prompts) || [];
  const id = els.polishStyle.value;
  const hit = prompts.find(p => p.id === id);
  els.polishPromptPreview.value = hit ? hit.system : '';
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
    setHeadLine(els.recordHeadLine, "Incognito: this take won't be saved", 'neutral');
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
function renderSettingsHeadLine(s) {
  const line = els.settingsHeadLine;
  if (!s) setHeadLine(line, "Couldn't check services", 'attention');
  else if (!s.whisper.running && !s.llm_hub.reachable) setHeadLine(line, 'Transcription offline', 'danger');
  else if (!s.ffmpeg_present) setHeadLine(line, 'ffmpeg missing', 'danger');
  else if (!s.llm_hub.reachable) setHeadLine(line, 'Polish unavailable: hub not reachable', 'attention');
  else if (!s.whisper.running) setHeadLine(line, 'Whisper server down', 'attention');
  else setHeadLine(line, 'All services up');
}

export async function refreshStatus() {
  try {
    const r = await authFetch('/api/status');
    if (!r.ok) throw new Error(`/api/status returned ${r.status}`);
    const s = await r.json();
    state.services = s;
    renderSettingsHeadLine(s);
    renderRecordHeadLine();
    // Tokenized status dots (success/danger), not emoji — labels are the
    // fixed strings below, so innerHTML is safe.
    const dot = (ok, label) =>
      `<span class="status-dot ${ok ? 'on' : 'off'}"></span> ${label}`;
    els.statusReadout.innerHTML = [
      dot(s.whisper.running, 'whisper'),
      dot(s.llm_hub.reachable, 'hub'),
      dot(s.ffmpeg_present, 'ffmpeg'),
    ].join('&ensp;');
    els.polishBtn.disabled = !s.llm_hub.reachable || !state.transcript;
  } catch (err) {
    console.warn('status poll failed:', err);
    state.services = null;
    renderSettingsHeadLine(null);
    renderRecordHeadLine();
  }
}

export async function onSaveSettings() {
  const patch = {
    polish_model_default: els.polishModel.value,
    polish_prompt_default: els.polishStyle.value,
    force_builtin_mic_default: isOn(els.forceBuiltinMic),
    preferred_mic_id: els.micSelect.value || null,
    history_retention_days: parseInt(els.retentionDays.value, 10) || 30,
    vad_auto_stop_enabled: isOn(els.vadAutoStopToggle),
    auto_stop_silence_ms: parseInt((els.autoStopSilenceMs && els.autoStopSilenceMs.value) || '1500', 10),
    gain_boost_enabled: isOn(els.gainBoostToggle),
    gain_boost_db: parseFloat((els.gainBoostDb && els.gainBoostDb.value) || '12'),
  };
  try {
    const r = await authFetch('/api/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(patch),
    });
    if (!r.ok) throw new Error(await r.text());
    await loadConfig();
    showToast('Settings saved');
  } catch (err) {
    showToast('Save failed: ' + err.message, 'error');
  }
}
