/* Network layer — token-aware fetch, retry, the sign-in call, and
 * error-message extraction.
 *
 * Every API call in the app goes through `authFetch`; nothing else
 * attaches the bearer token.
 */

'use strict';

import { requestSignIn } from './_vendored/sign-in/sign-in.js';
import { getStoredToken, TOKEN_KEY } from './state.js';
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

// The vendored sign-in sheet owns the dialog, the busy and error states and the
// shared in-flight promise, so every 401 that lands while it is open awaits the
// same sign-in. The token goes to this app's own storage key, never the sheet's.
export function promptForPassword() {
  return requestSignIn({
    endpoint: '/api/login',
    hint: 'Enter the password to use this voice transcriber.',
    onToken: (token) => {
      try { localStorage.setItem(TOKEN_KEY, token); } catch (_) {}
    },
  }).then(() => true);
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
