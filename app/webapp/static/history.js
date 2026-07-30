/* History list — paginated take list, per-item copy / redo / delete,
 * multi-take copy-selection, and the clean-all action.
 */

'use strict';

import { emptyStateEl } from './_vendored/empty-state/empty-state.js';
import { icon } from './_vendored/icons/icons.js';
import { els, state } from './state.js';
import { authFetch } from './api.js';
import { copyText, flashDanger, renderTranscript, showToast } from './ui.js';
import { mergeForAppend } from './recorder.js';

const HISTORY_PAGE_SIZE = 10;

export async function refreshHistory() {
  // Reset to page 1.
  els.historyList.innerHTML = '';
  await fetchHistoryPage(0);
  // After a fresh refresh, the newest take is at the top — default-check
  // it so a single click on "Copy selection" copies the latest, while the
  // user can extend the selection up the list to grab more takes.
  const firstCheckbox = els.historyList.querySelector('input.select-checkbox');
  if (firstCheckbox) firstCheckbox.checked = true;
  refreshAnalytics();
}

// Today's take count / words-per-minute / estimated time saved — a compact
// line above the History actions row. Refreshed alongside History itself
// since both change together (a new take affects both). See issue #95.
async function refreshAnalytics() {
  if (!els.analyticsSummary) return;
  try {
    const r = await authFetch('/api/analytics/summary');
    if (!r.ok) throw new Error(String(r.status));
    const s = await r.json();
    // The numbers come from our own API (counts/wpm), so innerHTML with the
    // sprite icon prefix is safe here.
    els.analyticsSummary.innerHTML =
      icon('activity') + ' ' + formatAnalyticsSummary(s);
  } catch (err) {
    els.analyticsSummary.textContent = '';
  }
}

function formatAnalyticsSummary(s) {
  if (!s || !s.take_count) return 'No takes yet today';
  const parts = [`${s.take_count} take${s.take_count === 1 ? '' : 's'}`];
  if (typeof s.words_per_minute === 'number') parts.push(`${s.words_per_minute} wpm`);
  if (typeof s.time_saved_minutes === 'number') parts.push(`~${s.time_saved_minutes} min saved`);
  return `Today: ${parts.join(' · ')}`;
}

export async function loadMoreHistory() {
  await fetchHistoryPage(els.historyList.children.length);
}

async function fetchHistoryPage(offset) {
  try {
    els.loadMoreHistory.disabled = true;
    const r = await authFetch(
      `/api/sessions?limit=${HISTORY_PAGE_SIZE}&offset=${offset}`
    );
    const data = await r.json();
    const list = data.sessions || [];
    const total = typeof data.total === 'number' ? data.total : list.length;
    for (const s of list) {
      els.historyList.appendChild(renderHistoryItem(s));
    }
    const shown = els.historyList.children.length;
    els.historyCount.textContent = total > shown ? `${shown}/${total}` : `${shown}`;
    // `has_more` is the authoritative, incognito-aware pagination signal
    // (the server derives it from a one-row-over probe); `total` is a
    // cheap folder count that may run slightly high, so don't gate the
    // button on `shown >= total`. See #139.
    els.loadMoreHistory.hidden = !data.has_more;
    renderEmptyState(shown);
  } catch (err) { /* swallow */ }
  finally {
    els.loadMoreHistory.disabled = false;
  }
}

// The canonical fleet empty-state (vendored component) whenever the list can
// legitimately render zero takes — never a silent blank area.
function renderEmptyState(shown) {
  const existing = els.historyList.parentElement.querySelector('.empty-state');
  if (existing) existing.remove();
  if (shown === 0) {
    els.historyList.after(emptyStateEl('history', 'No takes yet — record something'));
  }
}

function renderHistoryItem(s) {
  const li = document.createElement('li');

  const selectLabel = document.createElement('label');
  selectLabel.className = 'select';
  selectLabel.title = 'Include this take in "Copy selection"';
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.className = 'select-checkbox';
  checkbox.dataset.sessionId = s.session_id;
  selectLabel.append(checkbox);

  const content = document.createElement('div');
  content.className = 'content';

  const when = document.createElement('div');
  when.className = 'when';
  when.textContent = s.created_at + (s.language ? ` · ${s.language}` : '');
  // Attribution badge — who created the take. "webapp" gets a muted
  // pill; an external consumer (e.g. "app-launcher") gets the accented
  // variant so externally-sourced takes stand out in History.
  if (s.source) {
    const badge = document.createElement('span');
    badge.className = 'source-badge' + (s.source === 'webapp' ? '' : ' external');
    badge.textContent = s.source;
    when.append(' ', badge);
  }
  const preview = document.createElement('div');
  preview.className = 'preview';
  preview.textContent = s.polished_preview || s.transcript_preview || '(no transcript)';
  const actions = document.createElement('div');
  actions.className = 'actions';

  const copyBtn = document.createElement('button');
  copyBtn.className = 'copy-btn';
  copyBtn.innerHTML = icon('clipboard') + ' Copy';
  copyBtn.addEventListener('click', async () => {
    // The list payload only carries 200-char previews; fetch the full
    // text on demand so what the user pastes matches what's on disk.
    try {
      const r = await authFetch(`/api/sessions/${s.session_id}/text`);
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      const full = data.polished || data.transcript || '';
      await copyText(full, copyBtn);
    } catch (err) {
      showToast('Copy failed: ' + (err.message || err), 'error');
    }
  });

  const reBtn = document.createElement('button');
  reBtn.className = 'button-tint compact';
  reBtn.innerHTML = icon('rotate-cw') + ' Redo';
  reBtn.addEventListener('click', () => retranscribe(s.session_id));

  const delBtn = document.createElement('button');
  delBtn.className = 'button-tint compact';
  delBtn.innerHTML = icon('trash-2') + ' Delete';
  delBtn.addEventListener('click', async () => {
    try {
      const r = await authFetch(`/api/sessions/${s.session_id}`, {
        method: 'DELETE',
      });
      if (!r.ok) throw new Error(await r.text());
      // Refresh the whole list so counts and pagination stay correct.
      refreshHistory();
    } catch (err) {
      showToast('Delete failed: ' + (err.message || err), 'error');
    }
  });

  actions.append(copyBtn, reBtn, delBtn);
  content.append(when, preview, actions);
  li.append(selectLabel, content);
  return li;
}

async function retranscribe(id) {
  showToast('Re-transcribing…', 'success');
  try {
    const r = await authFetch(`/api/sessions/${id}/retranscribe`, { method: 'POST' });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    state.sessionId = id;
    renderTranscript(mergeForAppend(state.transcript, data.transcript || ''));
    refreshHistory();
    showToast('Done', 'success');
  } catch (err) {
    showToast('Re-transcribe failed', 'error');
  }
}

export async function onCleanAll() {
  if (!confirm('Delete every saved recording and transcript?')) return;
  try {
    const r = await authFetch('/api/sessions', { method: 'DELETE' });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    showToast(`Removed ${data.removed}`, 'success');
    flashDanger(els.cleanAll);
    refreshHistory();
  } catch (err) {
    showToast('Clean failed', 'error');
  }
}

// Multi-take copy. The newest take is auto-checked on every refresh, so
// a one-click flow ("just copy the last one") still works. Tick more
// boxes to bundle older takes — the result is concatenated in
// chronological order (oldest → newest of the selection) with a
// blank-line separator so it's obvious where one take ends and the
// next begins.
export async function onCopySelection() {
  const btn = els.copySelection;
  // Captured up front (icon + text) so the "…" busy state can restore it.
  const restoreLabel = btn.innerHTML;
  const checked = Array.from(
    els.historyList.querySelectorAll('input.select-checkbox:checked')
  );
  if (!checked.length) {
    showToast('Tick at least one take first', 'error');
    return;
  }
  btn.disabled = true;
  btn.textContent = '…';
  let copyDone = false;
  try {
    // The list is rendered newest-first; reverse selection to get the
    // chronological order the user reads: oldest piece first, latest last.
    const idsChronOrder = checked.map(c => c.dataset.sessionId).reverse();
    const parts = [];
    for (const id of idsChronOrder) {
      const r = await authFetch(`/api/sessions/${id}/text`);
      if (!r.ok) continue;
      const data = await r.json();
      const text = (data.polished || data.transcript || '').trim();
      if (text) parts.push(text);
    }
    if (!parts.length) {
      showToast('Selected takes have no text', 'error');
      return;
    }
    const combined = parts.join('\n\n');
    // Restore the label before the green flash so flashCopied captures
    // the idle icon+text as the original, not "…".
    btn.innerHTML = restoreLabel;
    await copyText(combined, btn);
    copyDone = true;
  } catch (err) {
    showToast('Copy selected failed: ' + (err.message || err), 'error');
  } finally {
    btn.disabled = false;
    if (!copyDone) btn.innerHTML = restoreLabel;
  }
}
