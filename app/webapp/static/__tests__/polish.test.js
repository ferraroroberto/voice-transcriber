// Vitest tests for the Save action in polish.js. Run with:
//
//   npm install
//   npx vitest run app/webapp/static/__tests__/
//
// polish.js is the shipped module; the DOM, network and sibling modules are
// fakes. The Save button is a stateful stand-in because the bug lived in its
// `disabled` flag, which renderTextCard never touches (it only toggles
// `hidden`).

import { beforeEach, describe, expect, it, vi } from 'vitest';

const h = vi.hoisted(() => ({
  els: {
    saveTranscript: { innerHTML: '<svg></svg> Save', disabled: false, hidden: false },
    languageSelect: { value: 'en' },
    recordStatus: { textContent: '' },
  },
  state: { transcript: 'pasted words', sessionId: null },
  authFetch: vi.fn(),
  showToast: vi.fn(),
}));

vi.mock('../state.js', () => ({ els: h.els, state: h.state }));
vi.mock('../take.js', () => ({ endTake: vi.fn(), isTake: () => true }));
vi.mock('../api.js', () => ({
  authFetch: h.authFetch, readErrorMessage: async () => 'boom',
}));
vi.mock('../ui.js', () => ({
  renderPolished: vi.fn(), renderTextCard: vi.fn(), renderTranscript: vi.fn(),
  showToast: h.showToast, truncate: String, tryAutoCopy: vi.fn(),
}));
vi.mock('../history.js', () => ({ refreshHistory: vi.fn() }));
vi.mock('../recorder.js', () => ({
  cleanupIncognitoSession: vi.fn(), hideResumeButton: vi.fn(),
}));
vi.mock('../partials.js', () => ({ closePartialStream: vi.fn() }));

import { onSaveTranscript } from '../polish.js';

describe('onSaveTranscript', () => {
  beforeEach(() => {
    h.els.saveTranscript.disabled = false;
    h.els.saveTranscript.innerHTML = '<svg></svg> Save';
    h.state.sessionId = null;
    h.authFetch.mockReset();
  });

  it('leaves the button enabled after a successful save', async () => {
    h.authFetch.mockResolvedValue({ ok: true, json: async () => ({ session_id: 's1' }) });

    await onSaveTranscript();

    expect(h.state.sessionId).toBe('s1');
    expect(h.els.saveTranscript.disabled).toBe(false);
    expect(h.els.saveTranscript.innerHTML).toBe('<svg></svg> Save');
  });

  it('leaves the button enabled after a failed save', async () => {
    h.authFetch.mockResolvedValue({ ok: false });

    await onSaveTranscript();

    expect(h.els.saveTranscript.disabled).toBe(false);
  });
});
