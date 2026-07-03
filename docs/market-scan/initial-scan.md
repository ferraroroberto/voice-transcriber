# Voice dictation competitive reference

A durable comparison of this repo's feature set against three reference paid
apps. Not a roadmap or build plan — open ideas surfaced by this comparison are
tracked as GitHub issues (linked below), not as TODOs in this file. Update this
file's content in place as features ship or the competitive landscape moves;
don't append dated snapshots.

*Last verified: 2026-07-03.* The `/voice-market-scan` command refreshes this
file in place and files a GitHub issue for each newly-identified gap — it does
not write a new dated file.

- **Wispr Flow** — cloud, $15/mo, Mac/Windows/iOS/Android, the polish/popularity leader
- **Superwhisper** — Mac-only, $249.99 lifetime option, fully on-device, Privacy Award winner
- **Aqua Voice** — cloud, $8/mo, killer feature is real-time streaming preview + screen-context awareness

Sources used for the initial comparison are listed at the bottom.

---

## What this repo currently has

**Core inference**
- Local whisper.cpp with bundled `ggml-large-v3-turbo.bin`, CUDA + CPU fallback
- Configurable model swap via `whisper_server.yaml`
- Tray-resident process owns server lifecycle (spawn/stop/status/logs)
- Live rolling partial transcription while recording (text fills in as you speak)

**Dictation flow**
- Tray + global hotkey (default `F8`) — tap to toggle, hold to push-to-talk
- Type-at-caret injection (auto-paste into the focused window), plus auto-copy to clipboard
- All 99 Whisper-supported languages available; a per-user `enabled_languages` filter narrows the picker
- Translation mode (speak in another language, get English text back) via a second, translate-capable whisper-server instance
- Custom vocabulary / dictionary passed to whisper.cpp as a per-language `--prompt` (`config/vocabulary.json`)
- Auto-snippets / text replacements applied post-transcribe (`config/snippets.json`)
- Auto-stop on silence (RMS/dBFS-based) during recording
- Silence-skip dBFS gate (anti-hallucination) — distinct from the gain-boost idea below
- VU meter, recording popup
- Per-machine mic preferences

**Polish (LLM cleanup)**
- Local-LLM-hub integration (Gemini/Claude aliases via subscription CLIs)
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
- Incognito mode (no-storage toggle) — skips history/archive writes for a session
- History UI with re-transcribe, copy-selected, bulk clean

---

## Feature comparison

| Feature | This repo | Wispr Flow | Superwhisper | Aqua Voice |
|---|---|---|---|---|
| Local-only inference | Yes | No (cloud) | Yes | No (cloud) |
| Polish / filler removal | Yes, configurable | Yes | Yes | Yes |
| Type-at-caret injection | Yes | Yes | Yes | Yes |
| Custom vocabulary / dictionary | Yes | Yes | Yes | Yes |
| Voice command mode on the previous take | No — [tracked in #93](https://github.com/ferraroroberto/voice-transcriber/issues/93) | Yes | Yes | Yes |
| Auto-snippets / text replacements | Yes | Yes | Yes | No |
| App-aware polish style (per foreground app) | No — [tracked in #94](https://github.com/ferraroroberto/voice-transcriber/issues/94) | Yes | Partial | Yes |
| All 99 Whisper languages in picker | Yes | Yes | Yes | Yes |
| Translation mode | Yes | Yes | Yes | No |
| Live streaming preview (text appears as you speak) | Yes | Partial | No | Yes (killer feature) |
| Push-to-talk alongside toggle | Yes | Yes | Yes | Yes |
| Auto-stop on silence | Yes | Yes | Yes | Yes |
| Quiet-environment gain boost (distinct from anti-hallucination silence gate) | No — [tracked in #97](https://github.com/ferraroroberto/voice-transcriber/issues/97) | Yes | Yes | — |
| Usage analytics (words/min, daily count, time saved) | No — [tracked in #95](https://github.com/ferraroroberto/voice-transcriber/issues/95), low priority | Yes | Yes | Yes |
| Cross-device sync of dictionary/snippets | No — [tracked in #96](https://github.com/ferraroroberto/voice-transcriber/issues/96) | Yes (cloud) | Yes (iCloud) | Yes |
| Privacy mode / no-storage toggle | Yes (incognito mode) | Yes | Always private | — |
| Cloudflare-tunnel public URL with iOS PWA | Yes | No | No | No |
| Multi-surface (webapp + tk + tray + CLI) | Yes | Desktop + mobile | Desktop only | Desktop + iOS |
| Subscription cost | $0 | $15/mo | $249 lifetime | $8/mo |

## Open ideas

Everything marked "No" above with a tracked issue is a candidate for future
work, not a commitment — pick them up like any other issue when priority
allows. Nothing here should be treated as a build order; each issue stands on
its own and is independently shippable.

- [#93 — Voice command mode](https://github.com/ferraroroberto/voice-transcriber/issues/93)
- [#94 — App-aware polish style](https://github.com/ferraroroberto/voice-transcriber/issues/94)
- [#95 — Usage analytics](https://github.com/ferraroroberto/voice-transcriber/issues/95) (low priority)
- [#96 — Cross-device sync of vocabulary/snippets](https://github.com/ferraroroberto/voice-transcriber/issues/96)
- [#97 — Quiet-environment gain boost](https://github.com/ferraroroberto/voice-transcriber/issues/97)

## Where this repo already wins

- **Local-only inference** with no subscription, unlike Wispr Flow and Aqua Voice.
- **Cloudflare-tunnel iOS PWA** — none of the three competitors offer a
  self-hosted persistent public URL with home-screen install.
- **Multi-surface parity** — webapp, tk window, tray, and CLI all reach the
  same feature set, versus competitors' desktop-only or desktop+mobile split.
- **$0 subscription cost** against $8–15/mo (or a $249 lifetime buy-in).

---

## Sources

- [Wispr Flow — Features](https://wisprflow.ai/features)
- [Wispr Flow Review 2026 (tldv)](https://tldv.io/blog/wisprflow/)
- [Wispr Flow vs Superwhisper (Voibe)](https://www.getvoibe.com/resources/wispr-flow-vs-superwhisper/)
- [Aqua Voice vs Wispr Flow (Voibe)](https://www.getvoibe.com/resources/aqua-voice-vs-wispr-flow/)
- [11 Best Superwhisper Alternatives 2026 (Voibe)](https://www.getvoibe.com/blog/superwhisper-alternatives/)
- [Best AI Dictation Apps 2026 (Zapier)](https://zapier.com/blog/best-text-dictation-software/)
- [Best Speech Recognition Software 2026 (Medium / Ryan Shrott)](https://medium.com/@ryanshrott/the-best-speech-recognition-software-in-2026-why-you-should-stop-typing-26f9fd650b60)
