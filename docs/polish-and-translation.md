# Translation and polish

## Translation

Whisper can transcribe speech in any of its supported languages, but
*translation* — speak Spanish, get English back — is a separate task and
the bundled `large-v3-turbo` model has no translation training data, so
turbo + `task=translate` returns garbage.

The fix is two whisper-server instances side-by-side:

- `:8090` — turbo (transcription, fast, GPU). What this repo's
  `whisper_server.yaml` configures.
- `:8091` — a non-turbo, translate-capable model (e.g.
  `ggml-medium.bin`, CPU). Run by the sibling
  [`local-llm-hub`](https://github.com/ferraroroberto/local-llm-hub) hub as a lazy-spawn proxy: idle most
  of the time, cold-starts in 3–8 s on the first translate request after
  idle, then unloads after 5 min of inactivity.

Both webapp and tk window expose a **🌐 Translate to English** toggle.
When on, the request routes to `translate_base_url` (default
`http://127.0.0.1:8091`) with `task=translate`. When off, it routes to
the primary whisper-server as usual. The toggle is ephemeral — off on
every launch.

If you don't run the second instance, leave the toggle off and translate
is invisible. The hotkey path always transcribes — there is no F8
translate mode by design.

## Polish models

Defaults to `gemini_flash` — the local-llm-hub alias for Google's
Gemini Flash, routed via the `gemini` CLI on a Google AI Pro
subscription. The polish client allocates a generous `max_tokens`
(16k) to leave room for reasoning chains on models that emit them
(`<think>…</think>`); the hub strips the reasoning server-side before
returning. Other options surfaced in the dropdown:
`claude_haiku`, `claude_sonnet`, `claude_opus` (Claude subscription
via the `claude` CLI), `gemini_lite`, `gemini_pro` (the other two
Gemini tiers), and `agentic_light`, `agentic_heavy` (agentic-mode
tiers). These are stable version-free aliases — when the hub
points an alias at a newer display_name nothing in this repo needs
to change. Both surfaces (webapp and tk) expose a dropdown so
you can pick per-take. In the webapp the dropdown lives under
**Settings › Polish** (*Model*, *Style*, and *Instructions* to read the
prompt in a sheet); in the tk main window it sits inline on the polish
row. The webapp saves a choice the moment you make it (the tk window
uses **⭐ Save defaults**), persisting it to `config/webapp_config.json`
so the next launch defaults to it. Both surfaces share that file, so the defaults
sync between them.

The dropdown values come from `config/webapp_config.sample.json` — not
from Python. Adding or removing a hub alias is a one-line JSON edit;
no code change needed. Friendly labels are derived from the alias by
title-casing segments (`gemini_flash` → "Gemini Flash"), so a new
alias also needs no JS change.

## Polish styles

The system prompt is not hard-coded — it lives in
`config/polish_prompts.json` as a list of named entries, and the UI
exposes a **Polish style** dropdown alongside the model picker. The
file ships with one entry (`filler-words`) by default:

> You are a transcript polisher. Your only job is to remove filler
> words (uh, um, like, you know, sort of, kind of), false starts, and
> word repetitions. Do NOT summarize. Do NOT rephrase. Do NOT reorder
> sentences. Do NOT add new ideas. Do NOT remove any ideas.

To add a new style (e.g. grammar-only, correctness, raw-idea-to-prompt),
append an entry to `config/polish_prompts.json`:

```json
{
  "id": "grammar-only",
  "label": "Grammar fixes only",
  "description": "Fix spelling, grammar, punctuation. No content changes.",
  "system": "You are a copy editor..."
}
```

Restart the webapp + tk window. The new style appears in both
dropdowns. No Python changes required.

In the webapp's **Settings**, a read-only preview shows the system
prompt that will be sent for whichever style is currently selected — a
quick way to verify what the LLM will see. The tk window has a
**👁 Show prompt** button that opens the same preview in a popup.

If the JSON file is missing or invalid, the app falls back to a
hard-coded built-in `filler-words` entry so polish never breaks.

