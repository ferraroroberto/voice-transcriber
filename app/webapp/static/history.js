/* History — the Latest take card, the day-grouped take list (paged),
 * Select mode for a multi-take copy, the take sheet (full text, Copy, Redo,
 * facts, Delete) and Settings' delete-all action.
 *
 * One in-memory list (state.historyTakes, newest first) is the source of
 * truth: every page load, select toggle or refresh re-renders the card and
 * the groups from it, so grouping across pages and Select mode never drift
 * from what was fetched (#264).
 */

'use strict';

import { emptyStateEl } from './_vendored/empty-state/empty-state.js';
import { icon } from './_vendored/icons/icons.js';
import { els, state } from './state.js';
import { authFetch } from './api.js';
import {
  capitalize, copyText, formatWhen, renderTranscript, setHeadLine, showToast,
} from './ui.js';
import { mergeForAppend } from './recorder.js';

const HISTORY_PAGE_SIZE = 10;

// The wide layout (#266, design.md layout.wide): the same condition as the
// vendored nav's left rail. There the take sheet's card is shown in place,
// beside the list, instead of in a modal.
const wideLayout = window.matchMedia('(min-width: 1100px) and (pointer: fine)');

export async function refreshHistory() {
  // Back to page 1; a refresh also leaves Select mode.
  state.historySelecting = false;
  state.historySelected.clear();
  await fetchHistoryPage(0, true);
  refreshAnalytics();
}

// Today's take count / words-per-minute / estimated time saved, the foot of
// the Latest take card. Refreshed alongside History itself since both
// change together (a new take affects both). See issue #95.
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
  await fetchHistoryPage(state.historyTakes.length, false);
}

async function fetchHistoryPage(offset, replace) {
  try {
    els.loadMoreHistory.disabled = true;
    const r = await authFetch(
      `/api/sessions?limit=${HISTORY_PAGE_SIZE}&offset=${offset}`
    );
    if (!r.ok) throw new Error(`/api/sessions returned ${r.status}`);
    const data = await r.json();
    const list = data.sessions || [];
    state.historyTakes = replace ? list : state.historyTakes.concat(list);
    state.historyTotal = typeof data.total === 'number' ? data.total : state.historyTakes.length;
    // `has_more` is the authoritative, incognito-aware pagination signal
    // (the server derives it from a one-row-over probe); `total` is a
    // cheap folder count that may run slightly high, so don't gate the
    // button on a shown/total comparison. See #139.
    els.loadMoreHistory.hidden = !data.has_more;
    renderHistory();
  } catch (err) {
    console.warn('history page failed:', err);
    setHeadLine(els.historyHeadLine, "Couldn't load history", 'danger');
  }
  finally {
    els.loadMoreHistory.disabled = false;
  }
}

// History's header line (#261): "N takes · kept D days", counted from the
// same list `total` the pages draw from, with D the retention setting; "N
// selected" in Select mode. Also re-run when the config (and so the
// retention) reloads.
export function renderHistoryHeadLine() {
  if (state.historySelecting) {
    setHeadLine(els.historyHeadLine, `${state.historySelected.size} selected`);
    return;
  }
  const n = state.historyTotal;
  if (typeof n !== 'number') return;
  const days = state.config && state.config.history_retention_days;
  const takes = `${n} take${n === 1 ? '' : 's'}`;
  setHeadLine(els.historyHeadLine, days ? `${takes} · kept ${days} days` : takes);
}

// ------------------------------------------------------------ rendering

function renderHistory() {
  const takes = state.historyTakes;
  const selecting = state.historySelecting;
  els.historyGroups.classList.toggle('selecting', selecting);
  renderLatestCard(selecting ? null : takes[0]);
  // The newest take is the Latest card; in Select mode it is a row too.
  const rows = selecting ? takes : takes.slice(1);
  els.historyGroups.replaceChildren(...dayGroups(rows));
  renderEmptyState(takes.length);
  renderHistoryHeadLine();
  renderSelectBar();
  // Wide: the detail pane always shows a take — the one picked, or else the
  // newest — so the right column is never an empty hole.
  if (wideLayout.matches && !state.historySelecting) {
    const open = takes.find((t) => t.session_id === state.historyOpenId) || takes[0];
    if (open) openTakeSheet(open);
    else closeTakeSheet();
  }
}

// The canonical fleet empty-state (vendored component) whenever the list can
// legitimately render zero takes — never a silent blank area.
function renderEmptyState(count) {
  const existing = els.historyGroups.parentElement.querySelector(':scope > .empty-state');
  if (existing) existing.remove();
  if (count === 0) {
    els.historyGroups.after(emptyStateEl('history', 'No takes yet — record something'));
  }
}

// The Latest take card (#264): the newest take, whatever its source, with
// one-tap Copy — the owner's most common History action.
function renderLatestCard(s) {
  els.latestCard.hidden = !s;
  if (!s) return;
  els.latestWhen.textContent = formatWhen(s.created_at).replace(',', '');
  els.latestText.textContent = s.polished_preview || s.transcript_preview || '(no transcript)';
  els.latestMeta.textContent = '';
  els.latestMeta.append(...takeMeta(s));
  els.latestCopy.onclick = () => copyTake(s.session_id, els.latestCopy);
  els.latestMore.onclick = () => openTakeSheet(s);
}

const midnight = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

function dayLabel(d) {
  const days = Math.round((midnight(new Date()) - midnight(d)) / 86400000);
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  return d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' });
}

// Rows grouped under one overline per day, in list order. Built from the
// whole loaded list, so a page that continues a day extends its group.
function dayGroups(rows) {
  const groups = [];
  let current = null;
  for (const s of rows) {
    const d = new Date(s.created_at);
    const key = Number.isNaN(d.getTime()) ? 'unknown' : String(midnight(d));
    if (!current || current.key !== key) {
      current = { key, label: Number.isNaN(d.getTime()) ? 'Undated' : dayLabel(d), takes: [] };
      groups.push(current);
    }
    current.takes.push(s);
  }
  const out = [];
  groups.forEach((g, i) => {
    const head = document.createElement('div');
    head.className = 'day-head';
    const label = document.createElement('span');
    label.className = 'overline day-label';
    label.textContent = g.label;
    const n = document.createElement('span');
    n.className = 'overline day-count';
    n.textContent = String(g.takes.length);
    head.append(label, n);
    // Select mode is entered from the first group's head (not while in it:
    // the bottom bar's Cancel leaves).
    if (i === 0 && !state.historySelecting) {
      const sel = document.createElement('button');
      sel.type = 'button';
      sel.id = 'selectTakes';
      sel.className = 'day-select';
      sel.textContent = 'Select';
      sel.addEventListener('click', () => setSelecting(true));
      head.append(sel);
    }
    const card = document.createElement('section');
    card.className = 'card history-card';
    const ul = document.createElement('ul');
    ul.className = 'history-list';
    ul.append(...g.takes.map(renderHistoryItem));
    card.append(ul);
    out.push(head, card);
  });
  return out;
}

// "webapp" is this app; "api" is the server's generic fallback, which names
// the transport rather than the sending app, so a row hides it (the take
// sheet shows it verbatim). Any other value is the caller's own label.
function rowSourceLabel(src) {
  if (src === 'webapp') return 'This app';
  if (!src || src === 'api') return '';
  return src;
}

function languageIso(lang) {
  if (!lang) return '';
  const wanted = String(lang).toLowerCase();
  const hit = ((state.config && state.config.languages) || []).find(
    (l) => typeof l === 'object' && (l.iso === wanted || String(l.label).toLowerCase() === wanted)
  );
  return hit ? hit.iso : '';
}

// A row's meta: "61 words · 0:27 · This app" as plain text, then a neutral
// language chip only when the take is not in the default language.
function takeMeta(s) {
  const parts = [];
  if (typeof s.word_count === 'number') parts.push(`${s.word_count} word${s.word_count === 1 ? '' : 's'}`);
  if (typeof s.duration_seconds === 'number') parts.push(formatClock(s.duration_seconds));
  const src = rowSourceLabel(s.source);
  if (src) parts.push(src);
  const nodes = [];
  const text = document.createElement('span');
  text.className = 'meta-text';
  text.textContent = parts.join(' · ');
  nodes.push(text);
  const iso = languageIso(s.language);
  const defaultIso = state.config && state.config.language_default;
  if (iso && defaultIso && iso !== String(defaultIso).toLowerCase()) {
    const chip = document.createElement('span');
    chip.className = 'lang-chip';
    chip.textContent = iso.toUpperCase();
    chip.title = languageLabel(s.language);
    nodes.push(chip);
  }
  return nodes;
}

function formatTime(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// One take is one action-row (design.md): the time as its lead, one line
// of text, plain meta, and one trailing kebab to the take sheet. Tapping the
// row copies the take; in Select mode it ticks it instead, and the lead
// shows the tick.
function renderHistoryItem(s) {
  const li = document.createElement('li');
  li.className = 'take-row';
  li.dataset.sessionId = s.session_id;
  const picked = state.historySelected.has(s.session_id);
  li.classList.toggle('picked', picked);
  li.classList.toggle('open', s.session_id === state.historyOpenId);

  const lead = document.createElement('span');
  lead.className = 'take-lead';
  const time = document.createElement('span');
  time.className = 'take-time';
  time.textContent = formatTime(s.created_at);
  const tick = document.createElement('span');
  tick.className = 'take-tick';
  tick.setAttribute('aria-hidden', 'true');
  lead.append(time, tick);

  const main = document.createElement('button');
  main.type = 'button';
  main.className = 'history-main';
  main.dataset.sessionId = s.session_id;
  if (state.historySelecting) {
    main.setAttribute('aria-pressed', String(picked));
    main.title = 'Select this take';
  } else {
    main.title = 'Copy this take';
  }

  const preview = document.createElement('span');
  preview.className = 'preview';
  preview.textContent = s.polished_preview || s.transcript_preview || '(no transcript)';

  const meta = document.createElement('span');
  meta.className = 'meta';
  if (state.historySelecting) {
    const when = document.createElement('span');
    when.className = 'meta-time';
    when.textContent = formatTime(s.created_at) + ' · ';
    meta.append(when);
  }
  meta.append(...takeMeta(s));
  // Where the "Copied" confirmation flashes (ui.js flashCopied restores the
  // empty idle state afterwards).
  const copiedFlag = document.createElement('span');
  copiedFlag.className = 'history-copied';
  copiedFlag.setAttribute('role', 'status');
  meta.append(copiedFlag);

  main.append(preview, meta);
  main.addEventListener('click', () => {
    if (state.historySelecting) toggleSelected(s.session_id);
    else copyTake(s.session_id, copiedFlag);
  });
  li.append(lead, main);

  if (!state.historySelecting) {
    const more = document.createElement('button');
    more.type = 'button';
    more.className = 'icon-button history-more';
    more.setAttribute('aria-label', 'More actions');
    more.innerHTML = icon('ellipsis-vertical');
    more.addEventListener('click', () => openTakeSheet(s));
    li.append(more);
  }
  return li;
}

// ------------------------------------------------------------ Select mode

function setSelecting(on) {
  state.historySelecting = on;
  state.historySelected.clear();
  renderHistory();
}

function toggleSelected(id) {
  if (state.historySelected.has(id)) state.historySelected.delete(id);
  else state.historySelected.add(id);
  renderHistory();
}

function renderSelectBar() {
  els.selectBar.hidden = !state.historySelecting;
  const n = state.historySelected.size;
  els.selectCount.textContent = `${n} take${n === 1 ? '' : 's'} · oldest first`;
  els.copySelection.disabled = n === 0;
}

export function initSelectMode() {
  els.selectCancel.addEventListener('click', () => setSelecting(false));
  els.copySelection.addEventListener('click', onCopySelection);
}

// Full text of a take. The list payload only carries 200-char previews, so
// fetch on demand: what the user pastes matches what's on disk.
async function fetchTakeText(id) {
  const r = await authFetch(`/api/sessions/${id}/text`);
  if (!r.ok) throw new Error(await r.text());
  const data = await r.json();
  return data.polished || data.transcript || '';
}

async function copyTake(id, flag) {
  try {
    const full = await fetchTakeText(id);
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

// ------------------------------------------------------------ take sheet

const NOT_RECORDED = 'Not recorded';
// How long the armed "Tap again to delete" waits for the second tap.
const DELETE_ARM_MS = 4000;

// The sheet is one shared <dialog>; it remembers which take opened it and the
// full text once fetched (Copy reads it, so it never copies the preview).
let sheetTake = null;
let sheetText = '';
let deleteArmTimer = null;

// The sheet shows the stored source verbatim, "api" included (#264); only
// this app's own "webapp" reads as a name.
function sourceLabel(src) {
  if (!src) return NOT_RECORDED;
  return src === 'webapp' ? 'This app' : src;
}

function languageLabel(lang) {
  if (!lang) return NOT_RECORDED;
  const wanted = String(lang).toLowerCase();
  const hit = ((state.config && state.config.languages) || []).find(
    (l) => typeof l === 'object' && (l.iso === wanted || String(l.label).toLowerCase() === wanted)
  );
  return hit ? hit.label : capitalize(String(lang));
}

function formatClock(sec) {
  const total = Math.round(sec);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

// "61 words · 0:27"; the word count joins once the full text has landed.
function lengthLabel(s, text) {
  const parts = [];
  if (text) {
    const words = text.trim().split(/\s+/).filter(Boolean).length;
    parts.push(`${words} word${words === 1 ? '' : 's'}`);
  }
  if (typeof s.duration_seconds === 'number') parts.push(formatClock(s.duration_seconds));
  return parts.length ? parts.join(' · ') : NOT_RECORDED;
}

function servedByLabel(s) {
  if (!s.served_model) return NOT_RECORDED;
  return s.served_host ? `${s.served_model} · ${s.served_host}` : s.served_model;
}

function disarmDelete() {
  clearTimeout(deleteArmTimer);
  deleteArmTimer = null;
  delete els.takeDelete.dataset.armed;
  els.takeDelete.querySelector('.take-delete-label').textContent = 'Delete take';
}

// Mark the row whose take the wide layout's detail pane shows.
function markOpenRow() {
  for (const row of els.historyGroups.querySelectorAll('.take-row')) {
    row.classList.toggle('open', row.dataset.sessionId === state.historyOpenId);
  }
}

// Put the take card where the layout wants it: in the detail pane beside the
// list on a wide window, in the modal sheet otherwise. Moving the one card
// keeps one renderer and one set of handlers for both (#266).
function placeTakeCard() {
  if (wideLayout.matches) {
    if (els.takeSheet.open) els.takeSheet.close();
    if (els.takeCard.parentElement !== els.takeDetail) els.takeDetail.append(els.takeCard);
  } else {
    if (els.takeCard.parentElement !== els.takeSheet) els.takeSheet.append(els.takeCard);
    els.takeDetail.hidden = true;
    state.historyOpenId = null;
    markOpenRow();
  }
}

function closeTakeSheet() {
  if (wideLayout.matches) {
    els.takeDetail.hidden = true;
    state.historyOpenId = null;
    markOpenRow();
    disarmDelete();
    sheetTake = null;
  } else {
    els.takeSheet.close();
  }
}

async function openTakeSheet(s) {
  if (sheetTake && sheetTake.session_id === s.session_id && wideLayout.matches && !els.takeDetail.hidden) return;
  sheetTake = s;
  sheetText = '';
  disarmDelete();
  els.takeSheetTitle.textContent = formatWhen(s.created_at);
  // The preview fills the box at once; the full text replaces it on arrival.
  els.takeSheetText.textContent = s.polished_preview || s.transcript_preview || '';
  els.takeSheetText.scrollTop = 0;
  els.takeLength.textContent = lengthLabel(s, '');
  els.takeLanguage.textContent = languageLabel(s.language);
  els.takeSource.textContent = sourceLabel(s.source);
  els.takeServedBy.textContent = servedByLabel(s);
  if (wideLayout.matches) {
    state.historyOpenId = s.session_id;
    els.takeDetail.hidden = false;
    markOpenRow();
  } else {
    els.takeSheet.showModal();
  }
  try {
    const full = await fetchTakeText(s.session_id);
    if (sheetTake !== s) return;  // closed, or another take opened meanwhile
    sheetText = full;
    els.takeSheetText.textContent = full || '(no transcript)';
    els.takeLength.textContent = lengthLabel(s, full);
  } catch (err) {
    console.warn('take text failed:', err);
  }
}

export function initTakeSheet() {
  const dlg = els.takeSheet;
  placeTakeCard();
  wideLayout.addEventListener('change', () => {
    placeTakeCard();
    if (wideLayout.matches) renderHistory();
  });
  els.takeCard.querySelector('.detail-close').addEventListener('click', closeTakeSheet);
  // A tap on the backdrop (the dialog element itself, outside its card) dismisses.
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
  dlg.addEventListener('close', () => { disarmDelete(); sheetTake = null; });
  els.takeCopy.addEventListener('click', () => {
    if (!sheetText) {
      showToast(sheetTake ? 'Still loading this take' : 'This take has no text', 'error');
      return;
    }
    copyText(sheetText, els.takeCopy);
  });
  els.takeRedo.addEventListener('click', () => {
    const id = sheetTake && sheetTake.session_id;
    closeTakeSheet();
    if (id) retranscribe(id);
  });
  // Two taps on the same row delete: the first arms it ("Tap again to
  // delete"), the second confirms. It disarms after a few seconds and
  // whenever the sheet closes. Replaces the native confirm() (fleet modal
  // contract: never the browser's own dialogs).
  els.takeDelete.addEventListener('click', async () => {
    const id = sheetTake && sheetTake.session_id;
    if (!id) return;
    if (!els.takeDelete.dataset.armed) {
      els.takeDelete.dataset.armed = '1';
      els.takeDelete.querySelector('.take-delete-label').textContent = 'Tap again to delete';
      deleteArmTimer = setTimeout(disarmDelete, DELETE_ARM_MS);
      return;
    }
    closeTakeSheet();
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

// Settings › History › Delete all takes (#265): confirmed in a sheet, never
// the browser's confirm().
async function deleteAllTakes() {
  try {
    const r = await authFetch('/api/sessions', { method: 'DELETE' });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    showToast(`Removed ${data.removed} take${data.removed === 1 ? '' : 's'}`);
    refreshHistory();
  } catch (err) {
    showToast("Couldn't delete the takes", 'error');
  }
}

export function initDeleteAll() {
  const dlg = els.deleteAllSheet;
  els.cleanAll.addEventListener('click', () => dlg.showModal());
  dlg.querySelector('.detail-close').addEventListener('click', () => dlg.close());
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
  els.confirmDeleteAll.addEventListener('click', async () => {
    dlg.close();
    await deleteAllTakes();
  });
}

// Multi-take copy (Select mode). The picked takes are joined in the order
// people read them — oldest first — with a blank line between takes so it is
// obvious where one ends and the next begins. A copy leaves Select mode.
export async function onCopySelection() {
  const btn = els.copySelection;
  if (!state.historySelected.size) return;
  const restoreLabel = btn.innerHTML;
  btn.disabled = true;
  btn.textContent = '…';
  try {
    // The list is newest-first; reverse it for oldest-first.
    const ids = state.historyTakes
      .map((s) => s.session_id)
      .filter((id) => state.historySelected.has(id))
      .reverse();
    const parts = [];
    for (const id of ids) {
      const text = (await fetchTakeText(id).catch(() => '')).trim();
      if (text) parts.push(text);
    }
    btn.innerHTML = restoreLabel;
    if (!parts.length) {
      showToast('Selected takes have no text', 'error');
      return;
    }
    await copyText(parts.join('\n\n'));
    showToast(`Copied ${parts.length} take${parts.length === 1 ? '' : 's'}`);
    setSelecting(false);
  } catch (err) {
    showToast('Copy failed: ' + (err.message || err), 'error');
  } finally {
    btn.innerHTML = restoreLabel;
    btn.disabled = state.historySelected.size === 0;
  }
}
