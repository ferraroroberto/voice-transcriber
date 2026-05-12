// Vitest tests for the small pure functions in app.js. Run with:
//
//   npm install --save-dev vitest jsdom
//   npx vitest run app/webapp/static/__tests__/
//
// The functions exported here mirror the ones in app.js. We re-define
// them in a tiny module so vitest can import them without booting the
// whole IIFE that wraps app.js. When you change either copy, keep them
// in sync — the Python test `tests/test_static_app_js.py` enforces that
// the JS source still contains the expected logic.

import { describe, it, expect } from 'vitest';

// Mirror of `polishModelLabel` from app.js.
function polishModelLabel(id) {
  return String(id || '')
    .split('_')
    .filter(Boolean)
    .map(s => s.charAt(0).toUpperCase() + s.slice(1))
    .join(' ');
}

describe('polishModelLabel', () => {
  it.each([
    ['claude_haiku',  'Claude Haiku'],
    ['claude_sonnet', 'Claude Sonnet'],
    ['claude_opus',   'Claude Opus'],
    ['gemini_lite',   'Gemini Lite'],
    ['gemini_flash',  'Gemini Flash'],
    ['gemini_pro',    'Gemini Pro'],
  ])('title-cases %s → %s', (id, expected) => {
    expect(polishModelLabel(id)).toBe(expected);
  });

  it('handles unknown future aliases gracefully', () => {
    expect(polishModelLabel('gemini_2_flash')).toBe('Gemini 2 Flash');
    expect(polishModelLabel('brand_new_model')).toBe('Brand New Model');
  });

  it('returns empty string for empty input', () => {
    expect(polishModelLabel('')).toBe('');
    expect(polishModelLabel(null)).toBe('');
    expect(polishModelLabel(undefined)).toBe('');
  });

  it('collapses empty segments from double underscores', () => {
    // `__` produces an empty segment; .filter(Boolean) drops it.
    expect(polishModelLabel('foo__bar')).toBe('Foo Bar');
    expect(polishModelLabel('_foo')).toBe('Foo');
    expect(polishModelLabel('foo_')).toBe('Foo');
  });

  it('preserves single-segment ids', () => {
    expect(polishModelLabel('whisper')).toBe('Whisper');
  });
});
