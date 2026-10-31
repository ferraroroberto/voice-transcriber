// Vitest tests for the pure label helpers in labels.js. Run with:
//
//   npm install
//   npx vitest run app/webapp/static/__tests__/
//
// labels.js has no DOM access and no imports, so Vitest loads the shipped
// function directly. The Python test `tests/test_static_app_js.py` pins the
// same rule from the Python side.

import { describe, it, expect } from 'vitest';

import { polishModelLabel } from '../labels.js';

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
