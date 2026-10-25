/* Presentation helpers — clipboard, toast, button flashes, formatting.
 *
 * No app logic here: every function is a pure-ish UI primitive other
 * modules call. The only state touched is the shared `els` table.
 */

'use strict';

import { els, state } from './state.js';
import { icon } from './_vendored/icons/icons.js';
import { renderHeadStatus } from './_vendored/home-head/home-head.js';
import { showToast } from './_vendored/toast/toast.js';

// The one fleet toast (neutral frosted; only an error tints). Re-exported so
// every module keeps importing it from here.
export { showToast };

// The one read path for every role="switch" control (the vendored fleet
// switch and the compact header chips) — the aria-checked attribute is the
// state, mirroring the vendored setSwitch() write path.
export function isOn(el) {
  return !!el && el.getAttribute('aria-checked') === 'true';
}

export async function copyText(text, btn) {
  if (!text) return;
  // Drop any active selection so iOS doesn't bundle the styled DOM
  // alongside the plain text we're writing.
  if (window.getSelection) window.getSelection().removeAllRanges();
  const plain = String(text);
  try {
    await writePlainText(plain);
    flashCopied(btn);
  } catch (err) {
    // fallback: hidden textarea — execCommand('copy') always writes plain.
    const ta = document.createElement('textarea');
    ta.value = plain;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (_) {}
    document.body.removeChild(ta);
    showToast('Copied (fallback)');
  }
}

export async function tryAutoCopy(text, btn) {
  if (window.getSelection) window.getSelection().removeAllRanges();
  try {
    await writePlainText(String(text));
    if (btn) flashCopied(btn);
  } catch (err) {
    // iOS may reject auto-copy outside a user gesture — silent fallback.
    // Button stays in its idle state so the user can tap to copy.
  }
}

function flashCopied(btn) {
  if (!btn || btn.dataset.flashing === '1') return;
  // Save/restore innerHTML, not textContent — the idle label carries a
  // Lucide sprite icon that a textContent restore would strip.
  const original = btn.innerHTML;
  btn.dataset.flashing = '1';
  btn.innerHTML = icon('check') + ' Copied';
  btn.classList.add('copied');
  setTimeout(() => {
    btn.innerHTML = original;
    btn.classList.remove('copied');
    delete btn.dataset.flashing;
  }, 1400);
}

async function writePlainText(text) {
  // Prefer the explicit Clipboard API call so the only MIME type the
  // OS sees is text/plain — paste destinations can't pick up styled
  // HTML representations of the source DOM.
  if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
    try {
      const item = new ClipboardItem({
        'text/plain': new Blob([text], { type: 'text/plain' }),
      });
      await navigator.clipboard.write([item]);
      return;
    } catch (err) {
      // Some Safari builds reject ClipboardItem in non-secure contexts;
      // fall through to writeText.
    }
  }
  await navigator.clipboard.writeText(text);
}

// Push a transcript into the Text card. Shared by every "the transcript
// just changed" caller — a fresh take, a retranscribe, Clear — so the two
// textareas and everything derived from them are owned in one place. Always
// drops the polished version (a new transcript makes it stale) and shows the
// Transcript view. `take` ({at, seconds}) feeds the meta line; omit it for
// text that is not a fresh take. Pass '' to clear.
export function renderTranscript(text, take = null) {
  state.transcript = text || '';
  state.polished = '';
  state.polishStyle = '';
  state.take = take;
  state.textView = 'transcript';
  els.transcript.value = state.transcript;
  els.polished.value = '';
  renderTextCard();
}

// Show the polished result: switch to the Polished view so the
// result is what the user sees and what Copy takes.
export function renderPolished(text, styleLabel) {
  state.polished = text || '';
  state.polishStyle = styleLabel || '';
  els.polished.value = state.polished;
  state.textView = 'polished';
  renderTextCard();
}

export function setTextView(view) {
  state.textView = view === 'polished' ? 'polished' : 'transcript';
  renderTextCard();
}

// The text the Text card is showing — what Copy copies.
export function shownText() {
  return state.textView === 'polished' ? state.polished : state.transcript;
}

function countWords(text) {
  return text.trim().split(/\s+/).filter(Boolean).length;
}

function clock(sec) {
  const total = Math.round(sec);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

// The meta line under the text: "Today 08:48 · 212 words · 1:40" for a take,
// the polish style for the Polished view, a note while the text is live.
function textMetaLine() {
  if (state.mode === 'recording' && state.transcript) return 'Live text · refined when you stop';
  if (state.textView === 'polished') {
    return state.polishStyle ? `Polished · ${state.polishStyle}` : 'Polished';
  }
  if (!state.transcript) return '';
  const parts = [];
  if (state.take && state.take.at) parts.push(formatWhen(state.take.at.toISOString()).replace(',', ''));
  const words = countWords(state.transcript);
  parts.push(`${words} word${words === 1 ? '' : 's'}`);
  if (state.take && typeof state.take.seconds === 'number') parts.push(clock(state.take.seconds));
  return parts.join(' · ');
}

// Re-derive the whole Text card from `state`: the Transcript · Polished
// switch (only once a polish exists), which textarea shows, the meta line,
// and the verbs. Every caller that changes the text, the view, the mode or
// the hub status ends here, so no button state is owned anywhere else.
export function renderTextCard() {
  const hasPolish = !!state.polished || state.textView === 'polished';
  if (!hasPolish) state.textView = 'transcript';
  const polishedView = state.textView === 'polished';

  els.textView.hidden = !hasPolish;
  for (const seg of els.textView.querySelectorAll('.segmented-item')) {
    seg.setAttribute('aria-pressed', String(seg.dataset.view === state.textView));
  }
  els.transcript.hidden = polishedView;
  els.polished.hidden = !polishedView;

  const meta = textMetaLine();
  els.textMeta.textContent = meta;
  els.textMeta.hidden = !meta;

  els.textVerbs.hidden = !state.transcript && !state.polished;
  els.copyText.disabled = !shownText();

  // Polish stays one tap away but says why it can't run: the hub is the
  // only way to reach the LLM.
  const hubDown = !!state.services && !state.services.llm_hub.reachable;
  const label = els.polishBtn.querySelector('.polish-label');
  if (state.polishing) label.textContent = 'Polishing…';
  else if (hubDown) label.textContent = 'Polish unavailable';
  else label.textContent = state.polished ? 'Polish again' : 'Polish';
  els.polishBtn.title = hubDown ? 'Polish unavailable: hub not reachable' : '';
  els.polishBtn.disabled = state.polishing || hubDown || !state.transcript || state.mode !== 'idle';

  // Save only for text History does not hold yet (pasted, no session).
  els.saveTranscript.hidden = !state.transcript || !!state.sessionId;
}

// The page header's one live context line (design.md page-header): a
// plain fact in fg-muted, or a single exception in its tone from the
// styles.css tone map. The vendored renderHeadStatus draws the line; an
// attention/danger exception then leads with the warning glyph (the
// non-colour cue, app-side). The slot is in the markup from first paint and
// ellipsizes, so a line landing late never moves the row.
export function setHeadLine(el, text, tone) {
  if (!el) return;
  renderHeadStatus(el, tone ? [{ text, tone }] : [], text);
  if (tone === 'attention' || tone === 'danger') {
    el.insertAdjacentHTML('afterbegin', icon('triangle-alert') + ' ');
  }
  el.title = text || '';
}

export function capitalize(s) { return s ? s[0].toUpperCase() + s.slice(1) : s; }
export function truncate(s, n) { return (s && s.length > n) ? s.slice(0, n - 1) + '…' : s; }

// A take's created_at is a naive local ISO string; show it the way people say
// it ("Today, 14:32") instead of the raw timestamp.
export function formatWhen(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso || '';
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const midnight = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((midnight(new Date()) - midnight(d)) / 86400000);
  if (days === 0) return `Today, ${time}`;
  if (days === 1) return `Yesterday, ${time}`;
  return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${time}`;
}

export function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}
