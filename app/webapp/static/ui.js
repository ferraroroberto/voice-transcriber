/* Presentation helpers — clipboard, toast, button flashes, formatting.
 *
 * No app logic here: every function is a pure-ish UI primitive other
 * modules call. The only state touched is the shared `els` table.
 */

'use strict';

import { els } from './state.js';

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
  const original = btn.textContent;
  btn.dataset.flashing = '1';
  btn.textContent = '✓ Copied';
  btn.classList.add('copied');
  setTimeout(() => {
    btn.textContent = original;
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
  const original = btn.textContent;
  btn.classList.add('danger-flash');
  btn.textContent = '✓ Cleared';
  setTimeout(() => {
    btn.textContent = original;
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

export function capitalize(s) { return s ? s[0].toUpperCase() + s.slice(1) : s; }
export function truncate(s, n) { return (s && s.length > n) ? s.slice(0, n - 1) + '…' : s; }
