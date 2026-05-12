/* Mobile-first voice transcriber — single page.
 *
 * Phase 2: single-shot upload (whole blob on stop).
 * Phase 4 will replace recorder.start() / .stop() with chunked uploads.
 *
 * State machine:
 *   idle → recording → uploading → transcribing → idle
 * Polish is a separate flow on the existing transcript.
 */

(() => {
  'use strict';

  const els = {
    recordBtn:        document.getElementById('recordBtn'),
    recordLabel:      document.getElementById('recordLabel'),
    recordTimer:      document.getElementById('recordTimer'),
    recordStatus:     document.getElementById('recordStatus'),
    levelFill:        document.getElementById('levelFill'),

    transcript:       document.getElementById('transcript'),
    copyTranscript:   document.getElementById('copyTranscript'),
    saveTranscript:   document.getElementById('saveTranscript'),

    polishModel:        document.getElementById('polishModel'),
    polishStyle:        document.getElementById('polishStyle'),
    polishPromptPreview: document.getElementById('polishPromptPreview'),
    polishBtn:          document.getElementById('polishBtn'),
    copyPolished:       document.getElementById('copyPolished'),
    polished:           document.getElementById('polished'),

    historyCount:     document.getElementById('historyCount'),
    historyList:      document.getElementById('historyList'),
    refreshHistory:   document.getElementById('refreshHistory'),
    copySelection:    document.getElementById('copySelection'),
    cleanAll:         document.getElementById('cleanAll'),
    loadMoreHistory:  document.getElementById('loadMoreHistory'),

    resetBtn:         document.getElementById('resetBtn'),
    appendToggle:     document.getElementById('appendToggle'),
    incognitoToggle:  document.getElementById('incognitoToggle'),

    loginOverlay:     document.getElementById('loginOverlay'),
    loginForm:        document.getElementById('loginForm'),
    loginPassword:    document.getElementById('loginPassword'),
    loginError:       document.getElementById('loginError'),

    settingsPanel:    document.getElementById('settingsPanel'),
    languageSelect:   document.getElementById('languageSelect'),
    translateToggle:  document.getElementById('translateToggle'),
    micSelect:        document.getElementById('micSelect'),
    forceBuiltinMic:  document.getElementById('forceBuiltinMic'),
    retentionDays:    document.getElementById('retentionDays'),
    saveSettings:     document.getElementById('saveSettings'),
    statusReadout:    document.getElementById('statusReadout'),

    toast:            document.getElementById('toast'),
  };

  const state = {
    sessionId:   null,
    // Tracks the most recent incognito session so we can DELETE it
    // from the server when the user starts another recording or hits
    // Reset — keeps incognito sessions from piling up on disk even
    // though they're already filtered out of the History list.
    incognitoSessionId: null,
    recorder:    null,
    stream:      null,
    streamKey:   '',          // constraints fingerprint for the cached stream
    chunks:      [],          // queued chunks awaiting upload
    uploadChain: Promise.resolve(),  // serializes uploads so server appends in order
    pendingUploads: 0,
    bytesSent:   0,           // running total of chunk bytes uploaded
    startedAt:   0,
    timer:       null,
    levelTimer:  null,
    audioCtx:    null,
    analyser:    null,
    mode:        'idle',
    config:      null,
    transcript:  '',
    polished:    '',
    mimeType:    null,
  };

  // -------------------------------------------------------- bootstrap

  // Pick up a `?token=…` from the URL on first load (typical when the
  // user opens the tokenised URL the tray copied or `last_tunnel_url.txt`
  // emitted), persist it to localStorage, and strip it from the visible
  // URL so the bookmark / Home Screen icon stays clean. From then on
  // every API fetch attaches `Authorization: Bearer <token>`.
  const TOKEN_KEY = 'vt_auth_token';

  function getStoredToken() {
    try { return localStorage.getItem(TOKEN_KEY) || ''; }
    catch (_) { return ''; }
  }

  function captureTokenFromURL() {
    try {
      const url = new URL(window.location.href);
      const tok = url.searchParams.get('token');
      if (!tok) return;
      try { localStorage.setItem(TOKEN_KEY, tok); } catch (_) {}
      url.searchParams.delete('token');
      const clean = url.pathname + (url.search ? url.search : '') + url.hash;
      window.history.replaceState({}, '', clean);
    } catch (_) {}
  }

  function authFetch(input, init) {
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

  captureTokenFromURL();

  init().catch(err => {
    console.error(err);
    showToast('Init failed: ' + err.message + ' — pull down to retry', 'error');
  });

  async function init() {
    // Each step is wrapped so a single transient blip (iOS waking the
    // tailnet, Safari dropping a stale TLS connection) doesn't leave the
    // page dead. We bind events even if config/status fail so the user
    // can pull-to-refresh manually.
    bindEvents();
    try {
      await loadConfig();
    } catch (err) {
      console.warn('loadConfig failed, using defaults:', err);
      applyConfigDefaults();
      showToast('Config load failed — using defaults · pull to retry', 'error');
    }
    try { await populateMics(); } catch (err) { console.warn('populateMics:', err); }
    refreshStatus();
    refreshHistory();
  }

  async function fetchJsonWithRetry(url, init, attempts) {
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

  function applyConfigDefaults() {
    state.config = state.config || {
      // Empty offline fallback — the real list comes from /api/config,
      // which serves config/webapp_config.json. Hardcoding aliases here
      // would defeat the "edit JSON, no code change" rule.
      polish_model_default: '',
      polish_models_available: [],
      polish_prompt_default: 'filler-words',
      polish_prompts: [{
        id: 'filler-words',
        label: 'Filler-word cleanup',
        description: 'Remove uh/um/like, false starts, repetitions. No rephrasing.',
        system: '(prompt unavailable — server is offline)',
      }],
      languages: [{iso: 'en', label: 'English'}, {iso: 'es', label: 'Spanish'}, {iso: 'it', label: 'Italian'}],
      language_default: 'en',
      force_builtin_mic_default: false,
      preferred_mic_id: null,
      history_retention_days: 30,
    };
    populateConfigUI();
  }

  function polishModelLabel(id) {
    // Derive a friendly label from the hub alias by title-casing the
    // segments (claude_haiku → "Claude Haiku"). Keeping this rule
    // synthesis-only means adding a new model is a single edit in
    // config/webapp_config.sample.json with no code change here.
    return String(id || '')
      .split('_')
      .filter(Boolean)
      .map(s => s.charAt(0).toUpperCase() + s.slice(1))
      .join(' ');
  }

  function populateConfigUI() {
    els.polishModel.innerHTML = '';
    for (const model of state.config.polish_models_available) {
      const opt = document.createElement('option');
      opt.value = model;
      opt.textContent = polishModelLabel(model);
      if (model === state.config.polish_model_default) opt.selected = true;
      els.polishModel.appendChild(opt);
    }
    els.polishStyle.innerHTML = '';
    const prompts = state.config.polish_prompts || [];
    for (const p of prompts) {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.label || p.id;
      if (p.id === state.config.polish_prompt_default) opt.selected = true;
      els.polishStyle.appendChild(opt);
    }
    refreshPromptPreview();
    els.languageSelect.innerHTML = '';
    for (const lang of state.config.languages || []) {
      // Server returns [{iso, label}]; tolerate the legacy bare-string shape
      // for back-compat with any cached client code.
      const iso = (typeof lang === 'string') ? lang : lang.iso;
      const label = (typeof lang === 'string') ? capitalize(lang) : lang.label;
      const opt = document.createElement('option');
      opt.value = iso;
      opt.textContent = label;
      if (iso === state.config.language_default) opt.selected = true;
      els.languageSelect.appendChild(opt);
    }
    els.forceBuiltinMic.checked = !!state.config.force_builtin_mic_default;
    els.retentionDays.value = state.config.history_retention_days;
  }

  function refreshPromptPreview() {
    const prompts = (state.config && state.config.polish_prompts) || [];
    const id = els.polishStyle.value;
    const hit = prompts.find(p => p.id === id);
    els.polishPromptPreview.value = hit ? hit.system : '';
  }

  function bindEvents() {
    els.recordBtn.addEventListener('click', onRecordToggle);
    els.copyTranscript.addEventListener('click', () => copyText(state.transcript, els.copyTranscript));
    els.copyPolished.addEventListener('click', () => copyText(state.polished, els.copyPolished));

    // Keep state in sync with manual edits — paste, typing, deletion all flow through here.
    els.transcript.addEventListener('input', () => {
      state.transcript = els.transcript.value;
      els.copyTranscript.disabled = !state.transcript;
      els.polishBtn.disabled = !state.transcript;
      // Save is only meaningful for pasted text that doesn't yet belong
      // to a session — a real recording already lives in History.
      els.saveTranscript.disabled = !state.transcript || !!state.sessionId;
    });
    els.polished.addEventListener('input', () => {
      state.polished = els.polished.value;
      els.copyPolished.disabled = !state.polished;
    });

    els.resetBtn.addEventListener('click', onReset);
    els.polishBtn.addEventListener('click', onPolish);
    els.saveTranscript.addEventListener('click', onSaveTranscript);
    els.polishStyle.addEventListener('change', refreshPromptPreview);

    els.settingsPanel.addEventListener('toggle', () => {
      if (els.settingsPanel.open) refreshStatus();
    });
    els.saveSettings.addEventListener('click', onSaveSettings);

    els.refreshHistory.addEventListener('click', refreshHistory);
    els.copySelection.addEventListener('click', onCopySelection);
    els.cleanAll.addEventListener('click', onCleanAll);
    els.loadMoreHistory.addEventListener('click', loadMoreHistory);

    // Switching mic / forcing built-in invalidates the cached stream so
    // the next record() picks up the new constraints (and re-prompts only
    // if iOS deems the new device a different permission scope).
    els.micSelect.addEventListener('change', releaseCachedStream);
    els.forceBuiltinMic.addEventListener('change', releaseCachedStream);

    // Release the mic when the page goes away so the iOS recording
    // indicator turns off — iOS keeps the permission grant for the
    // document lifetime, so the next record won't re-prompt within
    // the same page session. Also opportunistically reload config when
    // the tab becomes visible again, in case Tailscale was asleep.
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden' && state.mode === 'idle') {
        releaseCachedStream();
      } else if (document.visibilityState === 'visible' && !state.config) {
        loadConfig().catch(() => {});
      }
    });
    window.addEventListener('pagehide', releaseCachedStream);

    // iOS auto-locks the screen during long records — ask for wake lock if available
    if ('wakeLock' in navigator) {
      els.recordBtn.addEventListener('click', () => navigator.wakeLock.request('screen').catch(() => {}), { once: true });
    }
  }

  function releaseCachedStream() {
    if (state.stream) {
      try { state.stream.getTracks().forEach(t => t.stop()); } catch (_) {}
    }
    state.stream = null;
    state.streamKey = '';
  }

  // ----------------------------------------------------- config

  async function loadConfig() {
    // Single attempt first so we can detect 401 and prompt for the
    // password instead of silently retrying with the same stale state.
    const r = await authFetch('/api/config');
    if (r.status === 401) {
      const ok = await promptForPassword();
      if (!ok) throw new Error('login cancelled');
      return loadConfig();
    }
    if (!r.ok) throw new Error(`/api/config → ${r.status}`);
    state.config = await r.json();
    populateConfigUI();
  }

  // ----------------------------------------------------- login overlay

  function promptForPassword() {
    return new Promise((resolve) => {
      els.loginOverlay.hidden = false;
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
          els.loginOverlay.hidden = true;
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

  async function refreshStatus() {
    try {
      const r = await authFetch('/api/status');
      if (!r.ok) return;
      const s = await r.json();
      const bits = [];
      bits.push(s.whisper.running ? '🟢 whisper' : '🔴 whisper');
      bits.push(s.llm_hub.reachable ? '🟢 hub' : '🔴 hub');
      bits.push(s.ffmpeg_present ? '🟢 ffmpeg' : '🔴 ffmpeg');
      els.statusReadout.textContent = bits.join('   ');
      els.polishBtn.disabled = !s.llm_hub.reachable || !state.transcript;
    } catch (err) { /* swallow */ }
  }

  // ----------------------------------------------------- mic enumeration

  async function populateMics() {
    els.micSelect.innerHTML = '';
    const sysOpt = document.createElement('option');
    sysOpt.value = '';
    sysOpt.textContent = 'System default';
    els.micSelect.appendChild(sysOpt);

    if (!('mediaDevices' in navigator) || !navigator.mediaDevices.enumerateDevices) {
      return;
    }
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const inputs = devices.filter(d => d.kind === 'audioinput');
      for (const d of inputs) {
        const opt = document.createElement('option');
        opt.value = d.deviceId;
        opt.textContent = d.label || `Mic ${opt.value.slice(0, 6)}`;
        if (state.config && state.config.preferred_mic_id === d.deviceId) {
          opt.selected = true;
        }
        els.micSelect.appendChild(opt);
      }
    } catch (err) { /* iOS sometimes refuses pre-grant — fine */ }
  }

  // ----------------------------------------------------- record flow

  async function onRecordToggle() {
    if (state.mode === 'idle') return startRecording();
    if (state.mode === 'recording') return stopRecording();
  }

  async function startRecording() {
    setMode('starting');

    const constraints = buildAudioConstraints();
    const wantedKey = JSON.stringify(constraints);
    let stream = state.stream;

    if (stream && state.streamKey === wantedKey && stream.getAudioTracks().every(t => t.readyState === 'live')) {
      // Reuse the existing grant — no permission prompt.
      els.recordStatus.textContent = 'Reusing mic…';
    } else {
      releaseCachedStream();
      els.recordStatus.textContent = 'Requesting mic…';
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: constraints,
          video: false,
        });
      } catch (err) {
        setMode('idle');
        els.recordStatus.textContent = '';
        showToast('Mic permission denied', 'error');
        return;
      }
      state.stream = stream;
      state.streamKey = wantedKey;
    }

    // Re-enumerate now that labels may be visible (iOS reveals after grant).
    populateMics();

    // If we left an incognito session lingering from a previous take,
    // clean it up before starting a new one so disk stays tidy.
    await cleanupIncognitoSession();

    const incognito = !!els.incognitoToggle.checked;
    const sessionRes = await authFetch('/api/sessions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        language: els.languageSelect.value,
        incognito,
      }),
    });
    if (!sessionRes.ok) {
      stream.getTracks().forEach(t => t.stop());
      setMode('idle');
      showToast('Could not create session', 'error');
      return;
    }
    const session = await sessionRes.json();
    state.sessionId = session.session_id;
    if (incognito) state.incognitoSessionId = session.session_id;

    const mimeType = pickMimeType();
    state.mimeType = mimeType;
    state.chunks = [];
    state.uploadChain = Promise.resolve();
    state.pendingUploads = 0;
    state.bytesSent = 0;
    state.recorder = new MediaRecorder(stream, mimeType ? {mimeType} : undefined);
    state.stream = stream;

    // Stream every chunk to disk on the PC the moment it arrives — if the
    // phone dies mid-record, the partial recording is still recoverable.
    state.recorder.ondataavailable = e => {
      if (!e.data || e.data.size === 0) return;
      state.chunks.push(e.data);
      enqueueChunkUpload(e.data);
    };
    state.recorder.onstop = () => onRecorderStopped(mimeType);
    state.recorder.start(1000); // 1 s chunk cadence — survives connection drops

    state.startedAt = Date.now();
    setMode('recording');
    setupLevelMeter(stream);
    startTimer();

    els.recordLabel.textContent = '◼︎ STOP';
    els.recordTimer.hidden = false;
    els.recordTimer.textContent = '00:00';
    els.recordStatus.textContent = 'Recording…';
    els.recordBtn.setAttribute('aria-pressed', 'true');
  }

  function stopRecording() {
    if (!state.recorder) return;
    setMode('uploading');
    els.recordStatus.textContent = 'Uploading…';
    els.recordLabel.textContent = '⏳';
    els.recordBtn.disabled = true;
    state.recorder.stop();
    // Keep `state.stream` alive so the next record reuses the grant —
    // the iOS mic indicator will linger but no re-prompt. Released on
    // visibilitychange/pagehide or when the mic selection changes.
    stopTimer();
    teardownLevelMeter();
  }

  function enqueueChunkUpload(chunk) {
    state.pendingUploads += 1;
    state.uploadChain = state.uploadChain.then(async () => {
      try {
        const r = await authFetch(`/api/sessions/${state.sessionId}/chunk`, {
          method: 'POST',
          headers: { 'Content-Type': chunk.type || state.mimeType || 'audio/webm' },
          body: chunk,
        });
        if (r.ok) {
          state.bytesSent += chunk.size;
        } else {
          console.warn('chunk upload failed', r.status, await r.text().catch(() => ''));
        }
      } catch (err) {
        console.warn('chunk upload errored', err);
      } finally {
        state.pendingUploads -= 1;
      }
    });
  }

  async function onRecorderStopped(mimeType) {
    try {
      // Wait for any in-flight chunks to land before asking the server
      // to transcode + transcribe.
      const startWait = Date.now();
      const flushStatus = setInterval(() => {
        if (state.pendingUploads > 0) {
          els.recordStatus.textContent =
            `Finalising upload · ${state.pendingUploads} chunk${state.pendingUploads === 1 ? '' : 's'} left`;
        }
      }, 200);
      els.recordStatus.textContent = 'Finalising upload…';
      await state.uploadChain;
      clearInterval(flushStatus);

      const elapsedSec = Math.max(0, (Date.now() - state.startedAt) / 1000);
      els.recordStatus.textContent =
        `Server: ffmpeg → whisper · ${formatDuration(elapsedSec)} of audio…`;
      const t0 = Date.now();
      const translate = !!els.translateToggle.checked;
      const finishUrl =
        `/api/sessions/${state.sessionId}/finish` +
        `?language=${encodeURIComponent(els.languageSelect.value)}` +
        `&translate=${translate ? 'true' : 'false'}`;
      if (translate) {
        els.recordStatus.textContent =
          `Server: ffmpeg → translate (cold-start ~5 s on first call)…`;
      }
      const r = await authFetch(finishUrl, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ duration_seconds: elapsedSec }),
      });
      if (!r.ok) {
        const text = await r.text();
        throw new Error(text || `${r.status}`);
      }
      const data = await r.json();
      const serverMs = Date.now() - t0;
      if (data.silent) {
        // Recording was below the silence threshold — whisper was skipped
        // so it can't hallucinate. Don't touch the transcript box; the
        // user may still have accumulated text from earlier takes.
        els.recordStatus.textContent =
          `🤫 Empty audio (${data.dbfs} dBFS) — skipped`;
        showToast('Empty audio — nothing transcribed', 'success');
        refreshHistory();
        return;
      }
      state.transcript = mergeForAppend(state.transcript, data.transcript || '');
      state.polished = '';
      els.transcript.value = state.transcript;
      els.polished.value = '';
      els.copyTranscript.disabled = !state.transcript;
      els.copyPolished.disabled = true;
      els.polishBtn.disabled = !state.transcript;
      // The take already lives on disk — saving again would duplicate it.
      els.saveTranscript.disabled = true;
      // Auto-copy reads from the textarea so what lands on the clipboard
      // is exactly what's on screen — including the merged accumulator
      // when Append is on.
      const transcriptForCopy = els.transcript.value;
      if (transcriptForCopy) await tryAutoCopy(transcriptForCopy, els.copyTranscript);
      const speed = elapsedSec > 0 ? (elapsedSec / (serverMs / 1000)).toFixed(1) : '?';
      els.recordStatus.textContent =
        `Done in ${(serverMs / 1000).toFixed(1)} s · ${speed}× realtime — tap Copy or Polish`;
      refreshHistory();
    } catch (err) {
      console.error(err);
      els.recordStatus.textContent = 'Failed — recording is still on the PC, see History';
      showToast('Transcribe failed: ' + truncate(err.message || String(err), 120), 'error');
      refreshHistory();
    } finally {
      els.recordBtn.disabled = false;
      els.recordLabel.textContent = '⬤ RECORD';
      els.recordTimer.hidden = true;
      els.recordBtn.setAttribute('aria-pressed', 'false');
      els.levelFill.style.width = '0%';
      setMode('idle');
    }
  }

  function buildAudioConstraints() {
    const wantBuiltin = els.forceBuiltinMic.checked;
    const deviceId = els.micSelect.value;
    if (deviceId) return { deviceId: { exact: deviceId } };
    if (wantBuiltin) {
      // best-effort: if labels are visible, prefer one whose label hints "built-in"
      const opts = Array.from(els.micSelect.options).filter(o => o.value);
      const hit = opts.find(o => /built[- ]?in|iphone microphone|internal/i.test(o.textContent));
      if (hit) return { deviceId: { exact: hit.value } };
    }
    return true;
  }

  function pickMimeType() {
    if (!('MediaRecorder' in window)) return null;
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/mp4;codecs=mp4a.40.2', // iOS Safari often only supports this
      'audio/mp4',
    ];
    for (const m of candidates) {
      if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m)) return m;
    }
    return null;
  }

  // ----------------------------------------------------- timer + VU

  function startTimer() {
    state.timer = setInterval(() => {
      const elapsedMs = Date.now() - state.startedAt;
      const elapsed = Math.floor(elapsedMs / 1000);
      const mm = String(Math.floor(elapsed / 60)).padStart(2, '0');
      const ss = String(elapsed % 60).padStart(2, '0');
      els.recordTimer.textContent = `${mm}:${ss}`;
      els.recordStatus.textContent =
        `Recording · ${formatBytes(state.bytesSent)} streamed to PC`;
    }, 250);
  }

  function formatBytes(n) {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(2)} MB`;
  }

  function formatDuration(sec) {
    if (sec < 60) return `${sec.toFixed(1)} s`;
    const m = Math.floor(sec / 60);
    const s = Math.round(sec - m * 60);
    return `${m}m ${s}s`;
  }

  function stopTimer() {
    if (state.timer) clearInterval(state.timer);
    state.timer = null;
  }

  function setupLevelMeter(stream) {
    try {
      state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const src = state.audioCtx.createMediaStreamSource(stream);
      const analyser = state.audioCtx.createAnalyser();
      analyser.fftSize = 512;
      src.connect(analyser);
      state.analyser = analyser;
      const data = new Uint8Array(analyser.frequencyBinCount);
      state.levelTimer = setInterval(() => {
        analyser.getByteTimeDomainData(data);
        let max = 0;
        for (let i = 0; i < data.length; i++) {
          const v = Math.abs(data[i] - 128);
          if (v > max) max = v;
        }
        const pct = Math.min(100, (max / 128) * 200);
        els.levelFill.style.width = pct + '%';
      }, 80);
    } catch (err) {
      console.warn('VU meter setup failed', err);
    }
  }

  function teardownLevelMeter() {
    if (state.levelTimer) clearInterval(state.levelTimer);
    state.levelTimer = null;
    if (state.audioCtx) {
      try { state.audioCtx.close(); } catch (err) {}
      state.audioCtx = null;
    }
  }

  // ----------------------------------------------------- polish

  async function onPolish() {
    if (!state.transcript) return;
    const model = els.polishModel.value;
    const promptId = els.polishStyle.value || undefined;
    els.polishBtn.disabled = true;
    els.polishBtn.textContent = '…';
    els.recordStatus.textContent = `LLM hub → ${model} · polishing…`;
    const t0 = Date.now();
    try {
      let r;
      if (state.sessionId) {
        // Send the (possibly edited) transcript so the archive matches
        // what's on screen, then polish runs on it.
        r = await authFetch(`/api/sessions/${state.sessionId}/polish`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            model,
            prompt_id: promptId,
            transcript: state.transcript,
          }),
        });
      } else {
        // No recording yet — paste-and-polish flow. Backend creates a
        // text-only session so it shows up in History.
        r = await authFetch('/api/polish-text', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            text: state.transcript,
            model,
            prompt_id: promptId,
            language: els.languageSelect.value,
          }),
        });
      }
      if (!r.ok) throw new Error(await readErrorMessage(r));
      const data = await r.json();
      if (data.session_id) state.sessionId = data.session_id;
      const ms = Date.now() - t0;
      state.polished = data.polished || '';
      els.polished.value = state.polished;
      els.copyPolished.disabled = !state.polished;
      const polishedForCopy = els.polished.value;
      if (polishedForCopy) await tryAutoCopy(polishedForCopy, els.copyPolished);
      els.recordStatus.textContent =
        `Polished in ${(ms / 1000).toFixed(1)} s — tap Copy`;
      showToast('Polish done', 'success');
      refreshHistory();
    } catch (err) {
      els.recordStatus.textContent = 'Polish failed — see toast';
      showToast('Polish failed: ' + truncate(err.message, 80), 'error');
    } finally {
      els.polishBtn.disabled = false;
      els.polishBtn.textContent = 'Go';
    }
  }

  function onReset() {
    cleanupIncognitoSession();
    state.sessionId = null;
    state.transcript = '';
    state.polished = '';
    els.transcript.value = '';
    els.polished.value = '';
    els.copyTranscript.disabled = true;
    els.copyPolished.disabled = true;
    els.polishBtn.disabled = true;
    els.saveTranscript.disabled = true;
    els.recordStatus.textContent = 'Tap to start';
    els.levelFill.style.width = '0%';
  }

  async function onSaveTranscript() {
    if (!state.transcript || state.sessionId) return;
    const original = els.saveTranscript.textContent;
    els.saveTranscript.disabled = true;
    els.saveTranscript.textContent = '…';
    try {
      const r = await authFetch('/api/save-text', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          text: state.transcript,
          language: els.languageSelect.value,
        }),
      });
      if (!r.ok) throw new Error(await readErrorMessage(r));
      const data = await r.json();
      if (data.session_id) state.sessionId = data.session_id;
      els.recordStatus.textContent = 'Saved to history — ready to polish or copy';
      showToast('Saved to history', 'success');
      refreshHistory();
    } catch (err) {
      showToast('Save failed: ' + truncate(err.message || String(err), 80), 'error');
      els.saveTranscript.disabled = false;
    } finally {
      els.saveTranscript.textContent = original;
    }
  }

  async function cleanupIncognitoSession() {
    const id = state.incognitoSessionId;
    if (!id) return;
    state.incognitoSessionId = null;
    try {
      await authFetch(`/api/sessions/${id}`, { method: 'DELETE' });
    } catch (_) {
      // best-effort — server-side retention will reap it eventually
    }
  }

  // ----------------------------------------------------- settings

  async function onSaveSettings() {
    const patch = {
      polish_model_default: els.polishModel.value,
      polish_prompt_default: els.polishStyle.value,
      force_builtin_mic_default: els.forceBuiltinMic.checked,
      preferred_mic_id: els.micSelect.value || null,
      history_retention_days: parseInt(els.retentionDays.value, 10) || 30,
    };
    try {
      const r = await authFetch('/api/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(patch),
      });
      if (!r.ok) throw new Error(await r.text());
      await loadConfig();
      showToast('Settings saved', 'success');
    } catch (err) {
      showToast('Save failed: ' + err.message, 'error');
    }
  }

  // ----------------------------------------------------- history

  const HISTORY_PAGE_SIZE = 10;

  async function refreshHistory() {
    // Reset to page 1.
    els.historyList.innerHTML = '';
    await fetchHistoryPage(0);
    // After a fresh refresh, the newest take is at the top — default-check
    // it so a single click on "Copy selection" copies the latest, while the
    // user can extend the selection up the list to grab more takes.
    const firstCheckbox = els.historyList.querySelector('input.select-checkbox');
    if (firstCheckbox) firstCheckbox.checked = true;
  }

  async function loadMoreHistory() {
    await fetchHistoryPage(els.historyList.children.length);
  }

  async function fetchHistoryPage(offset) {
    try {
      els.loadMoreHistory.disabled = true;
      const r = await authFetch(
        `/api/sessions?limit=${HISTORY_PAGE_SIZE}&offset=${offset}`
      );
      const data = await r.json();
      const list = data.sessions || [];
      const total = typeof data.total === 'number' ? data.total : list.length;
      for (const s of list) {
        els.historyList.appendChild(renderHistoryItem(s));
      }
      const shown = els.historyList.children.length;
      els.historyCount.textContent = total > shown ? `${shown}/${total}` : `${shown}`;
      els.loadMoreHistory.hidden = shown >= total;
    } catch (err) { /* swallow */ }
    finally {
      els.loadMoreHistory.disabled = false;
    }
  }

  function renderHistoryItem(s) {
    const li = document.createElement('li');

    const selectLabel = document.createElement('label');
    selectLabel.className = 'select';
    selectLabel.title = 'Include this take in "Copy selection"';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.className = 'select-checkbox';
    checkbox.dataset.sessionId = s.session_id;
    selectLabel.append(checkbox);

    const content = document.createElement('div');
    content.className = 'content';

    const when = document.createElement('div');
    when.className = 'when';
    when.textContent = s.created_at + (s.language ? ` · ${s.language}` : '');
    const preview = document.createElement('div');
    preview.className = 'preview';
    preview.textContent = s.polished_preview || s.transcript_preview || '(no transcript)';
    const actions = document.createElement('div');
    actions.className = 'actions';

    const copyBtn = document.createElement('button');
    copyBtn.className = 'copy-btn';
    copyBtn.textContent = '📋 Copy';
    copyBtn.addEventListener('click', async () => {
      // The list payload only carries 200-char previews; fetch the full
      // text on demand so what the user pastes matches what's on disk.
      try {
        const r = await authFetch(`/api/sessions/${s.session_id}/text`);
        if (!r.ok) throw new Error(await r.text());
        const data = await r.json();
        const full = data.polished || data.transcript || '';
        await copyText(full, copyBtn);
      } catch (err) {
        showToast('Copy failed: ' + (err.message || err), 'error');
      }
    });

    const reBtn = document.createElement('button');
    reBtn.className = 'ghost-btn';
    reBtn.textContent = '🔁 Redo';
    reBtn.addEventListener('click', () => retranscribe(s.session_id));

    const delBtn = document.createElement('button');
    delBtn.className = 'ghost-btn';
    delBtn.textContent = '🗑️ Delete';
    delBtn.addEventListener('click', async () => {
      try {
        const r = await authFetch(`/api/sessions/${s.session_id}`, {
          method: 'DELETE',
        });
        if (!r.ok) throw new Error(await r.text());
        // Refresh the whole list so counts and pagination stay correct.
        refreshHistory();
      } catch (err) {
        showToast('Delete failed: ' + (err.message || err), 'error');
      }
    });

    actions.append(copyBtn, reBtn, delBtn);
    content.append(when, preview, actions);
    li.append(selectLabel, content);
    return li;
  }

  async function retranscribe(id) {
    showToast('Re-transcribing…', 'success');
    try {
      const r = await authFetch(`/api/sessions/${id}/retranscribe`, { method: 'POST' });
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      state.sessionId = id;
      state.transcript = mergeForAppend(state.transcript, data.transcript || '');
      state.polished = '';
      els.transcript.value = state.transcript;
      els.polished.value = '';
      els.copyTranscript.disabled = !state.transcript;
      els.copyPolished.disabled = true;
      els.polishBtn.disabled = !state.transcript;
      els.saveTranscript.disabled = true;
      refreshHistory();
      showToast('Done', 'success');
    } catch (err) {
      showToast('Re-transcribe failed', 'error');
    }
  }

  async function onCleanAll() {
    if (!confirm('Delete every saved recording and transcript?')) return;
    try {
      const r = await authFetch('/api/sessions', { method: 'DELETE' });
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      showToast(`Removed ${data.removed}`, 'success');
      flashDanger(els.cleanAll);
      refreshHistory();
    } catch (err) {
      showToast('Clean failed', 'error');
    }
  }

  // Multi-take copy. The newest take is auto-checked on every refresh, so
  // a one-click flow ("just copy the last one") still works. Tick more
  // boxes to bundle older takes — the result is concatenated in
  // chronological order (oldest → newest of the selection) with a
  // blank-line separator so it's obvious where one take ends and the
  // next begins.
  async function onCopySelection() {
    const btn = els.copySelection;
    const restoreLabel = '📋 Copy selected';
    const checked = Array.from(
      els.historyList.querySelectorAll('input.select-checkbox:checked')
    );
    if (!checked.length) {
      showToast('Tick at least one take first', 'error');
      return;
    }
    btn.disabled = true;
    btn.textContent = '…';
    let copyDone = false;
    try {
      // The list is rendered newest-first; reverse selection to get the
      // chronological order the user reads: oldest piece first, latest last.
      const idsChronOrder = checked.map(c => c.dataset.sessionId).reverse();
      const parts = [];
      for (const id of idsChronOrder) {
        const r = await authFetch(`/api/sessions/${id}/text`);
        if (!r.ok) continue;
        const data = await r.json();
        const text = (data.polished || data.transcript || '').trim();
        if (text) parts.push(text);
      }
      if (!parts.length) {
        showToast('Selected takes have no text', 'error');
        return;
      }
      const combined = parts.join('\n\n');
      // Restore the label before the green flash so flashCopied captures
      // "📋 Copy selection" as the original, not "…".
      btn.textContent = restoreLabel;
      await copyText(combined, btn);
      copyDone = true;
    } catch (err) {
      showToast('Copy selected failed: ' + (err.message || err), 'error');
    } finally {
      btn.disabled = false;
      if (!copyDone) btn.textContent = restoreLabel;
    }
  }

  function flashDanger(btn) {
    if (!btn) return;
    const original = btn.textContent;
    btn.classList.add('danger-flash');
    btn.textContent = '✓ Cleared';
    setTimeout(() => {
      btn.textContent = original;
      btn.classList.remove('danger-flash');
    }, 1400);
  }

  // ----------------------------------------------------- helpers

  function setMode(m) { state.mode = m; }

  // When the Append toggle is on, glue the new take onto the existing
  // transcript with a blank-line separator. Otherwise replace.
  function mergeForAppend(prev, next) {
    if (!els.appendToggle || !els.appendToggle.checked) return next;
    const prevTrimmed = (prev || '').replace(/\s+$/, '');
    if (!prevTrimmed) return next;
    if (!next) return prevTrimmed;
    return prevTrimmed + '\n\n' + next;
  }

  async function copyText(text, btn) {
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

  async function tryAutoCopy(text, btn) {
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

  function showToast(msg, kind) {
    els.toast.textContent = msg;
    els.toast.className = 'toast visible' + (kind ? ' ' + kind : '');
    els.toast.hidden = false;
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => {
      els.toast.classList.remove('visible');
      setTimeout(() => { els.toast.hidden = true; }, 200);
    }, 2400);
  }

  function capitalize(s) { return s ? s[0].toUpperCase() + s.slice(1) : s; }
  function truncate(s, n) { return (s && s.length > n) ? s.slice(0, n - 1) + '…' : s; }

  // Turn a non-2xx fetch Response into a short, readable error message.
  // The webapp returns FastAPI-shaped JSON ({"detail": "..."}) on its own
  // errors, but a Cloudflare tunnel sitting in front can intercept with
  // an HTML 5xx page (hub timeout, edge cutoff, etc.) — we don't want to
  // dump that whole document into a toast.
  async function readErrorMessage(response) {
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
})();
