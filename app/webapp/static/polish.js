/* Polish flow + the text-entry actions that share its session model:
 * Polish (record-or-paste → LLM), Save (paste → history), Reset.
 */

'use strict';

import { els, state } from './state.js';
import { authFetch, readErrorMessage } from './api.js';
import { showToast, truncate, tryAutoCopy } from './ui.js';
import { refreshHistory } from './history.js';
import { cleanupIncognitoSession, closePartialStream, hideResumeButton } from './recorder.js';

export async function onPolish() {
  if (!state.transcript) return;
  const model = els.polishModel.value;
  const promptId = els.polishStyle.value || undefined;
  els.polishBtn.disabled = true;
  els.polishBtn.textContent = '…';
  els.recordStatus.textContent = `LLM hub → ${model} · polishing…`;
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
    state.polished = data.polished || '';
    els.polished.value = state.polished;
    els.copyPolished.disabled = !state.polished;
    const polishedForCopy = els.polished.value;
    if (polishedForCopy) await tryAutoCopy(polishedForCopy, els.copyPolished);
    els.recordStatus.textContent =
      `Polished in ${(ms / 1000).toFixed(1)} s — tap Copy`;
    showToast('Polish done', 'success');
    refreshHistory();
  } catch (err) {
    els.recordStatus.textContent = 'Polish failed — see toast';
    showToast('Polish failed: ' + truncate(err.message, 80), 'error');
  } finally {
    els.polishBtn.disabled = false;
    els.polishBtn.textContent = 'Go';
  }
}

export function onReset() {
  cleanupIncognitoSession();
  closePartialStream();
  hideResumeButton();
  state.forceAppend = false;
  state.sessionId = null;
  state.transcript = '';
  state.polished = '';
  state.partialVersion = 0;
  state.partialBaseTranscript = '';
  els.transcript.value = '';
  els.polished.value = '';
  els.copyTranscript.disabled = true;
  els.copyPolished.disabled = true;
  els.polishBtn.disabled = true;
  els.saveTranscript.disabled = true;
  els.recordStatus.textContent = 'Tap to start';
  els.levelFill.style.width = '0%';
}

export async function onSaveTranscript() {
  if (!state.transcript || state.sessionId) return;
  const original = els.saveTranscript.textContent;
  els.saveTranscript.disabled = true;
  els.saveTranscript.textContent = '…';
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
    els.recordStatus.textContent = 'Saved to history — ready to polish or copy';
    showToast('Saved to history', 'success');
    refreshHistory();
  } catch (err) {
    showToast('Save failed: ' + truncate(err.message || String(err), 80), 'error');
    els.saveTranscript.disabled = false;
  } finally {
    els.saveTranscript.textContent = original;
  }
}
