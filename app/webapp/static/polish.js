/* Polish flow + the Text card actions that share its session model:
 * Polish (record-or-paste → LLM), Save (paste → history), Clear.
 */

'use strict';

import { els, state } from './state.js';
import { authFetch, readErrorMessage } from './api.js';
import {
  renderPolished, renderTextCard, renderTranscript, showToast, truncate, tryAutoCopy,
} from './ui.js';
import { refreshHistory } from './history.js';
import { cleanupIncognitoSession, hideResumeButton } from './recorder.js';
import { closePartialStream } from './partials.js';

export async function onPolish() {
  if (!state.transcript) return;
  const model = els.polishModel.value;
  const promptId = els.polishStyle.value || undefined;
  const styleOpt = els.polishStyle.selectedOptions[0];
  state.polishing = true;
  renderTextCard();
  els.recordStatus.textContent = `LLM hub: ${model} · polishing…`;
  const t0 = Date.now();
  try {
    let r;
    if (state.sessionId) {
      // Send the (possibly edited) transcript so the archive matches
      // what's on screen, then polish runs on it.
      r = await authFetch(`/api/sessions/${state.sessionId}/polish`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          model,
          prompt_id: promptId,
          transcript: state.transcript,
        }),
      });
    } else {
      // No recording yet — paste-and-polish flow. Backend creates a
      // text-only session so it shows up in History.
      r = await authFetch('/api/polish-text', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          text: state.transcript,
          model,
          prompt_id: promptId,
          language: els.languageSelect.value,
        }),
      });
    }
    if (!r.ok) throw new Error(await readErrorMessage(r));
    const data = await r.json();
    if (data.session_id) state.sessionId = data.session_id;
    const ms = Date.now() - t0;
    renderPolished(data.polished || '', styleOpt ? styleOpt.textContent : '');
    if (state.polished) await tryAutoCopy(state.polished, els.copyText);
    els.recordStatus.textContent =
      `Polished in ${(ms / 1000).toFixed(1)} s — tap Copy`;
    showToast('Polish done');
    refreshHistory();
  } catch (err) {
    els.recordStatus.textContent = 'Polish failed — see toast';
    showToast('Polish failed: ' + truncate(err.message, 80), 'error');
  } finally {
    state.polishing = false;
    renderTextCard();
  }
}

// Clear (formerly Reset): the text, the session and any lingering incognito
// take, so the next record starts fresh.
export function onReset() {
  // A tap mid-recording/upload/transcribe would null state.sessionId (and,
  // in incognito, DELETE the in-flight session) out from under the still-
  // running MediaRecorder — every subsequent chunk then 404s against
  // "null" and the eventual Stop loses the take.
  // Reset is only meaningful once a take has settled back to idle.
  if (state.mode !== 'idle') return;
  cleanupIncognitoSession();
  closePartialStream();
  hideResumeButton();
  state.forceAppend = false;
  state.sessionId = null;
  renderTranscript('');
  state.partialVersion = 0;
  state.partialBaseTranscript = '';
  els.recordStatus.textContent = 'Tap to start';
  els.levelFill.style.width = '0%';
}

export async function onSaveTranscript() {
  if (!state.transcript || state.sessionId) return;
  // innerHTML, not textContent — the idle label carries a leading <svg>
  // sprite icon (ui.js's flashCopied does the same for the same reason).
  const original = els.saveTranscript.innerHTML;
  els.saveTranscript.disabled = true;
  els.saveTranscript.innerHTML = '…';
  try {
    const r = await authFetch('/api/save-text', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        text: state.transcript,
        language: els.languageSelect.value,
      }),
    });
    if (!r.ok) throw new Error(await readErrorMessage(r));
    const data = await r.json();
    if (data.session_id) state.sessionId = data.session_id;
    renderTextCard();
    els.recordStatus.textContent = 'Saved to history — ready to polish or copy';
    showToast('Saved to history');
    refreshHistory();
  } catch (err) {
    showToast('Save failed: ' + truncate(err.message || String(err), 80), 'error');
    els.saveTranscript.disabled = false;
  } finally {
    els.saveTranscript.innerHTML = original;
  }
}
