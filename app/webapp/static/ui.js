/* Presentation helpers — clipboard, toast, button flashes, formatting.
 *
 * No app logic here: every function is a pure-ish UI primitive other
 * modules call. The only state touched is the shared `els` table.
 */

'use strict';

import { els, state } from './state.js';
import { icon } from './_vendored/icons/icons.js';

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
    showToast('Copied (fallback)', 'success');
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

export function flashDanger(btn) {
  if (!btn) return;
  // innerHTML for the same icon-preserving reason as flashCopied above.
  const original = btn.innerHTML;
  btn.classList.add('danger-flash');
  btn.innerHTML = icon('check') + ' Cleared';
  setTimeout(() => {
    btn.innerHTML = original;
    btn.classList.remove('danger-flash');
  }, 1400);
}

export function showToast(msg, kind) {
  els.toast.textContent = msg;
  els.toast.className = 'toast visible' + (kind ? ' ' + kind : '');
  els.toast.hidden = false;
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => {
    els.toast.classList.remove('visible');
    setTimeout(() => { els.toast.hidden = true; }, 200);
  }, 2400);
}

// Push a transcript into the DOM and re-derive the buttons that depend on
// it. Shared by every "the transcript just changed" caller — a fresh take,
// a retranscribe, Reset — so `els.transcript`/`els.polished` and the
// derived copyTranscript/copyPolished/polishBtn/saveTranscript states are
// owned in exactly one place. Always clears the polished pane: a new
// transcript makes any prior polish stale. Pass '' to clear (Reset).
export function renderTranscript(text) {
  state.transcript = text || '';
  state.polished = '';
  els.transcript.value = state.transcript;
  els.polished.value = '';
  els.copyTranscript.disabled = !state.transcript;
  els.copyPolished.disabled = true;
  els.polishBtn.disabled = !state.transcript;
  els.saveTranscript.disabled = true;
}

export function capitalize(s) { return s ? s[0].toUpperCase() + s.slice(1) : s; }
export function truncate(s, n) { return (s && s.length > n) ? s.slice(0, n - 1) + '…' : s; }
