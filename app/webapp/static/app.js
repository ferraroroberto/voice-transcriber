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

    polishModel:      document.getElementById('polishModel'),
    setDefaultModel:  document.getElementById('setDefaultModel'),
    polishBtn:        document.getElementById('polishBtn'),
    copyPolished:     document.getElementById('copyPolished'),
    polished:         document.getElementById('polished'),

    historyCount:     document.getElementById('historyCount'),
    historyList:      document.getElementById('historyList'),
    refreshHistory:   document.getElementById('refreshHistory'),
    cleanAll:         document.getElementById('cleanAll'),

    resetBtn:         document.getElementById('resetBtn'),

    settingsToggle:   document.getElementById('settingsToggle'),
    settingsPanel:    document.getElementById('settingsPanel'),
    languageSelect:   document.getElementById('languageSelect'),
    micSelect:        document.getElementById('micSelect'),
    forceBuiltinMic:  document.getElementById('forceBuiltinMic'),
    retentionDays:    document.getElementById('retentionDays'),
    saveSettings:     document.getElementById('saveSettings'),
    closeSettings:    document.getElementById('closeSettings'),
    statusReadout:    document.getElementById('statusReadout'),

    toast:            document.getElementById('toast'),
  };

  const state = {
    sessionId:   null,
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
        const r = await fetch(url, init || {});
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
      polish_model_default: 'gemma4-e4b-it',
      polish_models_available: ['gemma4-e4b-it', 'gemma4-26b-a4b-it', 'claude-haiku-4-5'],
      languages: ['english', 'spanish'],
      language_default: 'english',
      force_builtin_mic_default: false,
      preferred_mic_id: null,
      history_retention_days: 30,
    };
    populateConfigUI();
  }

  function populateConfigUI() {
    els.polishModel.innerHTML = '';
    for (const model of state.config.polish_models_available) {
      const opt = document.createElement('option');
      opt.value = model;
      opt.textContent = model;
      if (model === state.config.polish_model_default) opt.selected = true;
      els.polishModel.appendChild(opt);
    }
    els.languageSelect.innerHTML = '';
    for (const lang of state.config.languages || []) {
      const opt = document.createElement('option');
      opt.value = lang;
      opt.textContent = capitalize(lang);
      if (lang === state.config.language_default) opt.selected = true;
      els.languageSelect.appendChild(opt);
    }
    els.forceBuiltinMic.checked = !!state.config.force_builtin_mic_default;
    els.retentionDays.value = state.config.history_retention_days;
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
    });
    els.polished.addEventListener('input', () => {
      state.polished = els.polished.value;
      els.copyPolished.disabled = !state.polished;
    });

    els.resetBtn.addEventListener('click', onReset);
    els.polishBtn.addEventListener('click', onPolish);
    els.setDefaultModel.addEventListener('click', onSetDefaultModel);

    els.settingsToggle.addEventListener('click', toggleSettings);
    els.closeSettings.addEventListener('click', toggleSettings);
    els.saveSettings.addEventListener('click', onSaveSettings);

    els.refreshHistory.addEventListener('click', refreshHistory);
    els.cleanAll.addEventListener('click', onCleanAll);

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
    state.config = await fetchJsonWithRetry('/api/config', null, 2);
    populateConfigUI();
  }

  async function refreshStatus() {
    try {
      const r = await fetch('/api/status');
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

    const sessionRes = await fetch('/api/sessions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({language: els.languageSelect.value}),
    });
    if (!sessionRes.ok) {
      stream.getTracks().forEach(t => t.stop());
      setMode('idle');
      showToast('Could not create session', 'error');
      return;
    }
    const session = await sessionRes.json();
    state.sessionId = session.session_id;

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
        const r = await fetch(`/api/sessions/${state.sessionId}/chunk`, {
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
      const r = await fetch(
        `/api/sessions/${state.sessionId}/finish?language=${encodeURIComponent(els.languageSelect.value)}`,
        {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ duration_seconds: elapsedSec }),
        }
      );
      if (!r.ok) {
        const text = await r.text();
        throw new Error(text || `${r.status}`);
      }
      const data = await r.json();
      const serverMs = Date.now() - t0;
      state.transcript = data.transcript || '';
      state.polished = '';
      els.transcript.value = state.transcript;
      els.polished.value = '';
      els.copyTranscript.disabled = !state.transcript;
      els.copyPolished.disabled = true;
      els.polishBtn.disabled = !state.transcript;
      if (state.transcript) await tryAutoCopy(state.transcript);
      const speed = elapsedSec > 0 ? (elapsedSec / (serverMs / 1000)).toFixed(1) : '?';
      els.recordStatus.textContent =
        `Done in ${(serverMs / 1000).toFixed(1)} s · ${speed}× realtime — tap Copy or Polish`;
      refreshHistory();
    } catch (err) {
      console.error(err);
      els.recordStatus.textContent = 'Failed — recording is still on the PC, see History';
      showToast('Transcribe failed: ' + (err.message || err), 'error');
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
    els.polishBtn.disabled = true;
    els.polishBtn.textContent = '✨ Polishing…';
    els.recordStatus.textContent = `LLM hub → ${model} · polishing…`;
    const t0 = Date.now();
    try {
      let r;
      if (state.sessionId) {
        // Send the (possibly edited) transcript so the archive matches
        // what's on screen, then polish runs on it.
        r = await fetch(`/api/sessions/${state.sessionId}/polish`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({model, transcript: state.transcript}),
        });
      } else {
        // No recording yet — paste-and-polish flow. Backend creates a
        // text-only session so it shows up in History.
        r = await fetch('/api/polish-text', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            text: state.transcript,
            model,
            language: els.languageSelect.value,
          }),
        });
      }
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      if (data.session_id) state.sessionId = data.session_id;
      const ms = Date.now() - t0;
      state.polished = data.polished || '';
      els.polished.value = state.polished;
      els.copyPolished.disabled = !state.polished;
      if (state.polished) await tryAutoCopy(state.polished);
      els.recordStatus.textContent =
        `Polished in ${(ms / 1000).toFixed(1)} s — tap Copy`;
      showToast('Polish done', 'success');
      refreshHistory();
    } catch (err) {
      els.recordStatus.textContent = 'Polish failed — see toast';
      showToast('Polish failed: ' + truncate(err.message, 80), 'error');
    } finally {
      els.polishBtn.disabled = false;
      els.polishBtn.textContent = '✨ Polish transcript';
    }
  }

  function onReset() {
    state.sessionId = null;
    state.transcript = '';
    state.polished = '';
    els.transcript.value = '';
    els.polished.value = '';
    els.copyTranscript.disabled = true;
    els.copyPolished.disabled = true;
    els.polishBtn.disabled = true;
    els.recordStatus.textContent = 'Tap to start';
    els.levelFill.style.width = '0%';
  }

  async function onSetDefaultModel() {
    const model = els.polishModel.value;
    try {
      const r = await fetch('/api/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({polish_model_default: model}),
      });
      if (!r.ok) throw new Error(await r.text());
      showToast(`Default → ${model}`, 'success');
      await loadConfig();
    } catch (err) {
      showToast('Save failed: ' + err.message, 'error');
    }
  }

  // ----------------------------------------------------- settings

  function toggleSettings() {
    els.settingsPanel.hidden = !els.settingsPanel.hidden;
    if (!els.settingsPanel.hidden) refreshStatus();
  }

  async function onSaveSettings() {
    const patch = {
      force_builtin_mic_default: els.forceBuiltinMic.checked,
      preferred_mic_id: els.micSelect.value || null,
      history_retention_days: parseInt(els.retentionDays.value, 10) || 30,
    };
    try {
      const r = await fetch('/api/config', {
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

  async function refreshHistory() {
    try {
      const r = await fetch('/api/sessions?limit=50');
      const data = await r.json();
      const list = data.sessions || [];
      els.historyCount.textContent = list.length;
      els.historyList.innerHTML = '';
      for (const s of list) {
        const li = document.createElement('li');
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
        copyBtn.addEventListener('click', () => {
          copyText(s.polished_preview || s.transcript_preview || '', copyBtn);
        });

        const reBtn = document.createElement('button');
        reBtn.className = 'ghost-btn';
        reBtn.textContent = '🔁 Re-transcribe';
        reBtn.addEventListener('click', () => retranscribe(s.session_id));

        actions.append(copyBtn, reBtn);
        li.append(when, preview, actions);
        els.historyList.appendChild(li);
      }
    } catch (err) { /* swallow */ }
  }

  async function retranscribe(id) {
    showToast('Re-transcribing…', 'success');
    try {
      const r = await fetch(`/api/sessions/${id}/retranscribe`, { method: 'POST' });
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      state.sessionId = id;
      state.transcript = data.transcript || '';
      state.polished = '';
      els.transcript.value = state.transcript;
      els.polished.value = '';
      els.copyTranscript.disabled = !state.transcript;
      els.copyPolished.disabled = true;
      els.polishBtn.disabled = !state.transcript;
      refreshHistory();
      showToast('Done', 'success');
    } catch (err) {
      showToast('Re-transcribe failed', 'error');
    }
  }

  async function onCleanAll() {
    if (!confirm('Delete every saved recording and transcript?')) return;
    try {
      const r = await fetch('/api/sessions', { method: 'DELETE' });
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      showToast(`Removed ${data.removed}`, 'success');
      refreshHistory();
    } catch (err) {
      showToast('Clean failed', 'error');
    }
  }

  // ----------------------------------------------------- helpers

  function setMode(m) { state.mode = m; }

  async function copyText(text, btn) {
    if (!text) return;
    // Drop any active selection so iOS doesn't bundle the styled DOM
    // alongside the plain text we're writing.
    if (window.getSelection) window.getSelection().removeAllRanges();
    const plain = String(text);
    try {
      await writePlainText(plain);
      const original = btn.textContent;
      btn.textContent = '✓ Copied';
      btn.classList.add('copied');
      setTimeout(() => {
        btn.textContent = original;
        btn.classList.remove('copied');
      }, 1400);
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

  async function tryAutoCopy(text) {
    if (window.getSelection) window.getSelection().removeAllRanges();
    try {
      await writePlainText(String(text));
    } catch (err) {
      // iOS may reject auto-copy outside a user gesture — silent fallback
    }
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
})();
