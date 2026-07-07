/* Network layer — token-aware fetch, retry, the login overlay, and
 * error-message extraction.
 *
 * Every API call in the app goes through `authFetch`; nothing else
 * attaches the bearer token.
 */

'use strict';

import { els, getStoredToken, TOKEN_KEY } from './state.js';
import { truncate } from './ui.js';

export function authFetch(input, init) {
  const tok = getStoredToken();
  if (!tok) return fetch(input, init);
  const opts = Object.assign({}, init || {});
  const headers = new Headers(opts.headers || {});
  if (!headers.has('Authorization')) {
    headers.set('Authorization', 'Bearer ' + tok);
  }
  opts.headers = headers;
  return fetch(input, opts);
}

export async function fetchJsonWithRetry(url, init, attempts) {
  let lastErr;
  for (let i = 0; i < (attempts || 2); i++) {
    try {
      const r = await authFetch(url, init || {});
      if (!r.ok) throw new Error(`${url} → ${r.status}`);
      return await r.json();
    } catch (err) {
      lastErr = err;
      if (i + 1 < (attempts || 2)) {
        await new Promise(res => setTimeout(res, 600));
      }
    }
  }
  throw lastErr;
}

// The auth gate is a native <dialog> (fleet modal contract) — Esc must not
// dismiss it: a closed gate is not an unlocked app. One listener, module-wide.
if (els.loginOverlay) {
  els.loginOverlay.addEventListener('cancel', (e) => e.preventDefault());
}

export function promptForPassword() {
  return new Promise((resolve) => {
    if (!els.loginOverlay.open) els.loginOverlay.showModal();
    els.loginPassword.value = '';
    els.loginError.hidden = true;
    els.loginError.textContent = '';
    // Defer focus so iOS Safari opens the keyboard reliably.
    setTimeout(() => {
      try { els.loginPassword.focus(); } catch (_) {}
    }, 80);

    const onSubmit = async (e) => {
      e.preventDefault();
      const password = els.loginPassword.value;
      if (!password) return;
      els.loginError.hidden = true;
      const submitBtn = els.loginForm.querySelector('button[type="submit"]');
      submitBtn.disabled = true;
      submitBtn.textContent = '…';
      try {
        const r = await fetch('/api/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password }),
        });
        if (r.status === 503) {
          const detail = await r.json().catch(() => ({}));
          els.loginError.textContent =
            'Password auth not configured: ' +
            (detail.detail || 'check server config');
          els.loginError.hidden = false;
          return;
        }
        if (r.status === 401) {
          els.loginError.textContent = 'Wrong password';
          els.loginError.hidden = false;
          els.loginPassword.select();
          return;
        }
        if (!r.ok) {
          els.loginError.textContent = 'Login failed: ' + r.status;
          els.loginError.hidden = false;
          return;
        }
        const data = await r.json();
        if (!data.token) {
          els.loginError.textContent = 'Server returned no token';
          els.loginError.hidden = false;
          return;
        }
        try { localStorage.setItem(TOKEN_KEY, data.token); } catch (_) {}
        els.loginOverlay.close();
        els.loginForm.removeEventListener('submit', onSubmit);
        resolve(true);
      } catch (err) {
        els.loginError.textContent = 'Login failed: ' + (err.message || err);
        els.loginError.hidden = false;
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Unlock';
      }
    };

    els.loginForm.addEventListener('submit', onSubmit);
  });
}

// Turn a non-2xx fetch Response into a short, readable error message.
// The webapp returns FastAPI-shaped JSON ({"detail": "..."}) on its own
// errors, but a Cloudflare tunnel sitting in front can intercept with
// an HTML 5xx page (hub timeout, edge cutoff, etc.) — we don't want to
// dump that whole document into a toast.
export async function readErrorMessage(response) {
  const ct = (response.headers.get('content-type') || '').toLowerCase();
  let body = '';
  try { body = await response.text(); } catch (_) { body = ''; }
  if (ct.includes('application/json')) {
    try {
      const j = JSON.parse(body);
      if (j && typeof j.detail === 'string') return j.detail;
    } catch (_) { /* fall through */ }
  }
  if (ct.includes('text/html') || /^\s*<(!doctype|html)/i.test(body)) {
    return `HTTP ${response.status} from upstream (tunnel or hub error)`;
  }
  return body ? truncate(body.trim(), 200) : `HTTP ${response.status}`;
}
