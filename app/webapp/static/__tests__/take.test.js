// Vitest tests for the take lifecycle (take.js) and the recorder that drives
// it (recorder.js). Run with:
//
//   npm install
//   npx vitest run app/webapp/static/__tests__/
//
// state-machine-proof: take
//
// take.js and recorder.js are the shipped modules. Everything they reach
// out to (the DOM handles, the network, the mic, the meters) is a fake, so
// the table below drives the real transition rules and the real teardown.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const h = vi.hoisted(() => {
  const makeEl = () => ({
    textContent: '', innerHTML: '', value: '', hidden: false, disabled: false,
    options: [], attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    appendChild() {}, addEventListener() {},
  });
  const els = new Proxy({}, {
    get(target, key) {
      if (typeof key !== 'string') return undefined;
      if (!(key in target)) target[key] = makeEl();
      return target[key];
    },
  });
  const freshState = () => ({
    mode: 'idle', sessionId: null, incognitoSessionId: null, recorder: null,
    stream: null, streamKey: '', uploadChain: Promise.resolve(), pendingUploads: 0,
    bytesSent: 0, hadDroppedChunk: false, startedAt: 0, timer: null,
    transcript: '', partialBaseTranscript: '', backgroundFinalized: false,
    forceAppend: false, vadStopFired: false, vadSilenceSince: 0, partialVersion: 0,
    vadStatusOwnedUntil: 0, mimeType: null, config: null, textView: 'transcript',
  });
  return {
    els, makeEl, freshState, state: freshState(),
    teardownLevelMeter: vi.fn(), setupLevelMeter: vi.fn(),
    releaseWakeLock: vi.fn(), acquireWakeLock: vi.fn(),
    closePartialStream: vi.fn(), openPartialStream: vi.fn(),
    stopNotice: vi.fn(), closeLevelSource: vi.fn(),
    showToast: vi.fn(), authFetch: vi.fn(),
  };
});

vi.mock('../state.js', () => ({ els: h.els, state: h.state, getStoredToken: () => '' }));
vi.mock('../api.js', () => ({ authFetch: h.authFetch }));
vi.mock('../ui.js', () => ({
  formatBytes: String, isOn: () => false, renderTextCard: vi.fn(),
  renderTranscript: vi.fn(), showToast: h.showToast, tryAutoCopy: vi.fn(),
  truncate: String,
}));
vi.mock('../history.js', () => ({ refreshHistory: vi.fn() }));
vi.mock('../level.js', () => ({
  setupLevelMeter: h.setupLevelMeter, teardownLevelMeter: h.teardownLevelMeter,
}));
vi.mock('../partials.js', () => ({
  openPartialStream: h.openPartialStream, closePartialStream: h.closePartialStream,
}));
vi.mock('../wakelock.js', () => ({
  acquireWakeLock: h.acquireWakeLock, releaseWakeLock: h.releaseWakeLock,
}));
vi.mock('../_vendored/icons/icons.js', () => ({ icon: () => '' }));
vi.mock('../_vendored/rec-wave/rec-wave.js', () => ({
  createLevelSource: () => ({ close: h.closeLevelSource }),
}));
vi.mock('../_vendored/mic-notice/mic-notice.js', () => ({
  attachMicNotice: () => h.stopNotice,
}));

import { TAKE_STATES, isTake, transition } from '../take.js';
import { finalizeForBackground, onRecordToggle } from '../recorder.js';

const STATES = Object.keys(TAKE_STATES);
const LEGAL = new Set([
  'idle>starting', 'starting>recording', 'starting>idle',
  'recording>finishing', 'finishing>idle',
]);

function goTo(path) {
  for (const next of path) expect(transition(next, 'test setup')).toBe(true);
}

const PATHS = {
  idle: [],
  starting: ['starting'],
  recording: ['starting', 'recording'],
  finishing: ['starting', 'recording', 'finishing'],
};

beforeEach(() => {
  Object.assign(h.state, h.freshState());
  for (const fn of [
    h.teardownLevelMeter, h.setupLevelMeter, h.releaseWakeLock, h.acquireWakeLock,
    h.closePartialStream, h.openPartialStream, h.stopNotice, h.closeLevelSource,
    h.showToast, h.authFetch,
  ]) fn.mockReset();
  h.els.recordBtn.disabled = false;
  vi.restoreAllMocks();
});

describe('transition table', () => {
  const pairs = STATES.flatMap(from => STATES.map(to => [from, to]));

  it.each(pairs)('%s -> %s', (from, to) => {
    vi.spyOn(console, 'info').mockImplementation(() => {});
    goTo(PATHS[from]);
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    const moved = transition(to, 'table row');
    if (LEGAL.has(`${from}>${to}`)) {
      expect(moved).toBe(true);
      expect(isTake(to)).toBe(true);
      expect(err).not.toHaveBeenCalled();
    } else {
      expect(moved).toBe(false);
      expect(isTake(from)).toBe(true);
      expect(err).toHaveBeenCalledTimes(1);
    }
  });

  it('refuses a state that is not declared', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(transition('transcribing', 'typo')).toBe(false);
    expect(isTake('idle')).toBe(true);
  });

  it('throws on a state name that is not declared', () => {
    expect(() => isTake('uploading')).toThrow();
  });

  it('leaves an info breadcrumb for each move', () => {
    const info = vi.spyOn(console, 'info').mockImplementation(() => {});
    transition('starting', 'record tapped');
    expect(info).toHaveBeenCalledWith('[take] idle -> starting (record tapped)');
  });
});

// ------------------------------------------------------------ recorder exits

class FakeTrack {
  constructor() {
    this.readyState = 'live';
    this.handlers = new Set();
    this.stop = vi.fn(() => { this.readyState = 'ended'; });
  }
  addEventListener(type, fn) { if (type === 'ended') this.handlers.add(fn); }
  removeEventListener(type, fn) { if (type === 'ended') this.handlers.delete(fn); }
  end() { this.readyState = 'ended'; [...this.handlers].forEach(fn => fn()); }
}

class FakeMediaRecorder {
  static throwOnConstruct = false;
  static last = null;
  static isTypeSupported() { return true; }
  constructor() {
    if (FakeMediaRecorder.throwOnConstruct) throw new Error('unsupported type');
    this.state = 'inactive';
    FakeMediaRecorder.last = this;
  }
  start() { this.state = 'recording'; }
  stop() {
    this.state = 'inactive';
    queueMicrotask(() => this.onstop && this.onstop());
  }
}

let track;

function installBrowser({ mic = 'granted', session = 'ok' } = {}) {
  track = new FakeTrack();
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] };
  FakeMediaRecorder.throwOnConstruct = false;
  FakeMediaRecorder.last = null;
  vi.stubGlobal('MediaRecorder', FakeMediaRecorder);
  vi.stubGlobal('window', { MediaRecorder: FakeMediaRecorder });
  vi.stubGlobal('document', { createElement: h.makeEl });
  vi.stubGlobal('navigator', {
    mediaDevices: {
      getUserMedia: mic === 'granted'
        ? vi.fn(async () => stream)
        : vi.fn(async () => { throw new Error('NotAllowedError'); }),
      enumerateDevices: vi.fn(async () => []),
    },
  });
  h.authFetch.mockImplementation(async (url) => {
    if (url === '/api/sessions') {
      if (session === 'fails') throw new Error('network down');
      return { ok: true, json: async () => ({ session_id: 's1' }) };
    }
    return {
      ok: true, text: async () => '',
      json: async () => ({ transcript: 'hello', silent: false }),
    };
  });
  vi.spyOn(console, 'info').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
}

function expectNothingLeftRunning() {
  expect(h.state.timer).toBeNull();
  expect(vi.getTimerCount()).toBe(0);
  expect(h.teardownLevelMeter).toHaveBeenCalled();
  expect(h.releaseWakeLock).toHaveBeenCalled();
  expect(h.closePartialStream).toHaveBeenCalled();
  expect(h.stopNotice).toHaveBeenCalled();
  expect(track.handlers.size).toBe(0);
}

describe('recorder exits', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it('a tap on Stop finishes the take and tears the capture down', async () => {
    installBrowser();
    await onRecordToggle();
    expect(isTake('recording')).toBe(true);
    expect(vi.getTimerCount()).toBe(1);

    const done = onRecordToggle();
    expect(isTake('finishing')).toBe(true);
    await done;
    await vi.runAllTimersAsync();

    expect(isTake('idle')).toBe(true);
    expect(h.els.recordBtn.disabled).toBe(false);
    expectNothingLeftRunning();
  });

  it('a recorder that stops by itself still tears the capture down', async () => {
    installBrowser();
    await onRecordToggle();
    expect(vi.getTimerCount()).toBe(1);
    expect(track.handlers.size).toBe(1);

    const rec = FakeMediaRecorder.last;
    const settled = rec.onstop();
    expect(isTake('finishing')).toBe(true);
    expect(h.state.timer).toBeNull();
    expect(h.teardownLevelMeter).toHaveBeenCalledTimes(1);
    expect(h.releaseWakeLock).toHaveBeenCalledTimes(1);
    await settled;
    await vi.runAllTimersAsync();

    expect(isTake('idle')).toBe(true);
    expect(h.showToast).toHaveBeenCalledWith(expect.stringContaining('recorder stopped'), 'error');
    expectNothingLeftRunning();
  });

  it('the mic ending finishes the take with the reason', async () => {
    installBrowser();
    await onRecordToggle();
    track.end();
    expect(isTake('finishing')).toBe(true);
    expect(h.els.recordStatus.textContent).toContain('mic was disconnected');
    await vi.runAllTimersAsync();
    expect(isTake('idle')).toBe(true);
    expectNothingLeftRunning();
  });

  it('a recorder error finishes the take once', async () => {
    installBrowser();
    await onRecordToggle();
    const rec = FakeMediaRecorder.last;
    rec.state = 'inactive';
    rec.onerror({ error: new Error('encoder failed') });
    expect(isTake('finishing')).toBe(true);
    await rec.onstop();
    await vi.runAllTimersAsync();
    expect(isTake('idle')).toBe(true);
    expect(h.authFetch.mock.calls.filter(c => String(c[0]).includes('/finish'))).toHaveLength(1);
    expectNothingLeftRunning();
  });

  it('a second tap while finishing does nothing', async () => {
    installBrowser();
    await onRecordToggle();
    const done = onRecordToggle();
    await onRecordToggle();
    await done;
    await vi.runAllTimersAsync();
    expect(h.authFetch.mock.calls.filter(c => String(c[0]).includes('/finish'))).toHaveLength(1);
    expect(isTake('idle')).toBe(true);
  });

  it('backgrounding finalises the take and flags it', async () => {
    installBrowser();
    await onRecordToggle();
    finalizeForBackground();
    expect(isTake('finishing')).toBe(true);
    expect(h.state.backgroundFinalized).toBe(true);
    await vi.runAllTimersAsync();
    expect(isTake('idle')).toBe(true);
  });
});

describe('recorder start failures', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it('new MediaRecorder throwing returns the button to idle', async () => {
    installBrowser();
    FakeMediaRecorder.throwOnConstruct = true;
    await onRecordToggle();

    expect(isTake('idle')).toBe(true);
    expect(h.showToast).toHaveBeenCalledWith('Could not start recording', 'error');
    expect(track.stop).toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);

    FakeMediaRecorder.throwOnConstruct = false;
    await onRecordToggle();
    expect(isTake('recording')).toBe(true);
  });

  it('a failed session create returns the button to idle', async () => {
    installBrowser({ session: 'fails' });
    await onRecordToggle();
    expect(isTake('idle')).toBe(true);
    expect(h.showToast).toHaveBeenCalledWith('Could not create session', 'error');
    expect(track.stop).toHaveBeenCalled();
    expect(h.els.recordStatus.textContent).toBe('');
  });

  it('a denied mic permission returns the button to idle', async () => {
    installBrowser({ mic: 'denied' });
    await onRecordToggle();
    expect(isTake('idle')).toBe(true);
    expect(h.showToast).toHaveBeenCalledWith('Mic permission denied', 'error');
    expect(h.authFetch).not.toHaveBeenCalled();
  });

  it('a throw once the mic is live finishes the take instead of leaking it', async () => {
    installBrowser();
    h.openPartialStream.mockImplementation(() => { throw new Error('stream setup exploded'); });
    await onRecordToggle();
    expect(isTake('finishing')).toBe(true);
    await vi.runAllTimersAsync();
    expect(isTake('idle')).toBe(true);
    expectNothingLeftRunning();
  });
});
