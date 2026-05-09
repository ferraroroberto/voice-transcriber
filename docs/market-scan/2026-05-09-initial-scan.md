# Voice dictation market scan — 2026-05-09

Initial baseline. Compares this repo's feature set against the three reference paid apps:

- **Wispr Flow** — cloud, $15/mo, Mac/Windows/iOS/Android, the polish/popularity leader
- **Superwhisper** — Mac-only, $249.99 lifetime option, fully on-device, Privacy Award winner
- **Aqua Voice** — cloud, $8/mo, killer feature is real-time streaming preview + screen-context awareness

Sources used for this scan are listed at the bottom.

---

## What this repo currently has

**Core inference**
- Local whisper.cpp with bundled `ggml-large-v3-turbo.bin`, CUDA + CPU fallback
- Configurable model swap via `whisper_server.yaml`
- Tray-resident process owns server lifecycle (spawn/stop/status/logs)

**Dictation flow**
- Tray + global hotkey (`Ctrl+Alt+Space` default, configurable) — toggle-style
- Auto-copy to clipboard
- Languages: English, Spanish, Italian (3 of Whisper's 99)
- Silence-skip dBFS gate (anti-hallucination)
- VU meter, recording popup
- Per-machine mic preferences

**Polish (LLM cleanup)**
- Local-LLM-hub integration (Gemma variants, Claude Haiku via subscription)
- JSON-configurable polish style library (`config/polish_prompts.json`)
- Prompt preview, model picker, style picker
- Editable transcript and polished text before/after polishing
- Append mode (chain takes across locations/apps)

**Surfaces (parity rule)**
- Tray (pystray + pynput)
- Tk main window
- FastAPI mobile-first webapp (HTTPS on `:8443`)
- CLI

**Remote access — unique vs. all three competitors**
- Cloudflare named tunnel (persistent public URL, e.g. `voice.<your-domain>`)
- Cloudflare Access (Google sign-in restricted to allowlist email)
- Bearer token auth + password gate (with login overlay UX)
- iOS PWA: home-screen icon, persistent mic permission, mic stream kept alive between recordings, partial-recording survival if the phone dies mid-take

**Storage / privacy**
- Local archive `archive/YYYY/MM/DD/HH-MM-SS-<id>/` with raw + transcoded + transcript + polished + meta
- 30-day auto-cleanup
- History UI with re-transcribe, copy-selected, bulk clean

---

## Feature comparison

Effort key: **S** = a weekend or less, **M** = 1–2 weeks, **L** = multi-week.

| Feature | This repo | Wispr Flow | Superwhisper | Aqua Voice | Effort | Verdict |
|---|---|---|---|---|---|---|
| Local-only inference | ✅ | ❌ cloud | ✅ | ❌ cloud | — | **Already win** |
| Polish / filler removal | ✅ configurable | ✅ | ✅ | ✅ | — | Parity |
| **Type-at-caret injection** (paste into focused app, not just clipboard) | ❌ | ✅ killer | ✅ | ✅ | **S** | **Build — biggest single unlock** |
| **Custom vocabulary / dictionary** (names, brands, jargon) | ❌ | ✅ | ✅ | ✅ | **S** | **Build** — pass to whisper.cpp `--prompt` |
| **Voice command mode** ("make it concise", "as bullets") on the previous take | partial via polish styles | ✅ | ✅ | ✅ | **M** | **Build** — leverages existing polish stack |
| **Auto-snippets / text replacements** ("ttyl" → full phrase) | ❌ | ✅ | ✅ | ❌ | **S** | **Build** |
| **App-aware polish style** (Slack vs Outlook vs VSCode) | ❌ | ✅ | partial | ✅ | **M** | **Build** — read foreground window title, route style |
| **All 99 Whisper languages** in picker | ❌ (3 only) | ✅ | ✅ | ✅ | **S** | **Build** |
| **Translation mode** (speak ES → EN out) | ❌ | ✅ | ✅ | ❌ | **S** | **Build** — whisper.cpp `--translate` flag |
| **Live streaming preview** (text appears as you speak) | ❌ | partial | ❌ | ✅ killer | **L** | **Skip** — turbo model already ~20× realtime, gain too small for the engineering cost |
| **Push-to-talk** (hold key) alongside toggle | ❌ toggle only | both | both | both | **S** | **Build** |
| **Auto-stop on silence** (VAD end-of-speech) | ❌ | ✅ | ✅ | ✅ | **M** | **Build** — `webrtcvad` or `silero-vad` |
| **Whisper-mode / quiet-environment gain boost** | partial (config dBFS) | ✅ | ✅ | — | **S** | Surface in UI |
| **Usage analytics** (words/min, daily count, time saved) | ❌ | ✅ | ✅ | ✅ | **M** | **Skip for now** — low ROI |
| **Cross-device sync of dict/snippets** | partial via shared config | ✅ cloud | ✅ iCloud | ✅ | **M** | **Build** — extend existing `webapp_config.json` sync |
| **Privacy mode / no-storage toggle** | ❌ (always archives) | ✅ | always private | — | **S** | **Build** — flip flag, skip archive write |
| Cloudflare-tunnel public URL with iOS PWA | ✅ | ❌ | ❌ | ❌ | — | **Already win** |
| Multi-surface (webapp + tk + tray + CLI) | ✅ | desktop+mobile | desktop only | desktop+iOS | — | **Already win** |
| Subscription cost | $0 | $15/mo | $249 lifetime | $8/mo | — | **Already win** |

---

## Recommended build order

### Phase 1 — high ROI, low effort (a weekend each)

1. **Type-at-caret injection.** Tray/config toggle: simulate paste (`Ctrl+V`) into the focused window via `pynput`/`SendInput` after auto-copy. Without this we're a great clipboard tool; with it we are Wispr-at-home. *Lands in:* `app/gui/tray.py` post-transcription hook, plus a new `src/inject.py` for the OS keystroke layer.
2. **Custom vocabulary.** New top-level `prompts` field in `config/config.json` → joined and passed to whisper.cpp as `--prompt "Roberto, Ferraro, Anthropic, Claude, ..."`. Whisper biases token probabilities toward those names. *Lands in:* `src/transcription_client.py` request builder + `config.sample.json` doc entry.
3. **All 99 languages in picker** + **translation mode** toggle. UI work + one yaml flag (`--translate`). *Lands in:* `src/app_config.py` (language enum → free string), webapp settings panel, tk language dropdown.
4. **Push-to-talk hotkey.** Second hotkey (e.g. `<F11>` hold) wired in parallel to the existing toggle. *Lands in:* `app/gui/tray.py` hotkey registration.
5. **Auto-snippets.** New `config/snippets.json` → applied post-transcribe, pre-clipboard. Trivial dict lookup. *Lands in:* `src/snippets.py`, called from the same point that decides clipboard payload.

### Phase 2 — medium effort, high daily payoff

6. **Voice command mode.** Detect a wake prefix in the transcript (e.g. `"flow:"` or `"edit:"`) → route to the LLM hub against the *previous* take instead of as new dictation. Polish pipeline already exists; this is a different system prompt + last-transcript memory. *Lands in:* `src/polish.py` + new `src/voice_command.py` for prefix parsing + a "last take" buffer in webapp/tk state.
7. **App-aware polish style.** On Windows, `pygetwindow.getActiveWindow().title` (or `pywin32`) → match against rules in a new `config/app_styles.json` → auto-select polish style. (Slack → casual, Outlook → formal, VSCode → raw.) *Lands in:* `src/app_context.py` + polish-style resolver.
8. **Auto-stop on silence (VAD).** `webrtcvad` or `silero-vad` running on the live audio buffer; stop recording N seconds after speech ends. *Lands in:* `src/recorder.py` (extend the capture loop) + a `vad_silence_ms` config knob.

### Phase 3 — defer or skip

9. **Live streaming preview.** Real engineering cost (chunked VAD + partial decoding window). Turbo model finishes in ~3s for a 60s take — gain is small, complexity is high. **Skip until 1–8 are done.**
10. **Usage analytics dashboard.** Cute, not load-bearing for the dictation flow. **Skip unless explicitly wanted.**

---

## Worth-investment verdict

**Yes, easily.** Phase 1 alone (5 features × ~weekend each) closes roughly 70% of the gap to the $15/mo apps, while keeping every advantage they cannot match: local inference, the Cloudflare-tunnel iOS PWA, multi-surface parity, configurable polish styles, no subscription.

The single biggest strategic unlock is **caret injection**. It is the difference between "great clipboard tool" and "Wispr-at-home, but mine."

---

## Sources

- [Wispr Flow — Features](https://wisprflow.ai/features)
- [Wispr Flow Review 2026 (tldv)](https://tldv.io/blog/wisprflow/)
- [Wispr Flow vs Superwhisper (Voibe)](https://www.getvoibe.com/resources/wispr-flow-vs-superwhisper/)
- [Aqua Voice vs Wispr Flow (Voibe)](https://www.getvoibe.com/resources/aqua-voice-vs-wispr-flow/)
- [11 Best Superwhisper Alternatives 2026 (Voibe)](https://www.getvoibe.com/blog/superwhisper-alternatives/)
- [Best AI Dictation Apps 2026 (Zapier)](https://zapier.com/blog/best-text-dictation-software/)
- [Best Speech Recognition Software 2026 (Medium / Ryan Shrott)](https://medium.com/@ryanshrott/the-best-speech-recognition-software-in-2026-why-you-should-stop-typing-26f9fd650b60)
