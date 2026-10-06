/* History list — paginated take list, per-item copy / redo / delete,
 * multi-take copy-selection, and the clean-all action.
 */

'use strict';

import { emptyStateEl } from './_vendored/empty-state/empty-state.js';
import { icon } from './_vendored/icons/icons.js';
import { els, state } from './state.js';
import { authFetch } from './api.js';
import { copyText, flashDanger, formatWhen, renderTranscript, showToast } from './ui.js';
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

// One take is one action-row (design.md): tap the body to copy it, one
// overflow control for everything else (Redo, Delete), and one leading tick
// for the multi-take "Copy selected". No per-row button strip.
function renderHistoryItem(s) {
  const li = document.createElement('li');

  const selectLabel = document.createElement('label');
  selectLabel.className = 'select';
  selectLabel.title = 'Include this take in "Copy selected"';
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.className = 'select-checkbox';
  checkbox.dataset.sessionId = s.session_id;
  checkbox.setAttribute('aria-label', 'Include in Copy selected');
  selectLabel.append(checkbox);

  const main = document.createElement('button');
  main.type = 'button';
  main.className = 'history-main';
  main.title = 'Copy this take';

  const preview = document.createElement('span');
  preview.className = 'preview';
  preview.textContent = s.polished_preview || s.transcript_preview || '(no transcript)';

  const meta = document.createElement('span');
  meta.className = 'meta';
  meta.append(formatWhen(s.created_at) + (s.language ? ` · ${s.language}` : ''));
  // Attribution badge — who created the take. One muted pill for every
  // source (issue #190): the accent is reserved for the contextually-next
  // action, so a badge never competes with anything.
  if (s.source) {
    const badge = document.createElement('span');
    badge.className = 'source-badge';
    badge.textContent = s.source;
    meta.append(' ', badge);
  }
  // Where the "Copied" confirmation flashes (ui.js flashCopied restores the
  // empty idle state afterwards).
  const copiedFlag = document.createElement('span');
  copiedFlag.className = 'history-copied';
  copiedFlag.setAttribute('role', 'status');
  meta.append(copiedFlag);

  main.append(preview, meta);
  main.addEventListener('click', () => copyTake(s.session_id, copiedFlag));

  const more = document.createElement('button');
  more.type = 'button';
  more.className = 'icon-button history-more';
  more.setAttribute('aria-label', 'More actions');
  more.innerHTML = icon('ellipsis-vertical');
  more.addEventListener('click', () => openTakeMenu(s));

  li.append(selectLabel, main, more);
  return li;
}

async function copyTake(id, flag) {
  // The list payload only carries 200-char previews; fetch the full
  // text on demand so what the user pastes matches what's on disk.
  try {
    const r = await authFetch(`/api/sessions/${id}/text`);
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    const full = data.polished || data.transcript || '';
    if (!full) {
      showToast('This take has no text', 'error');
      return;
    }
    await copyText(full, flag);
  } catch (err) {
    showToast('Copy failed: ' + (err.message || err), 'error');
  }
}

async function deleteTake(id) {
  try {
    const r = await authFetch(`/api/sessions/${id}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(await r.text());
    // Refresh the whole list so counts and pagination stay correct.
    refreshHistory();
  } catch (err) {
    showToast('Delete failed: ' + (err.message || err), 'error');
  }
}

// The overflow menu is one shared <dialog>; it remembers which take opened it.
let menuTake = null;

function openTakeMenu(s) {
  menuTake = s;
  els.takeMenuWhen.textContent =
    formatWhen(s.created_at) + (s.language ? ` · ${s.language}` : '');
  els.takeMenu.showModal();
}

export function initTakeMenu() {
  const dlg = els.takeMenu;
  dlg.querySelector('.detail-close').addEventListener('click', () => dlg.close());
  // A tap on the backdrop (the dialog element itself, outside its card) dismisses.
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
  els.takeRedo.addEventListener('click', () => {
    const id = menuTake && menuTake.session_id;
    dlg.close();
    if (id) retranscribe(id);
  });
  els.takeDelete.addEventListener('click', async () => {
    const id = menuTake && menuTake.session_id;
    if (!id || !confirm('Delete this take?')) return;
    dlg.close();
    await deleteTake(id);
  });
}

async function retranscribe(id) {
  showToast('Re-transcribing…');
  try {
    const r = await authFetch(`/api/sessions/${id}/retranscribe`, { method: 'POST' });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    state.sessionId = id;
    renderTranscript(mergeForAppend(state.transcript, data.transcript || ''));
    refreshHistory();
    showToast('Done');
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
    showToast(`Removed ${data.removed}`);
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
