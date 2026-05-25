/* Config + settings — loads /api/config, renders the Settings panel
 * dropdowns, persists changes, and polls /api/status.
 */

'use strict';

import { els, state } from './state.js';
import { authFetch, promptForPassword } from './api.js';
import { capitalize, showToast } from './ui.js';

export async function loadConfig() {
  // Single attempt first so we can detect 401 and prompt for the
  // password instead of silently retrying with the same stale state.
  const r = await authFetch('/api/config');
  if (r.status === 401) {
    const ok = await promptForPassword();
    if (!ok) throw new Error('login cancelled');
    return loadConfig();
  }
  if (!r.ok) throw new Error(`/api/config → ${r.status}`);
  state.config = await r.json();
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
  };
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
  els.forceBuiltinMic.checked = !!state.config.force_builtin_mic_default;
  els.retentionDays.value = state.config.history_retention_days;
  if (els.vadAutoStopToggle) {
    els.vadAutoStopToggle.checked = !!state.config.vad_auto_stop_enabled;
  }
  if (els.autoStopSilenceMs) {
    els.autoStopSilenceMs.value = state.config.auto_stop_silence_ms || 1500;
  }
}

export function refreshPromptPreview() {
  const prompts = (state.config && state.config.polish_prompts) || [];
  const id = els.polishStyle.value;
  const hit = prompts.find(p => p.id === id);
  els.polishPromptPreview.value = hit ? hit.system : '';
}

export async function refreshStatus() {
  try {
    const r = await authFetch('/api/status');
    if (!r.ok) return;
    const s = await r.json();
    const bits = [];
    bits.push(s.whisper.running ? '🟢 whisper' : '🔴 whisper');
    bits.push(s.translate && s.translate.reachable ? '🟢 translate' : '🔴 translate');
    bits.push(s.llm_hub.reachable ? '🟢 hub' : '🔴 hub');
    bits.push(s.ffmpeg_present ? '🟢 ffmpeg' : '🔴 ffmpeg');
    els.statusReadout.textContent = bits.join('   ');
    els.polishBtn.disabled = !s.llm_hub.reachable || !state.transcript;
    paintHealthDot(els.dotWhisper, !!s.whisper.running,
      'Whisper server', s.whisper.base_url);
    paintHealthDot(els.dotTranslate, !!(s.translate && s.translate.reachable),
      'Translate server', s.translate && s.translate.base_url);
  } catch (err) { /* swallow */ }
}

function paintHealthDot(dot, up, label, baseUrl) {
  if (!dot) return;
  dot.classList.remove('health-dot--up', 'health-dot--down', 'health-dot--unknown');
  dot.classList.add(up ? 'health-dot--up' : 'health-dot--down');
  const where = baseUrl ? ` (${baseUrl})` : '';
  dot.title = `${label}${where} — ${up ? 'up' : 'down'}`;
}

export async function onSaveSettings() {
  const patch = {
    polish_model_default: els.polishModel.value,
    polish_prompt_default: els.polishStyle.value,
    force_builtin_mic_default: els.forceBuiltinMic.checked,
    preferred_mic_id: els.micSelect.value || null,
    history_retention_days: parseInt(els.retentionDays.value, 10) || 30,
    vad_auto_stop_enabled: !!(els.vadAutoStopToggle && els.vadAutoStopToggle.checked),
    auto_stop_silence_ms: parseInt((els.autoStopSilenceMs && els.autoStopSilenceMs.value) || '1500', 10),
  };
  try {
    const r = await authFetch('/api/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(patch),
    });
    if (!r.ok) throw new Error(await r.text());
    await loadConfig();
    showToast('Settings saved', 'success');
  } catch (err) {
    showToast('Save failed: ' + err.message, 'error');
  }
}
