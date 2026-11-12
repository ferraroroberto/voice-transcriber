// Vitest tests for the VAD auto-stop tick in level.js. Run with:
//
//   npm install
//   npx vitest run app/webapp/static/__tests__/
//
// level.js is the shipped module. `els` is a plain object holding only the
// handles the tick may touch, never a Proxy that invents missing ones: a
// write to a handle the real `els` table lacks must throw here exactly as
// it does in the browser, which is how a dead meter write once silenced the
// "Stop after silence" setting without any test noticing.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const h = vi.hoisted(() => ({
  els: { recordStatus: { textContent: '' }, vadAutoStopToggle: {} },
  state: {
    startedAt: 0, vadStopFired: false, vadSilenceSince: 0,
    vadStatusOwnedUntil: 0, bytesSent: 0, config: null,
    levelTimer: null, audioCtx: null, analyser: null,
  },
}));

vi.mock('../state.js', () => ({ els: h.els, state: h.state }));
vi.mock('../take.js', () => ({ isTake: (name) => name === 'recording' }));
vi.mock('../ui.js', () => ({ formatBytes: String, isOn: () => true }));
vi.mock('../vad.js', () => ({ silenceTriggerMs: () => 1000 }));

import { setupLevelMeter, teardownLevelMeter } from '../level.js';

function fakeAudioContext(sample) {
  return class {
    createMediaStreamSource() { return { connect() {} }; }
    createAnalyser() {
      return {
        fftSize: 0,
        frequencyBinCount: 4,
        getByteTimeDomainData(buf) { buf.fill(sample); },
      };
    }
    close() {}
  };
}

describe('level meter tick', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    Object.assign(h.state, {
      startedAt: Date.now() - 10_000, vadStopFired: false, vadSilenceSince: 0,
      vadStatusOwnedUntil: 0, levelTimer: null, audioCtx: null, analyser: null,
    });
  });

  afterEach(() => {
    teardownLevelMeter();
    delete globalThis.window;
    vi.useRealTimers();
  });

  it('fires the stop callback after sustained silence with auto-stop on', () => {
    globalThis.window = { AudioContext: fakeAudioContext(128) };
    const onAutoStop = vi.fn();
    setupLevelMeter({}, onAutoStop);

    // 1000 ms silence trigger + 500 ms grace, with margin.
    vi.advanceTimersByTime(2500);

    expect(onAutoStop).toHaveBeenCalledTimes(1);
  });

  it('does not stop while the mic stays loud', () => {
    globalThis.window = { AudioContext: fakeAudioContext(228) };
    const onAutoStop = vi.fn();
    setupLevelMeter({}, onAutoStop);

    vi.advanceTimersByTime(5000);

    expect(onAutoStop).not.toHaveBeenCalled();
  });
});
